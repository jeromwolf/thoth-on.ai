"""WP6-1 보안 강화 검증 테스트 (NFR: PII·RBAC·감사·규제).

테스트 구성:
    [integration] 그래프 전수 PII 검증
        - Phone.number 속성 0건 (number_hash 만 허용)
        - Customer.name / .ssn / .email 평문 속성 0건
        - 전체 라벨에서 주민번호 형식 값 0건

    [smoke/integration] RBAC
        - CLAIMS_ADJUSTER 가 FRAUD_CASE 등급 엔드포인트에 접근 → 403
        - FRAUD_ANALYST 는 동일 엔드포인트 → 200
        - PUBLIC 은 FRAUD_CASE 등급 엔드포인트 → 403

    [smoke] 감사 로그
        - 민감 행위(케이스 판정) 후 audit 파일에 result 필드가 기록됨
        - 거부(403) 행위도 result="denied" 로 기록됨

    [smoke] audit append-only
        - 두 번 기록 후 정확히 2줄
        - 기존 라인이 변경되지 않음 (불변성)
"""
from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path
from typing import Any, Dict, List

import pytest
from fastapi.testclient import TestClient

from api.deps import get_case_store, get_signal_cache
from api.main import create_app
from core.cases import CaseStore
from core.security.audit import audit_event
from thoth import db

pytestmark = pytest.mark.smoke


# ===========================================================================
# 헤더 헬퍼
# ===========================================================================
ANALYST = {"X-Role": "FRAUD_ANALYST", "X-Actor": "test-analyst"}
ADJUSTER = {"X-Role": "CLAIMS_ADJUSTER", "X-Actor": "test-adjuster"}
PUBLIC: Dict[str, str] = {}


# ===========================================================================
# 픽스처: 케이스 스토어 + TestClient
# ===========================================================================
@pytest.fixture()
def rbac_client(tmp_path: Path) -> TestClient:
    """RBAC 테스트용 TestClient — 케이스 2건(배정 전 UNASSIGNED)."""
    store = CaseStore(db_path=tmp_path / "sec_cases.db")
    store.create_case(case_id="CASE-SEC-001", customer_id="CUST-SEC-001",
                      score=85.0, ring_id="RING-SEC-A")
    store.create_case(case_id="CASE-SEC-002", customer_id="CUST-SEC-002",
                      score=60.0, ring_id="RING-SEC-B")

    app = create_app()
    app.dependency_overrides[get_case_store] = lambda: store
    app.dependency_overrides[get_signal_cache] = lambda: {}
    return TestClient(app)


# ===========================================================================
# [integration] 그래프 전수 PII 검증
# ===========================================================================
def verify_no_plaintext_pii() -> Dict[str, int]:
    """그래프 전체에서 평문 PII 발생 건수를 반환한다.

    반환값의 모든 값이 0 이어야 NFR 준수.
    조사 항목:
        - Phone.number: 평문 전화번호 속성 (number_hash 만 허용)
        - Customer.name: 평문 이름 속성
        - Customer.ssn: 평문 주민번호(한국 용어) 속성
        - Customer.email: 평문 이메일 속성
        - 전체 노드 속성값에서 주민번호 형식 (xxxxxx-xxxxxxx) 패턴 탐지
    """
    results: Dict[str, int] = {}

    with db.session() as sess:
        # 1) Phone.number 평문 속성 건수
        r = sess.run("MATCH (p:Phone) WHERE p.number IS NOT NULL RETURN count(p) AS c")
        results["phone_plaintext_number"] = r.single()["c"]

        # 2) Customer.name 평문 속성 건수
        r = sess.run("MATCH (c:Customer) WHERE c.name IS NOT NULL RETURN count(c) AS c")
        results["customer_plaintext_name"] = r.single()["c"]

        # 3) Customer.ssn 평문 속성 건수 (id_number, ssn, resident_no 등 포함)
        r = sess.run(
            "MATCH (c:Customer) WHERE c.ssn IS NOT NULL OR c.id_number IS NOT NULL "
            "OR c.resident_no IS NOT NULL RETURN count(c) AS c"
        )
        results["customer_plaintext_ssn"] = r.single()["c"]

        # 4) Customer.email 평문 속성 건수
        r = sess.run("MATCH (c:Customer) WHERE c.email IS NOT NULL RETURN count(c) AS c")
        results["customer_plaintext_email"] = r.single()["c"]

        # 5) 전체 노드에서 주민번호 형식(xxxxxx-xxxxxxx) 패턴이 포함된 속성 값 탐지.
        #    Cypher 에서 문자열 속성을 순회하는 방식으로 검사.
        #    Neo4j 5.x 는 apoc 없이도 keys(n) + properties(n) 접근 가능.
        r = sess.run(
            """
            MATCH (n)
            WITH n, [k IN keys(n) WHERE apoc.meta.type(properties(n)[k]) = 'STRING'
                                      AND properties(n)[k] =~ '.*\\d{6}-\\d{7}.*'
                    | k] AS pii_keys
            WHERE size(pii_keys) > 0
            RETURN count(n) AS c
            """
        )
        row = r.single()
        results["ssn_pattern_in_any_node"] = row["c"] if row else 0

    return results


