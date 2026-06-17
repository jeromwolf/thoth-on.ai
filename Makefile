.PHONY: help venv install up down logs wait-neo4j schema synth seed reset serve test test-smoke test-int psh gds embed evaluate detect ml

THOTH_API_PORT ?= 8468

PY ?= python3
VENV ?= .venv
PIP := $(VENV)/bin/pip
PYTHON := $(VENV)/bin/python
PYTEST := $(VENV)/bin/pytest

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

venv: ## 가상환경 생성
	$(PY) -m venv $(VENV)

install: venv ## 의존성 설치
	$(PIP) install -U pip
	$(PIP) install -r requirements.txt

up: ## Neo4j 컨테이너 기동
	docker compose up -d

down: ## Neo4j 컨테이너 중지
	docker compose down

logs: ## Neo4j 로그
	docker compose logs -f neo4j

wait-neo4j: ## Neo4j 헬스 대기
	$(PYTHON) -m thoth.db wait

schema: ## 스키마 적용 (WP1)
	$(PYTHON) -m thoth.db apply graph/01_schema.cypher

synth: ## 합성 데이터 생성 (WP1, data/synthetic/*.csv)
	$(PYTHON) -m ingest.synth_generator

seed: ## 합성 데이터 멱등 적재 (WP1)
	$(PYTHON) -m ingest.loader load data/synthetic

reset: ## 그래프 전체 삭제
	$(PYTHON) -m thoth.db reset

gds: ## GDS 군집·중심성 파이프라인 (WP3 · FR-3.4)
	$(PYTHON) -m detection.gds_pipeline run

embed: ## 그래프 임베딩 + 비지도 이상탐지 파이프라인 (WP3 · FR-3.6)
	$(PYTHON) -m detection.embedding run

detect: gds embed ## 전체 탐지 파이프라인 (GDS + 임베딩) 갱신

evaluate: ## 탐지 성능 평가 (룰만 vs 룰+임베딩 비교 + 임계치 스윕)
	$(PYTHON) -m detection.evaluate --threshold 50 --embedding --compare-embedding

ml: ## ML 분류기 + 앙상블 (WP3 · FR-3.7) — 누수 없는 CV + 3단 비교 + 피처 중요도
	$(PYTHON) -m detection.ml_model --model rf --folds 5

test: ## 전체 테스트
	$(PYTEST)

test-smoke: ## 스모크 테스트 (Neo4j 불필요)
	$(PYTEST) -m smoke

test-int: ## 통합 테스트 (Neo4j 필요)
	$(PYTEST) -m integration

serve: ## FastAPI 기동 (THOTH_API_PORT, 기본 8468)
	$(VENV)/bin/uvicorn api.main:app --reload --host 127.0.0.1 --port $(THOTH_API_PORT)

psh: ## cypher-shell 접속
	docker exec -it thoth-neo4j cypher-shell -u neo4j -p $${NEO4J_PASSWORD:-thothpass}
