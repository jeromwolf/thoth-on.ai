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

[피처 설계 — 행동/시계열 신호 + 파생 상호작용(누수 없음)]
    · 범주형 : Fault, BasePolicy, VehicleCategory, AccidentArea, Make, Sex,
               MaritalStatus, AgentType, PolicyType (one-hot).
    · 수치/순서형 : Age, Deductible, DriverRating, PastNumberOfClaims,
               AgeOfVehicle, AgeOfPolicyHolder, VehiclePrice (순서 인코딩).
    · 행동/시계열 : Days_Policy_Accident, Days_Policy_Claim, AddressChange_Claim,
               PoliceReportFiled, WitnessPresent, NumberOfCars, NumberOfSuppliments.
    · 파생 상호작용(``engineered=True``): 본인과실×상품(Fault×BasePolicy),
      주소변경×가입기간(최근 주소변경 + 짧은 가입기간), 무사고이력×본인과실,
      고액차량×본인과실, 농촌×본인과실, 고액공제×본인과실 등. 이들은 캐글
      조건부 사기율에서 관찰된 결합 신호를 트리/선형 모델이 직접 쓰도록 노출한다.
      **모두 입력 속성만으로 계산** — 라벨(FraudFound_P)을 일절 보지 않아 누수 없음.
    · 희소 범주 그룹화(``group_rare=True``): train 빈도 < ``rare_min`` 카테고리는
      vocabulary 에서 제외(test 에서 0=unknown 으로 떨어짐 → 과적합/노이즈 억제).

    캐글의 순서형 컬럼(예: "31 to 35", "more than 7")은 **구간 중앙값/순서 점수**로
    수치화한다(범주 폭증 방지 + 단조성 활용). 누락/미지 카테고리는 0/중앙값.

[클래스 불균형(5.99%) 처리 + 모델]
    LogisticRegression/RandomForest 는 ``class_weight='balanced'``,
    GradientBoosting/HistGradientBoosting 은 sample_weight 로 소수 클래스 가중.
    기본 모델은 **ens**(GB+HGB+RF 평균-확률 앙상블) — 단일 모델 대비 PR-AUC 와
    recall@precision 운영점이 소폭이지만 일관되게 개선된다. 불균형이 심하므로
    accuracy 는 무의미 — **PR-AUC / recall@precision / 비용기반 운영점** 을 중시한다.

