"""속성 기반 ML 레이어 (개인 사기 보완 — 듀얼 레이어 ①) — 캐글 실데이터 검증판.

[배경 — 그래프 탐지의 정직한 한계]
    THOTH-ON 그래프 탐지는 **조직형 공모 사기**(같은 계좌 수령·교차목격·브로커
    방사형)엔 강하다(링 단위 F1 ≈ 0.93). 그러나 ``docs/kaggle_findings.md`` 가
    실측으로 보였듯, 현실 자동차보험 사기의 **76%는 공모 네트워크가 없는 개인
    단발성 과장청구**이고 이를 그래프는 거의 못 잡는다(배경 사기 recall ≈ 0.2%).

    이 모듈은 그 빈자리를 **청구 단위 속성 기반 ML 분류기**로 보완한다. 캐글
    Oracle ``fraud_oracle.csv``(15,420건, 개인 사기 라벨 ``FraudFound_P``,
    사기율 5.99%)를 실데이터로 학습/검증하여, 그래프가 못 보는 "가입 직후 청구·
    본인과실·고액 차량·주소 변경 직후" 같은 **개인 사기 신호**를 잡는다.

[피처 설계 — 행동/시계열 신호 포함]
    · 범주형 : Fault, BasePolicy, VehicleCategory, AccidentArea, Make, Sex,
               MaritalStatus, AgentType, PolicyType (one-hot).
    · 수치/순서형 : Age, Deductible, DriverRating, PastNumberOfClaims,
               AgeOfVehicle, AgeOfPolicyHolder, VehiclePrice (순서 인코딩).
    · 행동/시계열 : Days_Policy_Accident, Days_Policy_Claim, AddressChange_Claim,
               PoliceReportFiled, WitnessPresent, NumberOfCars, NumberOfSuppliments.

    캐글의 순서형 컬럼(예: "31 to 35", "more than 7")은 **구간 중앙값/순서 점수**로
    수치화한다(범주 폭증 방지 + 단조성 활용). 누락/미지 카테고리는 0/중앙값.

[클래스 불균형(5.99%) 처리]
    LogisticRegression/RandomForest 는 ``class_weight='balanced'``,
    GradientBoosting 은 sample_weight 로 소수 클래스 가중. 불균형이 심하므로
    accuracy 는 무의미 — **PR-AUC / recall@임계 / F1** 을 중시한다.

[평가 누수 절대 금지]
    · 학습/검증은 **Stratified K-fold out-of-fold** 또는 hold-out 으로만 보고한다.
      학습에 쓴 데이터로 평가하지 않는다(in-sample 부풀리기 금지).
    · 인코딩(순서 매핑·카테고리 vocabulary)은 **train 분할에서만 학습**해 test 에
      적용한다(fold 내 fit/transform 분리 — 카테고리 누수 차단).
    · ``FraudFound_P`` 는 타깃(y)으로만 쓰고 절대 피처(X)에 넣지 않는다.
    · ``PolicyNumber`` 등 식별자/누수 위험 컬럼은 피처에서 제외한다.

[설명가능성 — "왜 의심인가"]
    SHAP 미설치 환경이므로, (1) LogisticRegression 계수(부호+크기)와 (2) 모델
    무관 **permutation importance**(out-of-fold)로 피처 기여를 산출한다. 또한
    개별 청구에 대해 LR 계수×표준화 피처값으로 **상위 기여 사유**(가입직후 청구·
    본인과실·고액 등)를 자연어로 설명한다.

CLI:
    .venv/bin/python -m detection.attribute_model            # 캐글 CV 학습/평가
    .venv/bin/python -m detection.attribute_model --model lr # 모델 선택(lr/rf/gb)
    .venv/bin/python -m detection.attribute_model --folds 5  # fold 수
    .venv/bin/python -m detection.attribute_model --holdout  # 단일 hold-out 평가
"""
from __future__ import annotations

import argparse
import csv
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

# numpy 2.0 + 일부 BLAS 에서 zero-variance 스케일링 시 무해한 overflow/divide
# 경고가 난다(결과 정상). CLI 출력을 깨끗하게 유지하기 위해 억제한다.
np.seterr(all="ignore")
warnings.filterwarnings("ignore", category=RuntimeWarning)

