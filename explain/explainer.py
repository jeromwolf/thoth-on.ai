"""자연어 근거문 생성 + 환각 가드 (WP4-3 · FR-5.2) — 차별화 핵심.

경로 데이터(``detection.paths.build_paths``) + 온톨로지 컨텍스트 → LLM provider
로 자연어 소명문을 생성하고, **환각 가드**로 "소명문이 인용한 엔티티가 모두 실재
경로 데이터에 존재하는지" 대조 검증한다. 경로에 없는 엔티티(고객ID·계좌·정비소
등)를 인용하면 검증 실패로 표시·거부한다 — 제품 신뢰의 핵심.

[환각 가드 동작 — 형식적 검사가 아님]
    1. 경로 데이터에서 **인용 가능한 실재 엔티티 id 집합**을 모은다
       (customer_id / account_no / shop_id / hospital_id / vin / address_id /
       phone_hash). 마스킹된 표시 라벨은 원본·마스킹 양쪽을 허용.
    2. 생성된 소명문에서 **엔티티 id 패턴**(CUST-xxxxx, RSH-xxxx, HSP-xxxx,
       account/phone 숫자열 등)을 추출한다.
    3. 추출된 인용 엔티티가 실재 집합에 **모두 포함**되는지 대조.
       하나라도 실재 집합에 없으면 → grounded=False (거부).

ground 집합에 없는 가짜 엔티티를 인용한 소명문은 절대 통과하지 못한다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

from detection import paths as path_builder
from explain.provider import PATH_DATA_MARKER, LLMProvider, get_provider
import json

# 도메인 엔티티 식별자 패턴(합성 데이터 컨벤션 기반).
# 한국어가 식별자에 곧바로 붙는 경우(예: "CUST-99999가")가 많아 ASCII \b 워드
# 바운더리를 쓰면 매칭이 깨진다. 따라서 앞쪽만 비식별자(또는 시작)로 막고,
# 뒤쪽은 더 긴 식별자 문자(영숫자·하이픈)가 이어지지 않도록만 제한한다.
#   CUST-05014 / RSH-0089 / HSP-0012 / RING-003 등 PREFIX-숫자
_ID_TOKEN = re.compile(r"(?<![A-Za-z0-9])([A-Z]{2,5}-\d{2,})(?![A-Za-z0-9-])")
#   마스킹 토큰: 053***12 형태(앞2~4 + *** + 끝2~4). 원본 키와 대조. (숫자보다 먼저 검사)
_MASKED_TOKEN = re.compile(r"(?<![\d*])(\d{2,4})\*{2,}(\d{2,4})(?![\d*])")
#   계좌/전화 등 6자리 이상 연속 숫자(마스킹 전 원본).
_NUM_TOKEN = re.compile(r"(?<![\d*])(\d{6,})(?![\d*])")


@dataclass
class GroundingResult:
    """환각 가드 검증 결과."""

    grounded: bool                                  # 모든 인용 엔티티가 실재하는가
    cited_entities: list[str] = field(default_factory=list)   # 소명문이 인용한 엔티티
    known_entities: list[str] = field(default_factory=list)   # 실재 경로 엔티티
    hallucinated: list[str] = field(default_factory=list)     # 실재하지 않는 인용(위반)

    def to_dict(self) -> dict[str, Any]:
        return {
            "grounded": self.grounded,
            "cited_entities": self.cited_entities,
            "hallucinated": self.hallucinated,
            "num_known": len(self.known_entities),
        }


@dataclass
class Explanation:
    """케이스 1건의 자연어 소명문 + grounding 검증 결과."""

    customer_id: str
    text: str
    provider: str
    grounding: GroundingResult
    paths: list[dict[str, Any]] = field(default_factory=list)

    @property
    def accepted(self) -> bool:
        """환각 가드를 통과한(거부되지 않은) 소명문인지."""
        return self.grounding.grounded

    def to_dict(self) -> dict[str, Any]:
        return {
            "customer_id": self.customer_id,
            "text": self.text,
            "provider": self.provider,
            "accepted": self.accepted,
            "grounding": self.grounding.to_dict(),
        }


# ==================================================================
# 프롬프트 빌드
# ==================================================================
_SYSTEM_GUIDE = """당신은 보험 사기 조사 보조 AI 입니다. 아래 '의심 관계 경로' 데이터에
실제로 등장하는 엔티티(고객ID·계좌·정비소·병원 등)만 인용하여, 왜 이 고객이 의심되는지
간결한 한국어 소명문을 작성하세요. 경로 데이터에 없는 엔티티를 절대 지어내지 마세요.
"""


def build_prompt(customer_id: str, paths: list[dict[str, Any]]) -> str:
    """LLM 프롬프트 생성 — 온톨로지 가이드 + 경로 JSON 임베드.

    MockProvider 가 파싱할 수 있도록 ``PATH_DATA_MARKER`` 뒤에 경로 JSON 을 넣는다.
    """
    path_json = json.dumps(paths, ensure_ascii=False)
    return (
        f"{_SYSTEM_GUIDE}\n"
        f"대상 고객: {customer_id}\n"
        f"의심 관계 경로 수: {len(paths)}\n\n"
        f"{PATH_DATA_MARKER}\n{path_json}\n"
    )


# ==================================================================
# 환각 가드 (FR-5.2 AC) — 실제 엔티티 대조 검증
# ==================================================================
def _known_entity_set(paths: list[dict[str, Any]]) -> set[str]:
    """경로 데이터에서 인용 허용 엔티티 id 집합 + 마스킹 변형을 구성."""
    base = path_builder.collect_entities(paths)
    known: set[str] = set(base)
    # 마스킹 표시 라벨(앞3+***+끝2)도 허용 토큰으로 추가.
    for key in list(base):
        if key.isdigit() and len(key) > 4:
            known.add(f"{key[:3]}***{key[-2:]}")
    return known


def _extract_cited_entities(text: str) -> list[str]:
    """소명문에서 인용된 엔티티 토큰을 추출(ID·숫자열·마스킹)."""
    cited: list[str] = []
    cited.extend(_ID_TOKEN.findall(text))
    cited.extend(_NUM_TOKEN.findall(text))
    for prefix, suffix in _MASKED_TOKEN.findall(text):
        cited.append(f"{prefix}***{suffix}")
    # 중복 제거(순서 보존).
    seen: set[str] = set()
    uniq: list[str] = []
    for c in cited:
        if c not in seen:
            seen.add(c)
            uniq.append(c)
    return uniq


def verify_grounding(text: str, paths: list[dict[str, Any]]) -> GroundingResult:
    """환각 가드(FR-5.2 AC): 소명문 인용 엔티티가 모두 실재 경로에 있는지 검증.

    소명문에서 추출한 엔티티 토큰 중 실재 경로 엔티티 집합에 없는 것이 하나라도
    있으면 ``grounded=False`` (거부). 인용 엔티티가 전혀 없으면(순수 서술) 통과로
    본다(인용 위반이 없으므로). 실재 집합과 마스킹 변형을 모두 허용한다.

    Args:
        text: 검증할 소명문.
        paths: 근거 경로 데이터(``build_paths`` 출력).

    Returns:
        ``GroundingResult`` — grounded 여부 + 인용/실재/환각 엔티티 목록.
    """
    known = _known_entity_set(paths)
    cited = _extract_cited_entities(text)
    hallucinated = [c for c in cited if c not in known]
    return GroundingResult(
        grounded=len(hallucinated) == 0,
        cited_entities=cited,
        known_entities=sorted(known),
        hallucinated=hallucinated,
    )


# ==================================================================
# 설명 생성 (provider + grounding)
# ==================================================================
def generate_explanation(
    customer_id: str,
    paths: list[dict[str, Any]],
    *,
    provider: Optional[LLMProvider] = None,
) -> Explanation:
    """경로 데이터로부터 소명문을 생성하고 환각 가드를 적용(FR-5.2).

    Args:
        customer_id: 대상 고객 ID.
        paths: 근거 관계 경로(``detection.paths.build_paths`` 출력).
        provider: LLM provider. None 이면 env 기반 ``get_provider()``.

    Returns:
        생성 소명문 + grounding 검증 결과를 담은 ``Explanation``.
        ``accepted == False`` 이면 환각 가드에 의해 거부된 소명문이다.
    """
    prov = provider or get_provider()
    prompt = build_prompt(customer_id, paths)
    text = prov.generate(prompt)
    grounding = verify_grounding(text, paths)
    return Explanation(
        customer_id=customer_id,
        text=text,
        provider=prov.name,
        grounding=grounding,
        paths=paths,
    )


def explain_case(
    customer_id: str,
    signals: list[dict[str, Any]],
    *,
    provider: Optional[LLMProvider] = None,
) -> Explanation:
    """기여 신호로부터 경로를 만들고 소명문을 생성하는 원스톱 헬퍼.

    케이스에 첨부된 ``signals`` (scoring) → 경로 빌드 → 소명문 + 환각 가드.
    """
    paths = path_builder.build_paths(customer_id, signals)
    return generate_explanation(customer_id, paths, provider=provider)
