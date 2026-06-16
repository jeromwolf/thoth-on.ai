"""LLM provider 추상화 (WP4-3 · FR-5.2 / PRD §7 Q3).

설명 LLM 을 provider 추상화로 둔다(API=anthropic/openai, 폐쇄망=ollama sLLM).
``THOTH_LLM_PROVIDER`` env 로 스위칭하며, **API 키가 없으면 결정적 MockProvider 로
자동 fallback** 하여 네트워크 없이도 동작·테스트 가능하다.

[설계]
    · 공통 인터페이스: ``LLMProvider.generate(prompt) -> str``.
    · MockProvider(기본): 프롬프트에 포함된 구조화 경로 데이터를 템플릿으로
      자연어화. 결정적(동일 입력 → 동일 출력) · 네트워크 불필요. 테스트 기본.
    · Anthropic/OpenAI/Ollama: 실제 API 호출 코드를 작성하되 패키지/키 부재 시
      MockProvider 로 우아하게 fallback.

MockProvider 는 단순 패스스루가 아니라, 프롬프트에 임베드된 경로 JSON 을 파싱해
실제 엔티티만 인용하는 소명문을 만든다 → 환각 가드 통과를 보장(결정적 grounding).
"""
from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from typing import Any, Optional

from thoth.config import Settings, get_settings

# MockProvider 가 프롬프트에서 경로 데이터를 추출할 때 사용하는 마커.
PATH_DATA_MARKER = "===PATH_DATA_JSON==="


class LLMProvider(ABC):
    """LLM provider 공통 인터페이스. 구현은 ``generate`` 만 제공하면 된다."""

    name: str = "base"

    @abstractmethod
    def generate(self, prompt: str) -> str:
        """프롬프트로부터 자연어 소명문을 생성해 반환."""
        raise NotImplementedError


# ==================================================================
# MockProvider — 기본·결정적·테스트용
# ==================================================================
class MockProvider(LLMProvider):
    """경로 데이터를 템플릿으로 자연어화하는 결정적 provider.

    프롬프트에 ``PATH_DATA_MARKER`` 로 임베드된 경로 JSON 을 파싱하여, **경로에
    실재하는 엔티티만** 인용하는 소명문을 만든다. 네트워크 불필요. 동일 입력 →
    동일 출력(결정적). LLM 환각 가드의 신뢰 baseline.
    """

    name = "mock"

    def generate(self, prompt: str) -> str:
        paths = _extract_path_data(prompt)
        if not paths:
            return "탐지된 의심 관계 경로가 없어 소명문을 생성할 수 없습니다."
        return _render_korean_summary(paths)


def _extract_path_data(prompt: str) -> list[dict[str, Any]]:
    """프롬프트에서 ``PATH_DATA_MARKER`` 뒤의 경로 JSON 블록을 추출·파싱."""
    idx = prompt.find(PATH_DATA_MARKER)
    if idx == -1:
        return []
    tail = prompt[idx + len(PATH_DATA_MARKER):].strip()
    # 첫 JSON 배열만 취득(마커 이후 첫 [ ... ] 매칭).
    match = re.search(r"\[.*\]", tail, re.DOTALL)
    if not match:
        return []
    try:
        data = json.loads(match.group(0))
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, ValueError):
        return []


def _render_korean_summary(paths: list[dict[str, Any]]) -> str:
    """경로 목록을 한국어 소명문으로 결정적 렌더링.

    경로에 등장하는 customer_id/엔티티 id 만 인용 → 환각 가드 통과 보장.
    """
    lines: list[str] = []
    for p in paths:
        stype = p.get("signal_type", "")
        members = p.get("members", [])
        if stype.startswith("SHARED_"):
            label = p.get("label", "공유")
            key = p.get("shared_key", "")
            masked = _mask_inline(key, p.get("entity_type", ""))
            who = "·".join(members)
            lines.append(
                f"고객 {who}은(는) {label}({masked})을(를) 공유합니다."
            )
        elif stype == "CROSS_WITNESS":
            who = "·".join(members)
            lines.append(
                f"고객 {who}은(는) 서로의 사고를 교차 목격했습니다(crash-for-cash 순환)."
            )
        elif stype.startswith("HOTSPOT_"):
            ename = p.get("entity_name", p.get("entity_id", ""))
            cust = p.get("nodes", [{}])[0].get("id", "")
            lines.append(
                f"고객 {cust}은(는) 다수 고객이 집중 이용하는 핫스팟({ename})을(를) 이용했습니다."
            )
        elif stype.startswith("GDS_"):
            cust = p.get("nodes", [{}])[0].get("id", "")
            lines.append(
                f"고객 {cust}은(는) 그래프 구조 분석상 동일 사기 커뮤니티 군집에 속합니다."
            )
    return " ".join(lines)