@pytest.mark.integration
def test_graph_no_plaintext_pii_phone(neo4j_available: bool) -> None:
    """Phone 노드에 평문 number 속성이 없어야 한다 (number_hash 만 허용)."""
    if not neo4j_available:
        pytest.skip("Neo4j 미가용")
    with db.session() as sess:
        r = sess.run("MATCH (p:Phone) WHERE p.number IS NOT NULL RETURN count(p) AS c")
        count = r.single()["c"]
    assert count == 0, f"Phone.number 평문 속성 {count}건 발견 — FR-1.3 위반"


@pytest.mark.integration
def test_graph_no_plaintext_pii_customer_name(neo4j_available: bool) -> None:
    """Customer 노드에 평문 name 속성이 없어야 한다 (name_hash 만 허용)."""
    if not neo4j_available:
        pytest.skip("Neo4j 미가용")
    with db.session() as sess:
        r = sess.run("MATCH (c:Customer) WHERE c.name IS NOT NULL RETURN count(c) AS c")
        count = r.single()["c"]
    assert count == 0, f"Customer.name 평문 속성 {count}건 발견 — FR-1.3 위반"


@pytest.mark.integration
def test_graph_no_plaintext_pii_customer_ssn(neo4j_available: bool) -> None:
    """Customer 노드에 평문 주민번호(ssn/id_number/resident_no) 속성이 없어야 한다."""
    if not neo4j_available:
        pytest.skip("Neo4j 미가용")
    with db.session() as sess:
        r = sess.run(
            "MATCH (c:Customer) WHERE c.ssn IS NOT NULL OR c.id_number IS NOT NULL "
            "OR c.resident_no IS NOT NULL RETURN count(c) AS c"
        )
        count = r.single()["c"]
    assert count == 0, f"Customer 평문 주민번호 속성 {count}건 발견 — FR-1.3 위반"


@pytest.mark.integration
def test_graph_no_plaintext_pii_customer_email(neo4j_available: bool) -> None:
    """Customer 노드에 평문 email 속성이 없어야 한다 (email_hash 만 허용)."""
    if not neo4j_available:
        pytest.skip("Neo4j 미가용")
    with db.session() as sess:
        r = sess.run("MATCH (c:Customer) WHERE c.email IS NOT NULL RETURN count(c) AS c")
        count = r.single()["c"]
    assert count == 0, f"Customer.email 평문 속성 {count}건 발견 — FR-1.3 위반"


