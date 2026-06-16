"""가명처리(PII 해시) 헬퍼 (FR-1.3).

PII 원문은 그래프에 평문 저장 금지. 저장 방식은 ``sha256(SALT + 원문)``,
SALT 는 환경변수 ``THOTH_PII_SALT`` 로 주입(thoth.config 경유).

mapping.md §6 가명처리 대상:
    Customer.name_hash, Customer.id_hash, Customer.email_hash,
    Phone.number_hash, Account.account_no_hash, Account.holder_hash

가명처리는 WP1-6 의 정식 책임이나, 적재 단계에서 평문 PII 가
그래프에 들어가지 않도록 loader 가 이 헬퍼를 사용해 해시 후 저장한다.
"""
from __future__ import annotations

import hashlib

from thoth.config import get_settings


def hash_pii(value: str | None, *, salt: str | None = None) -> str:
    """PII 원문을 ``sha256(salt + value)`` 16진 문자열로 가명처리.

    Args:
        value: 평문 PII (None/빈값이면 빈 문자열 반환).
        salt: 명시 salt. 미지정 시 ``THOTH_PII_SALT`` 사용.

    Returns:
        64자 16진 해시 문자열. 입력이 비면 ``""``.
    """
    if value is None or value == "":
        return ""
    effective_salt = salt if salt is not None else get_settings().pii_salt
    digest = hashlib.sha256((effective_salt + value).encode("utf-8"))
    return digest.hexdigest()
