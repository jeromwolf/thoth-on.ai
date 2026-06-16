"""설명가능성 패키지 (WP4-3 · FR-5.2) — 차별화 핵심.

경로 데이터를 자연어 소명문으로 생성하는 LLM provider 추상화(``provider``)와
경로 grounding·환각 가드를 포함한 설명 생성기(``explainer``)를 제공한다.
"""
from __future__ import annotations

from explain.explainer import (
    Explanation,
    GroundingResult,
    explain_case,
    generate_explanation,
    verify_grounding,
)
from explain.provider import (
    AnthropicProvider,
    LLMProvider,
    MockProvider,
    OllamaProvider,
    OpenAIProvider,
    get_provider,
)

__all__ = [
    "LLMProvider",
    "MockProvider",
    "AnthropicProvider",
    "OpenAIProvider",
    "OllamaProvider",
    "get_provider",
    "Explanation",
    "GroundingResult",
    "explain_case",
    "generate_explanation",
    "verify_grounding",
]
