"""WP0 통합: Neo4j 기동 + 쿼리 + GDS 가용성 확인."""
import pytest

pytestmark = pytest.mark.integration


def test_neo4j_returns_one(graph):
    rec = graph.run("RETURN 1 AS ok").single()
    assert rec["ok"] == 1


def test_gds_available(graph):
    rec = graph.run("RETURN gds.version() AS v").single()
    assert rec and rec["v"], "GDS 플러그인 미설치 — docker-compose NEO4J_PLUGINS 확인"
