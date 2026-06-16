# ── THOTH-ON Console Dockerfile (멀티스테이지) ───────────────────────────────
# Stage 1: Node.js 빌드 → Stage 2: nginx 정적 서빙
# VITE_API_BASE 빌드 인자로 API 엔드포인트 주입.
# 폐쇄망: node:20-alpine 이미지를 사전 반입(docker save/load) 후 사용.
# ─────────────────────────────────────────────────────────────────────────────

# ── Stage 1: 빌드 ────────────────────────────────────────────────────────────
FROM node:20-alpine AS builder

WORKDIR /app

# 의존성 레이어 캐시 최적화
COPY console/package.json console/package-lock.json* ./
RUN npm ci --prefer-offline

# 소스 복사
COPY console/ ./

# VITE_API_BASE: 브라우저→API URL. 컨테이너 배포 시 nginx 프록시를 통해
# /api/* 를 api 컨테이너로 전달하므로 기본값은 상대경로 '/api'.
ARG VITE_API_BASE=/api
ENV VITE_API_BASE=${VITE_API_BASE}

RUN npm run build

# ── Stage 2: nginx 정적 서빙 ─────────────────────────────────────────────────
FROM nginx:1.27-alpine AS runtime

# SPA 라우팅: 모든 경로를 index.html 로 fallback
RUN rm /etc/nginx/conf.d/default.conf
COPY console/nginx.conf /etc/nginx/conf.d/default.conf

# 빌드 산출물 복사
COPY --from=builder /app/dist /usr/share/nginx/html

EXPOSE 80

HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=4 \
    CMD wget -qO- http://localhost:80/ || exit 1

CMD ["nginx", "-g", "daemon off;"]
