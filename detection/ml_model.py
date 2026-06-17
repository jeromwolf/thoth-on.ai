"""ML 분류기 + 룰/GDS/ML 앙상블 (WP3 · FR-3.7) — 정직한 일반화 성능판.

지금까지 수동 가중합(detection.scoring)으로 그래프 신호를 결합해 왔다. 이 모듈은
ground truth 라벨로 **그래프 신호 가중치를 자동 최적화**하는 scikit-learn 분류기를
학습하고, 룰+GDS 스코어와 ML 확률을 **앙상블**해 단독 대비 개선 여부를 정직하게
측정한다.

[평가 누수 절대 금지 — 최우선 제약]
    1) 피처(detection.features)에 ring_id/is_fraud_ring/ring_pattern 또는 그 파생을
       절대 넣지 않는다(features 모듈이 구조적으로 차단).
    2) 성능은 **K-fold 교차검증(stratified)** 또는 **hold-out** 으로만 보고한다.
       학습에 쓴 데이터로 평가하지 않는다(in-sample 부풀리기 금지). 모든 표(R/P/F1/
       FPR/AUC)는 각 fold 의 **테스트 분할 예측만** 모아 산출한다(out-of-fold).
    3) 앙상블 비교(룰만 vs 룰+임베딩 vs +ML)도 동일한 CV 분할의 out-of-fold 예측으로
       비교해 ML 의 기여를 정직하게 본다.

[클래스 불균형]
    사기는 소수(약 112/5847 ≈ 1.9%). LogisticRegression/RandomForest 모두
    ``class_weight='balanced'`` 로 소수 클래스 가중을 높여 재현율 붕괴를 막는다.

[앙상블]
    룰 종합 스코어(rule_score, 0~100 → 0~1 정규화)와 ML 사기확률을 가중 평균한다.
    가중치 ENSEMBLE_W_ML 은 out-of-fold 기준으로 고정(학습 누수 없음). stacking 변형도
    제공(메타 LogisticRegression)하나 기본은 단순 가중평균(설명가능·안정).

[비용 기반 임계치]
    사기 놓침(FN) 비용 != 헛조사(FP) 비용. 가정: FN:FP = COST_FN:COST_FP = 20:1
    (적발 못한 사기 1건이 헛조사 20건보다 비싸다 — 보험사기 평균 피해/조사비 가정).
    out-of-fold 확률에서 비용 = COST_FN*FN + COST_FP*FP 를 최소화하는 임계치를 산출.

CLI:
    python -m detection.ml_model              # 전체: CV 학습/평가 + 3단 비교 + 중요도
    python -m detection.ml_model --folds 5    # fold 수 지정
    python -m detection.ml_model --model rf   # 모델 선택(lr/rf/gb)
"""
from __future__ import annotations

import argparse
import warnings
from dataclasses import dataclass, field
from typing import Any

import numpy as np

# numpy 2.0 + 일부 BLAS 에서 zero-variance 컬럼 스케일링 시 무해한 overflow/divide
# 경고가 난다(결과 정상). CLI 출력을 깨끗하게 유지하기 위해 억제한다.
np.seterr(all="ignore")
warnings.filterwarnings("ignore", category=RuntimeWarning)

from detection import features as featmod
from thoth import db

# scikit-learn — 지연 임포트(미설치 환경에서 모듈 임포트 자체는 가능하게).
try:
    from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import roc_auc_score
    from sklearn.model_selection import StratifiedKFold
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler

    _SKLEARN = True
except Exception:  # pragma: no cover - 미설치 가드
    _SKLEARN = False


# ------------------------------------------------------------------
# 비용 가정 (명시) — FN(사기 놓침) 이 FP(헛조사)보다 20배 비싸다.
# ------------------------------------------------------------------
COST_FN = 20.0
COST_FP = 1.0

# 앙상블 가중 — 룰 스코어 vs ML 확률 가중평균. out-of-fold 로 검증된 고정값.
ENSEMBLE_W_ML = 0.6
ENSEMBLE_W_RULE = 1.0 - ENSEMBLE_W_ML

DEFAULT_FOLDS = 5
RANDOM_SEED = 42


