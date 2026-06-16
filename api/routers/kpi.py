"""KPI 라우터 (WP5 · FR-9.2).

경영 대시보드 요약 KPI 를 반환한다. 케이스 저장소(SQLite) 기반 집계는 Neo4j
없이 가능하나, 의심 링 수 보강은 탐지(Neo4j)에 의존하므로 가용 시에만 사용한다.
경영 지표 열람은 ``CLAIMS`` 등급(CLAIMS_ADJUSTER 이상)으로 인가한다.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from api import service
from api.deps import Principal, get_case_store, require_data_class
from api.schemas import KpiResponse
from core.cases import CaseStore
from core.security.rbac import DataClass
from detection import scoring

router = APIRouter(prefix="/kpi", tags=["kpi"])


@router.get(
    "",
    response_model=KpiResponse,
    summary="경영 대시보드 KPI 요약",
    description="총 케이스·상태 분포·의심 링 수·고위험 적발 건수·평균 점수 분리도 등. "
                "CLAIMS 등급(CLAIMS_ADJUSTER 이상) 권한 필요.",
    responses={403: {"description": "권한 부족"}},
)
def get_kpi(
    threshold: float = Query(
        scoring.DEFAULT_ALERT_THRESHOLD, ge=0, le=100,
        description="고위험 판정 임계치(기본 50)",
    ),
    principal: Principal = Depends(
        require_data_class(DataClass.CLAIMS, "api.kpi.summary")
    ),
    store: CaseStore = Depends(get_case_store),
) -> KpiResponse:
    """경영 대시보드 KPI 집계."""
    # 의심 링 수는 탐지(Neo4j)가 가용하면 보강, 아니면 케이스 ring_id 로 추정.
    suspected_rings = None
    try:
        suspected_rings = service.count_suspected_rings()
    except Exception:
        suspected_rings = None  # Neo4j 미가용 — 케이스 기반 추정으로 폴백.

    data = service.compute_kpi(
        store, threshold=threshold, suspected_rings=suspected_rings
    )
    return KpiResponse(**data)
