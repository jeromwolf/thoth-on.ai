"""API 요청/응답 Pydantic 모델 (WP5 · FR-8.1).

모든 엔드포인트의 입출력 스키마를 명시적 Pydantic 모델로 정의한다. FastAPI 가
이 모델들로 OpenAPI(/docs, /openapi.json) 스펙을 자동 생성하므로, 응답 구조가
명확히 문서화되어 React 콘솔이 안정적으로 소비할 수 있다.

Python 3.9 호환을 위해 ``Optional[...]``·``List[...]``·``Dict[...]`` 표기를
사용한다(pydantic v2 가 런타임에 해석).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ==================================================================
# 공통 / 헬스
# ==================================================================
class HealthResponse(BaseModel):
    """헬스체크 응답 (Neo4j 연결 상태 포함)."""

    status: str = Field(..., description="전체 상태: ok | degraded")
    service: str = Field("thoth-on-api", description="서비스 식별자")
    neo4j_connected: bool = Field(..., description="Neo4j bolt 연결 가능 여부")
    case_store: bool = Field(..., description="케이스 저장소(SQLite) 접근 가능 여부")


# ==================================================================
# 케이스
# ==================================================================
class SignalSummary(BaseModel):
    """케이스에 기여한 신호 1건의 요약(설명가능성 근거)."""

    type: str = Field(..., description="신호 유형(SHARED_ACCOUNT / CROSS_WITNESS 등)")
    weight: Optional[float] = Field(None, description="가산 가중치(점수 기여)")
    detail: Dict[str, Any] = Field(
        default_factory=dict, description="신호별 상세(공유키·동료 고객 등)"
    )


class CaseListItem(BaseModel):
    """케이스 큐 항목(점수순 정렬). 목록 화면용 요약."""

    case_id: str = Field(..., description="케이스 식별자")
    customer_id: str = Field(..., description="대상 고객 ID")
    score: float = Field(..., description="0~100 리스크 스코어")
    status: str = Field(..., description="케이스 상태")
    ring_id: str = Field("", description="ground truth 링 ID(있으면)")
    assignee: str = Field("", description="배정 담당자")
    signal_summary: List[str] = Field(
        default_factory=list, description="기여 신호 유형 요약 목록"
    )


class CaseListResponse(BaseModel):
    """케이스 큐 응답(페이징·임계치 메타 포함)."""

    total: int = Field(..., description="필터 적용 후 전체 건수")
    count: int = Field(..., description="이번 페이지 반환 건수")
    offset: int = Field(..., description="페이징 오프셋")
    threshold: float = Field(..., description="적용된 점수 임계치")
    items: List[CaseListItem] = Field(default_factory=list)


class PathNode(BaseModel):
    """근거 경로의 노드(vis-network 호환)."""

    id: str
    type: str = Field(..., description="노드 타입(Customer/Account/...)")
    label: str = Field(..., description="표시 라벨(민감 식별자는 마스킹)")


class PathEdge(BaseModel):
    """근거 경로의 엣지."""

    source: str
    target: str
    type: str = Field(..., description="관계 유형(PAID_TO/WITNESSED_BY 등)")


class EvidencePath(BaseModel):
    """기여 신호 1건에서 파생된 구조화 근거 경로."""

    signal_type: str
    weight: Optional[float] = None
    label: str = Field(..., description="사람이 읽는 경로 설명")
    nodes: List[PathNode] = Field(default_factory=list)
    edges: List[PathEdge] = Field(default_factory=list)
    entities: List[str] = Field(
        default_factory=list, description="인용 가능한 실재 엔티티 id 집합"
    )


class GroundingResultModel(BaseModel):
    """환각 가드 검증 결과(FR-5.2)."""

    grounded: bool = Field(..., description="모든 인용 엔티티가 실재 경로에 존재하는가")
    cited_entities: List[str] = Field(
        default_factory=list, description="소명문이 인용한 엔티티"
    )
    hallucinated: List[str] = Field(
        default_factory=list, description="실재하지 않는 인용(위반)"
    )
    num_known: int = Field(0, description="실재 경로 엔티티 수")


class ExplanationModel(BaseModel):
    """자연어 소명문 + 환각 가드 결과."""

    text: str = Field(..., description="자연어 소명문")
    provider: str = Field(..., description="생성 provider(mock/anthropic/...)")
    accepted: bool = Field(..., description="환각 가드 통과 여부")
    grounding: GroundingResultModel


class HistoryEntryModel(BaseModel):
    """상태변경 이력 1건."""

    from_status: str
    to_status: str
    actor: str
    note: str = ""
    ts: str


class VerdictModel(BaseModel):
    """조사관 판정 피드백 1건."""

    label: str
    actor: str
    comment: str = ""
    ts: str


class CaseDetailResponse(BaseModel):
    """케이스 상세: 점수·기여신호·근거경로·소명문·환각가드·이력·판정."""

    case_id: str
    customer_id: str
    score: float
    status: str
    ring_id: str = ""
    assignee: str = ""
    created_at: str = ""
    updated_at: str = ""
    signals: List[SignalSummary] = Field(default_factory=list)
    paths: List[EvidencePath] = Field(default_factory=list)
    explanation: ExplanationModel
    history: List[HistoryEntryModel] = Field(default_factory=list)
    verdicts: List[VerdictModel] = Field(default_factory=list)


# ==================================================================
# 케이스 액션 요청/응답
# ==================================================================
class AssignRequest(BaseModel):
    """담당자 배정 요청."""

    assignee: str = Field(..., description="배정할 담당자 식별자")
    note: str = Field("", description="배정 메모")


class AssignResponse(BaseModel):
    """담당자 배정 결과."""

    case_id: str
    assignee: str
    status: str = Field(..., description="배정 후 케이스 상태")


class VerdictRequest(BaseModel):
    """조사관 판정 요청(사기/정상/보류)."""

    verdict: str = Field(
        ..., description="판정: FRAUD(사기) | NORMAL(정상) | HOLD(보류)"
    )
    comment: str = Field("", description="판정 코멘트")


class VerdictResponse(BaseModel):
    """판정 기록 결과."""

    case_id: str
    verdict: str
    status: str = Field(..., description="판정 반영 후 케이스 상태")
    recorded: bool = Field(..., description="판정 기록 성공 여부")


# ==================================================================
# 그래프 탐색 (vis-network)
# ==================================================================
class GraphNode(BaseModel):
    """vis-network 노드: {id, label, group}."""

    id: str
    label: str
    group: str = Field(..., description="노드 그룹(타입) — vis-network 색상 구분")
    title: Optional[str] = Field(None, description="툴팁(호버) 텍스트")
    suspicious: bool = Field(False, description="의심 경로/링 멤버 플래그")


class GraphEdge(BaseModel):
    """vis-network 엣지: {from, to, label, suspicious}."""

    # 'from' 은 파이썬 예약어가 아니지만 vis-network 키와 맞추기 위해 alias 사용.
    from_: str = Field(..., alias="from")
    to: str
    label: str = Field(..., description="관계 유형 라벨")
    suspicious: bool = Field(False, description="의심 경로 엣지 플래그")

    model_config = {"populate_by_name": True}


class GraphResponse(BaseModel):
    """고객 주변 서브네트워크(vis-network 가 그릴 수 있는 JSON)."""

    customer_id: str
    center: str = Field(..., description="중심 노드 id")
    ring_id: str = Field("", description="중심 고객의 링 ID(있으면)")
    nodes: List[GraphNode] = Field(default_factory=list)
    edges: List[GraphEdge] = Field(default_factory=list)
    node_count: int = 0
    edge_count: int = 0


# ==================================================================
# KPI (FR-9.2)
# ==================================================================
class KpiResponse(BaseModel):
    """경영 대시보드 요약 KPI (FR-9.2).

    실측 지표(케이스 저장소 기반)와 추정 지표(가정 명시)를 포함한다.
    추정 지표(``daily_throughput_estimate``, ``estimated_savings_krw``,
    ``detection_rate_pct``)는 PoC 합성 데이터 기반 추정치로, 실운영 시
    조직 처리 능력·보험금 규모에 맞게 재보정 필요.
    """

    # ── 실측: 케이스 저장소 집계 ────────────────────────────────
    total_cases: int = Field(..., description="총 케이스 수")
    status_distribution: Dict[str, int] = Field(
        default_factory=dict, description="상태별 케이스 분포"
    )
    suspected_rings: int = Field(..., description="의심 링(군집) 수")
    high_risk_cases: int = Field(..., description="고위험(임계 이상) 적발 건수")
    fraud_verdicts: int = Field(..., description="사기 판정 누계")
    avg_score: float = Field(..., description="전체 케이스 평균 점수")
    avg_high_risk_score: float = Field(..., description="고위험 케이스 평균 점수")
    avg_low_risk_score: float = Field(..., description="저위험 케이스 평균 점수")
    score_separation: float = Field(
        ..., description="평균 점수 분리도(고위험 평균 - 저위험 평균)"
    )
    threshold: float = Field(..., description="고위험 판정 임계치")

    # ── 추정: 처리량·적발률·절감액 (가정 명시) ───────────────────
    daily_throughput_estimate: int = Field(
        ...,
        description=(
            "[추정] 조사관 1인 기준 일일 처리 가능 케이스 수. "
            "가정: 케이스당 평균 검토시간 20분, 1일 순수 업무시간 4h → 12건/일."
        ),
    )
    detection_rate_pct: float = Field(
        ...,
        description=(
            "[추정] 탐지율(%) = 고위험 케이스 / 전체 케이스 × 100. "
            "실측 케이스 저장소 기반이나, 전체 청구 모집단 대비 탐지율은 아님."
        ),
    )
    estimated_savings_krw: int = Field(
        ...,
        description=(
            "[추정] 절감액 추정(원). "
            "가정: 고위험 케이스당 평균 청구액 500만 원(PoC 합성 데이터 기준 추정), "
            "사기 판정 케이스가 실제 절감으로 이어진다고 가정. "
            "실운영 시 실제 보험금 지급액 기준으로 재보정 필요."
        ),
    )
    savings_assumption: str = Field(
        ...,
        description="절감액 추정 가정 요약 (투명성 확보용)",
    )


# ==================================================================
# 오류 응답
# ==================================================================
class ErrorResponse(BaseModel):
    """표준 오류 응답."""

    detail: str