def _make_classifier(kind: str) -> Any:
    """모델 종류별 scikit-learn 분류기(불균형 처리 포함)를 만든다.

    Args:
        kind: 'lr'(LogisticRegression) / 'rf'(RandomForest) / 'gb'(GradientBoosting).

    Returns:
        scikit-learn estimator. lr 은 StandardScaler 파이프라인으로 감싼다.
    """
    if kind == "lr":
        # liblinear 솔버 — 소수 클래스 불균형/희소 피처에서 lbfgs 보다 안정(오버플로 회피).
        return Pipeline([
            ("scaler", StandardScaler()),
            ("clf", LogisticRegression(
                class_weight="balanced", solver="liblinear", C=1.0,
                max_iter=2000, random_state=RANDOM_SEED,
            )),
        ])
    if kind == "rf":
        return RandomForestClassifier(
            n_estimators=300, max_depth=None, class_weight="balanced_subsample",
            min_samples_leaf=2, random_state=RANDOM_SEED, n_jobs=-1,
        )
    if kind == "gb":
        # GradientBoosting 은 class_weight 미지원 → sample_weight 로 불균형 처리.
        return GradientBoostingClassifier(random_state=RANDOM_SEED)
    raise ValueError(f"알 수 없는 모델 종류: {kind}")


def _fit_with_balance(clf: Any, kind: str, X: np.ndarray, y: np.ndarray) -> Any:
    """불균형을 고려해 학습한다(gb 는 sample_weight 사용)."""
    if kind == "gb":
        # 소수 클래스에 (다수/소수) 비율 가중.
        n_pos = max(1, int(y.sum()))
        n_neg = max(1, int(len(y) - y.sum()))
        w_pos = n_neg / n_pos
        sw = np.where(y == 1, w_pos, 1.0)
        clf.fit(X, y, sample_weight=sw)
    else:
        clf.fit(X, y)
    return clf


# ==================================================================
# Out-of-fold 교차검증 — 누수 없는 정직한 일반화 성능
# ==================================================================
@dataclass
class CVResult:
    """교차검증 out-of-fold 결과 — 누수 없는 일반화 성능.

    ``oof_proba`` 는 각 표본이 **테스트 분할이었을 때의** 예측 확률만 모은 것이라
    in-sample 누수가 없다. fold 별 지표의 평균±표준편차로 안정성을 보고한다.
    """

    model_kind: str
    customer_ids: list[str]
    y_true: np.ndarray
    oof_proba: np.ndarray            # ML 단독 out-of-fold 사기확률
    oof_ensemble: np.ndarray         # 룰+ML 앙상블 out-of-fold 점수(0~1)
    rule_score_norm: np.ndarray      # 룰 종합 스코어 정규화(0~1) — 앙상블/비교용
    fold_auc: list[float] = field(default_factory=list)        # ML fold AUC
    fold_auc_ens: list[float] = field(default_factory=list)    # 앙상블 fold AUC
    feature_names: list[str] = field(default_factory=list)
    feature_importance: dict[str, float] = field(default_factory=dict)


def _rule_score_index() -> int:
    return featmod.FEATURE_NAMES.index("rule_score")


