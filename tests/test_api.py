"""WP5 API 계약 테스트 (FR-8.1 / FR-9.x).

FastAPI TestClient 로 REST 엔드포인트를 검증한다:
    · /health 200 (Neo4j 미가용이어도 degraded 200)
    · /cases 200 + 점수 내림차순 정렬 + 임계치/페이징
    · /cases/{id} 상세에 소명문·경로·환각가드 포함
    · 인증/인가: 권한 없는 역할 403, 적절 역할 200
    · 판정 POST → 상태 변경 반영
    · /kpi 200 + 필드 존재
    · 그래프 탐색은 Neo4j 필요 → integration 마커

대부분 smoke(임시 SQLite + 신호 캐시 주입 → Neo4j 불필요). 그래프/실데이터는
integration. 케이스 저장소·신호 캐시는 의존성 오버라이드로 격리한다.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import pytest
from fastapi.testclient import TestClient

from api.deps import get_case_store, get_signal_cache
from api.main import create_app
from core.cases import CaseStore, CaseStatus

pytestmark = pytest.mark.smoke


# ---------------------------------------------------------------------------
# 헤더(역할) 헬퍼
# ---------------------------------------------------------------------------
ANALYST = {"X-Role": "FRAUD_ANALYST", "X-Actor": "analyst-kim"}
ADJUSTER = {"X-Role": "CLAIMS_ADJUSTER", "X-Actor": "adjuster-lee"}
PUBLIC = {"X-Role": "PUBLIC"}
ADMIN_TOKEN = {"Authorization": "Bearer admin-token"}


# ---------------------------------------------------------------------------
# 픽스처: 임시 케이스 저장소 + 신호 캐시를 주입한 TestClient
# ---------------------------------------------------------------------------
@pytest.fixture()
def sample_signals() -> List[Dict[str, Any]]:
    """공유 계좌 + 교차 목격 + 핫스팟 기여 신호(scoring 출력 형태)."""
    return [
        {"type": "SHARED_ACCOUNT", "weight": 57.0, "shared_key": "0118569172667",
         "num_customers": 3, "shared_with": ["CUST-05015", "CUST-05013"]},
        {"type": "CROSS_WITNESS", "weight": 48.0, "cluster_size": 3,
         "witnessed_with": ["CUST-05015", "CUST-05013"]},
        {"type": "HOTSPOT_REPAIR_SHOP", "weight": 8.0, "entity_id": "RSH-0089",
         "entity_name": "명품카정비89", "num_customers": 104},
        {"type": "_alert_threshold", "value": 50.0},
    ]


@pytest.fixture()
def client(tmp_path: Path, sample_signals: List[Dict[str, Any]]) -> TestClient:
    """임시 DB + 신호 캐시를 주입한 TestClient.

    케이스 3건(점수 90/72/40)을 생성하고 상위 고객에 기여 신호를 캐시한다.
    """
    store = CaseStore(db_path=tmp_path / "api_cases.db")
    store.create_case(case_id="CASE-CUST-05014", customer_id="CUST-05014",
                      score=90.0, ring_id="RING-003")
    store.create_case(case_id="CASE-CUST-02000", customer_id="CUST-02000",
                      score=72.0, ring_id="RING-007")
    store.create_case(case_id="CASE-CUST-00001", customer_id="CUST-00001",
                      score=40.0)  # 임계 미만(기본 50)

    signal_cache: Dict[str, List[Dict[str, Any]]] = {
        "CUST-05014": sample_signals,
        "CUST-02000": [
            {"type": "SHARED_PHONE", "weight": 35.0, "shared_key": "01099887766",
             "num_customers": 2, "shared_with": ["CUST-02001"]},
        ],
    }

    app = create_app()
    app.dependency_overrides[get_case_store] = lambda: store
    app.dependency_overrides[get_signal_cache] = lambda: signal_cache
    return TestClient(app)


# ===========================================================================
# /health
# ===========================================================================
def test_health_returns_200(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["service"] == "thoth-on-api"
    assert "neo4j_connected" in body
    assert body["case_store"] is True
    assert body["status"] in {"ok", "degraded"}


# ===========================================================================
# /cases — 큐(점수순·임계치·페이징)
# ===========================================================================
def test_cases_list_sorted_desc_and_thresholded(client: TestClient) -> None:
    resp = client.get("/cases", headers=ANALYST)
    assert resp.status_code == 200
    body = resp.json()
    # 기본 임계 50 → 40점 케이스는 제외(2건만).
    assert body["threshold"] == 50.0
    assert body["total"] == 2
    scores = [item["score"] for item in body["items"]]
    assert scores == sorted(scores, reverse=True), "점수 내림차순 정렬 실패"
    assert scores == [90.0, 72.0]
    # 기여 신호 요약 노출.
    top = body["items"][0]
    assert top["case_id"] == "CASE-CUST-05014"
    assert "SHARED_ACCOUNT" in top["signal_summary"]
    assert "_alert_threshold" not in top["signal_summary"]  # 내부 메타 제외


def test_cases_list_threshold_query(client: TestClient) -> None:
    # 임계 0 → 3건 모두 포함.
    resp = client.get("/cases", headers=ANALYST, params={"threshold": 0})
    assert resp.status_code == 200
    assert resp.json()["total"] == 3


def test_cases_list_pagination(client: TestClient) -> None:
    resp = client.get("/cases", headers=ANALYST,
                      params={"threshold": 0, "limit": 1, "offset": 1})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 3
    assert body["count"] == 1
    assert body["offset"] == 1
    # 정렬상 2번째(72점).
    assert body["items"][0]["score"] == 72.0


def test_cases_list_status_filter(client: TestClient) -> None:
    resp = client.get("/cases", headers=ANALYST,
                      params={"threshold": 0, "status": "UNASSIGNED"})
    assert resp.status_code == 200
    assert all(i["status"] == "UNASSIGNED" for i in resp.json()["items"])


def test_cases_list_bad_status_400(client: TestClient) -> None:
    resp = client.get("/cases", headers=ANALYST, params={"status": "NONSENSE"})
    assert resp.status_code == 400


# ===========================================================================
# /cases/{id} — 상세(소명문·경로·환각가드) + 인가
# ===========================================================================
def test_case_detail_includes_explanation_and_paths(client: TestClient) -> None:
    resp = client.get("/cases/CASE-CUST-05014", headers=ANALYST)
    assert resp.status_code == 200
    body = resp.json()
    assert body["case_id"] == "CASE-CUST-05014"
    assert body["customer_id"] == "CUST-05014"
    # 근거 경로(시각화 입력).
    assert len(body["paths"]) == 3
    path_types = {p["signal_type"] for p in body["paths"]}
    assert "SHARED_ACCOUNT" in path_types
    # 자연어 소명문 + 환각가드 결과.
    exp = body["explanation"]
    assert exp["text"], "소명문이 비어있음"
    assert "grounding" in exp
    assert exp["accepted"] is True, "Mock 소명문은 환각가드를 통과해야 함"
    assert exp["grounding"]["grounded"] is True
    assert exp["grounding"]["hallucinated"] == []
    # 기여 신호.
    sig_types = {s["type"] for s in body["signals"]}
    assert "SHARED_ACCOUNT" in sig_types and "CROSS_WITNESS" in sig_types


def test_case_detail_404_for_missing(client: TestClient) -> None:
    resp = client.get("/cases/CASE-NOPE", headers=ANALYST)
    assert resp.status_code == 404


def test_case_detail_forbidden_for_public(client: TestClient) -> None:
    # PUBLIC 역할은 FRAUD_CASE 등급에 접근 불가 → 403.
    resp = client.get("/cases/CASE-CUST-05014", headers=PUBLIC)
    assert resp.status_code == 403
    assert "권한" in resp.json()["detail"]


def test_case_detail_forbidden_for_adjuster(client: TestClient) -> None:
    # CLAIMS_ADJUSTER(권한 1) < FRAUD_CASE(요구 2) → 403.
    resp = client.get("/cases/CASE-CUST-05014", headers=ADJUSTER)
    assert resp.status_code == 403


def test_case_detail_allowed_via_bearer_token(client: TestClient) -> None:
    # admin-token → ADMIN(권한 4) ≥ FRAUD_CASE → 200.
    resp = client.get("/cases/CASE-CUST-05014", headers=ADMIN_TOKEN)
    assert resp.status_code == 200


# ===========================================================================
# /cases/{id}/assign — 배정 + 인가
# ===========================================================================
def test_assign_transitions_to_investigating(client: TestClient) -> None:
    resp = client.post("/cases/CASE-CUST-05014/assign", headers=ANALYST,
                       json={"assignee": "analyst-kim"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["assignee"] == "analyst-kim"
    assert body["status"] == CaseStatus.INVESTIGATING.value


def test_assign_forbidden_for_public(client: TestClient) -> None:
    resp = client.post("/cases/CASE-CUST-05014/assign", headers=PUBLIC,
                       json={"assignee": "x"})
    assert resp.status_code == 403


def test_assign_404_for_missing(client: TestClient) -> None:
    resp = client.post("/cases/CASE-NOPE/assign", headers=ANALYST,
                       json={"assignee": "x"})
    assert resp.status_code == 404


# ===========================================================================
# /cases/{id}/verdict — 판정 → 상태 변경 반영 + 인가
# ===========================================================================
def test_verdict_changes_status_to_fraud(client: TestClient) -> None:
    # 배정으로 INVESTIGATING 전이 후 FRAUD 판정.
    client.post("/cases/CASE-CUST-05014/assign", headers=ANALYST,
                json={"assignee": "analyst-kim"})
    resp = client.post("/cases/CASE-CUST-05014/verdict", headers=ANALYST,
                       json={"verdict": "FRAUD", "comment": "공모 확인"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["recorded"] is True
    assert body["status"] == CaseStatus.FRAUD.value

    # 상세에서도 상태 반영 + 판정 이력 확인.
    detail = client.get("/cases/CASE-CUST-05014", headers=ANALYST).json()
    assert detail["status"] == CaseStatus.FRAUD.value
    assert any(v["label"] == "FRAUD" for v in detail["verdicts"])


def test_verdict_hold_transition(client: TestClient) -> None:
    client.post("/cases/CASE-CUST-02000/assign", headers=ANALYST,
                json={"assignee": "analyst-kim"})
    resp = client.post("/cases/CASE-CUST-02000/verdict", headers=ANALYST,
                       json={"verdict": "HOLD", "comment": "자료 대기"})
    assert resp.status_code == 200
    assert resp.json()["status"] == CaseStatus.HOLD.value


def test_verdict_invalid_label_400(client: TestClient) -> None:
    resp = client.post("/cases/CASE-CUST-05014/verdict", headers=ANALYST,
                       json={"verdict": "MAYBE"})
    assert resp.status_code == 400


def test_verdict_forbidden_for_public(client: TestClient) -> None:
    resp = client.post("/cases/CASE-CUST-05014/verdict", headers=PUBLIC,
                       json={"verdict": "FRAUD"})
    assert resp.status_code == 403


# ===========================================================================
# /kpi — 경영 대시보드 요약(FR-9.2) + 인가
# ===========================================================================
def test_kpi_returns_fields(client: TestClient) -> None:
    resp = client.get("/kpi", headers=ANALYST)
    assert resp.status_code == 200
    body = resp.json()
    for field in (
        "total_cases", "status_distribution", "suspected_rings",
        "high_risk_cases", "fraud_verdicts", "avg_score",
        "score_separation", "threshold",
    ):
        assert field in body, f"KPI 필드 누락: {field}"
    assert body["total_cases"] == 3
    # 고위험(>=50) 2건.
    assert body["high_risk_cases"] == 2
    # 상태 분포 합 == 총 케이스.
    assert sum(body["status_distribution"].values()) == body["total_cases"]


def test_kpi_accessible_to_adjuster(client: TestClient) -> None:
    # CLAIMS 등급 → CLAIMS_ADJUSTER 이상이면 200.
    resp = client.get("/kpi", headers=ADJUSTER)
    assert resp.status_code == 200


def test_kpi_forbidden_for_public(client: TestClient) -> None:
    resp = client.get("/kpi", headers=PUBLIC)
    assert resp.status_code == 403


def test_kpi_score_separation(client: TestClient) -> None:
    resp = client.get("/kpi", headers=ANALYST)
    body = resp.json()
    # 고위험 평균(90,72) - 저위험 평균(40) = 81 - 40 = 41.
    assert body["avg_high_risk_score"] == 81.0
    assert body["avg_low_risk_score"] == 40.0
    assert body["score_separation"] == 41.0


# ===========================================================================
# OpenAPI 스펙 노출
# ===========================================================================
def test_openapi_spec_exposed(client: TestClient) -> None:
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    spec = resp.json()
    assert spec["info"]["title"] == "THOTH-ON API"
    paths = spec["paths"]
    assert "/cases" in paths
    assert "/cases/{case_id}" in paths
    assert "/graph/customer/{customer_id}" in paths
    assert "/kpi" in paths


# ===========================================================================
# 그래프 탐색 — Neo4j 필요(integration)
# ===========================================================================
@pytest.mark.integration
def test_graph_customer_subnetwork(neo4j_available: bool) -> None:
    if not neo4j_available:
        pytest.skip("Neo4j 미가용 — `make up && make wait-neo4j` 후 재실행")

    from detection import scoring

    # 실데이터에서 링 멤버 고객 1명을 고른다.
    risks = scoring.score_customers()
    flagged = scoring.alerts(risks)
    if not flagged:
        pytest.skip("탐지된 알림 케이스 없음 — 시드 데이터 적재 필요")
    customer_id = flagged[0].customer_id

    app = create_app()
    c = TestClient(app)
    resp = c.get(f"/graph/customer/{customer_id}", headers=ANALYST)
    assert resp.status_code == 200
    body = resp.json()
    assert body["customer_id"] == customer_id
    assert body["node_count"] >= 1
    # vis-network 형태 확인.
    assert all({"id", "label", "group"} <= set(n) for n in body["nodes"])
    for e in body["edges"]:
        assert "from" in e and "to" in e and "label" in e


@pytest.mark.integration
def test_graph_forbidden_for_public(neo4j_available: bool) -> None:
    if not neo4j_available:
        pytest.skip("Neo4j 미가용")
    app = create_app()
    c = TestClient(app)
    resp = c.get("/graph/customer/CUST-00001", headers=PUBLIC)
    assert resp.status_code == 403
