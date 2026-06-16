"""WP1-5 엔티티 해소 테스트.

smoke 마커: normalize 함수 단위 테스트 (Neo4j 불필요).
integration 마커: 실제 Neo4j에서 병합 검증 (``graph`` 픽스처 필요).

AC (FR-1.2 Q4):
    표기 차이(공백·하이픈 삽입)가 있는 동일 엔티티는 정규화 후 단일 노드로 병합.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.smoke  # 기본은 smoke — integration은 개별 마킹


# ===========================================================================
# smoke: normalize 함수 단위 테스트 (외부 의존 없음)
# ===========================================================================

class TestNormalizeAccountNo:
    """계좌번호 정규화 — 하이픈·공백 제거 후 동일 문자열."""

    def test_hyphen_removed(self):
        from ingest.normalize import normalize_account_no
        assert normalize_account_no("011-1234-567890") == "0111234567890"

    def test_space_removed(self):
        from ingest.normalize import normalize_account_no
        assert normalize_account_no("011 1234 567890") == "0111234567890"

    def test_hyphen_and_space_same_result(self):
        """표기만 다른 동일 계좌번호가 같은 정규화 값을 가져야 함 (AC 핵심)."""
        from ingest.normalize import normalize_account_no
        a = normalize_account_no("123-456-789")
        b = normalize_account_no("123456789")
        assert a == b == "123456789"

    def test_empty_returns_empty(self):
        from ingest.normalize import normalize_account_no
        assert normalize_account_no("") == ""
        assert normalize_account_no(None) == ""


class TestNormalizeVin:
    """차대번호 정규화 — 대문자, 하이픈·공백 제거."""

    def test_lowercase_normalized(self):
        from ingest.normalize import normalize_vin
        assert normalize_vin("kmhdn41bp3u123456") == "KMHDN41BP3U123456"

    def test_hyphen_removed(self):
        from ingest.normalize import normalize_vin
        assert normalize_vin("kmhdn41bp3u-123456") == "KMHDN41BP3U123456"

    def test_space_and_case_same_result(self):
        """표기 차이 있는 동일 VIN → 동일 정규화 값 (AC 핵심)."""
        from ingest.normalize import normalize_vin
        a = normalize_vin("KMHDN41BP3U 123456")
        b = normalize_vin("kmhdn41bp3u123456")
        assert a == b == "KMHDN41BP3U123456"

    def test_empty_returns_empty(self):
        from ingest.normalize import normalize_vin
        assert normalize_vin(None) == ""


class TestNormalizePhone:
    """전화번호 정규화 — 숫자만 추출."""

    def test_hyphen_removed(self):
        from ingest.normalize import normalize_phone
        assert normalize_phone("010-1234-5678") == "01012345678"

    def test_space_removed(self):
        from ingest.normalize import normalize_phone
        assert normalize_phone("010 1234 5678") == "01012345678"

    def test_hyphen_and_space_same_result(self):
        """표기 차이 있는 동일 전화번호 → 동일 정규화 값 (AC 핵심)."""
        from ingest.normalize import normalize_phone
        a = normalize_phone("010-7581-1635")
        b = normalize_phone("01075811635")
        c = normalize_phone("010 7581 1635")
        assert a == b == c == "01075811635"

    def test_empty_returns_empty(self):
        from ingest.normalize import normalize_phone
        assert normalize_phone(None) == ""


class TestPhonePiiHash:
    """Phone.number_hash — 동일 번호는 동일 해시, 평문은 해시에 포함되지 않음."""

    def test_same_number_same_hash(self):
        """동일 번호(표기만 다름) → 동일 number_hash → 단일 Phone 노드 병합 보장."""
        from ingest.normalize import normalize_phone
        from ingest.pii import hash_pii

        salt = "test-salt"
        h1 = hash_pii(normalize_phone("010-7581-1635"), salt=salt)
        h2 = hash_pii(normalize_phone("01075811635"), salt=salt)
        assert h1 == h2
        assert len(h1) == 64  # sha256 hex = 64자

    def test_hash_not_contains_plaintext(self):
        """해시 결과에 평문 번호가 포함되지 않음."""
        from ingest.normalize import normalize_phone
        from ingest.pii import hash_pii

        phone = "01075811635"
        h = hash_pii(normalize_phone(phone), salt="test-salt")
        assert phone not in h

    def test_different_numbers_different_hash(self):
        from ingest.normalize import normalize_phone
        from ingest.pii import hash_pii

        salt = "test-salt"
        h1 = hash_pii(normalize_phone("010-1111-1111"), salt=salt)
        h2 = hash_pii(normalize_phone("010-2222-2222"), salt=salt)
        assert h1 != h2


class TestEntityResolutionStub:
    """ingest.entity_resolution 스텁 동작 확인."""

    def test_review_queue_empty_initially(self):
        from ingest.entity_resolution import clear_review_queue, get_review_queue
        clear_review_queue()
        assert get_review_queue() == []

    def test_enqueue_and_retrieve(self):
        from ingest.entity_resolution import (
            ReviewCandidate,
            clear_review_queue,
            enqueue_for_review,
            get_review_queue,
        )
        clear_review_queue()
        c = ReviewCandidate(
            entity_type="Account",
            candidate_a={"account_no": "123-456-789"},
            candidate_b={"account_no": "123 456 789"},
            score=0.9,
            reason="edit_distance=1 (separator only)",
        )
        enqueue_for_review(c)
        q = get_review_queue()
        assert len(q) == 1
        assert q[0].entity_type == "Account"
        clear_review_queue()

    def test_resolve_fuzzy_stub_returns_empty(self):
        """스텁은 빈 목록 반환 — 실제 퍼지 로직 없음."""
        from ingest.entity_resolution import resolve_fuzzy
        result = resolve_fuzzy("Account", [{"account_no": "123456"}])
        assert result == []


# ===========================================================================
# integration: 실제 Neo4j MERGE 병합 검증 (Neo4j 미가용 시 자동 skip)
# ===========================================================================

@pytest.mark.integration
def test_account_merge_strips_hyphen(graph):
    """account_no 표기 차이(하이픈 유무)가 정규화 후 단일 Account 노드로 병합됨.

    AC (FR-1.2 Q4): "123-456-789" 와 "123456789" 는 정규화 후 동일값이므로
    MERGE 로 단일 노드가 되어야 한다.
    """
    from ingest.normalize import normalize_account_no

    acc_no_a = normalize_account_no("TEST-ACC-001")
    acc_no_b = normalize_account_no("TESTACC001")

    # 두 정규화 값이 같을 때 MERGE 가 단일 노드를 만드는지 검증
    graph.run(
        "MERGE (a:Account {account_no: $no}) SET a.test_tag = 'er_test'",
        no=acc_no_a,
    )
    graph.run(
        "MERGE (a:Account {account_no: $no}) SET a.test_tag = 'er_test'",
        no=acc_no_b,
    )

    result = graph.run(
        "MATCH (a:Account {account_no: $no}) RETURN count(a) AS cnt",
        no=acc_no_a,
    ).single()
    assert result["cnt"] == 1, (
        f"동일 정규화 계좌번호 '{acc_no_a}' 가 단일 노드로 병합되지 않음"
    )

    # 정리
    graph.run(
        "MATCH (a:Account {account_no: $no}) DELETE a",
        no=acc_no_a,
    )


@pytest.mark.integration
def test_phone_node_has_no_plaintext(graph):
    """Phone 노드에 평문 number 속성이 없고 number_hash(64자)만 존재함.

    WP1-6 (FR-1.3): PII 평문 미저장 AC.
    """
    result = graph.run(
        "MATCH (p:Phone) RETURN p.number AS num, p.number_hash AS nh LIMIT 5"
    ).data()

    for row in result:
        assert row["num"] is None, (
            f"Phone 노드에 평문 number 가 존재함: {row['num']}"
        )
        if row["nh"] is not None:
            assert len(row["nh"]) == 64, (
                f"number_hash 가 64자 hex 가 아님: {row['nh']}"
            )