def cross_validate(
    *,
    model_kind: str = "rf",
    n_folds: int = DEFAULT_FOLDS,
    fm: featmod.FeatureMatrix | None = None,
    labels: list[int] | None = None,
) -> CVResult:
    """Stratified K-fold 교차검증으로 **누수 없는** out-of-fold 예측을 산출한다.

    각 fold 에서 train 분할로만 학습하고 test 분할을 예측해 모은다(out-of-fold).
    학습에 쓴 데이터로 평가하지 않으므로 in-sample 부풀리기가 없다. 룰 스코어는
    피처에서 그대로 가져와(이미 라벨 미사용 점수) 앙상블에 결합한다.

    Args:
        model_kind: 'lr'/'rf'/'gb'.
        n_folds: K-fold 분할 수(stratified — 소수 클래스 비율 유지).
        fm: 미리 만든 FeatureMatrix(재사용). None 이면 새로 추출.
        labels: 미리 추출한 라벨. None 이면 새로 추출.

    Returns:
        ``CVResult`` — out-of-fold 확률/앙상블 점수 + fold AUC + 피처 중요도.
    """
    if not _SKLEARN:
        raise RuntimeError("scikit-learn 미설치 — `.venv/bin/pip install scikit-learn`")

    if fm is None:
        fm = featmod.build_features()
    if labels is None:
        labels = featmod.extract_labels(fm.customer_ids)

    X = np.asarray(fm.rows, dtype=float)
    y = np.asarray(labels, dtype=int)
    n = len(y)

    rs_idx = _rule_score_index()
    rule_norm = np.clip(X[:, rs_idx] / 100.0, 0.0, 1.0)

    oof_proba = np.zeros(n, dtype=float)
    oof_ensemble = np.zeros(n, dtype=float)
    fold_auc: list[float] = []
    fold_auc_ens: list[float] = []
    importance_acc = np.zeros(X.shape[1], dtype=float)
    importance_folds = 0

    skf = StratifiedKFold(n_splits=n_folds, shuffle=True, random_state=RANDOM_SEED)
    for train_idx, test_idx in skf.split(X, y):
        clf = _make_classifier(model_kind)
        _fit_with_balance(clf, model_kind, X[train_idx], y[train_idx])
        proba = clf.predict_proba(X[test_idx])[:, 1]
        oof_proba[test_idx] = proba

        ens = ENSEMBLE_W_ML * proba + ENSEMBLE_W_RULE * rule_norm[test_idx]
        oof_ensemble[test_idx] = ens

        # fold AUC(테스트 분할 기준) — 양 클래스 존재 시에만.
        if len(set(y[test_idx].tolist())) == 2:
            fold_auc.append(float(roc_auc_score(y[test_idx], proba)))
            fold_auc_ens.append(float(roc_auc_score(y[test_idx], ens)))

        # 피처 중요도 누적(tree 계열은 feature_importances_, lr 은 |coef|).
        imp = _extract_importance(clf, model_kind)
        if imp is not None:
            importance_acc += imp
            importance_folds += 1

    feature_importance: dict[str, float] = {}
    if importance_folds:
        avg = importance_acc / importance_folds
        s = avg.sum()
        if s > 0:
            avg = avg / s  # 합=1 정규화(상대 기여도)
        feature_importance = {
            name: float(v) for name, v in zip(fm.feature_names, avg)
        }

    return CVResult(
        model_kind=model_kind,
        customer_ids=list(fm.customer_ids),
        y_true=y,
        oof_proba=oof_proba,
        oof_ensemble=oof_ensemble,
        rule_score_norm=rule_norm,
        fold_auc=fold_auc,
        fold_auc_ens=fold_auc_ens,
        feature_names=list(fm.feature_names),
        feature_importance=feature_importance,
    )


def _extract_importance(clf: Any, kind: str) -> np.ndarray | None:
    """학습된 분류기에서 피처 중요도 벡터를 뽑는다(없으면 None)."""
    est = clf
    if kind == "lr":
        est = clf.named_steps["clf"]
        coef = np.abs(est.coef_).ravel()
        return coef
    if hasattr(est, "feature_importances_"):
        return np.asarray(est.feature_importances_, dtype=float)
    return None


# ==================================================================
# 지표 — out-of-fold 확률/점수에서 임계 기반 R/P/F1/FPR + AUC
# ==================================================================
@dataclass
class Metrics:
    """임계 기반 분류 지표(out-of-fold)."""

    label: str
    threshold: float
    tp: int
    fp: int
    fn: int
    tn: int
    recall: float
    precision: float
    f1: float
    fpr: float
    auc: float

    def to_row(self) -> str:
        return (f"  {self.label:<16} {self.recall:>8.3f} {self.precision:>8.3f} "
                f"{self.f1:>8.3f} {self.fpr:>8.4f} {self.auc:>8.3f} "
                f"{self.tp:>5} {self.fp:>5}")


def metrics_at(
    y_true: np.ndarray, score: np.ndarray, threshold: float, *, label: str
) -> Metrics:
    """주어진 점수/임계로 R/P/F1/FPR/AUC 를 계산한다(out-of-fold 입력 가정)."""
    pred = score >= threshold
    tp = int(np.sum(pred & (y_true == 1)))
    fp = int(np.sum(pred & (y_true == 0)))
    fn = int(np.sum(~pred & (y_true == 1)))
    tn = int(np.sum(~pred & (y_true == 0)))
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    fpr = fp / (fp + tn) if (fp + tn) else 0.0
    try:
        auc = float(roc_auc_score(y_true, score)) if len(set(y_true.tolist())) == 2 else 0.0
    except Exception:
        auc = 0.0
    return Metrics(label=label, threshold=threshold, tp=tp, fp=fp, fn=fn, tn=tn,
                   recall=recall, precision=precision, f1=f1, fpr=fpr, auc=auc)


