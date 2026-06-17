"""WP3 ML 분류기 + 앙상블 수용기준(AC) 테스트 (FR-3.7).

검증 대상:
    1. 피처 추출(detection.features)이 라벨(ring_id/is_fraud_ring/ring_pattern)을
       **피처로 전혀 쓰지 않음**(평가 누수 차단 — 최우선 제약).
    2. 피처/라벨 정렬·차원 일치, 라벨 분리(extract_labels) 동작.
    3. 모델 학습 smoke — Stratified K-fold out-of-fold 예측이 산출되고, **학습
       데이터로 평가하지 않음**(누수 없는 일반화 성능).
    4. 라벨 셔플 시 AUC 가 무작위(~0.5)로 붕괴 — 피처에 누수 경로가 없음을 입증.
    5. 앙상블/비용임계/피처중요도 산출 동작 + ML 이 룰 대비 AUC 를 떨어뜨리지 않음.

순수 단위 테스트(누수 가드·소스 검사)는 Neo4j 없이도 돌고, 학습 smoke 는
integration(실제 적재 데이터 필요)으로 분리한다.
"""
from __future__ import annotations

import inspect

import numpy as np
import pytest

from detection import features as featmod
from detection import ml_model


# ===========================================================================
# 평가 누수 차단 — 라벨 미사용 (Neo4j 불필요한 순수 단위 테스트)
# ===========================================================================
@pytest.mark.smoke
def test_feature_names_are_label_free() -> None:
    """FEATURE_NAMES 에 ring_id/is_fraud/ring_pattern 파생 항목이 없어야 한다(누수)."""
    assert featmod.feature_names_are_label_free(), (
        f"피처 이름에 라벨 파생이 섞임: {featmod.FEATURE_NAMES}"
    )
    joined = " ".join(featmod.FEATURE_NAMES).lower()
    for tok in ("is_fraud", "ring_id", "ring_pattern", "fraud_label"):
        assert tok not in joined, f"피처 이름이 라벨 토큰 '{tok}' 포함 — 누수"


@pytest.mark.smoke
def test_build_features_source_label_free() -> None:
    """build_features 소스가 ground truth 라벨을 참조하지 않아야 한다(누수 금지).

    피처 추출 함수가 라벨 컬럼/속성을 읽지 않음을 소스 검사로 직접 입증한다.
    (라벨은 별도 extract_labels 만 읽는다.)
    """
    # docstring 은 "라벨을 쓰지 않는다"를 설명하느라 토큰을 언급하므로 제거하고
    # 실제 코드 본문만 검사한다(누수는 코드가 라벨을 '읽을' 때 발생).
    src = inspect.getsource(featmod.build_features)
    doc = featmod.build_features.__doc__ or ""
    code_body = src.replace(doc, "")
    for tok in ("is_fraud_ring", "ring_id", "ring_pattern", "fraud_label"):
        assert tok not in code_body, (
            f"build_features 가 라벨 '{tok}' 을 참조함 — 평가 누수 위험"
        )


@pytest.mark.smoke
def test_labels_only_in_extract_labels() -> None:
    """라벨 추출은 extract_labels 에만 있고, 거기서만 is_fraud_ring 을 읽어야 한다."""
    src = inspect.getsource(featmod.extract_labels)
    assert "is_fraud_ring" in src, "extract_labels 가 라벨을 읽지 않음(타깃 누락)"


# ===========================================================================
# 피처 추출 smoke (Neo4j 필요)
# ===========================================================================
@pytest.mark.integration
def test_build_features_shapes(graph) -> None:
    """피처 행렬 차원/정렬/라벨 분리가 정합해야 한다(누수 없는 X/y 분리)."""
    fm = featmod.build_features()
    assert fm.n_samples > 0, "피처 표본이 비었습니다"
    assert fm.n_features == len(featmod.FEATURE_NAMES)
    # 모든 행이 동일한 피처 차원.
    assert all(len(row) == fm.n_features for row in fm.rows)
    # customer_ids 정렬·고유.
    assert fm.customer_ids == sorted(fm.customer_ids)
    assert len(set(fm.customer_ids)) == len(fm.customer_ids)

    labels = featmod.extract_labels(fm.customer_ids)
    assert len(labels) == fm.n_samples
    assert set(labels) <= {0, 1}
    assert sum(labels) > 0, "양성(사기) 라벨이 하나도 없습니다"


@pytest.mark.integration
def test_features_separate_fraud_from_normal(graph) -> None:
    """rule_score 피처가 사기에서 정상보다 유의미하게 높아야 한다(신호 존재)."""
    fm = featmod.build_features()
    y = np.asarray(featmod.extract_labels(fm.customer_ids))
    X = np.asarray(fm.rows)
    j = fm.feature_names.index("rule_score")
    fraud_mean = X[y == 1, j].mean()
    normal_mean = X[y == 0, j].mean()
    assert fraud_mean > normal_mean + 20.0, (
        f"rule_score 분리 부족: 사기 {fraud_mean:.1f} vs 정상 {normal_mean:.1f}"
    )