def _mask_inline(key: str, entity_type: str) -> str:
    if entity_type in {"Account", "Phone"} and len(str(key)) > 4:
        key = str(key)
        return key[:3] + "***" + key[-2:]
    return str(key)


# ==================================================================
# AnthropicProvider
# ==================================================================
class AnthropicProvider(LLMProvider):
    """Anthropic Claude API provider. 키/패키지 부재 시 Mock 로 fallback."""

    name = "anthropic"

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self._settings = settings or get_settings()
        self._fallback = MockProvider()
        self._client = None
        if self._settings.anthropic_api_key:
            try:  # pragma: no cover - 네트워크/패키지 의존
                import anthropic  # type: ignore

                self._client = anthropic.Anthropic(
                    api_key=self._settings.anthropic_api_key
                )
            except Exception:
                self._client = None

    def generate(self, prompt: str) -> str:
        if self._client is None:
            return self._fallback.generate(prompt)
        try:  # pragma: no cover - 네트워크 의존
            resp = self._client.messages.create(
                model="claude-3-5-sonnet-latest",
                max_tokens=512,
                messages=[{"role": "user", "content": prompt}],
            )
            return "".join(
                block.text for block in resp.content if hasattr(block, "text")
            ).strip()
        except Exception:
            return self._fallback.generate(prompt)


# ==================================================================
# OpenAIProvider
# ==================================================================
class OpenAIProvider(LLMProvider):
    """OpenAI API provider. 키/패키지 부재 시 Mock 로 fallback."""

    name = "openai"

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self._settings = settings or get_settings()
        self._fallback = MockProvider()
        self._client = None
        if self._settings.openai_api_key:
            try:  # pragma: no cover - 네트워크/패키지 의존
                from openai import OpenAI  # type: ignore

                self._client = OpenAI(api_key=self._settings.openai_api_key)
            except Exception:
                self._client = None

    def generate(self, prompt: str) -> str:
        if self._client is None:
            return self._fallback.generate(prompt)
        try:  # pragma: no cover - 네트워크 의존
            resp = self._client.chat.completions.create(
                model="gpt-4o-mini",
                max_tokens=512,
                messages=[{"role": "user", "content": prompt}],
            )
            return (resp.choices[0].message.content or "").strip()
        except Exception:
            return self._fallback.generate(prompt)


# ==================================================================
# OllamaProvider (폐쇄망 sLLM)
# ==================================================================
class OllamaProvider(LLMProvider):
    """Ollama 로컬 sLLM provider(폐쇄망). 서버 미가용 시 Mock 로 fallback."""

    name = "ollama"

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self._settings = settings or get_settings()
        self._fallback = MockProvider()

    def generate(self, prompt: str) -> str:
        try:  # pragma: no cover - 네트워크 의존
            import urllib.request

            payload = json.dumps(
                {
                    "model": self._settings.ollama_model,
                    "prompt": prompt,
                    "stream": False,
                }
            ).encode("utf-8")
            req = urllib.request.Request(
                f"{self._settings.ollama_base_url}/api/generate",
                data=payload,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            text = (body.get("response") or "").strip()
            return text or self._fallback.generate(prompt)
        except Exception:
            return self._fallback.generate(prompt)


# ==================================================================
# 팩토리
# ==================================================================
_PROVIDERS: dict[str, type[LLMProvider]] = {
    "mock": MockProvider,
    "anthropic": AnthropicProvider,
    "openai": OpenAIProvider,
    "ollama": OllamaProvider,
}


def get_provider(
    name: Optional[str] = None, *, settings: Optional[Settings] = None
) -> LLMProvider:
    """``THOTH_LLM_PROVIDER`` (또는 인자) 에 따라 provider 를 생성.

    알 수 없는 이름이면 MockProvider 로 fallback. Mock 은 인자 불필요,
    그 외는 settings 를 주입한다.
    """
    settings = settings or get_settings()
    key = (name or settings.llm_provider or "mock").lower()
    cls = _PROVIDERS.get(key, MockProvider)
    if cls is MockProvider:
        return MockProvider()
    return cls(settings=settings)  # type: ignore[call-arg]