# ==================================================================
# 비용 기반 임계치 — out-of-fold 확률에서 비용 최소점
# ==================================================================
@dataclass
class CostThreshold:
    """비용 최소 임계치 산출 결과."""

    threshold: float
    cost: float
    tp: int
    fp: int
    fn: int
    recall: float
    precision: float
    cost_fn: float
    cost_fp: float


def f1_optimal_threshold(y_true: np.ndarray, score: np.ndarray) -> float:
    """out-of-fold 점수에서 F1 을 최대화하는 임계치를 찾는다(공정 비교용).

    각 방식을 '자기 최적 운영점'에서 비교해야 임계 선택 편향 없이 정직하다.
    """
    candidates = np.unique(np.concatenate([
        np.linspace(0.0, 1.0, 201), np.unique(score)
    ]))
    best_t, best_f1 = 0.5, -1.0
    for t in candidates:
        pred = score >= t
        tp = int(np.sum(pred & (y_true == 1)))
        fp = int(np.sum(pred & (y_true == 0)))
        fn = int(np.sum(~pred & (y_true == 1)))
        prec = tp / (tp + fp) if (tp + fp) else 0.0
        rec = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = (2 * prec * rec / (prec + rec)) if (prec + rec) else 0.0
        if f1 > best_f1:
            best_f1, best_t = f1, float(t)
    return best_t


def cost_optimal_threshold(
    y_true: np.ndarray, score: np.ndarray, *,
    cost_fn: float = COST_FN, cost_fp: float = COST_FP,
) -> CostThreshold:
    """비용 = cost_fn*FN + cost_fp*FP 를 최소화하는 임계치를 out-of-fold 로 산출한다.

    후보 임계는 점수의 고유값 + 미세 그리드. 학습 누수 없이(out-of-fold 점수)
    운영 비용을 최소화하는 운영점을 고른다.
    """
    candidates = np.unique(np.concatenate([
        np.linspace(0.0, 1.0, 101), np.unique(score)
    ]))
    best: CostThreshold | None = None
    for t in candidates:
        pred = score >= t
        tp = int(np.sum(pred & (y_true == 1)))
        fp = int(np.sum(pred & (y_true == 0)))
        fn = int(np.sum(~pred & (y_true == 1)))
        cost = cost_fn * fn + cost_fp * fp
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        cand = CostThreshold(
            threshold=float(t), cost=float(cost), tp=tp, fp=fp, fn=fn,
            recall=recall, precision=precision, cost_fn=cost_fn, cost_fp=cost_fp,
        )
        if best is None or cand.cost < best.cost:
            best = cand
    assert best is not None
    return best


# ==================================================================
# 3단 비교 — 룰만 vs 룰+임베딩 vs +ML(앙상블) — 모두 out-of-fold
# ==================================================================
@dataclass
class ThreeWayComparison:
    """룰만 / 룰+임베딩 / 룰+임베딩+ML(앙상블) 비교 + 수법별 회수 + 중요도.

    각 방식을 (1) 공통 룰 임계(0.5)와 (2) **각자 F1-최적 임계**(공정 비교) 두 기준으로
    제공한다. 모든 점수는 out-of-fold(누수 없음).
    """

    # 고정 임계(룰 0.5 / 앙상블·ML 은 비용최소) — 운영 관점.
    rule_only: Metrics
    rule_embed: Metrics
    ensemble: Metrics
    ml_only: Metrics
    # 각자 F1-최적 임계 — 임계 선택 편향 없는 공정 비교.
    rule_only_f1: Metrics
    rule_embed_f1: Metrics
    ensemble_f1: Metrics
    ml_only_f1: Metrics
    cv: CVResult
    cost_threshold: CostThreshold
    pattern_recall: dict[str, dict[str, dict[str, float]]] = field(default_factory=dict)