# ===========================================================================
# 모델 학습 smoke — 누수 없는 K-fold out-of-fold (Neo4j 필요)
# ===========================================================================
@pytest.mark.integration
def test_cross_validate_out_of_fold(graph) -> None:
    """Stratified K-fold out-of-fold 예측이 산출되고 일반화 AUC 가 높아야 한다.

    out-of-fold 예측(테스트 분할에서만 예측)을 쓰므로 학습 데이터로 평가하지 않는다.
    """
    fm = featmod.build_features()
    labels = featmod.extract_labels(fm.customer_ids)
    cv = ml_model.cross_validate(model_kind="rf", n_folds=5, fm=fm, labels=labels)

    assert cv.oof_proba.shape[0] == fm.n_samples
    assert cv.oof_ensemble.shape[0] == fm.n_samples
    assert np.all((cv.oof_proba >= 0) & (cv.oof_proba <= 1))
    # fold AUC 가 충분히 높아야 한다(일반화 성능 — 누수 없는 hold-out).
    assert len(cv.fold_auc) == 5
    assert np.mean(cv.fold_auc) >= 0.85, (
        f"out-of-fold AUC 미달(일반화): {np.mean(cv.fold_auc):.3f}"
    )
    # 피처 중요도가 산출되고 합이 ~1.
    assert cv.feature_importance
    assert abs(sum(cv.feature_importance.values()) - 1.0) < 1e-6


@pytest.mark.integration
def test_shuffled_labels_collapse_auc(graph) -> None:
    """라벨을 셔플하면 out-of-fold AUC 가 무작위(~0.5)로 붕괴해야 한다.

    피처에 라벨 누수 경로가 있으면 셔플해도 AUC 가 높게 남는다. 붕괴를 확인해
    실제 신호가 누수가 아닌 구조에서 온다는 점을 입증한다(핵심 누수 가드).
    """
    fm = featmod.build_features()
    y = np.asarray(featmod.extract_labels(fm.customer_ids))
    rng = np.random.RandomState(0)
    y_shuf = y.copy()
    rng.shuffle(y_shuf)
    cv = ml_model.cross_validate(
        model_kind="rf", n_folds=5, fm=fm, labels=list(y_shuf)
    )
    assert np.mean(cv.fold_auc) < 0.65, (
        f"셔플 라벨 AUC 가 너무 높음(누수 의심): {np.mean(cv.fold_auc):.3f}"
    )


@pytest.mark.integration
def test_ensemble_does_not_hurt_auc(graph) -> None:
    """앙상블(룰+ML)의 out-of-fold AUC 가 룰만 대비 떨어지지 않아야 한다."""
    cmp = ml_model.compare_three_way(model_kind="rf", n_folds=5)
    # 룰+임베딩(AUC) <= 앙상블(AUC) — ML 이 순위품질을 개선(또는 유지).
    assert cmp.ensemble_f1.auc >= cmp.rule_embed_f1.auc - 1e-6, (
        f"앙상블 AUC 가 룰보다 낮음: 룰 {cmp.rule_embed_f1.auc:.3f} "
        f"-> 앙상블 {cmp.ensemble_f1.auc:.3f}"
    )
    # F1-최적 공정 비교에서 앙상블 F1 이 룰+임베딩 이상이어야 한다(ML 기여).
    assert cmp.ensemble_f1.f1 >= cmp.rule_embed_f1.f1 - 1e-9, (
        f"앙상블 F1 이 룰+임베딩보다 낮음(공정 비교): "
        f"{cmp.rule_embed_f1.f1:.3f} -> {cmp.ensemble_f1.f1:.3f}"
    )


@pytest.mark.integration
def test_cost_threshold_minimizes_cost(graph) -> None:
    """비용 기반 임계치가 명시 비용비(FN:FP)로 비용 최소점을 고른다."""
    fm = featmod.build_features()
    labels = featmod.extract_labels(fm.customer_ids)
    cv = ml_model.cross_validate(model_kind="rf", n_folds=5, fm=fm, labels=labels)
    ct = ml_model.cost_optimal_threshold(cv.y_true, cv.oof_ensemble)
    assert 0.0 <= ct.threshold <= 1.0
    assert ct.cost_fn == ml_model.COST_FN and ct.cost_fp == ml_model.COST_FP
    # 임의의 다른 임계보다 비용이 작거나 같아야 한다(최소성).
    for t in (0.1, 0.5, 0.9):
        other = ml_model.metrics_at(cv.y_true, cv.oof_ensemble, t, label="x")
        other_cost = ml_model.COST_FN * other.fn + ml_model.COST_FP * other.fp
        assert ct.cost <= other_cost + 1e-9, (
            f"비용 최소성 위반: 선택 {ct.cost} > 임계 {t} 비용 {other_cost}"
        )
