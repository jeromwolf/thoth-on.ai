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

import logging
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routers import cases, graph, health, kpi
from core.cases import CaseStore
from thoth.config import get_settings

logger = logging.getLogger(__name__)

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

    # CORS: React 콘솔(dev/preview)이 브라우저에서 API를 호출하도록 허용.
    # THOTH_CORS_ORIGINS(쉼표구분)로 운영 도메인 지정, 기본은 로컬 dev 포트.
    origins = os.getenv(
        "THOTH_CORS_ORIGINS",
        "http://localhost:5847,http://127.0.0.1:5847,"
        "http://localhost:5173,http://127.0.0.1:5173,"
        "http://localhost:4173,http://127.0.0.1:4173",
    ).split(",")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in origins if o.strip()],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 앱 상태: 케이스 저장소 + 신호 캐시(의존성에서 주입).
    app.state.case_store = CaseStore()
    app.state.signal_cache = {}

    app.include_router(health.router)
    app.include_router(cases.router)
    app.include_router(graph.router)
    app.include_router(kpi.router)

    # ------------------------------------------------------------------
    # 기동 부트스트랩: Neo4j 가용 시 리스크 스코어링 → 케이스 큐 + 신호 캐시 적재.
    # THOTH_API_BOOTSTRAP=0 으로 비활성화 가능(테스트/오프라인 환경).
    # 부트스트랩 실패해도 앱은 정상 기동(best-effort).
    # ------------------------------------------------------------------
    @app.on_event("startup")
    async def _bootstrap() -> None:
        """서버 기동 시 Neo4j 에서 리스크 신호를 읽어 케이스 큐·신호 캐시를 채운다.

        1. THOTH_API_BOOTSTRAP 환경 변수가 "0" 이면 스킵(테스트/오프라인 안전).
        2. Neo4j 미가용이면 즉시 return(예외 발생 없이 — 서버는 계속 기동).
        3. 스코어링 → 임계 초과 고객을 케이스로 등록(멱등) + 기여 신호를
           ``app.state.signal_cache[customer_id] = signals`` 로 적재.
        4. 모든 예외를 잡아 로깅 후 return — 앱 기동 실패 방지.
        """
        if os.getenv("THOTH_API_BOOTSTRAP", "1") == "0":
            logger.info("[bootstrap] THOTH_API_BOOTSTRAP=0 — 스킵")
            return

        try:
            from thoth import db
            from detection import scoring as _scoring
            from api import service as _service

            # Neo4j 가용성 확인. 미가용이면 조용히 종료.
            if not db.healthcheck():
                logger.warning(
                    "[bootstrap] Neo4j 미가용 — 신호 캐시 비적재(앱은 정상 기동)"
                )
                return

            logger.info("[bootstrap] Neo4j 연결 확인 완료. 리스크 스코어링 시작…")
            risks = _scoring.score_customers()

            # 케이스 큐 생성(멱등 — 이미 있는 케이스는 덮어쓰지 않음).
            store: CaseStore = app.state.case_store
            created = 0
            for r in _scoring.alerts(risks):
                case_id = f"CASE-{r.customer_id}"
                store.create_case(
                    case_id=case_id,
                    customer_id=r.customer_id,
                    score=r.score,
                    ring_id=r.ring_id,
                    actor="bootstrap",
                )
                created += 1

            # 신호 캐시 적재: customer_id → signals(내부 메타 _* 포함, 라우터에서 필터).
            # CustomerRisk.signals 는 List[Dict] 형태 그대로 캐시.
            cache: dict = app.state.signal_cache
            for customer_id, risk in risks.items():
                if risk.signals:
                    cache[customer_id] = risk.signals

            logger.info(
                "[bootstrap] 완료 — 케이스 %d건 등록, 신호 캐시 %d명 적재",
                created,
                len(cache),
            )

        except Exception:
            logger.exception(
                "[bootstrap] 부트스트랩 중 예외 발생 — 신호 캐시 미적재(앱은 계속 기동)"
            )

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    s = get_settings()
    uvicorn.run("api.main:app", host=s.api_host, port=s.api_port, reload=True)
