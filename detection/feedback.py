"""조사관 판정 피드백 루프 실학습 (WP4-3 · FR-4.3) — 운영 라벨로의 재학습.

실배포 환경엔 ground truth(``is_fraud_ring``)가 존재하지 않는다. 조사관의 케이스
판정(FRAUD/NORMAL)이 곧 **운영 라벨**이며, 모델은 이 피드백으로 학습/재학습돼야
한다. 이 모듈은 ``core.cases.CaseStore`` 에 쌓인 판정 이력을 고객별 라벨로 환원하고,
ground truth 라벨에 **override** 해 재학습용 라벨 집합을 만든다.

[평가 누수 절대 금지 — 최우선 제약]
    1) 이 모듈은 **라벨(y) 만 조작**한다. 피처(X, detection.features)는 절대 건드리지
       않는다 — 판정 결과가 피처로 새어 들어가는 일이 없도록 구조적으로 분리한다.
    2) 성능은 detection.ml_model 의 out-of-fold 교차검증으로만 보고한다(in-sample
       부풀리기 금지). baseline 과 feedback 모두 각자 라벨에 대한 out-of-fold 성능.

[정직성 — baseline vs feedback 비교의 한계]
    baseline(ground truth 라벨)과 feedback(판정 반영 라벨)은 **서로 다른 라벨 집합**
    으로 평가되므로, 두 지표는 동일 척도의 직접 비교가 아니다. delta 는 "운영 라벨로
    재정의했을 때 모델이 그 라벨을 얼마나 잘 맞히는가"의 참고치일 뿐, ground truth
    대비 절대 개선을 뜻하지 않는다. 이 한계를 표/문서에 항상 명시한다.

CLI:
    python -m detection.feedback              # 판정 라벨로 재학습 + provenance 표
    python -m detection.feedback --folds 5    # fold 수 지정
    python -m detection.feedback --model rf   # 모델 선택(lr/rf/gb)
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Optional

from core.cases import CaseStore
from detection import features as featmod
from detection import ml_model
from detection.ml_model import Metrics
from thoth import db

# 판정 라벨 문자열 → 정수 라벨(사기=1/정상=0).
_VERDICT_TO_INT = {"FRAUD": 1, "NORMAL": 0}

# 재학습에 필요한 최소 양성(1) 판정 수 — stratified CV 가능 하한.
_MIN_POSITIVE = 2


# ==================================================================
# 1) 판정 → 운영 라벨 환원
# ==================================================================
def collect_verdict_labels(store: CaseStore) -> dict[str, int]:
    """케이스 판정 이력을 고객별 **최신 운영 라벨**로 환원한다(FRAUD=1/NORMAL=0).

    전체 케이스 큐를 돌며 각 케이스의 판정 이력을 모은다. 한 고객이 여러 케이스를
    갖거나 한 케이스에 여러 판정(재오픈 후 재판정)이 있을 수 있으므로, **고객 단위로
    ts(판정 시각)가 가장 최근인 판정**을 채택한다. 판정이 없는 케이스/고객은 제외한다.

    판정 시각 ts 는 ISO8601(UTC) 문자열이라 사전식 비교가 곧 시간순 비교가 된다.

    Args:
        store: 케이스 저장소(``CaseStore``).

    Returns:
        ``{customer_id: label}`` — 판정이 있었던 고객만. label∈{0,1}.
    """
    # customer_id -> (가장 최근 판정 ts, 라벨)
    latest: dict[str, tuple[str, int]] = {}
    for case in store.queue():
        cid = case.customer_id
        for v in store.verdicts(case.case_id):
            label = _VERDICT_TO_INT.get(v.label.upper())
            if label is None:
                continue
            prev = latest.get(cid)
            # ts 최대(가장 최근)를 채택. ISO 문자열 사전식 비교 = 시간순.
            if prev is None or v.ts > prev[0]:
                latest[cid] = (v.ts, label)
    return {cid: lbl for cid, (_ts, lbl) in latest.items()}


# ==================================================================
# 2) 라벨 출처(provenance) 집계
# ==================================================================
@dataclass
class LabelProvenance:
    """병합 라벨의 출처 분해 — 운영 라벨이 ground truth 를 얼마나 덮었는지 정직 보고.

    Attributes:
        n_total: 전체 고객 수.
        n_feedback: 판정으로 라벨이 결정된 고객 수(override 후보 모집단).
        n_overrides: 판정 라벨 != 기존 ground truth 라벨이라 실제로 뒤집힌 고객 수.
        n_agree: 판정 라벨 == 기존 ground truth 라벨(판정이 GT 를 재확인).
        n_base: 판정이 없어 기존 ground truth 라벨을 그대로 유지한 고객 수.
    """

    n_total: int
    n_feedback: int
    n_overrides: int
    n_agree: int
    n_base: int


def merge_labels(
    customer_ids: list[int] | list[str],
    base_labels: list[int],
    verdict_labels: dict[str, int],
) -> tuple[list[int], LabelProvenance]:
    """ground truth 라벨에 판정 라벨을 override 해 재학습용 라벨을 만든다.

    ``customer_ids`` 순서대로, 해당 고객의 판정 라벨이 있으면 그 값으로 덮고(override),
    없으면 ``base_labels`` 의 ground truth 값을 유지한다. 동시에 출처(provenance)를
    정확히 집계한다.

    Args:
        customer_ids: 라벨 순서를 정의하는 고객 ID 리스트(FeatureMatrix.customer_ids).
        base_labels: ``customer_ids`` 와 같은 길이의 ground truth 라벨(0/1).
        verdict_labels: ``collect_verdict_labels`` 가 만든 고객→판정 라벨 맵.

    Returns:
        (merged_labels, provenance) — 병합 라벨 리스트 + 출처 집계.
    """
    if len(customer_ids) != len(base_labels):
        raise ValueError("customer_ids 와 base_labels 길이가 일치해야 합니다")

    merged: list[int] = []
    n_overrides = n_agree = n_base = 0
    for cid, base in zip(customer_ids, base_labels):
        v = verdict_labels.get(cid)
        if v is None:
            merged.append(int(base))
            n_base += 1
        else:
            merged.append(int(v))
            if int(v) != int(base):
                n_overrides += 1
            else:
                n_agree += 1
    prov = LabelProvenance(
        n_total=len(customer_ids),
        n_feedback=n_overrides + n_agree,
        n_overrides=n_overrides,
        n_agree=n_agree,
        n_base=n_base,
    )
    return merged, prov


# ==================================================================
# 3) 재학습 결과
# ==================================================================
@dataclass
class RetrainResult:
    """판정 피드백 재학습 결과 — provenance + baseline/feedback 성능 + delta.

    **정직성 주의**: ``baseline`` 은 ground truth 라벨, ``feedback`` 은 판정 반영 라벨로
    각각 평가된다. 즉 **서로 다른 라벨 집합**에 대한 out-of-fold 성능이므로 동일 척도의
    직접 비교가 아니다. ``delta_auc``/``delta_f1`` 은 "운영 라벨로 재정의했을 때 모델이
    그 라벨을 얼마나 잘 맞히는가"의 참고치이며, ground truth 대비 절대 개선이 아니다.

    Attributes:
        provenance: 병합 라벨의 출처 분해.
        baseline: ground truth 라벨 기준 out-of-fold 지표(F1-최적 운영점).
        feedback: 판정 반영 라벨 기준 out-of-fold 지표(F1-최적 운영점).
        model_kind: 사용한 모델('lr'/'rf'/'gb').
        n_folds: 교차검증 fold 수.
        delta_auc: feedback.auc - baseline.auc (참고치 — 라벨 집합 상이).
        delta_f1: feedback.f1 - baseline.f1 (참고치 — 라벨 집합 상이).
    """

    provenance: LabelProvenance
    baseline: Metrics
    feedback: Metrics
    model_kind: str
    n_folds: int
    delta_auc: float
    delta_f1: float


def _oof_metrics(
    fm: featmod.FeatureMatrix,
    labels: list[int],
    *,
    model_kind: str,
    n_folds: int,
    label: str,
) -> Metrics:
    """주어진 라벨로 out-of-fold CV → F1-최적 임계 → Metrics 산출(누수 없음)."""
    cv = ml_model.cross_validate(
        model_kind=model_kind, n_folds=n_folds, fm=fm, labels=labels
    )
    thr = ml_model.f1_optimal_threshold(cv.y_true, cv.oof_proba)
    return ml_model.metrics_at(cv.y_true, cv.oof_proba, thr, label=label)


def retrain_with_feedback(
    *,
    model_kind: str = "rf",
    n_folds: int = ml_model.DEFAULT_FOLDS,
    store: Optional[CaseStore] = None,
) -> RetrainResult:
    """조사관 판정을 운영 라벨로 반영해 재학습하고 baseline 과 정직 비교한다.

    절차:
        1) 피처 1회 추출(fm). ground truth 라벨(base) 추출.
        2) 케이스 판정 → 고객별 최신 운영 라벨(verdict_labels) 환원.
        3) base 에 판정 라벨 override(merge) + provenance 집계.
        4) baseline CV(ground truth 라벨) → F1-최적 운영점 Metrics.
        5) feedback CV(병합 라벨) → F1-최적 운영점 Metrics.
        6) delta 산출(참고치 — 라벨 집합이 달라 직접 비교 아님).

    피처(X)는 절대 건드리지 않고 라벨(y)만 바꾼다(누수 차단).

    Args:
        model_kind: ML 모델 종류('lr'/'rf'/'gb').
        n_folds: 교차검증 fold 수.
        store: 케이스 저장소. None 이면 ``CaseStore()`` 를 새로 만든다.

    Returns:
        ``RetrainResult``.

    Raises:
        ValueError: 병합 라벨의 양성(1)이 ``_MIN_POSITIVE`` 미만이거나 단일 클래스라
            stratified 교차검증이 불가능할 때.
    """
    if store is None:
        store = CaseStore()

    fm = featmod.build_features()
    base_labels = featmod.extract_labels(fm.customer_ids)

    verdict_labels = collect_verdict_labels(store)
    merged, prov = merge_labels(fm.customer_ids, base_labels, verdict_labels)

    n_pos = sum(1 for v in merged if v == 1)
    n_classes = len(set(merged))
    if n_pos < _MIN_POSITIVE or n_classes < 2:
        raise ValueError("판정 라벨이 부족해 재학습 불가(최소 양성 2건 필요)")

    baseline = _oof_metrics(
        fm, base_labels, model_kind=model_kind, n_folds=n_folds, label="baseline(GT)"
    )
    feedback = _oof_metrics(
        fm, merged, model_kind=model_kind, n_folds=n_folds, label="feedback(판정)"
    )

    return RetrainResult(
        provenance=prov,
        baseline=baseline,
        feedback=feedback,
        model_kind=model_kind,
        n_folds=n_folds,
        delta_auc=feedback.auc - baseline.auc,
        delta_f1=feedback.f1 - baseline.f1,
    )


# ==================================================================
# CLI — 재학습 리포트(provenance + baseline/feedback + delta)
# ==================================================================
def _print_report(res: RetrainResult) -> None:
    line = "=" * 76
    p = res.provenance
    print(line)
    print(" THOTH-ON WP4 조사관 판정 피드백 재학습 (FR-4.3) — 운영 라벨 실학습")
    print(line)
    print(f"  모델            : {res.model_kind}  (class_weight=balanced)")
    print(f"  검증 방식       : Stratified {res.n_folds}-fold out-of-fold "
          f"(학습데이터 평가 금지 — 누수 차단)")
    print("-" * 76)
    print(" 라벨 출처(provenance) — 판정이 ground truth 를 얼마나 덮었나")
    print("-" * 76)
    print(f"  전체 고객           : {p.n_total:,}")
    print(f"  판정으로 라벨결정   : {p.n_feedback:,}  "
          f"(override {p.n_overrides:,} + 재확인 {p.n_agree:,})")
    print(f"  판정으로 뒤집힘     : {p.n_overrides:,}  (판정 != 기존 ground truth)")
    print(f"  판정이 GT 재확인    : {p.n_agree:,}  (판정 == 기존 ground truth)")
    print(f"  판정없어 GT 유지    : {p.n_base:,}")
    print(line)
    print(" 성능 비교 (각자 F1-최적 운영점 · out-of-fold)")
    print(" ⚠ baseline 과 feedback 은 서로 다른 라벨 집합으로 평가 — 직접 비교 아님(참고)")
    print("-" * 76)
    print(f"  {'기준':<16} {'recall':>8} {'prec':>8} {'F1':>8} {'FPR':>8} "
          f"{'AUC':>8} {'TP':>5} {'FP':>5}")
    print("-" * 76)
    print(res.baseline.to_row())
    print(res.feedback.to_row())
    print("-" * 76)
    print(f"  ΔF1  (참고)         : {res.delta_f1:+.3f}")
    print(f"  ΔAUC (참고)         : {res.delta_auc:+.3f}")
    print(line)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="THOTH-ON WP4 조사관 판정 피드백 재학습 (FR-4.3)")
    p.add_argument("--model", default="rf", choices=["lr", "rf", "gb"],
                   help="분류 모델(lr=LogisticRegression, rf=RandomForest, gb=GradientBoosting)")
    p.add_argument("--folds", type=int, default=ml_model.DEFAULT_FOLDS,
                   help="교차검증 fold 수")
    args = p.parse_args(argv)

    if not ml_model._SKLEARN:
        print("scikit-learn 미설치 — `.venv/bin/pip install scikit-learn` 후 재실행")
        return 1
    if not db.healthcheck():
        print("Neo4j 미가용 — `make up && make wait-neo4j` 후 재실행")
        return 1

    try:
        res = retrain_with_feedback(model_kind=args.model, n_folds=args.folds)
    except ValueError as e:
        print(f"재학습 불가: {e}")
        return 2
    _print_report(res)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
