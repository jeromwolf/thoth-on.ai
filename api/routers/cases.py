"""케이스 라우터 (WP5 · FR-8.1 / FR-4.x).

케이스 큐 조회·상세·배정·판정 엔드포인트. 케이스 상세/판정은 민감 데이터
등급(``FRAUD_CASE``)으로 RBAC 인가하며, 모든 접근/행위는 감사 기록된다.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from api import service
from api.deps import (
    Principal,
    get_case_store,
    get_principal,
    get_signal_cache,
    require_data_class,
)
from api.schemas import (
    AssignRequest,
    AssignResponse,
    CaseDetailResponse,
    CaseListItem,
    CaseListResponse,
    VerdictRequest,
    VerdictResponse,
)
from core.cases import CaseNotFound, CaseStatus, CaseStore, InvalidTransition
from core.security.audit import audit_event
from core.security.rbac import DataClass
from detection import scoring

router = APIRouter(prefix="/cases", tags=["cases"])


def _signal_types(signals: List[Dict[str, Any]]) -> List[str]:
    """기여 신호 유형 요약(내부 메타 제외)."""
    return [
        str(s.get("type"))
        for s in signals
        if not str(s.get("type", "")).startswith("_")
    ]


# ==================================================================
# GET /cases — 의심 케이스 큐
# ==================================================================
@router.get(
    "",
    response_model=CaseListResponse,
    summary="의심 케이스 큐 조회",
    description="리스크 스코어 내림차순 케이스 큐. 임계치·상태 필터, 페이징 지원.",
)
def list_cases(
    threshold: float = Query(
        scoring.DEFAULT_ALERT_THRESHOLD, ge=0, le=100,
        description="이 점수 이상만 반환(기본 50)",
    ),
    status_filter: Optional[str] = Query(
        None, alias="status", description="상태 필터(UNASSIGNED/INVESTIGATING/...)"
    ),
    limit: int = Query(50, ge=1, le=500, description="페이지 크기"),
    offset: int = Query(0, ge=0, description="페이징 오프셋"),
    principal: Principal = Depends(get_principal),
    store: CaseStore = Depends(get_case_store),
    signal_cache: Dict[str, List[Dict[str, Any]]] = Depends(get_signal_cache),
) -> CaseListResponse:
    """케이스 큐를 점수 내림차순으로 반환(페이징·임계치 필터)."""
    status_enum: Optional[CaseStatus] = None
    if status_filter:
        try:
            status_enum = CaseStatus(status_filter.upper())
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"알 수 없는 상태: {status_filter}",
            )

    all_cases = store.queue(status=status_enum)
    filtered = [c for c in all_cases if c.score >= threshold]
    total = len(filtered)
    page = filtered[offset: offset + limit]

    items: List[CaseListItem] = []
    for c in page:
        sigs = signal_cache.get(c.customer_id, [])
        items.append(
            CaseListItem(
                case_id=c.case_id,
                customer_id=c.customer_id,
                score=round(c.score, 1),
                status=c.status.value,
                ring_id=c.ring_id,
                assignee=c.assignee,
                signal_summary=_signal_types(sigs),
            )
        )

    audit_event("api.case.list", principal.actor,
                meta={"role": principal.role.name, "threshold": threshold,
                      "total": total})
    return CaseListResponse(
        total=total, count=len(items), offset=offset,
        threshold=threshold, items=items,
    )


# ==================================================================
# GET /cases/{case_id} — 케이스 상세 (민감: FRAUD_CASE)
# ==================================================================
@router.get(
    "/{case_id}",
    response_model=CaseDetailResponse,
    summary="케이스 상세 조회(사기 케이스 등급)",
    description="점수·기여신호·근거경로·자연어 소명문·환각가드·이력·판정 포함. "
                "FRAUD_CASE 등급 권한(FRAUD_ANALYST 이상) 필요.",
    responses={403: {"description": "권한 부족"}, 404: {"description": "케이스 없음"}},
)
def get_case_detail(
    case_id: str,
    principal: Principal = Depends(
        require_data_class(DataClass.FRAUD_CASE, "api.case.detail")
    ),
    store: CaseStore = Depends(get_case_store),
    signal_cache: Dict[str, List[Dict[str, Any]]] = Depends(get_signal_cache),
) -> CaseDetailResponse:
    """케이스 상세 — 근거 경로·소명문·환각가드 결과를 포함."""
    case = store.get_case(case_id)
    if case is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"케이스 없음: {case_id}",
        )

    signals = signal_cache.get(case.customer_id, [])
    detail = service.build_case_detail(case, signals)

    history = [
        {"from_status": h.from_status, "to_status": h.to_status,
         "actor": h.actor, "note": h.note, "ts": h.ts}
        for h in store.history(case_id)
    ]
    verdicts = [
        {"label": v.label, "actor": v.actor, "comment": v.comment, "ts": v.ts}
        for v in store.verdicts(case_id)
    ]

    return CaseDetailResponse(
        case_id=case.case_id,
        customer_id=case.customer_id,
        score=round(case.score, 1),
        status=case.status.value,
        ring_id=case.ring_id,
        assignee=case.assignee,
        created_at=case.created_at,
        updated_at=case.updated_at,
        signals=detail["signals"],
        paths=detail["paths"],
        explanation=detail["explanation"],
        history=history,
        verdicts=verdicts,
    )


# ==================================================================
# POST /cases/{case_id}/assign — 담당자 배정 (민감: FRAUD_CASE)
# ==================================================================
@router.post(
    "/{case_id}/assign",
    response_model=AssignResponse,
    summary="케이스 담당자 배정",
    description="담당자를 배정하고 미배정 케이스를 조사중으로 전이. 감사 기록.",
    responses={403: {"description": "권한 부족"}, 404: {"description": "케이스 없음"}},
)
def assign_case(
    case_id: str,
    body: AssignRequest,
    principal: Principal = Depends(
        require_data_class(DataClass.FRAUD_CASE, "api.case.assign")
    ),
    store: CaseStore = Depends(get_case_store),
) -> AssignResponse:
    """담당자 배정 — 상태전이 + 감사 기록(저장소 계층에서 수행)."""
    try:
        case = store.assign(case_id, body.assignee, actor=principal.actor)
    except CaseNotFound:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"케이스 없음: {case_id}",
        )
    return AssignResponse(
        case_id=case.case_id, assignee=case.assignee, status=case.status.value
    )


# ==================================================================
# POST /cases/{case_id}/verdict — 조사관 판정 (민감: FRAUD_CASE)
# ==================================================================
@router.post(
    "/{case_id}/verdict",
    response_model=VerdictResponse,
    summary="조사관 판정 기록(사기/정상/보류)",
    description="조사관 판정을 기록하고 케이스 상태를 전이. 감사 기록. "
                "FRAUD_CASE 등급 권한 필요.",
    responses={403: {"description": "권한 부족"}, 404: {"description": "케이스 없음"},
               400: {"description": "잘못된 판정/전이"}},
)
def record_verdict(
    case_id: str,
    body: VerdictRequest,
    principal: Principal = Depends(
        require_data_class(DataClass.FRAUD_CASE, "api.case.verdict")
    ),
    store: CaseStore = Depends(get_case_store),
) -> VerdictResponse:
    """조사관 판정 기록. FRAUD/NORMAL 은 판정으로, HOLD 는 상태전이로 처리."""
    verdict = body.verdict.strip().upper()
    if store.get_case(case_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"케이스 없음: {case_id}",
        )

    try:
        if verdict in {"FRAUD", "NORMAL"}:
            store.record_verdict(
                case_id, verdict, actor=principal.actor, comment=body.comment
            )
        elif verdict == "HOLD":
            store.transition(
                case_id, CaseStatus.HOLD, actor=principal.actor,
                note=f"판정 보류: {body.comment}",
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="verdict 는 FRAUD | NORMAL | HOLD 중 하나여야 합니다",
            )
    except InvalidTransition as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"허용되지 않는 상태 전이: {e}",
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    updated = store.get_case(case_id)
    return VerdictResponse(
        case_id=case_id, verdict=verdict,
        status=updated.status.value if updated else "",
        recorded=True,
    )
