"""THOTH-ON FastAPI 앱 (WP5 · FR-8.1).

탐지·스코어링·케이스·설명 레이어를 REST 로 노출한다. OpenAPI 스펙은 FastAPI 가
자동 생성한다(/docs, /redoc, /openapi.json). 인증/인가는 헤더 기반 역할 주입 +
``core.security.rbac`` 데이터 등급 인가(``api.deps``)로 수행하며 전 접근을 감사한다.

[엔드포인트]
    · GET  /health                       — 헬스체크(Neo4j 포함)
    · GET  /cases                        — 의심 케이스 큐(점수순·페이징·임계치)
    · GET  /cases/{case_id}              — 케이스 상세(경로·소명문·환각가드)  [FRAUD_CASE]
    · POST /cases/{case_id}/assign       — 담당자 배정                        [FRAUD_CASE]
    · POST /cases/{case_id}/verdict      — 조사관 판정(사기/정상/보류)        [FRAUD_CASE]
    · GET  /graph/customer/{customer_id} — 고객 서브네트워크(vis-network)     [FRAUD_CASE]
    · GET  /kpi                          — 경영 대시보드 KPI(FR-9.2)          [CLAIMS]

실행:
    make serve                       # THOTH_API_PORT(기본 8468)
    .venv/bin/uvicorn api.main:app --reload --port 8468
"""
from __future__ import annotations

from fastapi import FastAPI

from api.routers import cases, graph, health, kpi
from core.cases import CaseStore
from thoth.config import get_settings

_DESCRIPTION = """\
**THOTH-ON** — 관계 중심 보험 사기 탐지 지식그래프 플랫폼의 REST API.

지식그래프 탐지·GDS·스코어링·케이스 관리·설명(소명문 + 환각가드) 레이어를
React 조사관 콘솔이 소비할 수 있도록 노출한다.

### 인증/인가 (PoC)
헤더 기반 역할 주입 + 데이터 등급 RBAC:
- `X-Role: FRAUD_ANALYST` (역할 직접 지정), 또는
- `Authorization: Bearer analyst-token` / `X-API-Key: analyst-token`
- 역할 토큰: `adjuster-token`(심사역) · `analyst-token`(조사관) ·
  `risk-token`(리스크팀) · `admin-token`(관리자)

민감 엔드포인트(케이스 상세·배정·판정·그래프)는 `FRAUD_ANALYST` 이상,
KPI 는 `CLAIMS_ADJUSTER` 이상이 필요하다. 권한 부족 시 403. 전 접근은 감사 기록.
"""

tags_metadata = [
    {"name": "health", "description": "헬스체크/모니터링"},
    {"name": "cases", "description": "의심 케이스 큐·상세·배정·판정 (FR-4.x)"},
    {"name": "graph", "description": "고객 서브네트워크 탐색(vis-network)"},
    {"name": "kpi", "description": "경영 대시보드 KPI (FR-9.2)"},
]


def create_app() -> FastAPI:
    """FastAPI 앱 팩토리. 라우터 등록 + 케이스 저장소/신호 캐시 초기화."""
    app = FastAPI(
        title="THOTH-ON API",
        version="0.2.0",
        description=_DESCRIPTION,
        openapi_tags=tags_metadata,
    )

    # 앱 상태: 케이스 저장소 + 신호 캐시(의존성에서 주입).
    app.state.case_store = CaseStore()
    app.state.signal_cache = {}

    app.include_router(health.router)
    app.include_router(cases.router)
    app.include_router(graph.router)
    app.include_router(kpi.router)

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    s = get_settings()
    uvicorn.run("api.main:app", host=s.api_host, port=s.api_port, reload=True)
