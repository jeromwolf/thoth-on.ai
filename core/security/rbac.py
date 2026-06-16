"""RBAC 골격 (NFR). 보험 도메인 역할 + 데이터 등급 기반 접근 결정.

WP0: 인메모리 정책으로 day-one 활성화. WP6에서 그래프 네이티브로 확장.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum


class Role(IntEnum):
    """숫자가 클수록 높은 권한."""

    PUBLIC = 0
    CLAIMS_ADJUSTER = 1   # 청구 심사역
    FRAUD_ANALYST = 2     # SIU 조사관
    RISK_MANAGER = 3      # 리스크팀
    ADMIN = 4


class DataClass(IntEnum):
    """데이터 민감도 등급 (요구 최소 권한)."""

    PUBLIC = 0
    CLAIMS = 1            # 일반 청구 데이터
    FRAUD_CASE = 2        # 사기 의심 케이스
    PII = 3              # 가명처리 전 개인정보
    ADMIN = 4


@dataclass(frozen=True)
class AccessDecision:
    allowed: bool
    reason: str


def check_access(role: Role, data_class: DataClass) -> AccessDecision:
    """역할 권한이 데이터 등급 요구치 이상이면 허용."""
    if int(role) >= int(data_class):
        return AccessDecision(True, f"{role.name} >= {data_class.name}")
    return AccessDecision(
        False, f"{role.name}(권한 {int(role)}) < {data_class.name}(요구 {int(data_class)})"
    )
