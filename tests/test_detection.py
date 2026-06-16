"""WP2 탐지 코어 수용기준(AC) 테스트 (FR-3.1~3.3, 3.5).

Q1 공유 엔티티 / Q2 핫스팟 / Q3 crash-for-cash 순환 쿼리가 결과를 반환하는지,
리스크 스코어가 정상(저점) vs 링 멤버(고점)을 분리하는지, 그리고 **주입 링
재현율**이 합리적 임계(recall >= 0.8) 이상인지 단언한다.

임계값(0.8)은 실측(recall=1.000, precision=1.000, ring_recall=1.000) 대비
보수적으로 설정한 하한이다. 측정값을 위장하기 위한 인위적 하향이 아니라,
데이터 재생성/시드 변경에도 견디는 안전 마진이다.

integration 마커 — 실제 Neo4j 적재 데이터 필요. graph 픽스처가 미가용 시 skip.
"""
from __future__ import annotations

import pytest

from detection import detect, evaluate, scoring

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Q1~Q3 가 결과를 반환하는지 (FR-3.1 / 3.2 / 3.3)
# ---------------------------------------------------------------------------
def test_q1_shared_entities_returns_results(graph) -> None:
    """Q1 공유 엔티티 — 공유 계좌 군집을 1건 이상 반환(FR-3.1)."""
    results = detect.run_shared_entities()
    assert results, "Q1 공유 엔티티 결과가 비었습니다"
    # 동일 계좌 공유는 주입 링의 핵심 신호 → ACCOUNT 타입이 반드시 존재
    account_groups = [r for r in results if r["shared_type"] == "ACCOUNT"]
    assert account_groups, "공유 계좌(ACCOUNT) 군집이 없습니다"
    for g in account_groups:
        assert g["num_customers"] >= 2
        assert len(g["customer_ids"]) == g["num_customers"]


def test_q2_hotspots_returns_results(graph) -> None:
    """Q2 핫스팟 — 임계 이상 집중 엔티티를 1건 이상 반환(FR-3.2)."""
    results = detect.run_hotspots()
    assert results, "Q2 핫스팟 결과가 비었습니다"
    for h in results:
        assert h["num_customers"] >= 1
        assert h["entity_type"] in {"HOSPITAL", "REPAIR_SHOP", "ACCOUNT"}


def test_q3_crash_rings_returns_results(graph) -> None:
    """Q3 crash-for-cash 순환 — 상호 교차 목격 군집을 반환(FR-3.3)."""
    clusters = detect.run_crash_rings()
    assert clusters, "Q3 crash 순환 군집이 비었습니다"
    for c in clusters:
        assert c["cluster_size"] >= 2
        assert c["seed_customer"] in c["members"]

    pairs = detect.run_crash_ring_pairs()
    assert pairs, "Q3 교차 목격 쌍이 비었습니다"
    for p in pairs:
        assert p["customer_a"] != p["customer_b"]


# ---------------------------------------------------------------------------
# 정상 0점(저점) vs 링 고점 분리 (FR-3.5 AC)
# ---------------------------------------------------------------------------
def test_scoring_separates_normal_from_ring(graph) -> None:
    """리스크 스코어가 정상 저점 vs 링 고점으로 분리되어야 한다(FR-3.5 AC)."""
    risks = scoring.score_customers()
    fraud = [r for r in risks.values() if r.is_fraud_ring]
    normal = [r for r in risks.values() if not r.is_fraud_ring]

    assert fraud, "링 멤버 점수가 산출되지 않았습니다"

    fraud_avg = sum(r.score for r in fraud) / len(fraud)
    normal_avg = (sum(r.score for r in normal) / len(normal)) if normal else 0.0
    normal_max = max((r.score for r in normal), default=0.0)
    fraud_min = min(r.score for r in fraud)

    # 링 멤버는 높은 점수, 정상은 낮은 점수
    assert fraud_avg >= 80.0, f"링 평균 점수 너무 낮음: {fraud_avg}"
    assert normal_avg <= 25.0, f"정상 평균 점수 너무 높음: {normal_avg}"
    # 분리: 링 최저점이 정상 최고점보다 높아야(겹침 없는 깨끗한 분리)
    assert fraud_min > normal_max, (
        f"링 최저({fraud_min}) <= 정상 최고({normal_max}) — 분리 실패"
    )


def test_normal_alone_address_share_not_alerted(graph) -> None:
    """주소만 공유하는 정상 고객은 알림 임계(50)를 넘지 않아야 한다(오탐 방지)."""
    risks = scoring.score_customers()
    # 주소(약 신호)만 가진 정상 고객 표본 — 알림 미발생 확인
    addr_only_normal = [
        r for r in risks.values()
        if not r.is_fraud_ring
        and all(s["type"].startswith(("SHARED_ADDRESS", "HOTSPOT"))
                for s in r.signals if not s["type"].startswith("_"))
    ]
    for r in addr_only_normal:
        assert r.score < scoring.DEFAULT_ALERT_THRESHOLD, (
            f"정상 고객 {r.customer_id} 가 약신호만으로 알림됨: {r.score}"
        )


# ---------------------------------------------------------------------------
# 주입 링 재현율 (WP2 핵심 산출물) — recall >= 0.8
# ---------------------------------------------------------------------------
def test_injected_ring_recall(graph) -> None:
    """주입 링 재현율이 합리적 임계(>= 0.8) 이상이어야 한다 (WP2 핵심)."""
    res = evaluate.evaluate()
    assert res.total_fraud_customers > 0, "ground truth 링 멤버가 없습니다"
    assert res.recall >= 0.8, f"재현율 미달: {res.recall:.3f}"
    # 정밀도도 합리적이어야 오탐 폭발이 아님
    assert res.precision >= 0.8, f"정밀도 미달: {res.precision:.3f}"


def test_injected_ring_ring_recall(graph) -> None:
    """링 단위 재현율 — 주입된 모든 링의 대다수(>= 0.8)를 적발해야 한다."""
    res = evaluate.evaluate()
    assert res.ring_recall >= 0.8, (
        f"링 단위 재현율 미달: {res.ring_recall:.3f} "
        f"({res.rings_covered}/{res.total_rings})"
    )


def test_score_separation_margin(graph) -> None:
    """점수 분리도 — 링 평균과 정상 평균 간 큰 격차(>= 40점)를 확인."""
    res = evaluate.evaluate()
    assert res.separation >= 40.0, f"점수 분리도 부족: {res.separation:.1f}"
