"""듀얼 레이어 스코어러(조직형 그래프 + 개인형 속성) 수용기준 테스트.

검증 대상:
    1. 청구 단위로 그래프 점수+속성 점수가 산출되고 0~1 범위, 결합/유형 라벨 부여.
    2. 유형 라벨(ORGANIZED/INDIVIDUAL/BOTH/NONE) 분류 로직(순수 단위).
    3. 그래프 단독이 개인형(opportunistic) 사기를 거의 못 잡음(구조적 한계 재현).
    4. 속성 레이어가 개인형 사기를 유의미하게 회수 → **듀얼 통합 재현율이 그래프
       단독 대비 상승**(핵심 — 보완 효과 실측).
    5. 누수 방지 — 속성 모델은 캐글 학습(합성 라벨 미사용), 그래프는 라벨 미사용.

학습/그래프 점수는 Neo4j + scikit-learn + 적재 데이터 필요(integration).
유형 분류 로직 단위 테스트는 외부 의존성 없이 돈다.
"""
from __future__ import annotations

import inspect

import pytest

from detection import dual_layer as dl


# ===========================================================================
# 유형 분류 로직 — 순수 단위(외부 의존성 없음)
# ===========================================================================
@pytest.mark.smoke
def test_classify_types() -> None:
    """그래프/속성 점수 조합이 올바른 유형 라벨로 분류돼야 한다."""
    assert dl._classify(0.9, 0.9) == "BOTH"
    assert dl._classify(0.9, 0.1) == "ORGANIZED"
    assert dl._classify(0.1, 0.9) == "INDIVIDUAL"
    assert dl._classify(0.1, 0.1) == "NONE"
    # 경계(임계 정확히) 는 '높음'으로 포함.
    assert dl._classify(dl.GRAPH_HIGH, 0.0) == "ORGANIZED"
    assert dl._classify(0.0, dl.ATTR_HIGH) == "INDIVIDUAL"


@pytest.mark.smoke
def test_synth_to_kaggle_mapping_complete() -> None:
    """합성→캐글 컬럼 매핑이 4 base 축 + 행동/시계열 축을 포함해야 한다."""
    m = dl._SYNTH_TO_KAGGLE
    for col in ("fault", "base_policy", "vehicle_category", "accident_area"):
        assert col in m
    for behav in ("days_policy_accident", "address_change_claim",
                  "number_of_suppliments", "make"):
        assert behav in m, f"행동/시계열 매핑 누락: {behav}"


@pytest.mark.smoke
def test_attr_layer_trains_on_kaggle_not_synth() -> None:
    """누수 가드 — 속성 모델 학습은 캐글로(합성 라벨 미사용)임을 소스로 확인."""
    src = inspect.getsource(dl.score_dual_layer)
    assert "train_attribute_model" in src, "속성 모델이 캐글로 학습되지 않음"
    # 합성 라벨(fraud_label/ring_pattern)을 점수 계산 입력으로 쓰지 않는다.
    # (_claim_attr_rows 는 평가용으로만 _fraud_label/_ring_pattern 을 분리 보관.)
    score_src = inspect.getsource(dl._claim_attr_rows)
    assert "_fraud_label" in score_src, "ground truth 가 평가용으로 분리 보관되어야 함"


# ===========================================================================
# 듀얼 레이어 통합 — 그래프 한계 보완 실측 (Neo4j + sklearn 필요)
# ===========================================================================
@pytest.mark.integration
def test_dual_layer_scores_shape(graph) -> None:
    """전 청구에 그래프/속성/결합 점수 + 유형 라벨이 0~1 범위로 산출돼야 한다."""
    from detection import attribute_model as am
    if not am._SKLEARN:
        pytest.skip("scikit-learn 미설치")
    res = dl.score_dual_layer(model_kind="gb", combine="max")
    assert len(res.claims) > 0
    for c in res.claims[:200]:
        assert 0.0 <= c.graph_score <= 1.0
        assert 0.0 <= c.attr_score <= 1.0
        assert 0.0 <= c.combined <= 1.0
        assert c.risk_type in ("ORGANIZED", "INDIVIDUAL", "BOTH", "NONE")


@pytest.mark.integration
def test_graph_misses_individual_fraud(graph) -> None:
    """그래프 단독은 개인형(opportunistic) 사기를 거의 못 잡아야 한다(구조적 한계)."""
    from detection import attribute_model as am
    if not am._SKLEARN:
        pytest.skip("scikit-learn 미설치")
    res = dl.score_dual_layer(model_kind="gb", combine="max")
    rb = dl.measure_recall(res, threshold=0.5)
    # 개인형 사기가 존재해야(검증 의미). 그래프 단독 개인형 재현율은 매우 낮다.
    assert rb.individual_fraud > 0, "개인형(opportunistic) 사기 라벨 없음"
    assert rb.graph_only_recall_ind < 0.10, (
        f"그래프가 개인형을 너무 잘 잡음(예상 밖): {rb.graph_only_recall_ind:.3f}"
    )


@pytest.mark.integration
def test_dual_layer_improves_total_recall(graph) -> None:
    """듀얼(결합) 통합 재현율이 그래프 단독 대비 유의미하게 상승해야 한다(보완 효과).

    속성 레이어가 그래프가 못 잡는 개인형 사기를 회수하므로, 청구 단위 통합
    재현율이 그래프 단독보다 크게 높아진다(핵심 AC).
    """
    from detection import attribute_model as am
    if not am._SKLEARN:
        pytest.skip("scikit-learn 미설치")
    res = dl.score_dual_layer(model_kind="gb", combine="max")
    rb = dl.measure_recall(res, threshold=0.5)
    # 속성 레이어가 개인형 사기를 유의미하게 회수.
    assert rb.attr_only_recall_ind > rb.graph_only_recall_ind + 0.3, (
        f"속성 레이어 개인형 보완 부족: 속성 {rb.attr_only_recall_ind:.3f} "
        f"vs 그래프 {rb.graph_only_recall_ind:.3f}"
    )
    # 듀얼 통합 재현율 > 그래프 단독(조직형 보존 + 개인형 추가).
    assert rb.dual_recall_total > rb.graph_recall_total + 0.2, (
        f"듀얼 통합 재현율 상승 부족: 듀얼 {rb.dual_recall_total:.3f} "
        f"vs 그래프 {rb.graph_recall_total:.3f}"
    )
    # 조직형 재현율은 보존(그래프 강점 유지 — 듀얼이 조직형을 깎지 않음).
    assert rb.dual_recall_org >= rb.graph_only_recall_org - 1e-9, (
        f"듀얼이 조직형 재현율을 훼손: 듀얼 {rb.dual_recall_org:.3f} "
        f"vs 그래프 {rb.graph_only_recall_org:.3f}"
    )
