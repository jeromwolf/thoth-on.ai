"""WP3 그래프 임베딩 + 비지도 이상탐지 수용기준(AC) 테스트 (FR-3.6).

검증 대상:
    1. 유사 엣지(SIMILAR_TO) 멱등 적재 + FastRP 임베딩 write.
    2. 프로젝션 멱등 생성(재실행 시 drop 후 재생성).
    3. **비지도 이상신호가 라벨을 전혀 쓰지 않음(평가 누수 없음)** — 신호 산출
       쿼리가 ground truth(is_fraud_ring/ring_id)를 참조하지 않는다.
    4. CLIQUE(차수>=2) 신호가 정상보다 사기 멤버에 강하게 집중(정밀도).
    5. CLIQUE 신호가 룰 미탐 수법(weak/hotspot_only)을 회수.
    6. 임베딩 결합 후 detection.evaluate 재현율이 baseline 대비 향상.

ground truth(ring_id/is_fraud)는 신호 산출 입력에 일절 쓰지 않는다 — 유사 엣지는
순수 구조+시간(병원/정비소/계좌/목격/주소)만으로 만든다.

integration 마커 — 실제 Neo4j + GDS 적재 데이터 필요. 미가용 시 skip.
"""
from __future__ import annotations

import inspect

import pytest

from detection import embedding, evaluate, scoring
from thoth import db

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def gds_available(neo4j_available) -> bool:
    if not neo4j_available:
        pytest.skip("Neo4j 미가용 — `make up && make wait-neo4j` 후 재실행")
    if not db.has_gds():
        pytest.skip("GDS 플러그인 미가용 — gds.version() 응답 없음")
    return True


@pytest.fixture(scope="module")
def embed_run(gds_available):
    """모듈 1회 임베딩 파이프라인 실행(유사 엣지 적재 + 프로젝션 + FastRP write)."""
    return embedding.run_pipeline()


# ---------------------------------------------------------------------------
# 멱등 적재 + write 성공
# ---------------------------------------------------------------------------
def test_similarity_edges_and_fastrp_written(embed_run) -> None:
    """SIMILAR_TO 유사 엣지 적재 + FastRP 임베딩 write 가 성공해야 한다."""
    res = embed_run
    assert res.similarity_edges > 0, "SIMILAR_TO 엣지가 적재되지 않음"
    assert res.projection["nodeCount"] > 0
    assert res.fastrp["nodePropertiesWritten"] > 0

    # Customer 에 임베딩이 실제로 write 됐는지 직접 확인.
    n = db.run(
        f"MATCH (c:Customer) WHERE c.{embedding.EMBED_PROPERTY} IS NOT NULL "
        "RETURN count(*) AS n"
    )[0]["n"]
    assert n > 0, "Customer 에 fastrp_embedding 이 write 되지 않음"


def test_similarity_edges_idempotent(embed_run) -> None:
    """유사 엣지 재적재가 멱등(같은 엣지 수)이어야 한다."""
    before = db.run("MATCH ()-[r:SIMILAR_TO]->() RETURN count(r) AS n")[0]["n"]
    again = embedding.build_similarity_edges()
    after = db.run("MATCH ()-[r:SIMILAR_TO]->() RETURN count(r) AS n")[0]["n"]
    assert again == after
    assert after == before, f"멱등 위반: {before} -> {after}"


def test_projection_idempotent(embed_run) -> None:
    """프로젝션 재생성이 멱등이어야 한다."""
    assert embedding.graph_exists(embedding.EMBED_GRAPH)
    again = embedding.project_embed_graph()
    assert again["nodeCount"] == embed_run.projection["nodeCount"]
    assert again["relationshipCount"] == embed_run.projection["relationshipCount"]


# ---------------------------------------------------------------------------
# 평가 누수 없음 — 신호가 라벨을 전혀 쓰지 않는다 (핵심)
# ---------------------------------------------------------------------------
def test_similarity_edge_queries_label_free() -> None:
    """유사 엣지 생성 쿼리가 ground truth(라벨)를 참조하지 않아야 한다(누수 금지)."""
    combined = (
        embedding._E1_HOTSPOT_PAIR
        + embedding._E2_WITNESS_PAIR
        + embedding._E3_ACCOUNT_PAIR
    )
    for token in ("is_fraud_ring", "ring_id", "ring_pattern", "fraud_label"):
        assert token not in combined, (
            f"유사 엣지 쿼리가 라벨 '{token}' 을 참조함 — 평가 누수 위험"
        )


def test_anomaly_signal_independent_of_labels(embed_run) -> None:
    """비지도 이상신호(차수)는 라벨 부착 여부와 무관하게 동일해야 한다.

    신호 산출이 라벨을 입력으로 쓰지 않음을 직접 입증한다(누수 불가능).
    """
    with_labels = embedding.compute_anomaly_signals(attach_labels=True)
    without_labels = embedding.compute_anomaly_signals(attach_labels=False)
    assert set(with_labels) == set(without_labels)
    for cid in with_labels:
        assert with_labels[cid].similar_degree == without_labels[cid].similar_degree, (
            "라벨 부착이 신호(차수)를 바꿈 — 비지도 위반"
        )


def test_compute_signals_source_no_label_before_attach() -> None:
    """신호 산출 함수가 attach_labels 분기 이전에 라벨을 참조하지 않아야 한다."""
    src = inspect.getsource(embedding.compute_anomaly_signals)
    pre_attach = src.split("if attach_labels")[0]
    for token in ("is_fraud_ring", "ring_id"):
        assert token not in pre_attach, (
            f"신호 산출이 attach 이전에 '{token}' 참조 — 누수 위험"
        )


