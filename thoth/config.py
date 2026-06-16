"""환경설정 (.env 로드). FR 전반에서 공유."""
from __future__ import annotations

import os
from dataclasses import dataclass

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # python-dotenv 미설치 시에도 동작
    pass


@dataclass(frozen=True)
class Settings:
    neo4j_uri: str = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    neo4j_user: str = os.getenv("NEO4J_USER", "neo4j")
    neo4j_password: str = os.getenv("NEO4J_PASSWORD", "thothpass")
    neo4j_database: str = os.getenv("NEO4J_DATABASE", "neo4j")

    pii_salt: str = os.getenv("THOTH_PII_SALT", "change-me-in-prod")

    # API 서버 (WP5). 흔한 8000/8080 회피 — THOT 폰 키패드(T8·H4·O6·T8) 유래 8468.
    api_host: str = os.getenv("THOTH_API_HOST", "127.0.0.1")
    api_port: int = int(os.getenv("THOTH_API_PORT", "8468"))

    llm_provider: str = os.getenv("THOTH_LLM_PROVIDER", "mock")
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    ollama_base_url: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    ollama_model: str = os.getenv("OLLAMA_MODEL", "qwen2.5:14b")


def get_settings() -> Settings:
    return Settings()