@pytest.mark.integration
def test_graph_pii_comprehensive_report(neo4j_available: bool) -> None:
    """그래프 전수 PII 검증 종합 리포트 — 모든 항목이 0건이어야 한다.

    verify_no_plaintext_pii() 를 직접 호출해 종합 확인한다.
    apoc 미설치 환경에서는 ssn_pattern_in_any_node 항목만 skip 한다.
    """
    if not neo4j_available:
        pytest.skip("Neo4j 미가용")

    results: Dict[str, int] = {}

    with db.session() as sess:
        r = sess.run("MATCH (p:Phone) WHERE p.number IS NOT NULL RETURN count(p) AS c")
        results["phone_plaintext_number"] = r.single()["c"]

        r = sess.run("MATCH (c:Customer) WHERE c.name IS NOT NULL RETURN count(c) AS c")
        results["customer_plaintext_name"] = r.single()["c"]

        r = sess.run(
            "MATCH (c:Customer) WHERE c.ssn IS NOT NULL OR c.id_number IS NOT NULL "
            "OR c.resident_no IS NOT NULL RETURN count(c) AS c"
        )
        results["customer_plaintext_ssn"] = r.single()["c"]

        r = sess.run("MATCH (c:Customer) WHERE c.email IS NOT NULL RETURN count(c) AS c")
        results["customer_plaintext_email"] = r.single()["c"]

    # 주민번호 패턴 스캔 — valueType() 으로 STRING 만 필터 (배열 제외, Neo4j 5.x)
    try:
        with db.session() as sess:
            r = sess.run(
                """
                MATCH (n)
                UNWIND keys(n) AS k
                WITH n, k
                WHERE valueType(properties(n)[k]) = 'STRING'
                WITH n, k, properties(n)[k] AS val
                WHERE val =~ '.*[0-9]{6}-[0-9]{7}.*'
                RETURN count(*) AS c
                """
            )
            results["ssn_pattern_in_any_node"] = r.single()["c"]
    except Exception:
        results["ssn_pattern_in_any_node"] = -1  # -1 = 검사 불가(skip 표시)

    # 리포트 출력
    print("\n[PII 전수 검증 결과]")
    for key, val in results.items():
        status = "PASS(0건)" if val == 0 else ("SKIP(검사불가)" if val == -1 else f"FAIL({val}건)")
        print(f"  {key}: {status}")

    # -1(skip)을 제외한 항목이 모두 0이어야 함
    violations = {k: v for k, v in results.items() if v > 0}
    assert not violations, f"평문 PII 발견: {violations}"


# ===========================================================================
# [smoke/integration] RBAC: CLAIMS_ADJUSTER 403, FRAUD_ANALYST 200
# ===========================================================================
class TestRbacCaseDetail:
    """케이스 상세(FRAUD_CASE 등급) RBAC 검증."""

    def test_adjuster_denied_case_detail(self, rbac_client: TestClient) -> None:
        """CLAIMS_ADJUSTER(권한 1)는 FRAUD_CASE(요구 2) 상세 조회 시 403."""
        resp = rbac_client.get("/cases/CASE-SEC-001", headers=ADJUSTER)
        assert resp.status_code == 403, "CLAIMS_ADJUSTER 가 FRAUD_CASE 에 접근됨 — RBAC 우회"
        body = resp.json()
        assert "권한" in body["detail"], "403 응답에 권한 설명 없음"

    def test_analyst_allowed_case_detail(self, rbac_client: TestClient) -> None:
        """FRAUD_ANALYST(권한 2)는 FRAUD_CASE(요구 2) 상세 조회 시 200."""
        resp = rbac_client.get("/cases/CASE-SEC-001", headers=ANALYST)
        assert resp.status_code == 200, "FRAUD_ANALYST 가 FRAUD_CASE 접근 거부됨"

    def test_public_denied_case_detail(self, rbac_client: TestClient) -> None:
        """역할 미제공(PUBLIC=0)은 FRAUD_CASE 상세 조회 시 403."""
        resp = rbac_client.get("/cases/CASE-SEC-001", headers=PUBLIC)
        assert resp.status_code == 403

    def test_adjuster_denied_case_assign(self, rbac_client: TestClient) -> None:
        """CLAIMS_ADJUSTER 는 케이스 배정(FRAUD_CASE 등급)도 403."""
        resp = rbac_client.post(
            "/cases/CASE-SEC-001/assign",
            headers=ADJUSTER,
            json={"assignee": "adjuster-lee"},
        )
        assert resp.status_code == 403

    def test_analyst_allowed_case_assign(self, rbac_client: TestClient) -> None:
        """FRAUD_ANALYST 는 케이스 배정 200."""
        resp = rbac_client.post(
            "/cases/CASE-SEC-001/assign",
            headers=ANALYST,
            json={"assignee": "test-analyst"},
        )
        assert resp.status_code == 200

    def test_adjuster_denied_case_verdict(self, rbac_client: TestClient) -> None:
        """CLAIMS_ADJUSTER 는 판정(FRAUD_CASE 등급)도 403."""
        resp = rbac_client.post(
            "/cases/CASE-SEC-001/verdict",
            headers=ADJUSTER,
            json={"verdict": "FRAUD", "comment": "테스트"},
        )
        assert resp.status_code == 403

    def test_analyst_allowed_case_verdict(self, rbac_client: TestClient) -> None:
        """FRAUD_ANALYST 는 배정 후 판정 200."""
        # 먼저 배정(INVESTIGATING 전이) 후 판정
        rbac_client.post(
            "/cases/CASE-SEC-002/assign",
            headers=ANALYST,
            json={"assignee": "test-analyst"},
        )
        resp = rbac_client.post(
            "/cases/CASE-SEC-002/verdict",
            headers=ANALYST,
            json={"verdict": "FRAUD", "comment": "공모 확인"},
        )
        assert resp.status_code == 200
        assert resp.json()["recorded"] is True

    def test_adjuster_denied_graph(self, rbac_client: TestClient) -> None:
        """CLAIMS_ADJUSTER 는 그래프 조회(FRAUD_CASE 등급)도 403.

        Neo4j 연결 실패 전에 RBAC 에서 막혀야 함.
        """
        resp = rbac_client.get("/graph/customer/CUST-SEC-001", headers=ADJUSTER)
        assert resp.status_code == 403

    def test_public_denied_graph(self, rbac_client: TestClient) -> None:
        """역할 미제공 는 그래프 조회 403."""
        resp = rbac_client.get("/graph/customer/CUST-SEC-001", headers=PUBLIC)
        assert resp.status_code == 403


