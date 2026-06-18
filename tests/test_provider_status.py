"""probe_provider + OllamaProvider fallback 테스트 (WP4-3 하드닝).

@pytest.mark.smoke — Neo4j 불필요. 모든 네트워크 호출은 monkeypatch.
"""
from __future__ import annotations

import io
import json
from unittest.mock import MagicMock, patch

import pytest

from explain.provider import PATH_DATA_MARKER, OllamaProvider, probe_provider
from thoth.config import Settings


# ---------------------------------------------------------------------------
# 헬퍼: settings 픽스처 생성
# ---------------------------------------------------------------------------

def _ollama_settings(model: str = "qwen2.5:14b") -> Settings:
    return Settings(
        llm_provider="ollama",
        ollama_base_url="http://localhost:11434",
        ollama_model=model,
    )


def _fake_urlopen_tags(models: list[str]):
    """urllib.request.urlopen 을 모의하는 컨텍스트 매니저 팩토리.

    /api/tags 응답을 반환. /api/generate 는 호출되지 않는다고 가정.
    """
    body = json.dumps({"models": [{"name": m} for m in models]}).encode("utf-8")
    response = MagicMock()
    response.read.return_value = body
    response.__enter__ = lambda s: s
    response.__exit__ = MagicMock(return_value=False)
    return response


# ---------------------------------------------------------------------------
# probe_provider — ollama 케이스
# ---------------------------------------------------------------------------

@pytest.mark.smoke
def test_probe_ollama_generation_ready(monkeypatch):
    """설정 모델이 /api/tags 목록에 있으면 generation_ready=True, fallback=False."""
    settings = _ollama_settings("qwen2.5:14b")
    fake_resp = _fake_urlopen_tags(["qwen2.5:14b", "nomic-embed-text:latest"])

    with patch("urllib.request.urlopen", return_value=fake_resp):
        result = probe_provider(settings=settings)

    assert result["provider"] == "ollama"
    assert result["generation_ready"] is True
    assert result["fallback_to_mock"] is False
    assert result["reachable"] is True
    assert "qwen2.5:14b" in result["models_available"]


@pytest.mark.smoke
def test_probe_ollama_model_missing(monkeypatch):
    """설정 모델이 목록에 없으면 generation_ready=False, fallback=True."""
    settings = _ollama_settings("qwen2.5:14b")
    # 임베딩 전용 모델만 설치된 상황 (실 머신 시나리오)
    fake_resp = _fake_urlopen_tags(["nomic-embed-text:latest"])

    with patch("urllib.request.urlopen", return_value=fake_resp):
        result = probe_provider(settings=settings)

    assert result["generation_ready"] is False
    assert result["fallback_to_mock"] is True
    assert result["reachable"] is True
    assert "qwen2.5:14b" not in result["models_available"]


@pytest.mark.smoke
def test_probe_ollama_unreachable(monkeypatch):
    """urlopen 예외 시 reachable=False, fallback=True."""
    settings = _ollama_settings("qwen2.5:14b")

    with patch("urllib.request.urlopen", side_effect=OSError("connection refused")):
        result = probe_provider(settings=settings)

    assert result["reachable"] is False
    assert result["fallback_to_mock"] is True
    assert result["generation_ready"] is False


# ---------------------------------------------------------------------------
# probe_provider — mock 케이스
# ---------------------------------------------------------------------------

@pytest.mark.smoke
def test_probe_mock_provider():
    """llm_provider=mock 이면 fallback_to_mock=False(Mock 이 의도된 기본)."""
    settings = Settings(llm_provider="mock")
    result = probe_provider(settings=settings)

    assert result["provider"] == "mock"
    assert result["fallback_to_mock"] is False


# ---------------------------------------------------------------------------
# OllamaProvider.generate — urlopen 예외 시 Mock fallback
# ---------------------------------------------------------------------------

@pytest.mark.smoke
def test_ollama_generate_fallback_on_network_error():
    """OllamaProvider.generate 가 urlopen 예외 시 MockProvider 결과를 반환."""
    settings = _ollama_settings()
    provider = OllamaProvider(settings=settings)

    # PATH_DATA_MARKER 가 포함된 간단 경로 JSON 으로 Mock 이 소명문을 생성하는지 확인.
    path_data = json.dumps([
        {
            "signal_type": "SHARED_ACCOUNT",
            "label": "공유 계좌",
            "members": ["C001", "C002"],
            "shared_key": "ACC-999",
            "entity_type": "Account",
        }
    ])
    prompt = f"경로 데이터:\n{PATH_DATA_MARKER}\n{path_data}"

    with patch("urllib.request.urlopen", side_effect=OSError("timeout")):
        result = provider.generate(prompt)

    # Mock fallback 이 생성한 텍스트여야 하므로 빈 문자열이 아니어야 함.
    assert isinstance(result, str)
    assert len(result) > 0
    # Mock 은 경로에 등장하는 엔티티(C001, C002)를 인용한다.
    assert "C001" in result or "C002" in result
