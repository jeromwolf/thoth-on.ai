"""속성 기반 ML 레이어(개인 사기) 수용기준 테스트 — 캐글 fraud_oracle 실데이터.

검증 대상:
    1. 데이터 적재 — 캐글 CSV 행/라벨 분리(FraudFound_P 는 타깃, 피처 아님).
    2. 인코더 누수 차단 — vocabulary/중앙값을 **train 에서만 fit**, test 미지
       카테고리는 0(unknown)으로 떨어짐.
    3. 피처에 라벨(FraudFound_P) 누수 없음(소스/피처명 검사).
    4. out-of-fold CV 가 정직한 일반화 성능을 산출(불균형이라 PR-AUC > base rate).
    5. 라벨 셔플 시 AUC 가 무작위(~0.5)로 붕괴 — 누수 경로 없음 입증.
    6. 행동/시계열 피처가 실제로 인코딩됨(가입직후·주소변경 등).

scikit-learn 미설치 시 학습 테스트는 skip. CSV 만 있으면 Neo4j 불필요.
"""
from __future__ import annotations

import inspect
from pathlib import Path

import numpy as np
import pytest

from detection import attribute_model as am

_CSV = Path(am.KAGGLE_CSV)
_HAS_CSV = _CSV.exists()
_SK = am._SKLEARN

requires_data = pytest.mark.skipif(not _HAS_CSV, reason="캐글 CSV 없음")
requires_sklearn = pytest.mark.skipif(not _SK, reason="scikit-learn 미설치")


# ===========================================================================
# 누수 차단 — 라벨 미사용 (순수 단위, 외부 의존성 없음)
# ===========================================================================
@pytest.mark.smoke
def test_label_col_not_in_features() -> None:
    """FraudFound_P(라벨)가 피처 컬럼(범주/순서/수치) 어디에도 없어야 한다."""
    all_feature_cols = set(am.CATEGORICAL_COLS) | set(am.ORDINAL_MAPS) | set(am.NUMERIC_COLS)
    assert am.LABEL_COL not in all_feature_cols, "라벨이 피처에 섞임 — 누수"
    # 식별자/누수 위험 컬럼도 피처에 없어야 한다.
    for col in ("PolicyNumber", "RepNumber"):
        assert col not in all_feature_cols, f"식별자 {col} 가 피처에 포함 — 누수 위험"


@pytest.mark.smoke
def test_encoder_fit_then_transform_source() -> None:
    """인코더 transform 이 fit 산출물(vocabulary/중앙값)에만 의존해야 한다(누수 차단)."""
    src = inspect.getsource(am.AttributeEncoder.transform)
    # transform 은 라벨 컬럼을 읽지 않아야 한다.
    assert am.LABEL_COL not in src, "transform 이 라벨을 읽음 — 누수"


@pytest.mark.smoke
def test_encoder_unknown_category_is_zero() -> None:
    """train 에 없던 카테고리는 transform 에서 모두 0(unknown) — 누수/오류 없음."""
    train = [{"Fault": "Policy Holder", "BasePolicy": "Collision"}]
    enc = am.AttributeEncoder(
        categorical_cols=["Fault", "BasePolicy"], ordinal_cols=[], numeric_cols=[]
    ).fit(train)
    # test 에 train 미등장 카테고리("Third Party") → 해당 one-hot 0.
    X = enc.transform([{"Fault": "Third Party", "BasePolicy": "Collision"}])
    assert X.shape[0] == 1
    # Fault=Policy Holder 컬럼은 0, BasePolicy=Collision 은 1.
    names = enc.feature_names
    fph = names.index("Fault=Policy Holder")
    bcol = names.index("BasePolicy=Collision")
    assert X[0, fph] == 0.0
    assert X[0, bcol] == 1.0


# ===========================================================================
# 데이터 적재
# ===========================================================================
@requires_data
@pytest.mark.smoke
def test_load_kaggle_shapes() -> None:
    """캐글 CSV 가 15,420행 + 라벨 분리(약 6% 사기)로 로드되어야 한다."""
    data = am.load_kaggle()
    assert data.n == 15420, f"행 수 불일치: {data.n}"
    assert set(data.labels.tolist()) <= {0, 1}
    rate = data.n_fraud / data.n
    assert 0.05 < rate < 0.07, f"사기율 비현실적: {rate:.4f}"


