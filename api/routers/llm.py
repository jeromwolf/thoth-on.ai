"""LLM provider 상태 라우터 (폐쇄망 운영 가시성).

활성 LLM provider(ollama/mock/anthropic/openai) 의 도달 가능성·생성 준비 상태를
인증 없이 조회한다(헬스체크와 동일 정책 — 운영 모니터링 엔드포인트).
"""
from __future__ import annotations

from fastapi import APIRouter

from api.schemas import LlmStatusResponse
from explain.provider import probe_provider

router = APIRouter(prefix="/llm", tags=["llm"])


@router.get(
    "/status",
    response_model=LlmStatusResponse,
    summary="활성 LLM provider 상태 조회",
    description=(
        "활성 LLM provider(ollama/mock/anthropic/openai)의 도달 가능성·"
        "생성 모델 설치 여부·fallback 상태를 반환한다. 인증 불필요."
    ),
)
def llm_status() -> LlmStatusResponse:
    """폐쇄망 운영: ollama 서버·모델 설치 상태, fallback 여부를 실시간 점검."""
    data = probe_provider()
    return LlmStatusResponse(**data)
