"""WP2 탐지 코어 수용기준(AC) 테스트 (FR-3.1~3.3, 3.5) — 현실 데이터 기준.

Q1 공유 엔티티 / Q2 핫스팟 / Q3 crash-for-cash 순환 쿼리가 결과를 반환하는지,
리스크 스코어가 정상(저점)과 링 멤버(고점)을 **유의미하게 분리**하는지, 그리고
**주입 링 재현율**이 현실적 하한 이상인지 단언한다.

[현실판 임계 정정 — 상용화 검증 / 정밀도 회복판]
    이전 버전은 "완벽 패턴(모든 신호 동시)" 링만 심어 recall=precision=1.000 이
    나왔다. 이는 비현실적이었다. 현재 데이터는 정상 노이즈(가족 공유·정상 핫스팟·
    우연 단방향 목격)와 다양한 난이도의 사기 링(perfect/account_only/witness_only/
    hotspot_only/weak)을 함께 담는다.

    [정밀도 회복 — 정상(가족) 공유 구분 + 복수 신호 요구 + baseline 정규화]
        룰 단순화("공유=의심")로 인한 오탐 폭증(정상 426명)을 다음으로 해결했다:
          · 가족(같은 주소·연중 분산) vs 사기(다른 주소·짧은 기간 집중) 차등화.
          · 인기 대형 병원/정비소(정상 baseline) 제외, 집중/담합 핫스팟만 의심.
          · 서로 다른 강신호 그룹 2종+ 동시 충족 시에만 급가점.
        실측(현실 합성데이터, 임계 50): recall≈0.63 / precision≈0.91 / F1≈0.75 /
        FP≈7 (baseline R0.40/P0.10/F0.15/FP426 대비 정밀도·F1 대폭 개선).

    따라서 단언은 다음의 **현실적 하한**으로 둔다(수치 위장이 아니라, 탐지가
    동작하고 정밀도·분리가 유지됨을 보장):
        · recall >= 0.50
        · 링 단위 재현율 >= 0.55
        · precision >= 0.70 (정밀도 회복의 핵심 — 오탐 억제)
        · 링 멤버 평균 점수 > 정상 평균 점수 + 유의미 분리(>= 25점)
    GDS 신호를 쓰면 구조적 corroborating 으로 일부 약신호 링 탐지가 개선된다.

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
    # 동일 계좌 공유는 (사기 링 + 정상 가족) 모두에서 발생 → ACCOUNT 타입이 반드시 존재
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
# 정상 단방향 목격은 Q3(상호) 신호가 아니어야 한다 (견고성 — 오탐 방지)
# ---------------------------------------------------------------------------
def test_one_way_witness_not_a_ring(graph) -> None:
    """상호가 아닌 단방향 목격만 있는 청구는 Q3 군집에 잡히지 않아야 한다.

    Q3 는 양방향(상호) 교차목격만 잡는다. 우연한 단방향 목격(정상)이 링으로
    오탐되면 안 된다. 현실 데이터에 주입된 정상 단방향 목격이 군집을 폭증시키지
    않는지(=Q3 군집이 사실상 사기 링 멤버 위주인지) 확인한다.
    """
    clusters = detect.run_crash_rings()
    # Q3 에 잡힌 seed 고객 중 사기 링 멤버 비율이 높아야 한다(정상 단방향 목격은
    # 양방향이 아니므로 군집에 들지 않음).
    seeds = [c["seed_customer"] for c in clusters]
    assert seeds, "Q3 군집 seed 가 비었습니다"
    ring_member = [c for c in clusters if (c.get("ring_id") or "") != ""]
    ratio = len(ring_member) / len(clusters)
    assert ratio >= 0.7, (
        f"Q3 군집의 사기 링 멤버 비율 부족: {ratio:.2f} — 단방향 목격 오탐 의심"
    )


# ---------------------------------------------------------------------------
# 정상(저점) vs 링(고점) 유의미 분리 (FR-3.5 AC) — 현실판
# ---------------------------------------------------------------------------
def test_scoring_separates_normal_from_ring(graph) -> None:
    """리스크 스코어가 정상 평균 < 링 평균으로 유의미하게 분리되어야 한다(FR-3.5 AC).

    현실 데이터에서는 정상 가족 공유 때문에 일부 정상 고객도 고점이 될 수 있어
    '완전 분리(링 최저 > 정상 최고)'는 보장하지 않는다. 대신 **모집단 평균 분리**가
    유의미함을 단언한다.
    """
    risks = scoring.score_customers()
    fraud = [r for r in risks.values() if r.is_fraud_ring]
    normal = [r for r in risks.values() if not r.is_fraud_ring]

    assert fraud, "링 멤버 점수가 산출되지 않았습니다"

    fraud_avg = sum(r.score for r in fraud) / len(fraud)
    normal_avg = (sum(r.score for r in normal) / len(normal)) if normal else 0.0

    # 링 평균이 정상 평균보다 유의미하게 높아야 한다(분리 >= 25점, 정밀도 회복판).
    assert fraud_avg > normal_avg + 25.0, (
        f"점수 분리 부족: 링평균 {fraud_avg:.1f} vs 정상평균 {normal_avg:.1f}"
    )
    # 링 평균 자체도 알림 임계 부근 이상으로 충분히 높아야 한다.
    assert fraud_avg >= 40.0, f"링 평균 점수 너무 낮음: {fraud_avg:.1f}"
    # 신호 보유 정상 고객의 평균 점수도 임계(50)보다 훨씬 낮아야 한다(오탐 억제).
    #   (여기 normal 은 '신호가 있는' 정상만 모집단 — 점수 0 정상은 제외되어 평균↑.
    #    전체 모집단 정규화 평균은 evaluate.normal_avg_score 에서 ≈1.5 로 더 낮다.)
    assert normal_avg < 12.0, f"정상 평균 점수 과다(오탐 위험): {normal_avg:.1f}"


def test_normal_alone_address_share_not_alerted(graph) -> None:
    """약신호(주소·핫스팟)만 가진 정상 고객은 알림 임계(50)를 넘지 않아야 한다(오탐 방지)."""
    risks = scoring.score_customers()
    # 주소/핫스팟(약 신호)만 가진 정상 고객 표본 — 알림 미발생 확인
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
# 주입 링 재현율 (WP2 핵심 산출물) — 현실적 하한
# ---------------------------------------------------------------------------
def test_injected_ring_recall(graph) -> None:
    """주입 링 재현율이 현실적 하한(>= 0.50) 이상이어야 한다 (WP2 핵심).

    현실 데이터는 hotspot_only/weak 같은 약신호 링을 포함하므로 100% 가 아니다.
    perfect/account_only/witness_only 와 담합 핫스팟 회수로 하한을 충족한다.
    """
    res = evaluate.evaluate()
    assert res.total_fraud_customers > 0, "ground truth 링 멤버가 없습니다"
    assert res.recall >= 0.50, f"재현율 미달(현실 하한): {res.recall:.3f}"
    # FPR 이 폭증하지 않는지 확인(정밀도 회복판은 FPR 이 매우 낮아야 함).
    assert res.fpr < 0.02, f"FPR 과다(오탐 위험): {res.fpr:.4f}"


def test_precision_recovered(graph) -> None:
    """정밀도 회복 — 임계 50 기준 precision 이 현실 하한(>= 0.70) 이상이어야 한다.

    baseline(P≈0.10, 오탐 426명)의 처참한 정밀도를 정상 공유 구분·복수 신호
    요구·핫스팟 baseline 정규화로 회복했음을 보장한다(상용화 가능 수준).
    """
    res = evaluate.evaluate()
    assert res.precision >= 0.70, (
        f"정밀도 미달(현실 하한): {res.precision:.3f} "
        f"(TP={res.detected_fraud}, FP={res.detected_normal})"
    )
    # 오탐 절대 수도 상용 운영 가능 수준이어야 한다(baseline 426 대비 격감).
    assert res.detected_normal <= 30, f"오탐 과다: {res.detected_normal}명"
    # 정밀도가 회복되어도 F1 이 baseline(0.154) 대비 크게 개선되어야 한다.
    assert res.f1 >= 0.55, f"F1 미달: {res.f1:.3f}"


def test_injected_ring_ring_recall(graph) -> None:
    """링 단위 재현율 — 주입된 링의 일정 비율(>= 0.55) 이상을 적발해야 한다."""
    res = evaluate.evaluate()
    assert res.ring_recall >= 0.55, (
        f"링 단위 재현율 미달(현실 하한): {res.ring_recall:.3f} "
        f"({res.rings_covered}/{res.total_rings})"
    )


def test_score_separation_margin(graph) -> None:
    """점수 분리도 — 링 평균과 정상 평균 간 유의미 격차(>= 25점)를 확인."""
    res = evaluate.evaluate()
    assert res.separation >= 25.0, f"점수 분리도 부족: {res.separation:.1f}"


# ---------------------------------------------------------------------------
# 수법(ring_pattern)별 탐지율 — 강한 신호 수법은 잘 잡혀야 한다
# ---------------------------------------------------------------------------
def test_strong_pattern_detection(graph) -> None:
    """강신호 한국 수법(collision_ring/fake_admission_star)은 높은 재현율로 잡혀야 한다.

    어려운 수법(agent_fraud/driver_swap/repair_overbill)은 공유 신호가 약해 낮을 수
    있다. 여기서는 '탐지가 강신호 수법에 대해 확실히 작동함'만 보장한다.
    """
    res = evaluate.evaluate()
    pr = res.pattern_recall
    if not pr:
        pytest.skip("ring_pattern 라벨이 없는 데이터(구버전) — skip")
    # collision_ring(상호 교차목격 + 공통 정비소/계좌) — 전형적 강신호.
    if "collision_ring" in pr:
        assert pr["collision_ring"]["recall"] >= 0.7, (
            f"collision_ring 수법 재현율 미달: {pr['collision_ring']['recall']:.3f}"
        )
    # fake_admission_star(허위입원 — 병원 환자 집중 + 브로커 허브) — 강신호.
    if "fake_admission_star" in pr:
        assert pr["fake_admission_star"]["recall"] >= 0.7, (
            f"fake_admission_star 수법 재현율 미달: {pr['fake_admission_star']['recall']:.3f}"
        )


# ---------------------------------------------------------------------------
# 임계치 스윕 동작 검증
# ---------------------------------------------------------------------------
def test_threshold_sweep_monotonic_fpr(graph) -> None:
    """임계치가 높아질수록 FPR(오탐)은 단조 감소해야 한다(스윕 정합성)."""
    results = evaluate.sweep([30.0, 40.0, 50.0, 60.0, 70.0])
    assert len(results) == 5
    fprs = [r.fpr for r in results]
    # 임계 상승 → 탐지 감소 → FPR 비증가(단조 감소 허용 동률).
    for a, b in zip(fprs, fprs[1:]):
        assert b <= a + 1e-9, f"임계 상승에도 FPR 증가: {fprs}"
    # F1 최대 임계 권장이 동작하는지
    best = evaluate.recommend_threshold(results)
    assert best.threshold in {30.0, 40.0, 50.0, 60.0, 70.0}