# ---------------------------------------------------------------------------
# CLIQUE 신호 정밀도 — 사기에 집중
# ---------------------------------------------------------------------------
def test_clique_signal_concentrates_on_fraud(embed_run) -> None:
    """CLIQUE(차수>=2) 신호는 정상보다 사기 멤버에 압도적으로 집중되어야 한다."""
    signals = embedding.compute_anomaly_signals()
    clique = [s for s in signals.values() if s.is_clique]
    assert clique, "CLIQUE 신호가 전무합니다"

    fraud = sum(1 for s in clique if s.is_fraud)
    precision = fraud / len(clique)
    # 실측 정밀 ~0.81 — 현실 하한 0.7 단언(정상 클리크 혼입 감안).
    assert precision >= 0.7, (
        f"CLIQUE 신호 정밀도 미달: {precision:.3f} ({fraud}/{len(clique)})"
    )

    # 사기 모집단 대비 정상 모집단의 CLIQUE 발화율이 훨씬 낮아야 한다.
    total_fraud = db.run(
        "MATCH (c:Customer) WHERE c.is_fraud_ring RETURN count(*) AS n"
    )[0]["n"]
    total_normal = db.run(
        "MATCH (c:Customer) WHERE NOT coalesce(c.is_fraud_ring,false) "
        "RETURN count(*) AS n"
    )[0]["n"]
    fraud_rate = fraud / total_fraud if total_fraud else 0.0
    normal_rate = (len(clique) - fraud) / total_normal if total_normal else 0.0
    assert fraud_rate >= normal_rate * 50, (
        f"CLIQUE 신호가 사기에 집중되지 않음: "
        f"사기율 {fraud_rate:.3f} vs 정상율 {normal_rate:.5f}"
    )


def test_clique_recovers_hard_korean_patterns(embed_run) -> None:
    """CLIQUE 신호가 룰이 약한 한국 수법(driver_swap) 멤버를 회수해야 한다.

    driver_swap(공유 차량 동승/운전자 교체)은 공통 계좌·교차목격이 없어 룰만으로는
    탐지가 어렵다(룰 재현율 ≈ 0.21). 임베딩 비지도 클리크(E7 차량 동행)가 이를
    회수한다. collision_ring/agent_fraud 도 클리크로 함께 회수된다.
    """
    signals = embedding.compute_anomaly_signals()
    clique_fraud = [s for s in signals.values() if s.is_clique and s.is_fraud]
    by_pat: dict[str, int] = {}
    for s in clique_fraud:
        by_pat[s.ring_pattern] = by_pat.get(s.ring_pattern, 0) + 1
    # driver_swap 을 한 명 이상 회수해야 의미가 있다(룰 미탐 수법 회수).
    assert by_pat.get("driver_swap", 0) > 0, "CLIQUE 가 driver_swap 수법을 전혀 회수 못함"
    # collision_ring 도 클리크로 회수된다(상호 목격 동행).
    assert by_pat.get("collision_ring", 0) > 0, (
        "CLIQUE 가 collision_ring 수법을 전혀 회수 못함"
    )


# ---------------------------------------------------------------------------
# 결합 효과 — 재현율 향상 (정밀도 과도 하락 없이)
# ---------------------------------------------------------------------------
def test_embedding_improves_recall_over_rules(embed_run) -> None:
    """임베딩 결합이 룰 대비 재현율을 향상시켜야 한다(임계 50)."""
    base = evaluate.evaluate(use_gds=False, use_embedding=False)
    emb = evaluate.evaluate(use_gds=False, use_embedding=True)

    assert emb.recall > base.recall, (
        f"임베딩이 재현율을 못 올림: {base.recall:.3f} -> {emb.recall:.3f}"
    )
    # 정밀도가 과도하게 무너지면 안 된다(현실 하한 0.7).
    assert emb.precision >= 0.7, (
        f"임베딩 결합 후 정밀도 과도 하락: {emb.precision:.3f}"
    )
    # 룰이 이미 강하므로(F1≈0.93) 임베딩은 재현율↑·정밀도↓ 트레이드오프로 F1 이
    # 소폭 변동할 수 있다. F1 이 폭락하지 않고 현실 하한(0.85) 이상이면 OK.
    assert emb.f1 >= 0.85, (
        f"임베딩 결합 후 F1 폭락: {base.f1:.3f} -> {emb.f1:.3f}"
    )


def test_embedding_improves_hard_pattern_recall(embed_run) -> None:
    """수법별 — 임베딩 결합 후 룰이 약한 driver_swap 재현율이 향상되어야 한다."""
    base = evaluate.evaluate(use_embedding=False)
    emb = evaluate.evaluate(use_embedding=True)

    b = base.pattern_recall.get("driver_swap", {}).get("recall", 0.0)
    e = emb.pattern_recall.get("driver_swap", {}).get("recall", 0.0)
    assert e > b, (
        f"수법 driver_swap 재현율이 임베딩으로 개선 안 됨: {b:.3f} -> {e:.3f}"
    )


def test_embed_clique_signal_in_scoring(embed_run) -> None:
    """scoring 에 EMBED_CLIQUE 기여 신호가 부착되어 설명가능성을 유지해야 한다."""
    risks = scoring.score_customers(use_embedding=True)
    has_embed = [
        r for r in risks.values()
        if any(s["type"] == "EMBED_CLIQUE" for s in r.signals)
    ]
    assert has_embed, "EMBED_CLIQUE 기여 신호가 어떤 고객에도 부착되지 않음"
    # 기여 신호에 설명 근거(차수·피어)가 있어야 한다.
    sample = next(
        s for r in has_embed for s in r.signals if s["type"] == "EMBED_CLIQUE"
    )
    assert "similar_degree" in sample and "similar_peers" in sample