try:
    from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
    from sklearn.inspection import permutation_importance
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import (
        average_precision_score,
        f1_score,
        precision_score,
        recall_score,
        roc_auc_score,
    )
    from sklearn.model_selection import StratifiedKFold, train_test_split
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler

    _SKLEARN = True
except Exception:  # pragma: no cover - 미설치 가드
    _SKLEARN = False


KAGGLE_CSV = "data/kaggle/fraud_oracle.csv"
LABEL_COL = "FraudFound_P"
RANDOM_SEED = 42
DEFAULT_FOLDS = 5

# 식별자/누수 위험 — 피처에서 제외. PolicyNumber 는 행 식별자, Year 는 연도 상수성.
_EXCLUDE_COLS = {LABEL_COL, "PolicyNumber", "RepNumber", "Year"}

# ------------------------------------------------------------------
# 피처 분류 — 캐글 fraud_oracle 컬럼.
# ------------------------------------------------------------------
# 범주형(one-hot). 행동/시계열 범주 신호도 여기 포함(순서가 약하거나 명목형).
CATEGORICAL_COLS = [
    "Fault", "BasePolicy", "VehicleCategory", "AccidentArea", "Make", "Sex",
    "MaritalStatus", "AgentType", "PolicyType",
    # 행동/시계열(명목·이진)
    "PoliceReportFiled", "WitnessPresent",
    "Month", "DayOfWeek", "DayOfWeekClaimed", "MonthClaimed",
]

# 순서형(구간 → 순서 점수). 단조 신호를 보존하면서 차원을 절약한다.
#   값 → 정렬 점수(작을수록 "낮음/짧음/적음"). 미지 값은 중앙값으로 대체.
ORDINAL_MAPS: dict[str, dict[str, float]] = {
    "Days_Policy_Accident": {
        "none": 0.0, "1 to 7": 1.0, "8 to 15": 2.0, "15 to 30": 3.0,
        "more than 30": 4.0,
    },
    "Days_Policy_Claim": {
        "none": 0.0, "8 to 15": 1.0, "15 to 30": 2.0, "more than 30": 3.0,
    },
    "PastNumberOfClaims": {
        "none": 0.0, "1": 1.0, "2 to 4": 2.0, "more than 4": 3.0,
    },
    "NumberOfSuppliments": {
        "none": 0.0, "1 to 2": 1.0, "3 to 5": 2.0, "more than 5": 3.0,
    },
    "AddressChange_Claim": {
        # 최근 주소 변경일수록 점수 높음(사기 신호 — under 6 months 사기율 75%).
        "no change": 0.0, "4 to 8 years": 1.0, "2 to 3 years": 2.0,
        "1 year": 3.0, "under 6 months": 4.0,
    },
    "NumberOfCars": {
        "1 vehicle": 1.0, "2 vehicles": 2.0, "3 to 4": 3.0,
        "5 to 8": 4.0, "more than 8": 5.0,
    },
    "AgeOfVehicle": {
        "new": 0.0, "2 years": 2.0, "3 years": 3.0, "4 years": 4.0,
        "5 years": 5.0, "6 years": 6.0, "7 years": 7.0, "more than 7": 8.0,
    },
    "AgeOfPolicyHolder": {
        "16 to 17": 0.0, "18 to 20": 1.0, "21 to 25": 2.0, "26 to 30": 3.0,
        "31 to 35": 4.0, "36 to 40": 5.0, "41 to 50": 6.0, "51 to 65": 7.0,
        "over 65": 8.0,
    },
    "VehiclePrice": {
        "less than 20000": 0.0, "20000 to 29000": 1.0, "30000 to 39000": 2.0,
        "40000 to 59000": 3.0, "60000 to 69000": 4.0, "more than 69000": 5.0,
    },
}

# 직접 수치형(정수/실수 파싱). 미지/결측은 중앙값.
NUMERIC_COLS = ["Age", "Deductible", "DriverRating"]


# ==================================================================
# 데이터 적재
# ==================================================================
@dataclass
class RawData:
    """캐글 원시 행 + 라벨(인코딩 전)."""

    rows: list[dict[str, str]]
    labels: np.ndarray  # 0/1

    @property
    def n(self) -> int:
        return len(self.rows)

    @property
    def n_fraud(self) -> int:
        return int(self.labels.sum())


