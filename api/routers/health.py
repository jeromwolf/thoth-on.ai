"""헬스체크 라우터 (WP5).

서비스/Neo4j/케이스 저장소 상태를 반환한다. 인증 불필요(운영 모니터링용).
Neo4j 연결 실패는 200 + ``status="degraded"`` 로 보고한다(API 자체는 살아있음).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from api.deps import get_case_store
from api.schemas import HealthResponse
from core.cases import CaseStore

router = APIRouter(tags=["health"])


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="헬스체크(Neo4j 연결 상태 포함)",
    description="API·Neo4j·케이스 저장소 상태. Neo4j 미가용 시 status=degraded.",
)
def health(store: CaseStore = Depends(get_case_store)) -> HealthResponse:
    """서비스 헬스 + Neo4j 연결 상태."""
    from thoth import db

    try:
        neo4j_ok = db.healthcheck()
    except Exception:
        neo4j_ok = False

    try:
        store.queue(limit=1)
        case_ok = True
    except Exception:
        case_ok = False

    return HealthResponse(
        status="ok" if (neo4j_ok and case_ok) else "degraded",
        neo4j_connected=neo4j_ok,
        case_store=case_ok,
    )