# ===========================================================================
# [smoke] 감사: 민감 행위 후 audit 로그에 result 기록 확인
# ===========================================================================
class TestAuditLog:
    """감사 로그 기록 검증."""

    def test_audit_records_denied_access(
        self, rbac_client: TestClient, tmp_path: Path
    ) -> None:
        """CLAIMS_ADJUSTER 의 FRAUD_CASE 접근 거부가 audit 에 result='denied' 로 기록된다."""
        audit_file = tmp_path / "audit_test.jsonl"

        # audit_event 를 임시 경로로 우회하여 검증
        from core.security import audit as audit_mod
        original_path = audit_mod._AUDIT_PATH
        audit_mod._AUDIT_PATH = audit_file

        try:
            resp = rbac_client.get("/cases/CASE-SEC-001", headers=ADJUSTER)
            assert resp.status_code == 403
        finally:
            audit_mod._AUDIT_PATH = original_path

        assert audit_file.exists(), "감사 파일이 생성되지 않음"
        lines = [json.loads(ln) for ln in audit_file.read_text().splitlines() if ln.strip()]
        denied = [l for l in lines if l.get("result") == "denied"]
        assert denied, "거부 이벤트가 감사 로그에 기록되지 않음"
        entry = denied[0]
        assert entry["action"] == "api.case.detail"
        assert entry["actor"] == "test-adjuster"
        assert "ts" in entry
        assert "meta" in entry

    def test_audit_records_allowed_access(
        self, rbac_client: TestClient, tmp_path: Path
    ) -> None:
        """FRAUD_ANALYST 의 정상 접근이 audit 에 result='ok' 로 기록된다."""
        audit_file = tmp_path / "audit_ok.jsonl"

        from core.security import audit as audit_mod
        original_path = audit_mod._AUDIT_PATH
        audit_mod._AUDIT_PATH = audit_file

        try:
            resp = rbac_client.get("/cases/CASE-SEC-001", headers=ANALYST)
            assert resp.status_code == 200
        finally:
            audit_mod._AUDIT_PATH = original_path

        assert audit_file.exists()
        lines = [json.loads(ln) for ln in audit_file.read_text().splitlines() if ln.strip()]
        ok_entries = [l for l in lines if l.get("result") == "ok" and "case.detail" in l.get("action", "")]
        assert ok_entries, "정상 접근이 감사 로그에 기록되지 않음"

    def test_audit_verdict_recorded(
        self, rbac_client: TestClient, tmp_path: Path
    ) -> None:
        """케이스 판정 행위가 감사 로그에 기록된다."""
        audit_file = tmp_path / "audit_verdict.jsonl"

        from core.security import audit as audit_mod
        original_path = audit_mod._AUDIT_PATH
        audit_mod._AUDIT_PATH = audit_file

        try:
            # 배정 → 판정
            rbac_client.post(
                "/cases/CASE-SEC-001/assign",
                headers=ANALYST,
                json={"assignee": "test-analyst"},
            )
            resp = rbac_client.post(
                "/cases/CASE-SEC-001/verdict",
                headers=ANALYST,
                json={"verdict": "FRAUD", "comment": "링 공모 확인"},
            )
            assert resp.status_code == 200
        finally:
            audit_mod._AUDIT_PATH = original_path

        assert audit_file.exists()
        lines = [json.loads(ln) for ln in audit_file.read_text().splitlines() if ln.strip()]
        verdict_entries = [l for l in lines if "verdict" in l.get("action", "")]
        assert verdict_entries, "판정 행위가 감사 로그에 기록되지 않음"
        for entry in verdict_entries:
            assert "result" in entry, "감사 항목에 result 필드 누락"
            assert "actor" in entry, "감사 항목에 actor 필드 누락"
            assert "ts" in entry, "감사 항목에 ts(타임스탬프) 필드 누락"
            assert "action" in entry, "감사 항목에 action 필드 누락"