def load_kaggle(path: str | Path = KAGGLE_CSV) -> RawData:
    """캐글 ``fraud_oracle.csv`` 를 원시 dict 행 + 0/1 라벨로 로드한다.

    인코딩은 fold 내(train) 에서만 학습하므로 여기서는 원시 문자열을 그대로 둔다
    (누수 차단). ``utf-8-sig`` 로 읽어 BOM 을 제거한다.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"캐글 CSV 없음: {p}")
    with p.open(encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise ValueError(f"캐글 CSV 비어있음: {p}")
    labels = np.array([1 if str(r.get(LABEL_COL, "0")).strip() in ("1", "1.0")
                       else 0 for r in rows], dtype=int)
    return RawData(rows=rows, labels=labels)


# ==================================================================
# 인코더 — train 분할에서만 fit (카테고리 vocabulary/중앙값 학습 → 누수 차단)
# ==================================================================
def _to_float(val: str | None) -> float | None:
    if val is None:
        return None
    s = str(val).strip()
    if s == "" or s.lower() in ("na", "nan", "none"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


@dataclass
class AttributeEncoder:
    """캐글 청구 속성 → 수치 피처 벡터 인코더(누수 차단형).

    ``fit`` 은 **train 분할** 에서만 호출해 (1) 범주형 vocabulary, (2) 순서형/수치형
    중앙값을 학습한다. ``transform`` 은 학습된 vocabulary 만 사용하므로 test 분할의
    미지 카테고리는 모두 0(unknown)으로 떨어져 누수가 없다.
    """

    categorical_cols: list[str] = field(default_factory=lambda: list(CATEGORICAL_COLS))
    ordinal_cols: list[str] = field(default_factory=lambda: list(ORDINAL_MAPS.keys()))
    numeric_cols: list[str] = field(default_factory=lambda: list(NUMERIC_COLS))

    # fit 산출물
    cat_vocab: dict[str, list[str]] = field(default_factory=dict)
    ordinal_median: dict[str, float] = field(default_factory=dict)
    numeric_median: dict[str, float] = field(default_factory=dict)
    feature_names: list[str] = field(default_factory=list)

    def fit(self, rows: list[dict[str, str]]) -> "AttributeEncoder":
        """train 행에서 카테고리 vocabulary 와 순서형/수치형 중앙값을 학습한다."""
        # 범주형 vocabulary — 등장 카테고리만(빈도순 안정 정렬).
        for col in self.categorical_cols:
            seen: dict[str, int] = {}
            for r in rows:
                v = str(r.get(col, "")).strip()
                if v == "":
                    continue
                seen[v] = seen.get(v, 0) + 1
            self.cat_vocab[col] = sorted(seen.keys())

        # 순서형 중앙값(미지 대체용).
        for col in self.ordinal_cols:
            vals = [self._ordinal_value(col, str(r.get(col, "")).strip())
                    for r in rows]
            vals = [v for v in vals if v is not None]
            self.ordinal_median[col] = float(np.median(vals)) if vals else 0.0

        # 수치형 중앙값.
        for col in self.numeric_cols:
            vals = [self._to_float_or_none(col, str(r.get(col, "")).strip())
                    for r in rows]
            vals = [v for v in vals if v is not None]
            self.numeric_median[col] = float(np.median(vals)) if vals else 0.0

        # 피처 이름 순서 고정.
        names: list[str] = []
        for col in self.categorical_cols:
            for cat in self.cat_vocab[col]:
                names.append(f"{col}={cat}")
        names.extend(f"ord:{c}" for c in self.ordinal_cols)
        names.extend(f"num:{c}" for c in self.numeric_cols)
        self.feature_names = names
        return self

    @staticmethod
    def _ordinal_value(col: str, raw: str) -> float | None:
        m = ORDINAL_MAPS.get(col, {})
        return m.get(raw)

    def _to_float_or_none(self, col: str, raw: str) -> float | None:
        v = _to_float(raw)
        # Age=0 은 캐글에서 결측 sentinel(미상) → None 으로 처리(중앙값 대체).
        if col == "Age" and v is not None and v <= 0:
            return None
        return v

    def transform(self, rows: list[dict[str, str]]) -> np.ndarray:
        """학습된 인코더로 행들을 수치 피처 행렬로 변환한다(미지 카테고리=0)."""
        if not self.feature_names:
            raise RuntimeError("transform 전에 fit 필요")
        n = len(rows)
        X = np.zeros((n, len(self.feature_names)), dtype=float)

        # 컬럼 인덱스 사전 — 빠른 채우기.
        col_index: dict[str, int] = {name: i for i, name in enumerate(self.feature_names)}

        for i, r in enumerate(rows):
            # 범주형 one-hot.
            for col in self.categorical_cols:
                v = str(r.get(col, "")).strip()
                if v == "":
                    continue
                key = f"{col}={v}"
                j = col_index.get(key)
                if j is not None:  # 미지 카테고리는 무시(0) — 누수 차단
                    X[i, j] = 1.0
            # 순서형.
            for col in self.ordinal_cols:
                j = col_index[f"ord:{col}"]
                v = self._ordinal_value(col, str(r.get(col, "")).strip())
                X[i, j] = v if v is not None else self.ordinal_median[col]
            # 수치형.
            for col in self.numeric_cols:
                j = col_index[f"num:{col}"]
                v = self._to_float_or_none(col, str(r.get(col, "")).strip())
                X[i, j] = v if v is not None else self.numeric_median[col]
        return X


# ==================================================================
# 분류기
# ==================================================================
def make_classifier(kind: str) -> Any:
    """모델별 scikit-learn 분류기(불균형 처리 포함). lr 은 스케일러 파이프라인."""
    if kind == "lr":
        return Pipeline([
            ("scaler", StandardScaler()),
            ("clf", LogisticRegression(
                class_weight="balanced", solver="liblinear", C=1.0,
                max_iter=2000, random_state=RANDOM_SEED,
            )),
        ])
    if kind == "rf":
        return RandomForestClassifier(
            n_estimators=400, max_depth=None, class_weight="balanced_subsample",
            min_samples_leaf=2, random_state=RANDOM_SEED, n_jobs=-1,
        )
    if kind == "gb":
        return GradientBoostingClassifier(random_state=RANDOM_SEED)
    raise ValueError(f"알 수 없는 모델 종류: {kind}")


def _fit_with_balance(clf: Any, kind: str, X: np.ndarray, y: np.ndarray) -> Any:
    """불균형 고려 학습(gb 는 sample_weight)."""
    if kind == "gb":
        n_pos = max(1, int(y.sum()))
        n_neg = max(1, int(len(y) - y.sum()))
        sw = np.where(y == 1, n_neg / n_pos, 1.0)
        clf.fit(X, y, sample_weight=sw)
    else:
        clf.fit(X, y)
    return clf


# ==================================================================
# 지표 — 불균형(6%)이라 PR-AUC/recall/F1 중시
# ==================================================================
@dataclass
class AttrMetrics:
    """속성 ML 분류 성능(out-of-fold/hold-out). 불균형이라 PR-AUC 중시."""

    auc: float          # ROC-AUC
    pr_auc: float       # PR-AUC(average precision) — 불균형에서 핵심
    f1: float
    recall: float
    precision: float
    threshold: float
    tp: int
    fp: int
    fn: int
    tn: int


def best_f1_threshold(y_true: np.ndarray, proba: np.ndarray) -> float:
    """F1 을 최대화하는 임계치(불균형에서 0.5 는 부적절 → 운영점 탐색)."""
    cand = np.unique(np.concatenate([np.linspace(0.0, 1.0, 201), np.unique(proba)]))
    best_t, best_f1 = 0.5, -1.0
    for t in cand:
        pred = proba >= t
        tp = int(np.sum(pred & (y_true == 1)))
        fp = int(np.sum(pred & (y_true == 0)))
        fn = int(np.sum(~pred & (y_true == 1)))
        prec = tp / (tp + fp) if (tp + fp) else 0.0
        rec = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = (2 * prec * rec / (prec + rec)) if (prec + rec) else 0.0
        if f1 > best_f1:
            best_f1, best_t = f1, float(t)
    return best_t


def evaluate_proba(
    y_true: np.ndarray, proba: np.ndarray, *, threshold: float | None = None
) -> AttrMetrics:
    """확률 예측에서 AUC/PR-AUC/F1/recall/precision 산출(임계 미지정 시 F1-최적)."""
    auc = float(roc_auc_score(y_true, proba)) if len(set(y_true.tolist())) == 2 else 0.0
    pr_auc = float(average_precision_score(y_true, proba)) \
        if len(set(y_true.tolist())) == 2 else 0.0
    t = best_f1_threshold(y_true, proba) if threshold is None else threshold
    pred = proba >= t
    tp = int(np.sum(pred & (y_true == 1)))
    fp = int(np.sum(pred & (y_true == 0)))
    fn = int(np.sum(~pred & (y_true == 1)))
    tn = int(np.sum(~pred & (y_true == 0)))
    rec = tp / (tp + fn) if (tp + fn) else 0.0
    prec = tp / (tp + fp) if (tp + fp) else 0.0
    f1 = (2 * prec * rec / (prec + rec)) if (prec + rec) else 0.0
    return AttrMetrics(auc=auc, pr_auc=pr_auc, f1=f1, recall=rec, precision=prec,
                       threshold=t, tp=tp, fp=fp, fn=fn, tn=tn)


# ==================================================================
# 교차검증 — out-of-fold (인코더 fit 도 fold 내 train 에서만 → 누수 차단)
# ==================================================================
@dataclass
class AttrCVResult:
    """캐글 속성 ML 교차검증 결과 — out-of-fold 예측 + fold 지표 + 중요도."""

    model_kind: str
    y_true: np.ndarray
    oof_proba: np.ndarray
    fold_auc: list[float] = field(default_factory=list)
    fold_pr_auc: list[float] = field(default_factory=list)
    feature_names: list[str] = field(default_factory=list)
    coef_importance: dict[str, float] = field(default_factory=dict)   # LR 계수(부호)
    perm_importance: dict[str, float] = field(default_factory=dict)   # permutation


def cross_validate_kaggle(
    *,
    model_kind: str = "gb",
    n_folds: int = DEFAULT_FOLDS,
    data: RawData | None = None,
    compute_perm: bool = True,
) -> AttrCVResult:
    """캐글 hold-out 누수 없는 Stratified K-fold out-of-fold 평가.

    **인코더(카테고리 vocabulary/중앙값)를 각 fold 의 train 분할에서만 fit** 하여
    test 분할로 transform 한다(카테고리 누수 차단). 각 fold 의 test 예측만 모아
    (out-of-fold) 성능을 산출하므로 in-sample 부풀리기가 없다.

    Args:
        model_kind: 'lr'/'rf'/'gb'.
        n_folds: Stratified K-fold 수(소수 클래스 비율 유지).
        data: 미리 로드한 RawData(재사용). None 이면 새로 로드.
        compute_perm: permutation importance 계산 여부(느릴 수 있음).

    Returns:
        ``AttrCVResult`` — out-of-fold 확률 + fold AUC/PR-AUC + 중요도.
    """
    if not _SKLEARN:
        raise RuntimeError("scikit-learn 미설치 — `.venv/bin/pip install scikit-learn`")
    if data is None:
        data = load_kaggle()

    rows = data.rows
    y = data.labels
    n = len(rows)
    oof = np.zeros(n, dtype=float)
    fold_auc: list[float] = []
    fold_pr_auc: list[float] = []

    coef_acc: dict[str, float] = {}
    perm_acc: dict[str, float] = {}
    coef_folds = 0
    perm_folds = 0

    skf = StratifiedKFold(n_splits=n_folds, shuffle=True, random_state=RANDOM_SEED)
    idx = np.arange(n)
    for tr, te in skf.split(idx, y):
        enc = AttributeEncoder().fit([rows[i] for i in tr])
        Xtr = enc.transform([rows[i] for i in tr])
        Xte = enc.transform([rows[i] for i in te])
        clf = make_classifier(model_kind)
        _fit_with_balance(clf, model_kind, Xtr, y[tr])
        proba = clf.predict_proba(Xte)[:, 1]
        oof[te] = proba

        if len(set(y[te].tolist())) == 2:
            fold_auc.append(float(roc_auc_score(y[te], proba)))
            fold_pr_auc.append(float(average_precision_score(y[te], proba)))

        # LR 계수(부호 포함 — 설명가능성). fold 간 합산 후 평균.
        if model_kind == "lr":
            est = clf.named_steps["clf"]
            for name, c in zip(enc.feature_names, est.coef_.ravel()):
                coef_acc[name] = coef_acc.get(name, 0.0) + float(c)
            coef_folds += 1
        else:
            # tree 계열 feature_importances_(부호 없음 — 크기).
            imp = getattr(clf, "feature_importances_", None)
            if imp is not None:
                for name, c in zip(enc.feature_names, imp):
                    coef_acc[name] = coef_acc.get(name, 0.0) + float(c)
                coef_folds += 1

        # permutation importance(모델 무관, out-of-fold test 분할 기준).
        if compute_perm:
            try:
                pi = permutation_importance(
                    clf, Xte, y[te], scoring="average_precision",
                    n_repeats=3, random_state=RANDOM_SEED, n_jobs=-1,
                )
                for name, m in zip(enc.feature_names, pi.importances_mean):
                    perm_acc[name] = perm_acc.get(name, 0.0) + float(m)
                perm_folds += 1
            except Exception:
                pass

    coef_importance = {k: v / coef_folds for k, v in coef_acc.items()} \
        if coef_folds else {}
    perm_importance = {k: v / perm_folds for k, v in perm_acc.items()} \
        if perm_folds else {}

    # feature_names — 전체 데이터로 fit 한 인코더 기준(리포트 표시용).
    full_enc = AttributeEncoder().fit(rows)

    return AttrCVResult(
        model_kind=model_kind,
        y_true=y,
        oof_proba=oof,
        fold_auc=fold_auc,
        fold_pr_auc=fold_pr_auc,
        feature_names=full_enc.feature_names,
        coef_importance=coef_importance,
        perm_importance=perm_importance,
    )


def holdout_kaggle(
    *,
    model_kind: str = "gb",
    test_size: float = 0.25,
    data: RawData | None = None,
) -> AttrMetrics:
    """단일 stratified hold-out 평가(누수 없음 — 인코더는 train 에서만 fit)."""
    if not _SKLEARN:
        raise RuntimeError("scikit-learn 미설치")
    if data is None:
        data = load_kaggle()
    idx = np.arange(data.n)
    tr, te = train_test_split(
        idx, test_size=test_size, stratify=data.labels, random_state=RANDOM_SEED
    )
    enc = AttributeEncoder().fit([data.rows[i] for i in tr])
    Xtr = enc.transform([data.rows[i] for i in tr])
    Xte = enc.transform([data.rows[i] for i in te])
    clf = make_classifier(model_kind)
    _fit_with_balance(clf, model_kind, Xtr, data.labels[tr])
    proba = clf.predict_proba(Xte)[:, 1]
    return evaluate_proba(data.labels[te], proba)


# ==================================================================
# 학습된 모델 — 듀얼 레이어에서 합성 청구에 적용하기 위한 추론 래퍼
# ==================================================================
@dataclass
class AttributeModel:
    """전체 캐글로 학습한 속성 ML 추론기(듀얼 레이어 ②에서 사용).

    ``score_rows`` 는 캐글 호환 속성 dict 리스트(합성 청구 포함)를 받아 사기확률을
    반환한다. 미지/누락 속성은 인코더가 0/중앙값으로 처리하므로, 합성 청구에 캐글
    컬럼이 부분만 있어도 동작한다(graceful).
    """

    model_kind: str
    encoder: AttributeEncoder
    clf: Any

    def score_rows(self, rows: list[dict[str, str]]) -> np.ndarray:
        """캐글 호환 속성 행들의 개인 사기확률(0~1)을 반환한다."""
        if not rows:
            return np.zeros(0, dtype=float)
        X = self.encoder.transform(rows)
        return self.clf.predict_proba(X)[:, 1]

    def explain_row(self, row: dict[str, str], *, top_k: int = 5) -> list[dict[str, Any]]:
        """단일 청구의 상위 사기 기여 사유를 반환한다(설명가능성).

        LR 이면 (표준화 피처값 × 계수)로 양/음 기여를 산출한다. tree 계열이면
        feature_importances_ × (피처 활성 여부)로 근사 기여를 낸다. 둘 다 "왜
        의심인지"의 근거 피처를 제시한다.
        """
        X = self.encoder.transform([row])[0]
        contribs: list[tuple[str, float]] = []
        if self.model_kind == "lr":
            scaler = self.clf.named_steps["scaler"]
            est = self.clf.named_steps["clf"]
            Xs = scaler.transform(X.reshape(1, -1))[0]
            for name, xs, c in zip(self.encoder.feature_names, Xs, est.coef_.ravel()):
                contribs.append((name, float(xs * c)))
        else:
            imp = getattr(self.clf, "feature_importances_", None)
            if imp is not None:
                for name, xv, c in zip(self.encoder.feature_names, X, imp):
                    # 활성(비0) 피처에 중요도 비례 기여 부여(방향 미상 → 크기).
                    if xv != 0.0:
                        contribs.append((name, float(c)))
        contribs.sort(key=lambda kv: abs(kv[1]), reverse=True)
        out = []
        for name, val in contribs[:top_k]:
            out.append({"feature": name, "contribution": round(val, 4),
                        "direction": "사기↑" if val > 0 else "사기↓"})
        return out


def train_attribute_model(
    *, model_kind: str = "gb", data: RawData | None = None
) -> AttributeModel:
    """전체 캐글 데이터로 속성 ML 을 학습해 추론기를 반환한다(듀얼 레이어용).

    주의: 이 모델은 **캐글 전체**로 학습되므로 캐글 자기평가에 쓰면 누수다. 성능
    보고는 반드시 ``cross_validate_kaggle`` / ``holdout_kaggle`` (out-of-fold)을
    쓰고, 이 함수는 **다른 도메인(합성 청구)** 추론에만 사용한다.
    """
    if not _SKLEARN:
        raise RuntimeError("scikit-learn 미설치")
    if data is None:
        data = load_kaggle()
    enc = AttributeEncoder().fit(data.rows)
    X = enc.transform(data.rows)
    clf = make_classifier(model_kind)
    _fit_with_balance(clf, model_kind, X, data.labels)
    return AttributeModel(model_kind=model_kind, encoder=enc, clf=clf)


# ==================================================================
# 리포트
# ==================================================================
def _print_report(cv: AttrCVResult, *, holdout: AttrMetrics | None = None) -> None:
    line = "=" * 78
    print(line)
    print(" THOTH-ON 속성 기반 ML 레이어 (개인 사기) — 캐글 fraud_oracle 실데이터")
    print(line)
    n = len(cv.y_true)
    nf = int(cv.y_true.sum())
    print(f"  데이터          : 캐글 fraud_oracle.csv  {n:,}건 (개인 사기 라벨)")
    print(f"  사기(양성)      : {nf}건 ({nf / n * 100:.2f}%)  — 클래스 불균형(소수)")
    print(f"  모델            : {cv.model_kind}  (불균형: class_weight/sample_weight)")
    print(f"  검증            : Stratified {len(cv.fold_auc)}-fold out-of-fold "
          f"(인코더도 fold 내 train 에서만 fit — 누수 차단)")
    print("-" * 78)

    # out-of-fold 종합 성능(불균형이라 PR-AUC/recall 중시).
    oof = evaluate_proba(cv.y_true, cv.oof_proba)
    base_rate = nf / n
    print(" out-of-fold 종합 성능 (개인 사기 — 이것이 실데이터 검증 숫자)")
    print("-" * 78)
    print(f"  ROC-AUC         : {oof.auc:.3f}")
    print(f"  PR-AUC(AP)      : {oof.pr_auc:.3f}   (무작위 기준선 = 사기율 {base_rate:.3f}; "
          f"lift {oof.pr_auc / base_rate:.1f}x)")
    print(f"  F1-최적 임계    : {oof.threshold:.3f}")
    print(f"  recall          : {oof.recall:.3f}  (사기 {oof.tp}/{nf} 적발)")
    print(f"  precision       : {oof.precision:.3f}  (알림 {oof.tp + oof.fp}건 중 {oof.tp} 적중)")
    print(f"  F1              : {oof.f1:.3f}")
    print(f"  혼동행렬        : TP={oof.tp} FP={oof.fp} FN={oof.fn} TN={oof.tn}")
    if cv.fold_auc:
        print(f"  fold ROC-AUC    : 평균 {np.mean(cv.fold_auc):.3f} ± {np.std(cv.fold_auc):.3f}")
    if cv.fold_pr_auc:
        print(f"  fold PR-AUC     : 평균 {np.mean(cv.fold_pr_auc):.3f} ± {np.std(cv.fold_pr_auc):.3f}")
    print("-" * 78)

    # 다양한 임계의 recall/precision 트레이드오프(운영 선택 참고).
    print(" recall@임계 트레이드오프 (불균형 — 운영점 선택 참고)")
    print("-" * 78)
    print(f"  {'임계':>8}{'recall':>10}{'precision':>12}{'F1':>10}{'알림수':>10}")
    for t in (0.3, 0.4, 0.5, 0.6, 0.7):
        m = evaluate_proba(cv.y_true, cv.oof_proba, threshold=t)
        print(f"  {t:>8.2f}{m.recall:>10.3f}{m.precision:>12.3f}{m.f1:>10.3f}"
              f"{m.tp + m.fp:>10}")
    print("-" * 78)

    if holdout is not None:
        print(" 단일 hold-out 교차검증 (25% test, 누수 없음) — CV 일관성 확인")
        print("-" * 78)
        print(f"  ROC-AUC {holdout.auc:.3f}  PR-AUC {holdout.pr_auc:.3f}  "
              f"F1 {holdout.f1:.3f}  recall {holdout.recall:.3f}  "
              f"precision {holdout.precision:.3f}")
        print("-" * 78)

    # 피처 중요도(설명가능성).
    print(" 피처 중요도 — permutation(out-of-fold, PR-AUC 감소 기준) 상위")
    print("-" * 78)
    perm = sorted(cv.perm_importance.items(), key=lambda kv: kv[1], reverse=True)
    if perm:
        mx = max(abs(v) for _, v in perm) or 1.0
        for i, (name, v) in enumerate(perm[:15], 1):
            bar = "#" * int(abs(v) / mx * 40)
            print(f"  {i:>2}. {name:<32}{v:>9.4f}  {bar}")
    else:
        print("  (permutation importance 미산출)")
    print("-" * 78)

    print(" 피처 방향성 — " + ("LR 계수(부호: 사기↑/↓)" if cv.model_kind == "lr"
                              else "tree 중요도(크기)") + " 상위")
    print("-" * 78)
    coef = sorted(cv.coef_importance.items(), key=lambda kv: abs(kv[1]), reverse=True)
    for i, (name, v) in enumerate(coef[:15], 1):
        if cv.model_kind == "lr":
            arrow = "사기↑" if v > 0 else "사기↓"
            print(f"  {i:>2}. {name:<34}{v:>+9.3f}  {arrow}")
        else:
            print(f"  {i:>2}. {name:<34}{v:>9.4f}")
    print(line)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="THOTH-ON 속성 기반 ML 레이어 (개인 사기) — 캐글 실데이터")
    p.add_argument("--model", default="gb", choices=["lr", "rf", "gb"],
                   help="분류 모델(lr/rf/gb)")
    p.add_argument("--folds", type=int, default=DEFAULT_FOLDS, help="교차검증 fold 수")
    p.add_argument("--holdout", action="store_true",
                   help="단일 hold-out 평가도 함께 출력(CV 일관성 확인)")
    p.add_argument("--no-perm", action="store_true",
                   help="permutation importance 생략(빠른 실행)")
    args = p.parse_args(argv)

    if not _SKLEARN:
        print("scikit-learn 미설치 — `.venv/bin/pip install scikit-learn` 후 재실행")
        return 1

    data = load_kaggle()
    cv = cross_validate_kaggle(
        model_kind=args.model, n_folds=args.folds, data=data,
        compute_perm=not args.no_perm,
    )
    ho = holdout_kaggle(model_kind=args.model, data=data) if args.holdout else None
    _print_report(cv, holdout=ho)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