@requires_data
@pytest.mark.smoke
def test_behavioral_features_encoded() -> None:
    """행동/시계열 순서형 피처가 실제로 인코딩되어야 한다(가입직후·주소변경 등)."""
    data = am.load_kaggle()
    enc = am.AttributeEncoder().fit(data.rows)
    names = enc.feature_names
    for behav in ("ord:Days_Policy_Accident", "ord:AddressChange_Claim",
                  "ord:NumberOfSuppliments"):
        assert behav in names, f"행동/시계열 피처 누락: {behav}"
    X = enc.transform(data.rows[:50])
    assert X.shape == (50, len(names))
    assert np.all(np.isfinite(X)), "피처에 NaN/Inf 존재"


# ===========================================================================
# 학습/평가 — 누수 없는 out-of-fold (scikit-learn 필요)
# ===========================================================================
@requires_data
@requires_sklearn
@pytest.mark.integration
def test_cross_validate_no_leakage() -> None:
    """out-of-fold CV 가 정직한 성능을 산출(AUC>0.7, PR-AUC>base rate)."""
    data = am.load_kaggle()
    cv = am.cross_validate_kaggle(model_kind="gb", n_folds=5, data=data,
                                  compute_perm=False)
    assert cv.oof_proba.shape[0] == data.n
    assert np.all((cv.oof_proba >= 0) & (cv.oof_proba <= 1))
    m = am.evaluate_proba(cv.y_true, cv.oof_proba)
    # 불균형(6%)이라 PR-AUC 가 무작위 기준선(사기율)보다 유의미하게 높아야 한다.
    base_rate = data.n_fraud / data.n
    assert m.auc > 0.70, f"ROC-AUC 미달: {m.auc:.3f}"
    assert m.pr_auc > base_rate * 2, (
        f"PR-AUC lift 부족: {m.pr_auc:.3f} (base {base_rate:.3f})"
    )
    assert len(cv.fold_auc) == 5


@requires_data
@requires_sklearn
@pytest.mark.integration
def test_shuffled_labels_collapse_auc() -> None:
    """라벨 셔플 시 out-of-fold AUC 가 무작위(~0.5)로 붕괴 — 누수 경로 없음."""
    data = am.load_kaggle()
    rng = np.random.RandomState(0)
    y_shuf = data.labels.copy()
    rng.shuffle(y_shuf)
    shuffled = am.RawData(rows=data.rows, labels=y_shuf)
    cv = am.cross_validate_kaggle(model_kind="gb", n_folds=5, data=shuffled,
                                  compute_perm=False)
    assert np.mean(cv.fold_auc) < 0.60, (
        f"셔플 라벨 AUC 가 너무 높음(누수 의심): {np.mean(cv.fold_auc):.3f}"
    )


@requires_data
@requires_sklearn
@pytest.mark.integration
def test_holdout_consistent_with_cv() -> None:
    """단일 hold-out 성능이 CV 와 큰 차이 없이 일관(과적합/누수 아님)."""
    data = am.load_kaggle()
    ho = am.holdout_kaggle(model_kind="gb", data=data)
    assert ho.auc > 0.70, f"hold-out AUC 미달: {ho.auc:.3f}"


# ===========================================================================
# 개선 — 파생 피처(누수 없음) · 운영점 지표 · 앙상블
# ===========================================================================
@pytest.mark.smoke
def test_engineered_features_present_and_leakfree() -> None:
    """파생 상호작용/비율 피처가 추가되고, transform 이 라벨을 읽지 않아야 한다(누수 없음)."""
    enc = am.AttributeEncoder(engineered=True)
    # 파생 피처 이름이 정의돼 있고 fit 후 feature_names 에 포함된다.
    assert "ix:Fault_PH_x_AllPerils" in am.ENGINEERED_FEATURES
    assert "r:claims_per_age" in am.ENGINEERED_FEATURES
    train = [{"Fault": "Policy Holder", "BasePolicy": "All Perils",
              "AddressChange_Claim": "under 6 months", "Age": "22",
              "Days_Policy_Accident": "1 to 7", "PastNumberOfClaims": "none"}]
    enc.fit(train)
    for f in am.ENGINEERED_FEATURES:
        assert f in enc.feature_names, f"파생 피처 누락: {f}"
    X = enc.transform(train)
    assert np.all(np.isfinite(X))
    # transform/_fill_engineered 소스가 라벨을 읽지 않는다(누수 차단).
    import inspect
    src = inspect.getsource(am.AttributeEncoder._fill_engineered)
    assert am.LABEL_COL not in src, "파생 피처가 라벨을 읽음 — 누수"
    # 본인과실 × All Perils 결합이 1 로 계산됨(설계대로).
    j = enc.feature_names.index("ix:Fault_PH_x_AllPerils")
    assert X[0, j] == 1.0