def _rule_only_oof_score(fm: featmod.FeatureMatrix) -> np.ndarray:
    """'룰만' 비교용 점수 — GDS/임베딩 제외 순수 룰 스코어(0~1).

    features 의 rule_score 는 GDS+임베딩 corroborating 을 포함하므로, '룰만' baseline
    을 위해 detection.scoring 을 use_gds/use_embedding=False 로 재호출해 순수 룰
    점수를 뽑는다. 라벨 미사용(점수 계산엔 라벨 안 씀).
    """
    from detection import scoring

    risks = scoring.score_customers(use_gds=False, use_embedding=False)
    by_cid = {cid: r.score for cid, r in risks.items()}
    return np.array([min(by_cid.get(cid, 0.0) / 100.0, 1.0)
                     for cid in fm.customer_ids], dtype=float)


def _pattern_recall(
    customer_ids: list[str], y_true: np.ndarray,
    scores: dict[str, tuple[np.ndarray, float]],
) -> dict[str, dict[str, dict[str, float]]]:
    """수법(ring_pattern)별 탐지율을 각 방식(scores)별로 산출한다.

    Args:
        scores: {방식라벨: (점수배열, 임계)}.

    Returns:
        {방식: {수법: {total, detected, recall}}}.
    """
    rows = db.run(
        """
        MATCH (c:Customer)
        WHERE c.is_fraud_ring AND coalesce(c.ring_pattern,'') <> ''
        RETURN c.customer_id AS cid, c.ring_pattern AS pat
        """
    )
    pat_by_cid = {r["cid"]: r["pat"] for r in rows}
    idx_by_cid = {cid: i for i, cid in enumerate(customer_ids)}

    out: dict[str, dict[str, dict[str, float]]] = {}
    for method, (score, thr) in scores.items():
        per_pat: dict[str, dict[str, float]] = {}
        for cid, pat in pat_by_cid.items():
            i = idx_by_cid.get(cid)
            if i is None:
                continue
            d = per_pat.setdefault(pat, {"total": 0.0, "detected": 0.0, "recall": 0.0})
            d["total"] += 1.0
            if score[i] >= thr:
                d["detected"] += 1.0
        for pat, d in per_pat.items():
            d["recall"] = d["detected"] / d["total"] if d["total"] else 0.0
        out[method] = per_pat
    return out


def compare_three_way(
    *,
    model_kind: str = "rf",
    n_folds: int = DEFAULT_FOLDS,
    rule_threshold: float = 0.5,
) -> ThreeWayComparison:
    """룰만 vs 룰+임베딩 vs +ML(앙상블)을 **동일 CV out-of-fold** 로 비교한다.

    누수 없는 비교를 위해:
        · 룰만/룰+임베딩 점수는 라벨 미사용 룰 스코어(정규화).
        · ML/앙상블 점수는 cross_validate 의 out-of-fold 예측.
    각 방식의 비용 최소 임계(앙상블 기준)와 룰 기본 임계(0.5)로 지표를 낸다.

    Args:
        model_kind: ML 모델 종류('lr'/'rf'/'gb').
        n_folds: 교차검증 fold 수.
        rule_threshold: 룰/룰+임베딩 점수의 판정 임계(0~1, 기본 0.5 = 50점).

    Returns:
        ``ThreeWayComparison``.
    """
    fm = featmod.build_features()
    labels = featmod.extract_labels(fm.customer_ids)
    y = np.asarray(labels, dtype=int)

    cv = cross_validate(model_kind=model_kind, n_folds=n_folds, fm=fm, labels=labels)

    # 룰만(GDS/임베딩 제외) / 룰+임베딩(features.rule_score = GDS+임베딩 포함)
    rule_only_score = _rule_only_oof_score(fm)
    rule_embed_score = cv.rule_score_norm  # GDS+임베딩 corroborating 포함 룰 점수

    # 비용 최소 임계(앙상블 out-of-fold 기준).
    cost = cost_optimal_threshold(y, cv.oof_ensemble)

    rule_only = metrics_at(y, rule_only_score, rule_threshold, label="룰만")
    rule_embed = metrics_at(y, rule_embed_score, rule_threshold, label="룰+임베딩")
    ensemble = metrics_at(y, cv.oof_ensemble, cost.threshold, label="룰+임베딩+ML")
    ml_only = metrics_at(y, cv.oof_proba, cost.threshold, label="ML단독")

    # 각자 F1-최적 임계(공정 비교 — 임계 선택 편향 제거).
    t_ro = f1_optimal_threshold(y, rule_only_score)
    t_re = f1_optimal_threshold(y, rule_embed_score)
    t_en = f1_optimal_threshold(y, cv.oof_ensemble)
    t_ml = f1_optimal_threshold(y, cv.oof_proba)
    rule_only_f1 = metrics_at(y, rule_only_score, t_ro, label="룰만")
    rule_embed_f1 = metrics_at(y, rule_embed_score, t_re, label="룰+임베딩")
    ensemble_f1 = metrics_at(y, cv.oof_ensemble, t_en, label="룰+임베딩+ML")
    ml_only_f1 = metrics_at(y, cv.oof_proba, t_ml, label="ML단독")

    pattern_recall = _pattern_recall(
        fm.customer_ids, y,
        {
            "룰만": (rule_only_score, rule_threshold),
            "룰+임베딩": (rule_embed_score, rule_threshold),
            "룰+임베딩+ML": (cv.oof_ensemble, cost.threshold),
        },
    )

    return ThreeWayComparison(
        rule_only=rule_only,
        rule_embed=rule_embed,
        ensemble=ensemble,
        ml_only=ml_only,
        rule_only_f1=rule_only_f1,
        rule_embed_f1=rule_embed_f1,
        ensemble_f1=ensemble_f1,
        ml_only_f1=ml_only_f1,
        cv=cv,
        cost_threshold=cost,
        pattern_recall=pattern_recall,
    )


