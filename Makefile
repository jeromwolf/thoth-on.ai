.PHONY: help venv install up down logs wait-neo4j schema seed reset test test-smoke test-int psh

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

seed: ## 시드 적재 (WP1)
	$(PYTHON) -m thoth.db apply graph/02_seed_data.cypher

reset: ## 그래프 전체 삭제
	$(PYTHON) -m thoth.db reset

test: ## 전체 테스트
	$(PYTEST)

test-smoke: ## 스모크 테스트 (Neo4j 불필요)
	$(PYTEST) -m smoke

test-int: ## 통합 테스트 (Neo4j 필요)
	$(PYTEST) -m integration

psh: ## cypher-shell 접속
	docker exec -it thoth-neo4j cypher-shell -u neo4j -p $${NEO4J_PASSWORD:-thothpass}
