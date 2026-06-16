"""WP0 스모크: 외부 의존성 없이 패키지·설정·보안 골격 검증."""
import pytest

pytestmark = pytest.mark.smoke


def test_package_imports():
    import thoth
    from thoth import config, db  # noqa: F401

    assert thoth.__version__ == "0.2.0"


def test_settings_defaults():
    from thoth.config import get_settings

    s = get_settings()
    assert s.neo4j_uri.startswith("bolt://")
    assert s.neo4j_user == "neo4j"
    assert s.llm_provider in {"mock", "anthropic", "openai", "ollama"}


def test_cypher_statement_split():
    from thoth.db import _split_statements

    text = "// comment\nCREATE (a);\nCREATE (b);\n"
    stmts = _split_statements(text)
    assert stmts == ["CREATE (a)", "CREATE (b)"]


def test_rbac_allows_and_denies():
    from core.security.rbac import AccessDecision, DataClass, Role, check_access

    ok = check_access(Role.FRAUD_ANALYST, DataClass.FRAUD_CASE)
    assert isinstance(ok, AccessDecision) and ok.allowed

    denied = check_access(Role.CLAIMS_ADJUSTER, DataClass.PII)
    assert not denied.allowed


def test_audit_append(tmp_path):
    from core.security.audit import audit_event

    log_file = tmp_path / "audit.jsonl"
    audit_event("case.update", actor="u1", target="C-1", path=log_file)
    audit_event("graph.query", actor="u1", result="denied", path=log_file)

    lines = log_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert '"action": "case.update"' in lines[0]
    assert '"result": "denied"' in lines[1]