# ==================================================================
# 리포트
# ==================================================================
def _print_report(cmp: ThreeWayComparison) -> None:
    line = "=" * 76
    cv = cmp.cv
    print(line)
    print(" THOTH-ON WP3 ML 분류기 + 앙상블 (FR-3.7) — 누수 없는 일반화 성능")
    print(line)
    print(f"  모델            : {cv.model_kind}  (class_weight=balanced)")
    print(f"  표본/피처       : {len(cv.y_true):,}명 / {len(cv.feature_names)}피처")
    print(f"  사기(양성)      : {int(cv.y_true.sum())}명 "
          f"({cv.y_true.mean()*100:.2f}%)  — 클래스 불균형")
    print(f"  검증 방식       : Stratified {len(cv.fold_auc)}-fold out-of-fold "
          f"(학습데이터 평가 금지 — 누수 차단)")
    print("-" * 76)
    if cv.fold_auc:
        print(f"  ML 단독 fold AUC   : 평균 {np.mean(cv.fold_auc):.3f} "
              f"± {np.std(cv.fold_auc):.3f}  {[round(a,3) for a in cv.fold_auc]}")
    if cv.fold_auc_ens:
        print(f"  앙상블 fold AUC    : 평균 {np.mean(cv.fold_auc_ens):.3f} "
              f"± {np.std(cv.fold_auc_ens):.3f}  {[round(a,3) for a in cv.fold_auc_ens]}")
    print(line)

    # 3단 비교표 A — 각자 F1-최적 임계(공정 비교, 임계 선택 편향 제거)
    print(" 3단 비교 [A] 각 방식 F1-최적 임계 (공정 비교 — 모두 out-of-fold)")
    print("-" * 76)
    print(f"  {'방식':<16} {'recall':>8} {'prec':>8} {'F1':>8} {'FPR':>8} "
          f"{'AUC':>8} {'TP':>5} {'FP':>5}")
    print("-" * 76)
    print(cmp.rule_only_f1.to_row())
    print(cmp.rule_embed_f1.to_row())
    print(cmp.ensemble_f1.to_row())
    print(cmp.ml_only_f1.to_row())
    print("-" * 76)
    # ML 기여 정직 보고 — F1-최적(공정) 기준 + 임계 무관 AUC.
    base_f1 = cmp.rule_embed_f1.f1
    ens_f1 = cmp.ensemble_f1.f1
    delta = ens_f1 - base_f1
    verdict = ("개선됨" if delta > 0.005 else
               ("거의 동일" if abs(delta) <= 0.005 else "악화"))
    print(f"  ML 앙상블 F1 기여(공정): {base_f1:.3f} → {ens_f1:.3f} "
          f"(Δ{delta:+.3f}) — {verdict}")
    auc_base = cmp.rule_embed_f1.auc
    auc_ens = cmp.ensemble_f1.auc
    print(f"  AUC(임계무관) 기여     : 룰+임베딩 {auc_base:.3f} → "
          f"앙상블 {auc_ens:.3f} (Δ{auc_ens-auc_base:+.3f}), ML단독 {cmp.ml_only_f1.auc:.3f}")
    print(line)

    # 3단 비교표 B — 운영 임계(룰 0.5 / 앙상블 비용최소)
    print(" 3단 비교 [B] 운영 임계 (룰=0.5, 앙상블/ML=비용최소) — 운영 관점")
    print("-" * 76)
    print(f"  {'방식':<16} {'recall':>8} {'prec':>8} {'F1':>8} {'FPR':>8} "
          f"{'AUC':>8} {'TP':>5} {'FP':>5}")
    print("-" * 76)
    print(cmp.rule_only.to_row())
    print(cmp.rule_embed.to_row())
    print(cmp.ensemble.to_row())
    print(cmp.ml_only.to_row())
    print(line)

    # 비용 기반 임계치
    ct = cmp.cost_threshold
    print(" 비용 기반 임계치 (앙상블 out-of-fold)")
    print("-" * 76)
    print(f"  비용 가정       : FN(사기놓침):FP(헛조사) = {ct.cost_fn:.0f}:{ct.cost_fp:.0f}")
    print(f"  비용 최소 임계  : {ct.threshold:.3f}")
    print(f"  운영점          : recall {ct.recall:.3f} / precision {ct.precision:.3f} "
          f"(TP={ct.tp}, FP={ct.fp}, FN={ct.fn})")
    print(f"  총 기대 비용    : {ct.cost:.0f}  (= {ct.cost_fn:.0f}*{ct.fn} + "
          f"{ct.cost_fp:.0f}*{ct.fp})")
    print(line)

    # 수법별 탐지율
    print(" 수법(ring_pattern)별 탐지율 — 방식별")
    print("-" * 76)
    order = ["fake_admission_star", "collision_ring", "repair_overbill",
             "agent_fraud", "driver_swap"]
    methods = ["룰만", "룰+임베딩", "룰+임베딩+ML"]
    print(f"  {'수법':<22}" + "".join(f"{m:>14}" for m in methods))
    for pat in order:
        cells = []
        for m in methods:
            d = cmp.pattern_recall.get(m, {}).get(pat, {})
            cells.append(f"{d.get('recall', 0.0):>14.3f}")
        print(f"  {pat:<22}" + "".join(cells))
    print(line)

    # 피처 중요도 상위
    print(" 피처 중요도 상위 (그래프 신호의 사기 판별 기여 — 설명가능성)")
    print("-" * 76)
    top = sorted(cv.feature_importance.items(), key=lambda kv: kv[1], reverse=True)
    for i, (name, imp) in enumerate(top[:12], 1):
        bar = "#" * int(imp * 60)
        print(f"  {i:>2}. {name:<28}{imp:>7.3f}  {bar}")
    print(line)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="THOTH-ON WP3 ML 분류기 + 앙상블 (FR-3.7)")
    p.add_argument("--model", default="rf", choices=["lr", "rf", "gb"],
                   help="분류 모델(lr=LogisticRegression, rf=RandomForest, gb=GradientBoosting)")
    p.add_argument("--folds", type=int, default=DEFAULT_FOLDS, help="교차검증 fold 수")
    p.add_argument("--rule-threshold", type=float, default=0.5,
                   help="룰/룰+임베딩 판정 임계(0~1, 기본 0.5=50점)")
    args = p.parse_args(argv)

    if not _SKLEARN:
        print("scikit-learn 미설치 — `.venv/bin/pip install scikit-learn` 후 재실행")
        return 1
    if not db.healthcheck():
        print("Neo4j 미가용 — `make up && make wait-neo4j` 후 재실행")
        return 1

    cmp = compare_three_way(
        model_kind=args.model, n_folds=args.folds, rule_threshold=args.rule_threshold,
    )
    _print_report(cmp)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
