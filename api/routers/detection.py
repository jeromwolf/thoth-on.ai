"""탐지 라우터 — 판정 피드백 재학습 (WP4-3 · FR-4.3).

조사관의 케이스 판정(FRAUD/NORMAL)을 운영 라벨로 반영해 ML 모델을 재학습하고,
baseline(ground truth)과 feedback(판정 반영) 지표를 정직하게 비교·반환한다.

⚠ baseline 과 feedback 은 **서로 다른 라벨 집합**으로 평가된다. delta 는 직접 비교가
  아닌 참고치임을 응답 note 필드에 항상 명시한다(detection.feedback 모듈 정직성 원칙).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from typing import Any, Dict, List

from api import service
from api.deps import (
    Principal,
    get_case_store,
    get_signal_cache,
    require_data_class,
)
from api.schemas import (
    ActiveModelResponse,
    MetricsModel,
    ProvenanceModel,
    RescoreResponse,
    RetrainRequest,
    RetrainResponse,
)
from core.cases import CaseStore
from core.security.rbac import DataClass
from detection.feedback import retrain_with_feedback
from detection.ml_model import Metrics

router = APIRouter(prefix="/detection", tags=["detection"])

_NOTE = (
    "baseline(ground truth)과 feedback(판정 라벨)은 서로 다른 라벨 집합으로 평가 "
    "— delta는 직접 비교가 아닌 참고치"
)

_VALID_MODELS = {"lr", "rf", "gb"}


def _to_metrics_model(m: Metrics) -> MetricsModel:
    """detection.ml_model.Metrics → MetricsModel 변환."""
    return MetricsModel(
        recall=m.recall,
        precision=m.precision,
        f1=m.f1,
        fpr=m.fpr,
        auc=m.auc,
        tp=m.tp,
        fp=m.fp,
        fn=m.fn,
        tn=m.tn,
    )


# ==================================================================
# POST /detection/retrain — 판정 피드백 재학습
# ==================================================================
@router.post(
    "/retrain",
    response_model=RetrainResponse,
    summary="조사관 판정 피드백 재학습",
    description=(
        "케이스 저장소에 쌓인 조사관 판정(FRAUD/NORMAL)을 운영 라벨로 반영해 "
        "ML 모델을 재학습하고, baseline(ground truth)과 feedback(판정 반영) 지표를 "
        "비교해 반환한다. scikit-learn 및 Neo4j 가 모두 가용해야 실행 가능. "
        "FRAUD_CASE 등급 권한(FRAUD_ANALYST 이상) 필요."
    ),
    responses={
        400: {"description": "잘못된 모델 종류"},
        409: {"description": "판정 라벨 부족(양성 2건 미만)"},
        503: {"description": "scikit-learn 미설치 또는 Neo4j 미가용"},
    },
)
def retrain(
    body: RetrainRequest,
    principal: Principal = Depends(
        require_data_class(DataClass.FRAUD_CASE, "api.detection.retrain")
    ),
    store: CaseStore = Depends(get_case_store),
) -> RetrainResponse:
    """조사관 판정 피드백을 운영 라벨로 반영해 ML 재학습 후 지표를 반환한다."""
    # scikit-learn 설치 여부 확인.
    from detection import ml_model
    if not ml_model._SKLEARN:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="scikit-learn 미설치 — .venv/bin/pip install scikit-learn 후 재시도",
        )

    # Neo4j 가용성 확인.
    from thoth import db
    if not db.healthcheck():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Neo4j 미가용 — make up && make wait-neo4j 후 재시도",
        )

    # 모델 종류 검증.
    if body.model not in _VALID_MODELS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"model 은 lr | rf | gb 중 하나여야 합니다 (입력값: {body.model!r})",
        )

    # 재학습 실행.
    try:
        res = retrain_with_feedback(
            model_kind=body.model,
            n_folds=body.folds,
            store=store,
            persist=body.persist,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e),
        )

    prov = res.provenance
    return RetrainResponse(
        model_kind=res.model_kind,
        n_folds=res.n_folds,
        provenance=ProvenanceModel(
            n_total=prov.n_total,
            n_feedback=prov.n_feedback,
            n_overrides=prov.n_overrides,
            n_agree=prov.n_agree,
            n_base=prov.n_base,
        ),
        baseline=_to_metrics_model(res.baseline),
        feedback=_to_metrics_model(res.feedback),
        delta_auc=res.delta_auc,
        delta_f1=res.delta_f1,
        note=_NOTE,
        persisted=res.persisted,
        model_path=res.model_path,
        trained_at=res.trained_at,
    )


# ==================================================================
# POST /detection/rescore — 케이스 큐 재스코어링(모델 반영)
# ==================================================================
@router.post(
    "/rescore",
    response_model=RescoreResponse,
    summary="케이스 큐 재스코어링(모델 반영)",
    description=(
        "리스크 스코어를 재계산해 케이스 큐 점수와 신호 캐시에 반영한다. "
        "영속화된 활성 재학습 모델이 있으면 그 사기확률을 점수에 가산(use_ml)하므로, "
        "재학습 결과가 조사관 큐 우선순위에 즉시 반영된다. "
        "기존 케이스는 점수만 갱신(상태·이력 불변), 임계 초과 신규 고객은 케이스 생성. "
        "scikit-learn 및 Neo4j 가 모두 가용해야 실행 가능. "
        "FRAUD_CASE 등급 권한(FRAUD_ANALYST 이상) 필요."
    ),
    responses={
        503: {"description": "scikit-learn 미설치 또는 Neo4j 미가용"},
    },
)
def rescore(
    principal: Principal = Depends(
        require_data_class(DataClass.FRAUD_CASE, "api.detection.rescore")
    ),
    store: CaseStore = Depends(get_case_store),
    signal_cache: Dict[str, List[Dict[str, Any]]] = Depends(get_signal_cache),
) -> RescoreResponse:
    """리스크 스코어를 재계산해 케이스 큐 점수에 반영한다(활성 모델 있으면 ML 가산)."""
    # scikit-learn 설치 여부 확인.
    from detection import ml_model
    if not ml_model._SKLEARN:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="scikit-learn 미설치 — .venv/bin/pip install scikit-learn 후 재시도",
        )

    # Neo4j 가용성 확인.
    from thoth import db
    if not db.healthcheck():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Neo4j 미가용 — make up && make wait-neo4j 후 재시도",
        )

    # 활성 재학습 모델이 있으면 그 사기확률을 점수에 반영.
    from detection import model_store
    use_ml = model_store.active_model_meta() is not None

    summary = service.rescore_cases(
        store, signal_cache, use_ml=use_ml, actor=principal.actor
    )
    return RescoreResponse(**summary)


# ==================================================================
# GET /detection/model — 활성 재학습 모델 메타 조회
# ==================================================================
@router.get(
    "/model",
    response_model=ActiveModelResponse,
    summary="활성 재학습 모델 메타 조회",
    description=(
        "영속화된 활성 재학습 모델의 메타 정보를 반환한다. "
        "모델이 없으면 active=False 를 반환한다. "
        "FRAUD_CASE 등급 권한(FRAUD_ANALYST 이상) 필요."
    ),
    responses={
        200: {"description": "활성 모델 메타(없으면 active=False)"},
    },
)
def get_active_model(
    principal: Principal = Depends(
        require_data_class(DataClass.FRAUD_CASE, "api.detection.model")
    ),
) -> ActiveModelResponse:
    """영속화된 활성 모델 메타를 반환한다. 없으면 active=False."""
    from detection import model_store

    meta = model_store.active_model_meta()
    if meta is None:
        return ActiveModelResponse(active=False)
    return ActiveModelResponse(
        active=True,
        trained_at=meta.trained_at,
        model_kind=meta.model_kind,
        n_samples=meta.n_samples,
        n_positive=meta.n_positive,
        feature_count=len(meta.feature_names),
    )
