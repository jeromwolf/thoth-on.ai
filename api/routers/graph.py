"""그래프 탐색 라우터 (WP5 · FR-8.1 / FR-6.x 준비).

고객 주변 서브네트워크를 vis-network 호환 JSON 으로 반환한다(React 콘솔이 그대로
렌더). 그래프 데이터는 사기 케이스 맥락이므로 ``FRAUD_CASE`` 등급으로 인가한다.
Neo4j 가 필요하므로 integration 영역이다.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from api import service
from api.deps import Principal, require_data_class
from api.schemas import GraphResponse
from core.security.rbac import DataClass

router = APIRouter(prefix="/graph", tags=["graph"])


@router.get(
    "/customer/{customer_id}",
    response_model=GraphResponse,
    summary="고객 주변 서브네트워크(vis-network)",
    description="중심 고객과 직접 연결 엔티티 + 공유 엔티티로 연결된 동료 고객을 "
                "nodes/edges 로 반환. 의심 경로/링 멤버는 suspicious 플래그. "
                "FRAUD_CASE 등급 권한 필요.",
    responses={403: {"description": "권한 부족"}},
)
def get_customer_graph(
    customer_id: str,
    principal: Principal = Depends(
        require_data_class(DataClass.FRAUD_CASE, "api.graph.customer")
    ),
) -> GraphResponse:
    """고객 서브네트워크를 vis-network JSON 으로 반환."""
    data = service.customer_subgraph(customer_id)
    return GraphResponse(**data)
