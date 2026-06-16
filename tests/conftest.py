"""pytest 공용 픽스처. integration 테스트는 Neo4j 미가용 시 자동 skip."""
from __future__ import annotations

import pytest

from thoth import db


@pytest.fixture(scope="session")
def neo4j_available() -> bool:
    return db.healthcheck()


@pytest.fixture()
def graph(neo4j_available):
    """살아있는 Neo4j 세션. 없으면 skip."""
    if not neo4j_available:
        pytest.skip("Neo4j 미가용 — `make up && make wait-neo4j` 후 재실행")
    with db.session() as sess:
        yield sess
