"""WP1 데이터 코어 수용기준(AC) 종합 테스트.

FR-1.1 멱등 적재  : 재적재 후 노드/엣지 수 불변
FR-1.3 PII 미저장 : Phone.number 평문 없음, Customer 평문 name/ssn/email 없음
FR-2.1 스키마      : UNIQUE 제약 9종, 주요 인덱스 존재
엣지 완전성        : 11종 엣지 모두 1건 이상
사기 링 ground truth: ring_id 보유 Claim 존재, WITNESSED_BY 교차목격 관계 존재
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# 1. 스키마 검증 (FR-2.1)
# ---------------------------------------------------------------------------

NODE_LABELS_EXPECTED = {
    "Customer",
    "Claim",
    "Policy",
    "Vehicle",
    "Account",
    "Phone",
    "Hospital",
    "RepairShop",
    "Address",
}

# 각 노드 레이블에 대해 UNIQUE 제약이 반드시 존재해야 하는 (label, property) 쌍.
# schema.cypher 기준 — Phone 은 number_hash 를 병합키로 사용(WP1-6 FR-1.3).
UNIQUE_CONSTRAINTS_EXPECTED: set[tuple[str, str]] = {
    ("Customer",   "customer_id"),
    ("Claim",      "claim_id"),
    ("Policy",     "policy_id"),
    ("Vehicle",    "vin"),
    ("Account",    "account_no"),
    ("Phone",      "number_hash"),
    ("Address",    "address_id"),
    ("Hospital",   "hospital_id"),
    ("RepairShop", "shop_id"),
}

# 존재해야 하는 인덱스 이름 집합 (schema.cypher 에 정의된 명칭).
INDEXES_EXPECTED = {
    "claim_filed_at",
    "claim_risk_score",
    "claim_status",
    "customer_name_hash",
    "customer_dob",
    "policy_start_date",
    "policy_type",
    "vehicle_plate",
    "hospital_name",
    "repairshop_name",
}


def test_unique_constraints_exist(graph):
    """노드 9종에 UNIQUE 제약이 모두 존재함을 단언 (FR-2.1)."""
    rows = graph.run(
        "SHOW CONSTRAINTS YIELD type, labelsOrTypes, properties "
        "WHERE type = 'UNIQUENESS'"
    ).data()

    actual: set[tuple[str, str]] = set()
    for row in rows:
        label = row["labelsOrTypes"][0] if row["labelsOrTypes"] else ""
        prop = row["properties"][0] if row["properties"] else ""
        actual.add((label, prop))

    missing = UNIQUE_CONSTRAINTS_EXPECTED - actual
    assert not missing, (
        f"누락된 UNIQUE 제약: {missing}\n"
        f"현재 제약 목록: {actual}"
    )


def test_indexes_exist(graph):
    """주요 인덱스가 ONLINE 상태로 존재함을 단언 (FR-2.1)."""
    rows = graph.run(
        "SHOW INDEXES YIELD name, state "
        "WHERE state = 'ONLINE'"
    ).data()

    actual_names = {row["name"] for row in rows}
    missing = INDEXES_EXPECTED - actual_names
    assert not missing, (
        f"누락된 인덱스: {missing}\n"
        f"현재 ONLINE 인덱스: {actual_names}"
    )


# ---------------------------------------------------------------------------
# 2. 멱등성 검증 (FR-1.1)
# ---------------------------------------------------------------------------

def _count_nodes(graph) -> int:
    """그래프 전체 노드 수 반환."""
    rec = graph.run("MATCH (n) RETURN count(n) AS cnt").single()
    return rec["cnt"]


def _count_edges(graph) -> int:
    """그래프 전체 엣지 수 반환."""
    rec = graph.run("MATCH ()-[r]->() RETURN count(r) AS cnt").single()
    return rec["cnt"]


def test_idempotent_reload(graph):
    """동일 데이터 재적재 후 노드/엣지 총수가 변하지 않음 (FR-1.1 멱등성 AC).

    ingest.loader.load() 를 직접 호출하여 MERGE 멱등을 검증한다.
    테스트 완료 후 그래프 상태는 재적재 전과 동일 (중복 0 보장).
    """
    from pathlib import Path
    from ingest.loader import load

    data_dir = Path(__file__).parent.parent / "data" / "synthetic"

    nodes_before = _count_nodes(graph)
    edges_before = _count_edges(graph)

    # 동일 데이터 재적재
    counts = load(data_dir)

    nodes_after = _count_nodes(graph)
    edges_after = _count_edges(graph)

    assert nodes_after == nodes_before, (
        f"재적재 후 노드 수 변동: {nodes_before} → {nodes_after} "
        f"(증가 {nodes_after - nodes_before}개) — MERGE 멱등 실패"
    )
    assert edges_after == edges_before, (
        f"재적재 후 엣지 수 변동: {edges_before} → {edges_after} "
        f"(증가 {edges_after - edges_before}개) — MERGE 멱등 실패"
    )

    # loader 가 처리 건수를 정상 반환했는지 확인
    assert isinstance(counts, dict), "load() 가 dict 를 반환하지 않음"
    assert counts.get("node:Customer", 0) > 0, "Customer 적재 건수 0"
    assert counts.get("edge:FILED", 0) > 0, "FILED 엣지 적재 건수 0"


# ---------------------------------------------------------------------------
# 3. 엣지 완전성 (11종)
# ---------------------------------------------------------------------------

EDGE_TYPES_EXPECTED = [
    "FILED",
    "HOLDS",
    "COVERS",
    "INVOLVES",
    "TREATED_AT",
    "REPAIRED_AT",
    "PAID_TO",
    "LIVES_AT",
    "OWNS",
    "HAS_PHONE",
    "WITNESSED_BY",
]


@pytest.mark.parametrize("edge_type", EDGE_TYPES_EXPECTED)
def test_edge_type_exists(graph, edge_type: str):
    """엣지 11종이 각각 1건 이상 존재함을 단언."""
    rec = graph.run(
        f"MATCH ()-[r:{edge_type}]->() RETURN count(r) AS cnt"
    ).single()
    cnt = rec["cnt"]
    assert cnt > 0, (
        f"엣지 유형 {edge_type!r} 가 그래프에 존재하지 않음 (cnt=0)"
    )


# ---------------------------------------------------------------------------
# 4. PII 미저장 (FR-1.3)
# ---------------------------------------------------------------------------

def test_phone_node_has_no_plaintext_number(graph):
    """Phone 노드에 평문 number 속성이 없고 number_hash(64자 hex)만 존재함 (FR-1.3).

    WP1-6 가명처리 AC:
    - p.number 는 NULL 이어야 함 (평문 전화번호 미저장)
    - p.number_hash 는 64자 sha256 hex 이어야 함
    """
    rows = graph.run(
        "MATCH (p:Phone) RETURN p.number AS num, p.number_hash AS nh"
    ).data()

    assert rows, "Phone 노드가 존재하지 않음 — 적재 확인 필요"

    for row in rows:
        assert row["num"] is None, (
            f"Phone 노드에 평문 number 속성이 존재함: {row['num']!r}"
        )
        assert row["nh"] is not None, (
            "Phone 노드에 number_hash 가 없음 — PII 해시 미적재"
        )
        assert len(row["nh"]) == 64, (
            f"number_hash 가 64자 sha256 hex 가 아님: {row['nh']!r} (len={len(row['nh'])})"
        )


def test_customer_has_no_plaintext_pii(graph):
    """Customer 노드에 평문 name/ssn/email 속성이 없음 (FR-1.3).

    허용 속성: name_hash, id_hash, email_hash (sha256), gender, dob, created_at,
              customer_id, is_fraud_ring, ring_id
    금지 속성: name, ssn, id_number, email (평문)
    """
    # 평문 속성이 하나라도 있는 Customer 를 찾는다.
    rows_name = graph.run(
        "MATCH (c:Customer) WHERE c.name IS NOT NULL RETURN count(c) AS cnt"
    ).single()
    rows_email = graph.run(
        "MATCH (c:Customer) WHERE c.email IS NOT NULL RETURN count(c) AS cnt"
    ).single()
    rows_id = graph.run(
        "MATCH (c:Customer) WHERE c.id_number IS NOT NULL RETURN count(c) AS cnt"
    ).single()

    assert rows_name["cnt"] == 0, (
        f"Customer 노드 {rows_name['cnt']}건에 평문 name 속성이 존재함"
    )
    assert rows_email["cnt"] == 0, (
        f"Customer 노드 {rows_email['cnt']}건에 평문 email 속성이 존재함"
    )
    assert rows_id["cnt"] == 0, (
        f"Customer 노드 {rows_id['cnt']}건에 평문 id_number(ssn) 속성이 존재함"
    )


def test_customer_has_hash_properties(graph):
    """Customer 노드에 name_hash / id_hash / email_hash 가 존재함을 단언 (FR-1.3 해시 저장 확인)."""
    rows = graph.run(
        "MATCH (c:Customer) "
        "WHERE c.name_hash IS NOT NULL "
        "  AND c.id_hash IS NOT NULL "
        "  AND c.email_hash IS NOT NULL "
        "RETURN count(c) AS cnt"
    ).single()
    total = graph.run("MATCH (c:Customer) RETURN count(c) AS cnt").single()

    assert rows["cnt"] == total["cnt"], (
        f"name_hash/id_hash/email_hash 가 모두 있는 Customer: {rows['cnt']}건 "
        f"/ 전체 Customer: {total['cnt']}건 — 일부 해시 누락"
    )


# ---------------------------------------------------------------------------
# 5. 사기 링 ground truth (WP2 준비)
# ---------------------------------------------------------------------------

def test_fraud_ring_claims_exist(graph):
    """ring_id 를 가진 Claim 이 1건 이상 존재함 (WP2 탐지 기반 검증)."""
    rec = graph.run(
        "MATCH (c:Claim) "
        "WHERE c.ring_id IS NOT NULL AND c.ring_id <> '' "
        "RETURN count(c) AS cnt"
    ).single()
    assert rec["cnt"] > 0, (
        "ring_id 가 설정된 Claim 이 없음 — 합성 데이터 사기 링 주입 확인 필요"
    )


def test_fraud_ring_distinct_count(graph):
    """distinct ring_id 가 1개 이상 존재함 — 다중 링 시나리오 확인."""
    rec = graph.run(
        "MATCH (c:Claim) "
        "WHERE c.ring_id IS NOT NULL AND c.ring_id <> '' "
        "RETURN count(DISTINCT c.ring_id) AS ring_cnt"
    ).single()
    assert rec["ring_cnt"] >= 1, (
        f"distinct ring 수가 0 — 사기 링 데이터 없음"
    )


def test_witnessed_by_edges_exist(graph):
    """WITNESSED_BY (Claim→Claim) 교차목격 관계가 1건 이상 존재함.

    crash-for-cash 탐지의 핵심 엣지 — WP2 FR-3.3 탐지 쿼리 기반.
    """
    rec = graph.run(
        "MATCH (a:Claim)-[:WITNESSED_BY]->(b:Claim) RETURN count(*) AS cnt"
    ).single()
    assert rec["cnt"] > 0, (
        "WITNESSED_BY 엣지가 존재하지 않음 — claims.witness_claim_ids 주입 확인 필요"
    )


def test_cross_witnessed_ring_pattern(graph):
    """교차목격(상호 WITNESSED_BY) 패턴이 1쌍 이상 존재함 — crash-for-cash AC.

    (Claim A)-[:WITNESSED_BY]->(Claim B) 이고
    (Claim B)-[:WITNESSED_BY]->(Claim A) 인 쌍이 있어야 한다.
    """
    rec = graph.run(
        "MATCH (a:Claim)-[:WITNESSED_BY]->(b:Claim)-[:WITNESSED_BY]->(a) "
        "RETURN count(*) AS cnt"
    ).single()
    assert rec["cnt"] > 0, (
        "양방향 교차목격 (Claim A ↔ Claim B) 이 없음 — "
        "crash-for-cash ring 시나리오 데이터 확인 필요"
    )