@pytest.mark.smoke
def test_rare_category_grouping() -> None:
    """희소 범주(rare_min 미만 빈도)는 vocabulary 에서 제외 — test 에서 0(unknown)."""
    train = [{"Make": "Toyota"}] * 30 + [{"Make": "Ferrari"}] * 2
    enc = am.AttributeEncoder(
        categorical_cols=["Make"], ordinal_cols=[], numeric_cols=[],
        engineered=False, rare_min=20,
    ).fit(train)
    assert "Make=Toyota" in enc.feature_names      # 빈도 30 >= 20 → 유지
    assert "Make=Ferrari" not in enc.feature_names  # 빈도 2 < 20 → 제외(노이즈 억제)


@pytest.mark.smoke
def test_recall_at_precision_monotone() -> None:
    """recall_at_precision 이 PR 곡선에서 올바른 운영점을 산출(완벽 분리 시 recall=1)."""
    y = np.array([0, 0, 0, 0, 1, 1])
    proba = np.array([0.1, 0.2, 0.3, 0.4, 0.9, 0.95])  # 완벽 분리
    rec, thr = am.recall_at_precision(y, proba, 0.9)
    assert rec == 1.0 and thr is not None
    # 도달 불가 precision 은 (0.0, None).
    y2 = np.array([1, 0, 1, 0])
    proba2 = np.array([0.5, 0.5, 0.5, 0.5])  # 무작위 — precision=0.5 상한
    rec2, _ = am.recall_at_precision(y2, proba2, 0.99)
    assert rec2 == 0.0


@pytest.mark.smoke
def test_cost_threshold_responds_to_ratio() -> None:
    """비용비가 클수록(FN 더 비쌈) 임계가 낮아져 더 많이 적발해야 한다."""
    rng = np.random.RandomState(0)
    y = (rng.rand(500) < 0.1).astype(int)
    proba = np.clip(0.3 * y + rng.rand(500) * 0.5, 0, 1)
    t_cheap, _ = am.best_cost_threshold(y, proba, fn_cost=2.0, fp_cost=1.0)
    t_expensive, _ = am.best_cost_threshold(y, proba, fn_cost=20.0, fp_cost=1.0)
    assert t_expensive <= t_cheap, "FN 비용↑ 시 임계가 낮아져야(더 적발) 한다"


@requires_data
@requires_sklearn
@pytest.mark.integration
def test_ensemble_no_leakage_and_improves_pr() -> None:
    """기본 ens 앙상블이 누수 없이 동작하고 PR-AUC 가 base rate 의 2배 이상."""
    data = am.load_kaggle()
    cv = am.cross_validate_kaggle(model_kind="ens", n_folds=5, data=data,
                                  compute_perm=False)
    assert cv.oof_proba.shape[0] == data.n
    assert np.all((cv.oof_proba >= 0) & (cv.oof_proba <= 1))
    m = am.evaluate_proba(cv.y_true, cv.oof_proba)
    base_rate = data.n_fraud / data.n
    assert m.auc > 0.70
    assert m.pr_auc > base_rate * 2
    # 운영점 — precision 0.4 에서 양수 recall(헛알림 통제 가능 입증).
    rec40, _ = am.recall_at_precision(cv.y_true, cv.oof_proba, 0.40)
    assert rec40 > 0.0, "precision 0.4 운영점 도달 불가 — 헛알림 통제 불가"


@requires_data
@requires_sklearn
@pytest.mark.integration
def test_ensemble_shuffled_labels_collapse() -> None:
    """ens 도 라벨 셔플 시 AUC 가 무작위로 붕괴 — 파생 피처 경로에 누수 없음."""
    data = am.load_kaggle()
    rng = np.random.RandomState(0)
    y_shuf = data.labels.copy()
    rng.shuffle(y_shuf)
    cv = am.cross_validate_kaggle(model_kind="ens", n_folds=5,
                                  data=am.RawData(rows=data.rows, labels=y_shuf),
                                  compute_perm=False)
    assert np.mean(cv.fold_auc) < 0.60, (
        f"셔플 라벨 AUC 가 너무 높음(누수 의심): {np.mean(cv.fold_auc):.3f}"
    )


@requires_data
@requires_sklearn
@pytest.mark.integration
def test_train_and_explain() -> None:
    """전체 학습 추론기가 동작하고 개별 청구 설명(상위 기여)을 산출한다."""
    data = am.load_kaggle()
    model = am.train_attribute_model(model_kind="lr", data=data)
    proba = model.score_rows(data.rows[:10])
    assert proba.shape[0] == 10
    assert np.all((proba >= 0) & (proba <= 1))
    expl = model.explain_row(data.rows[0], top_k=5)
    assert 1 <= len(expl) <= 5
    assert all("feature" in e and "contribution" in e for e in expl)