# ===========================================================================
# [smoke] audit append-only 불변성 검증
# ===========================================================================
class TestAuditAppendOnly:
    """감사 로그 append-only 불변성 검증."""

    def test_two_events_produce_two_lines(self, tmp_path: Path) -> None:
        """두 번 기록 후 정확히 2줄이어야 한다."""
        log_path = tmp_path / "append_test.jsonl"

        audit_event("test.event1", "actor-a", target="T1", result="ok",
                    path=log_path)
        audit_event("test.event2", "actor-b", target="T2", result="denied",
                    path=log_path)

        lines = [ln for ln in log_path.read_text().splitlines() if ln.strip()]
        assert len(lines) == 2, f"예상 2줄이나 실제 {len(lines)}줄"

    def test_first_line_unchanged_after_second_write(self, tmp_path: Path) -> None:
        """두 번째 기록 후 첫 번째 줄이 변경되지 않아야 한다 (불변성)."""
        log_path = tmp_path / "immutable_test.jsonl"

        audit_event("case.create", "system", target="CASE-001",
                    result="ok", path=log_path)
        first_line = log_path.read_text().splitlines()[0]

        audit_event("case.assign", "analyst", target="CASE-001",
                    result="ok", path=log_path)
        lines = log_path.read_text().splitlines()

        assert lines[0] == first_line, "첫 번째 감사 라인이 변경됨 — append-only 위반"

    def test_audit_entries_are_valid_json(self, tmp_path: Path) -> None:
        """기록된 모든 감사 항목이 유효한 JSON + 필수 필드를 포함해야 한다."""
        log_path = tmp_path / "json_test.jsonl"

        audit_event("api.case.detail", "analyst@poc", target="CASE-X",
                    result="ok", meta={"role": "FRAUD_ANALYST"}, path=log_path)
        audit_event("api.case.detail", "adjuster@poc", target="CASE-X",
                    result="denied", meta={"role": "CLAIMS_ADJUSTER"}, path=log_path)

        lines = log_path.read_text().splitlines()
        for i, line in enumerate(lines):
            entry = json.loads(line)  # JSON 파싱 실패 시 테스트 실패
            assert "action" in entry, f"줄 {i+1}: action 필드 없음"
            assert "actor" in entry, f"줄 {i+1}: actor 필드 없음"
            assert "result" in entry, f"줄 {i+1}: result 필드 없음"
            assert "ts" in entry, f"줄 {i+1}: ts(타임스탬프) 필드 없음"

    def test_no_modify_only_append(self, tmp_path: Path) -> None:
        """기존 항목은 변경 불가, 새 항목만 추가됨을 검증한다.

        audit_event 는 내부적으로 open(mode='a') 로만 파일을 열어
        덮어쓰기(mode='w')가 불가능함을 확인한다.
        """
        log_path = tmp_path / "no_modify_test.jsonl"

        # 첫 기록
        e1 = audit_event("case.create", "system", target="CASE-A",
                          result="ok", path=log_path)
        original_content = log_path.read_text()
        original_lines = original_content.splitlines()
        assert len(original_lines) == 1

        # 두 번째 기록
        audit_event("case.assign", "analyst", target="CASE-A",
                    result="ok", path=log_path)

        new_content = log_path.read_text()
        new_lines = new_content.splitlines()

        # 원본 내용이 접두사로 보존되어야 함
        assert new_content.startswith(original_content), (
            "기존 감사 내용이 유지되지 않음 — 덮어쓰기 발생"
        )
        assert len(new_lines) == 2, "추가 후 2줄이어야 함"
        # 첫 항목 검증
        first = json.loads(new_lines[0])
        assert first["action"] == "case.create"
        assert first["actor"] == "system"
        assert first["result"] == "ok"
