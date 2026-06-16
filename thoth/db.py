"""Neo4j 드라이버 래퍼 + 간단 CLI.

사용:
    python -m thoth.db wait                  # 헬스 대기
    python -m thoth.db apply <file.cypher>   # 스크립트 적용 (세미콜론 분리)
    python -m thoth.db reset                 # 전체 노드/관계 삭제
    python -m thoth.db ping                  # 1회 연결 확인 (0/1 종료코드)
"""
from __future__ import annotations

import re
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from neo4j import Driver, GraphDatabase

from thoth.config import get_settings


def get_driver() -> Driver:
    s = get_settings()
    return GraphDatabase.driver(s.neo4j_uri, auth=(s.neo4j_user, s.neo4j_password))


@contextmanager
def session() -> Iterator[Any]:
    drv = get_driver()
    try:
        with drv.session(database=get_settings().neo4j_database) as sess:
            yield sess
    finally:
        drv.close()


def run(cypher: str, **params: Any) -> list[dict]:
    """단일 쿼리 실행 후 레코드 리스트 반환."""
    with session() as sess:
        return [r.data() for r in sess.run(cypher, **params)]


def healthcheck() -> bool:
    try:
        with session() as sess:
            sess.run("RETURN 1 AS ok").single()
        return True
    except Exception:
        return False


def wait_until_ready(timeout: float = 120.0, interval: float = 3.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if healthcheck():
            return True
        time.sleep(interval)
    return False


def has_gds() -> bool:
    """GDS 플러그인 사용 가능 여부."""
    try:
        rows = run("RETURN gds.version() AS v")
        return bool(rows and rows[0].get("v"))
    except Exception:
        return False


_STMT_SPLIT = re.compile(r";\s*(?:\n|$)")


def _split_statements(text: str) -> list[str]:
    # 줄 주석(//) 제거 후 세미콜론 분리
    lines = [ln for ln in text.splitlines() if not ln.strip().startswith("//")]
    body = "\n".join(lines)
    return [s.strip() for s in _STMT_SPLIT.split(body) if s.strip()]


def apply_file(path: str | Path) -> int:
    """.cypher 파일을 세미콜론 단위로 적용. 적용된 문장 수 반환."""
    text = Path(path).read_text(encoding="utf-8")
    stmts = _split_statements(text)
    with session() as sess:
        for stmt in stmts:
            sess.run(stmt)
    return len(stmts)


def reset_graph() -> None:
    run("MATCH (n) DETACH DELETE n")


def _main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    cmd = argv[0]
    if cmd == "wait":
        ok = wait_until_ready()
        print("neo4j ready" if ok else "neo4j NOT ready")
        return 0 if ok else 1
    if cmd == "ping":
        ok = healthcheck()
        print("ok" if ok else "fail")
        return 0 if ok else 1
    if cmd == "apply":
        if len(argv) < 2:
            print("usage: python -m thoth.db apply <file.cypher>")
            return 2
        n = apply_file(argv[1])
        print(f"applied {n} statements from {argv[1]}")
        return 0
    if cmd == "reset":
        reset_graph()
        print("graph reset")
        return 0
    print(f"unknown command: {cmd}")
    return 2


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