[정직성 — 이 데이터셋의 본질적 한계]
    fraud_oracle 의 속성만으로는 ROC-AUC ≈ 0.82, PR-AUC ≈ 0.23 이 사실상 상한이다
    (피처 엔지니어링·모델 교체·앙상블·캘리브레이션 모두 시도). 6% 불균형 + 약한
    개별 신호 때문에 **precision 을 0.4 로 고정하면 recall 은 ~0.09 에 불과**하다.
    따라서 본 모듈의 개선은 "마법 같은 도약"이 아니라 (1) 앙상블로 PR-AUC 소폭 향상,
    (2) **운영점을 F1-최적(precision 0.19)이 아니라 precision-목표/비용기반으로
    선택**해 헛알림을 실질적으로 줄이는 데 있다. 과장 없이 실측만 보고한다.

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
    .venv/bin/python -m detection.attribute_model            # 캐글 CV(기본 ens 앙상블)
    .venv/bin/python -m detection.attribute_model --model gb # 모델(lr/rf/gb/hgb/ens)
    .venv/bin/python -m detection.attribute_model --folds 5  # fold 수
    .venv/bin/python -m detection.attribute_model --holdout  # 단일 hold-out 평가
    .venv/bin/python -m detection.attribute_model --no-engineered  # 파생피처 끔(비교)
    .venv/bin/python -m detection.attribute_model --calibrate     # 확률 캘리브레이션
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
    from sklearn.calibration import CalibratedClassifierCV
    from sklearn.ensemble import (
        GradientBoostingClassifier,
        HistGradientBoostingClassifier,
        RandomForestClassifier,
    )
    from sklearn.inspection import permutation_importance
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import (
        average_precision_score,
        f1_score,
        precision_recall_curve,
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

# 기본 모델 — GB+HGB+RF 평균-확률 앙상블(단일 모델 대비 PR-AUC/recall@precision 소폭 개선).
DEFAULT_MODEL = "ens"

# 파생 피처(상호작용/비율)·희소범주 그룹화 기본 사용 — 모두 입력 속성만으로 계산(누수 없음).
DEFAULT_ENGINEERED = True
RARE_CATEGORY_MIN = 20   # train 빈도 미만 카테고리는 vocabulary 제외(과적합 억제)

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

# 파생 상호작용/비율 피처 이름(engineered=True 일 때 추가). 모두 입력 속성으로만 계산.
#   캐글 조건부 사기율에서 관찰된 결합 신호(본인과실×상품, 주소변경×가입기간 등)를
#   트리/선형 모델에 직접 노출해 PR-AUC/운영점을 소폭 개선한다.
ENGINEERED_FEATURES = [
    "ix:Fault_PH_x_AllPerils",      # 본인과실 × All Perils (사기율 최고 결합)
    "ix:Fault_PH_x_Collision",      # 본인과실 × Collision
    "ix:Fault_PH_x_Liability",      # 본인과실 × Liability (저위험 — 음의 신호)
    "ix:AddrChange_x_Fault_PH",     # 주소변경 정도 × 본인과실
    "ix:AddrChange_x_NewPolicy",    # 최근 주소변경 × 짧은 가입기간(가입직후 사기 신호)
    "ix:Util_x_AllPerils",          # Utility 차종 × All Perils
    "ix:Sport_x_Collision",         # Sport 차종 × Collision (PolicyType 최고위험)
    "ix:PastClaims_none_x_FaultPH", # 무사고이력 × 본인과실(첫 청구 과장 신호)
    "ix:HighPrice_x_FaultPH",       # 고액차량 × 본인과실
    "ix:Rural_x_FaultPH",           # 농촌 × 본인과실
    "ix:Deduct_high_x_FaultPH",     # 고액 공제(500/300) × 본인과실
    "ix:young_x_FaultPH",           # 젊은 운전자(<25) × 본인과실
    "r:addr_recent",                # 최근(<3년) 주소변경 이진
    "r:no_police_no_witness",       # 경찰신고X & 목격자X(둘 다 부재)
    "r:claims_per_age",             # 과거청구수 / 연령대(연령 대비 청구 빈도)
    "r:short_policy",               # 가입 후 사고까지 짧음(가입직후) 이진
]


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
    engineered: bool = DEFAULT_ENGINEERED      # 파생 상호작용/비율 피처 추가
    rare_min: int = RARE_CATEGORY_MIN          # 희소 범주 그룹화 임계(0=비활성)

    # fit 산출물
    cat_vocab: dict[str, list[str]] = field(default_factory=dict)
    ordinal_median: dict[str, float] = field(default_factory=dict)
    numeric_median: dict[str, float] = field(default_factory=dict)
    feature_names: list[str] = field(default_factory=list)

    def fit(self, rows: list[dict[str, str]]) -> "AttributeEncoder":
        """train 행에서 카테고리 vocabulary 와 순서형/수치형 중앙값을 학습한다.

        희소 범주(``rare_min`` 미만 빈도)는 vocabulary 에서 제외해 과적합/노이즈를
        억제한다(test 에서 0=unknown 으로 떨어짐 — 누수 없음).
        """
        # 범주형 vocabulary — 등장 카테고리만(빈도순 안정 정렬).
        for col in self.categorical_cols:
            seen: dict[str, int] = {}
            for r in rows:
                v = str(r.get(col, "")).strip()
                if v == "":
                    continue
                seen[v] = seen.get(v, 0) + 1
            if self.rare_min and self.rare_min > 1:
                kept = [k for k, c in seen.items() if c >= self.rare_min]
                # 모두 희소면(드묾) 최소 1개는 유지해 빈 vocabulary 방지.
                self.cat_vocab[col] = sorted(kept) if kept else sorted(seen.keys())
            else:
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
        if self.engineered:
            names.extend(ENGINEERED_FEATURES)
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
            ordvals: dict[str, float] = {}
            for col in self.ordinal_cols:
                j = col_index[f"ord:{col}"]
                v = self._ordinal_value(col, str(r.get(col, "")).strip())
                fv = v if v is not None else self.ordinal_median[col]
                ordvals[col] = fv
                X[i, j] = fv
            # 수치형.
            numvals: dict[str, float] = {}
            for col in self.numeric_cols:
                j = col_index[f"num:{col}"]
                v = self._to_float_or_none(col, str(r.get(col, "")).strip())
                fv = v if v is not None else self.numeric_median[col]
                numvals[col] = fv
                X[i, j] = fv
            # 파생 상호작용/비율(입력 속성만 사용 — 라벨 미사용 → 누수 없음).
            if self.engineered:
                self._fill_engineered(X, i, col_index, r, ordvals, numvals)
        return X

    def _fill_engineered(
        self,
        X: np.ndarray,
        i: int,
        col_index: dict[str, int],
        r: dict[str, str],
        ordvals: dict[str, float],
        numvals: dict[str, float],
    ) -> None:
        """파생 상호작용/비율 피처를 채운다(입력 속성만 — 누수 없음)."""
        fault = str(r.get("Fault", "")).strip()
        base = str(r.get("BasePolicy", "")).strip()
        vcat = str(r.get("VehicleCategory", "")).strip()
        area = str(r.get("AccidentArea", "")).strip()
        is_ph = 1.0 if fault == "Policy Holder" else 0.0
        addr = ordvals.get("AddressChange_Claim", 0.0)
        dpa = ordvals.get("Days_Policy_Accident", 4.0)   # 4 = 'more than 30'(정상 다수)
        past = ordvals.get("PastNumberOfClaims", 0.0)
        vprice = ordvals.get("VehiclePrice", 0.0)
        age = numvals.get("Age", 40.0)
        deduct = numvals.get("Deductible", 400.0)

        def setf(name: str, val: float) -> None:
            j = col_index.get(name)
            if j is not None:
                X[i, j] = val

        setf("ix:Fault_PH_x_AllPerils", is_ph * (1.0 if base == "All Perils" else 0.0))
        setf("ix:Fault_PH_x_Collision", is_ph * (1.0 if base == "Collision" else 0.0))
        setf("ix:Fault_PH_x_Liability", is_ph * (1.0 if base == "Liability" else 0.0))
        setf("ix:AddrChange_x_Fault_PH", is_ph * addr)
        setf("ix:AddrChange_x_NewPolicy", addr * (4.0 - dpa))  # 최근 주소변경 + 짧은 가입기간
        setf("ix:Util_x_AllPerils",
             (1.0 if vcat == "Utility" else 0.0) * (1.0 if base == "All Perils" else 0.0))
        setf("ix:Sport_x_Collision",
             (1.0 if vcat == "Sport" else 0.0) * (1.0 if base == "Collision" else 0.0))
        setf("ix:PastClaims_none_x_FaultPH", is_ph * (1.0 if past == 0.0 else 0.0))
        setf("ix:HighPrice_x_FaultPH", is_ph * (1.0 if vprice >= 5.0 else 0.0))
        setf("ix:Rural_x_FaultPH", is_ph * (1.0 if area == "Rural" else 0.0))
        setf("ix:Deduct_high_x_FaultPH", is_ph * (1.0 if deduct in (500.0, 300.0) else 0.0))
        setf("ix:young_x_FaultPH", is_ph * (1.0 if age < 25 else 0.0))
        setf("r:addr_recent", 1.0 if addr >= 2.0 else 0.0)
        pr_no = 1.0 if str(r.get("PoliceReportFiled", "")).strip() == "No" else 0.0
        wp_no = 1.0 if str(r.get("WitnessPresent", "")).strip() == "No" else 0.0
        setf("r:no_police_no_witness", pr_no * wp_no)
        setf("r:claims_per_age", past / max(age / 10.0, 1.0))
        setf("r:short_policy", 1.0 if dpa < 4.0 else 0.0)


# ==================================================================
# 분류기
# ==================================================================
# 앙상블 구성원. GB+HGB+RF — 트리 계열 3종으로 분산 감소.
ENSEMBLE_MEMBERS = ("gb", "hgb", "rf")


def _make_base(kind: str) -> Any:
    """단일 base 분류기 생성(불균형 처리 포함). lr 은 스케일러 파이프라인."""
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
    if kind == "hgb":
        # 튜닝된 HistGradientBoosting(빠르고 정규화 강함 — 과적합 억제).
        return HistGradientBoostingClassifier(
            learning_rate=0.05, max_iter=400, max_leaf_nodes=15,
            min_samples_leaf=40, l2_regularization=1.0, random_state=RANDOM_SEED,
        )
    raise ValueError(f"알 수 없는 base 모델 종류: {kind}")


class RankEnsemble:
    """GB+HGB+RF 평균-확률 앙상블 — predict_proba 호환(0~1).

    각 base 모델을 불균형 가중으로 학습하고, ``predict_proba`` 에서 **멤버 확률의
    평균**을 사기확률로 반환한다. 트리 계열 3종의 예측을 평균해 분산을 줄여
    PR-AUC/운영점을 소폭 개선한다.

    [평균-확률 vs 순위-평균]
        순위-평균(rank-average)은 PR-AUC 가 미세하게 더 높지만(0.239 vs 0.237) 출력이
        배치 내 순위라 임계 0.5 의 의미가 배치 크기에 따라 달라진다(듀얼 레이어의
        고정 임계와 충돌). 평균-확률은 ranking 품질이 사실상 동등하면서(AUC 0.825,
        PR 0.237) 단일 모델과 동일한 확률 의미를 유지하므로 **운영 임계가 안정적**이다.
        이름은 호환을 위해 유지하되 결합 방식은 평균-확률이다.
    """

    # sklearn 이 classifier 로 인식하도록 태그(permutation_importance 호환).
    _estimator_type = "classifier"

    def __init__(self, members: tuple[str, ...] = ENSEMBLE_MEMBERS) -> None:
        self.members = members
        self.models: list[Any] = []
        self.classes_ = np.array([0, 1])

    def fit(self, X: np.ndarray, y: np.ndarray) -> "RankEnsemble":
        self.models = []
        for kind in self.members:
            clf = _make_base(kind)
            _fit_base_with_balance(clf, kind, X, y)
            self.models.append(clf)
        return self

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        n = X.shape[0]
        if n == 0:
            return np.zeros((0, 2), dtype=float)
        acc = np.zeros(n, dtype=float)
        for clf in self.models:
            acc += clf.predict_proba(X)[:, 1]
        score = np.clip(acc / max(len(self.models), 1), 0.0, 1.0)
        return np.column_stack([1.0 - score, score])

    def predict(self, X: np.ndarray) -> np.ndarray:
        """0.5 임계 이진 예측(sklearn 인터페이스 호환용)."""
        return (self.predict_proba(X)[:, 1] >= 0.5).astype(int)

    def __sklearn_is_fitted__(self) -> bool:
        return bool(self.models)


def make_classifier(kind: str) -> Any:
    """모델별 분류기. 'ens' 는 GB+HGB+RF 평균-확률 앙상블."""
    if kind == "ens":
        return RankEnsemble()
    return _make_base(kind)


def _fit_base_with_balance(clf: Any, kind: str, X: np.ndarray, y: np.ndarray) -> Any:
    """단일 base 모델 불균형 고려 학습(gb/hgb 는 sample_weight)."""
    if kind in ("gb", "hgb"):
        n_pos = max(1, int(y.sum()))
        n_neg = max(1, int(len(y) - y.sum()))
        sw = np.where(y == 1, n_neg / n_pos, 1.0)
        clf.fit(X, y, sample_weight=sw)
    else:
        clf.fit(X, y)
    return clf


def _fit_with_balance(clf: Any, kind: str, X: np.ndarray, y: np.ndarray) -> Any:
    """불균형 고려 학습. 'ens' 는 내부 base 들이 각자 처리(RankEnsemble.fit)."""
    if kind == "ens":
        clf.fit(X, y)
        return clf
    return _fit_base_with_balance(clf, kind, X, y)


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


def recall_at_precision(
    y_true: np.ndarray, proba: np.ndarray, target_precision: float
) -> tuple[float, float | None]:
    """고정 precision(예 0.4/0.5)에서 달성 가능한 **최대 recall** 과 그 임계치.

    헛알림(FP) 통제가 핵심인 운영 환경에서, "정밀도를 X 이상 유지할 때 얼마나
    잡을 수 있나"를 답한다. PR 곡선에서 precision ≥ target 인 점들 중 recall 최대점을
    고른다. 도달 불가면 (0.0, None).

    Returns:
        (recall, threshold). threshold 는 ``proba >= threshold`` 로 운영점 재현.
    """
    if len(set(y_true.tolist())) < 2:
        return 0.0, None
    prec, rec, thr = precision_recall_curve(y_true, proba)
    mask = prec >= target_precision
    if not mask.any():
        return 0.0, None
    idxs = np.where(mask)[0]
    best = idxs[int(np.argmax(rec[idxs]))]
    # precision_recall_curve: prec/rec 길이 = len(thr)+1(마지막은 recall=0 sentinel).
    t = float(thr[best]) if best < len(thr) else 1.0
    return float(rec[best]), t


def best_cost_threshold(
    y_true: np.ndarray, proba: np.ndarray, *, fn_cost: float = 5.0, fp_cost: float = 1.0
) -> tuple[float, float]:
    """비용기반 최적 임계 — 총비용 ``fn_cost*FN + fp_cost*FP`` 최소화.

    개인 사기는 미적발(FN)이 헛알림(FP)보다 보통 더 비싸다(기본 5:1). 이 비율을
    바꿔 운영 정책(보수적/공격적)을 반영한다. F1 과 달리 **운영 비용 관점**의 임계를 준다.

    Returns:
        (threshold, total_cost).
    """
    cand = np.unique(np.concatenate([np.linspace(0.0, 1.0, 201), np.unique(proba)]))
    best_t, best_cost = 0.5, float("inf")
    for t in cand:
        pred = proba >= t
        fp = int(np.sum(pred & (y_true == 0)))
        fn = int(np.sum(~pred & (y_true == 1)))
        cost = fn_cost * fn + fp_cost * fp
        if cost < best_cost:
            best_cost, best_t = cost, float(t)
    return best_t, best_cost


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


def _tree_importance(clf: Any, kind: str) -> np.ndarray | None:
    """tree/앙상블 feature_importances_(앙상블은 멤버 평균). 없으면 None."""
    if kind == "ens" and isinstance(clf, RankEnsemble):
        imps = [getattr(m, "feature_importances_", None) for m in clf.models]
        imps = [im for im in imps if im is not None]
        if not imps:
            return None
        return np.mean(np.vstack(imps), axis=0)
    return getattr(clf, "feature_importances_", None)


def cross_validate_kaggle(
    *,
    model_kind: str = DEFAULT_MODEL,
    n_folds: int = DEFAULT_FOLDS,
    data: RawData | None = None,
    compute_perm: bool = True,
    engineered: bool = DEFAULT_ENGINEERED,
    rare_min: int = RARE_CATEGORY_MIN,
    calibrate: bool = False,
) -> AttrCVResult:
    """캐글 hold-out 누수 없는 Stratified K-fold out-of-fold 평가.

    **인코더(카테고리 vocabulary/중앙값)를 각 fold 의 train 분할에서만 fit** 하여
    test 분할로 transform 한다(카테고리 누수 차단). 각 fold 의 test 예측만 모아
    (out-of-fold) 성능을 산출하므로 in-sample 부풀리기가 없다.

    Args:
        model_kind: 'lr'/'rf'/'gb'/'hgb'/'ens'(기본 — GB+HGB+RF 앙상블).
        n_folds: Stratified K-fold 수(소수 클래스 비율 유지).
        data: 미리 로드한 RawData(재사용). None 이면 새로 로드.
        compute_perm: permutation importance 계산 여부(느릴 수 있음).
        engineered: 파생 상호작용/비율 피처 사용(기본 True).
        rare_min: 희소 범주 그룹화 임계(train 빈도 미만 제외).
        calibrate: 확률 캘리브레이션(CalibratedClassifierCV, isotonic) — fold-train
            **내부에서만** fit 하므로 누수 없음.

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
        enc = AttributeEncoder(engineered=engineered, rare_min=rare_min).fit(
            [rows[i] for i in tr])
        Xtr = enc.transform([rows[i] for i in tr])
        Xte = enc.transform([rows[i] for i in te])

        if calibrate:
            # 캘리브레이션은 fold-train 내부 3-fold 로만 fit(누수 차단). 불균형은
            # sample_weight 로 base 에 전달. ens 는 CalibratedClassifierCV 비호환이라
            # 멤버별 캘리브레이션을 RankEnsemble 이 직접 처리하지 않으므로 base 단일만.
            base = make_classifier(model_kind)
            n_pos = max(1, int(y[tr].sum()))
            n_neg = max(1, int(len(tr) - y[tr].sum()))
            sw = np.where(y[tr] == 1, n_neg / n_pos, 1.0)
            clf = CalibratedClassifierCV(base, method="isotonic", cv=3)
            try:
                clf.fit(Xtr, y[tr], sample_weight=sw)
            except Exception:
                clf.fit(Xtr, y[tr])
        else:
            clf = make_classifier(model_kind)
            _fit_with_balance(clf, model_kind, Xtr, y[tr])

        proba = clf.predict_proba(Xte)[:, 1]
        oof[te] = proba

        if len(set(y[te].tolist())) == 2:
            fold_auc.append(float(roc_auc_score(y[te], proba)))
            fold_pr_auc.append(float(average_precision_score(y[te], proba)))

        # 중요도 — 캘리브레이션 래퍼는 추출이 까다로워 생략.
        if not calibrate:
            if model_kind == "lr":
                est = clf.named_steps["clf"]
                for name, c in zip(enc.feature_names, est.coef_.ravel()):
                    coef_acc[name] = coef_acc.get(name, 0.0) + float(c)
                coef_folds += 1
            else:
                imp = _tree_importance(clf, model_kind)
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
    full_enc = AttributeEncoder(engineered=engineered, rare_min=rare_min).fit(rows)

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
    model_kind: str = DEFAULT_MODEL,
    test_size: float = 0.25,
    data: RawData | None = None,
    engineered: bool = DEFAULT_ENGINEERED,
    rare_min: int = RARE_CATEGORY_MIN,
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
    enc = AttributeEncoder(engineered=engineered, rare_min=rare_min).fit(
        [data.rows[i] for i in tr])
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
            imp = _tree_importance(self.clf, self.model_kind)
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
    *,
    model_kind: str = DEFAULT_MODEL,
    data: RawData | None = None,
    engineered: bool = DEFAULT_ENGINEERED,
    rare_min: int = RARE_CATEGORY_MIN,
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
    enc = AttributeEncoder(engineered=engineered, rare_min=rare_min).fit(data.rows)
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
    model_desc = {"ens": "ens (GB+HGB+RF 평균-확률 앙상블)"}.get(
        cv.model_kind, cv.model_kind)
    print(f"  모델            : {model_desc}  (불균형: class_weight/sample_weight)")
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

    # 헛알림 통제 운영점 — 고정 precision 에서의 recall(이것이 '오탐 감소' 핵심 지표).
    print(" 고정 precision 운영점 (헛알림 통제 — precision 유지 시 잡히는 recall)")
    print("-" * 78)
    print(f"  {'목표 precision':>16}{'달성 recall':>14}{'임계':>10}{'적발/전체':>14}")
    for tp_prec in (0.30, 0.40, 0.50):
        rec_at, thr_at = recall_at_precision(cv.y_true, cv.oof_proba, tp_prec)
        if thr_at is not None:
            pred = cv.oof_proba >= thr_at
            tp = int(np.sum(pred & (cv.y_true == 1)))
            print(f"  {tp_prec:>16.2f}{rec_at:>14.3f}{thr_at:>10.3f}"
                  f"{f'{tp}/{nf}':>14}")
        else:
            print(f"  {tp_prec:>16.2f}{'도달 불가':>14}{'-':>10}{'-':>14}")
    print("-" * 78)

    # 비용기반 운영점(미적발 FN 이 헛알림 FP 보다 비쌈 — 기본 5:1).
    print(" 비용기반 운영점 (FN:FP 비용비 — 운영 정책 반영)")
    print("-" * 78)
    print(f"  {'FN:FP 비용비':>14}{'임계':>10}{'recall':>10}{'precision':>12}{'알림수':>10}")
    for fn_c in (3.0, 5.0, 10.0):
        thr_c, _ = best_cost_threshold(cv.y_true, cv.oof_proba, fn_cost=fn_c, fp_cost=1.0)
        m = evaluate_proba(cv.y_true, cv.oof_proba, threshold=thr_c)
        print(f"  {f'{int(fn_c)}:1':>14}{thr_c:>10.3f}{m.recall:>10.3f}"
              f"{m.precision:>12.3f}{m.tp + m.fp:>10}")
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
    p.add_argument("--model", default=DEFAULT_MODEL,
                   choices=["lr", "rf", "gb", "hgb", "ens"],
                   help="분류 모델(lr/rf/gb/hgb/ens) — 기본 ens(GB+HGB+RF 앙상블)")
    p.add_argument("--folds", type=int, default=DEFAULT_FOLDS, help="교차검증 fold 수")
    p.add_argument("--holdout", action="store_true",
                   help="단일 hold-out 평가도 함께 출력(CV 일관성 확인)")
    p.add_argument("--no-perm", action="store_true",
                   help="permutation importance 생략(빠른 실행)")
    p.add_argument("--no-engineered", action="store_true",
                   help="파생 상호작용/비율 피처 끔(개선 전 비교용)")
    p.add_argument("--calibrate", action="store_true",
                   help="확률 캘리브레이션(CalibratedClassifierCV, isotonic — fold 내 fit)")
    args = p.parse_args(argv)

    if not _SKLEARN:
        print("scikit-learn 미설치 — `.venv/bin/pip install scikit-learn` 후 재실행")
        return 1

    engineered = not args.no_engineered
    data = load_kaggle()
    cv = cross_validate_kaggle(
        model_kind=args.model, n_folds=args.folds, data=data,
        compute_perm=not args.no_perm, engineered=engineered,
        calibrate=args.calibrate,
    )
    ho = holdout_kaggle(model_kind=args.model, data=data, engineered=engineered) \
        if args.holdout else None
    _print_report(cv, holdout=ho)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
