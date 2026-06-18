# ── THOTH-ON API Dockerfile ──────────────────────────────────────────────────
# Python 3.11 slim 기반 FastAPI/uvicorn 컨테이너.
# 컨테이너 네트워크에서 neo4j 서비스를 bolt://neo4j:7687 로 접근한다.
# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.11-slim AS base

# 보안: 루트 실행 금지
RUN addgroup --system thoth && adduser --system --ingroup thoth thoth

WORKDIR /app

# 시스템 의존성 (최소)
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
    && rm -rf /var/lib/apt/lists/*

# ── 의존성 레이어 (소스 변경 시 캐시 재사용) ──────────────────────────────
COPY requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

# ── 소스 복사 ─────────────────────────────────────────────────────────────
# .dockerignore 에서 제외된 항목은 자동으로 포함되지 않음
COPY api/      ./api/
COPY thoth/    ./thoth/
COPY core/     ./core/
COPY detection/ ./detection/
COPY explain/  ./explain/
COPY ingest/   ./ingest/
COPY graph/    ./graph/

# 패키지를 editable 모드 없이 PYTHONPATH 로 해결
# (저장소 루트는 패키지가 아님 — api/thoth/core/... 가 PYTHONPATH=/app 로 최상위 임포트됨)
ENV PYTHONPATH=/app

# 비루트 사용자로 전환
RUN chown -R thoth:thoth /app
USER thoth

# ── 런타임 설정 ───────────────────────────────────────────────────────────
ENV THOTH_API_HOST=0.0.0.0
ENV THOTH_API_PORT=8468
# 컨테이너 내부 Neo4j 접근 (docker-compose 네트워크 호스트명 neo4j)
ENV NEO4J_URI=bolt://neo4j:7687

EXPOSE 8468

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=6 \
    CMD curl -f http://localhost:8468/health || exit 1

# uvicorn 직접 실행 (reload 비활성화 — 프로덕션)
CMD ["python", "-m", "uvicorn", "api.main:app", \
     "--host", "0.0.0.0", "--port", "8468", "--workers", "2"]
