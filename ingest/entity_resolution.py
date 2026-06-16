"""엔티티 해소 모듈 — WP1-5 (FR-1.2 Q4).

결정적 완전일치(Deterministic Exact-Match) 자동 병합은 ingest/normalize.py 의
정규화 함수 + Neo4j MERGE 로 loader.py 에서 이미 처리된다.

이 모듈은 결정적 해소로 처리할 수 없는 **퍼지 매칭(Fuzzy Match)** 후보를
'조사관 확인 큐(review queue)' 로 분리하는 스텁을 제공한다.
실제 큐 구현은 V1 마일스톤에서 수행한다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ReviewCandidate:
    """퍼지 매칭 후보 — 자동 병합 불가, 조사관 확인 필요.

    Attributes:
        entity_type: 엔티티 유형 (예: ``"Account"``, ``"Vehicle"``, ``"Phone"``).
        candidate_a: 후보 레코드 A (소스 원본 dict).
        candidate_b: 후보 레코드 B (소스 원본 dict).
        score: 유사도 점수 (0.0–1.0). 1.0이면 완전일치(자동 병합 대상).
        reason: 퍼지 매칭 근거 설명.
    """

    entity_type: str
    candidate_a: dict[str, Any]
    candidate_b: dict[str, Any]
    score: float
    reason: str


# ---------------------------------------------------------------------------
# 조사관 확인 큐 (인메모리 스텁 — V1에서 영구 저장소로 교체)
# ---------------------------------------------------------------------------
_REVIEW_QUEUE: list[ReviewCandidate] = []


def enqueue_for_review(candidate: ReviewCandidate) -> None:
    """퍼지 매칭 후보를 조사관 확인 큐에 추가한다.

    V1 구현 전 스텁: 인메모리 리스트에 추가만 한다. 실제 구현 시에는
    Neo4j ``ReviewQueue`` 노드 또는 외부 메시지 큐(Kafka/SQS)로 발행한다.

    Args:
        candidate: 자동 병합 불가 판정을 받은 엔티티 쌍.
    """
    _REVIEW_QUEUE.append(candidate)


def get_review_queue() -> list[ReviewCandidate]:
    """현재 인메모리 확인 큐를 반환한다.

    Returns:
        조사관 검토 대기 중인 ``ReviewCandidate`` 목록.
    """
    return list(_REVIEW_QUEUE)


def clear_review_queue() -> None:
    """테스트 전용 — 인메모리 큐를 비운다."""
    _REVIEW_QUEUE.clear()


# ---------------------------------------------------------------------------
# 퍼지 해소 진입점 스텁 (V1 구현 예정)
# ---------------------------------------------------------------------------
def resolve_fuzzy(
    entity_type: str,
    records: list[dict[str, Any]],
    *,
    threshold: float = 0.85,
) -> list[ReviewCandidate]:
    """퍼지 매칭으로 동일 엔티티 후보를 탐색, 확인 큐에 등록한다.

    .. note::
        **V1 구현 전 스텁입니다.** 현재는 빈 목록을 반환하며 부작용이 없습니다.
        실제 구현 시 다음 전략을 사용한다:

        - ``Account.account_no``: edit-distance ≤ 2 이면 후보 등록.
        - ``Vehicle.vin``: 17자 중 14자 이상 일치하면 후보 등록.
        - ``Phone.number``: 앞 8자리 일치 + 뒤 4자리 차이 ≤ 1이면 후보 등록.

        score ≥ threshold 이면 자동 병합을 ``제안``하고,
        score < threshold 이면 조사관 확인 큐에만 추가한다.
        자동 병합 실행 여부는 항상 조사관 승인 후에만 확정한다 (FR-1.2 Q4 준수).

    Args:
        entity_type: 엔티티 유형 레이블 (예: ``"Account"``).
        records:     정규화 이전 소스 레코드 목록.
        threshold:   자동 병합 제안 유사도 임계값 (기본 0.85).

    Returns:
        이번 호출에서 새로 등록된 ``ReviewCandidate`` 목록.
    """
    # TODO(WP-V1): 퍼지 매칭 구현
    return []
