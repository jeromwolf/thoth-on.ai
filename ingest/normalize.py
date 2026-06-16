"""엔티티 해소 정규화 규칙 (FR-1.2 / Q4 결정적 완전일치).

mapping.md §5.2 정규화 규칙을 그대로 구현한다. 정규화 후 동일 값이면
Neo4j MERGE 로 단일 노드 병합되어 공유 엔티티 관계가 그래프에 드러난다.

함수:
    normalize_account_no(s)  계좌번호: 하이픈·공백·슬래시 제거
    normalize_vin(s)         차대번호: 대문자, 공백·하이픈 제거
    normalize_phone(s)       전화번호: 숫자만 추출
    normalize_address(s)     주소: 공백·특수문자 제거, 소문자화
"""
from __future__ import annotations

import re


def normalize_account_no(value: str | None) -> str:
    """계좌번호 정규화: 하이픈·공백·슬래시 등 비영숫자 제거.

    예) ``011-1234-567890`` → ``0111234567890``
    """
    if not value:
        return ""
    return re.sub(r"[^0-9A-Za-z]", "", value)


def normalize_vin(value: str | None) -> str:
    """차대번호(VIN) 정규화: 대문자 변환, 공백·하이픈 제거.

    예) ``kmhdn41bp3u-123456`` → ``KMHDN41BP3U123456``
    """
    if not value:
        return ""
    return re.sub(r"[^0-9A-Za-z]", "", value).upper()


def normalize_phone(value: str | None) -> str:
    """전화번호 정규화: 숫자만 추출.

    예) ``010-1234-5678`` → ``01012345678``
    """
    if not value:
        return ""
    return re.sub(r"\D", "", value)


def normalize_address(value: str | None) -> str:
    """주소 정규화: 공백·특수문자 제거, 소문자화.

    예) ``서울시 강남구 테헤란로 123 2층`` → ``서울시강남구테헤란로1232층``
    한글·영숫자만 남기고 나머지(공백·구두점)는 제거한 뒤 소문자화한다.
    """
    if not value:
        return ""
    # 공백 및 구두점/특수문자 제거 (한글·영숫자만 유지)
    cleaned = re.sub(r"[\s\W_]", "", value, flags=re.UNICODE)
    return cleaned.lower()
