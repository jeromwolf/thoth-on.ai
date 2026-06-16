"""탐지 쿼리 실행 래퍼 (WP2 · FR-3.1~3.3).

``detection/03_fraud_queries.cypher`` 의 Q1~Q3 패턴을 파라미터화해 실행하고,
결과를 dict 리스트로 반환한다. 모든 쿼리는 순수 cypher 패턴 매칭이며 GDS 를
사용하지 않는다(GDS 는 WP3).

[주입 링 패턴 — ingest/synth_generator.py 기준]
    · 동일 Account(account_no) 공유  → Q1 (공유 엔티티)
    · 동일 Hospital + RepairShop 집중 → Q2 (핫스팟)
    · 상호 WITNESSED_BY 교차 목격     → Q3 (crash-for-cash 순환)

탐지 함수:
    run_shared_entities()  — Q1: 공유 Account/Phone/Address/Vehicle 군집
    run_hotspots()         — Q2: 병원/정비소/계좌 청구 집중 엔티티
    run_crash_rings()      — Q3: 상호 교차 목격 군집(링 핵심)
"""
from __future__ import annotations

from typing import Any

from thoth import db

# ------------------------------------------------------------------
# 기본 임계치 — data/synthetic_test 분포 측정값에 근거한 합리적 기본값.
#   · 공유 계좌/전화/차량은 2명만 공유해도 강한 신호(정상 배경 0건).
#   · 주소 공유는 배경 노이즈가 많아(2인 공유 214건) 약한 신호로 둔다.
#   · 핫스팟은 정상 대형 병원도 100건+ → 단순 청구수 대신 "서로 다른 고객 수"
#     기준을 쓰고, 분포 p99 근처를 기본 임계치로 잡는다.
# ------------------------------------------------------------------
DEFAULT_MIN_CUSTOMERS = 2             # Q1 공유 최소 고객 수
DEFAULT_HOSPITAL_MIN_CUSTOMERS = 130  # Q2 병원 핫스팟(distinct 고객) — 분포 p90 근처
DEFAULT_SHOP_MIN_CUSTOMERS = 100      # Q2 정비소 핫스팟 — 분포 p95 근처
DEFAULT_ACCOUNT_MIN_CUSTOMERS = 2     # Q2 계좌 핫스팟 — 다수 고객 동일 계좌(강한 신호)
DEFAULT_MIN_CLUSTER = 2               # Q3 군집 최소 고객 수


# ==================================================================
# Q1 — 공유 엔티티 탐지 (FR-3.1)
# ==================================================================
_Q1_ACCOUNT = """
MATCH (c:Customer)-[:FILED]->(:Claim)-[:PAID_TO]->(a:Account)
WITH a, collect(DISTINCT c) AS customers
WHERE size(customers) >= $min_customers
RETURN 'ACCOUNT' AS shared_type,
       a.account_no AS shared_key,
       [x IN customers | x.customer_id] AS customer_ids,
       size(customers) AS num_customers,
       [x IN customers | x.ring_id] AS ring_ids
ORDER BY num_customers DESC
"""

_Q1_PHONE = """
MATCH (c:Customer)-[:HAS_PHONE]->(p:Phone)
WITH p, collect(DISTINCT c) AS customers
WHERE size(customers) >= $min_customers
RETURN 'PHONE' AS shared_type,
       p.number_hash AS shared_key,
       [x IN customers | x.customer_id] AS customer_ids,
       size(customers) AS num_customers,
       [x IN customers | x.ring_id] AS ring_ids
ORDER BY num_customers DESC
"""

_Q1_ADDRESS = """
MATCH (c:Customer)-[:LIVES_AT]->(a:Address)
WITH a, collect(DISTINCT c) AS customers
WHERE size(customers) >= $min_customers
RETURN 'ADDRESS' AS shared_type,
       a.address_id AS shared_key,
       [x IN customers | x.customer_id] AS customer_ids,
       size(customers) AS num_customers,
       [x IN customers | x.ring_id] AS ring_ids
ORDER BY num_customers DESC
"""

_Q1_VEHICLE = """
MATCH (c:Customer)-[:OWNS]->(v:Vehicle)
WITH v, collect(DISTINCT c) AS customers
WHERE size(customers) >= $min_customers
RETURN 'VEHICLE' AS shared_type,
       v.vin AS shared_key,
       [x IN customers | x.customer_id] AS customer_ids,
       size(customers) AS num_customers,
       [x IN customers | x.ring_id] AS ring_ids
ORDER BY num_customers DESC
"""


def run_shared_entities(
    *,
    min_customers: int = DEFAULT_MIN_CUSTOMERS,
    include_address: bool = True,
) -> list[dict[str, Any]]:
    """Q1 공유 엔티티 탐지(FR-3.1).

    동일 Account / Phone / Address / Vehicle 를 ``min_customers`` 명 이상
    공유하는 고객 군집을 반환한다.

    Args:
        min_customers: 공유로 간주할 최소 고객 수.
        include_address: 주소 공유(약한 신호)를 결과에 포함할지 여부.

    Returns:
        각 군집을 나타내는 dict 리스트. 키: ``shared_type``, ``shared_key``,
        ``customer_ids``, ``num_customers``, ``ring_ids``.
    """
    params = {"min_customers": min_customers}
    results: list[dict[str, Any]] = []
    results.extend(db.run(_Q1_ACCOUNT, **params))
    results.extend(db.run(_Q1_PHONE, **params))
    if include_address:
        results.extend(db.run(_Q1_ADDRESS, **params))
    results.extend(db.run(_Q1_VEHICLE, **params))
    return results


# ==================================================================
# Q2 — 엔티티 핫스팟 (FR-3.2)
# ==================================================================
_Q2_HOSPITAL = """
MATCH (c:Customer)-[:FILED]->(cl:Claim)-[:TREATED_AT]->(h:Hospital)
WITH h, count(DISTINCT cl) AS num_claims, collect(DISTINCT c) AS customers
WHERE size(customers) >= $hospital_min
RETURN 'HOSPITAL' AS entity_type,
       h.hospital_id AS entity_id,
       h.name AS entity_name,
       num_claims,
       size(customers) AS num_customers,
       [x IN customers | x.customer_id] AS customer_ids
ORDER BY num_customers DESC
"""

_Q2_SHOP = """
MATCH (c:Customer)-[:FILED]->(cl:Claim)-[:REPAIRED_AT]->(s:RepairShop)
WITH s, count(DISTINCT cl) AS num_claims, collect(DISTINCT c) AS customers
WHERE size(customers) >= $shop_min
RETURN 'REPAIR_SHOP' AS entity_type,
       s.shop_id AS entity_id,
       s.name AS entity_name,
       num_claims,
       size(customers) AS num_customers,
       [x IN customers | x.customer_id] AS customer_ids
ORDER BY num_customers DESC
"""

_Q2_ACCOUNT = """
MATCH (c:Customer)-[:FILED]->(cl:Claim)-[:PAID_TO]->(a:Account)
WITH a, count(DISTINCT cl) AS num_claims, collect(DISTINCT c) AS customers
WHERE size(customers) >= $account_min
RETURN 'ACCOUNT' AS entity_type,
       a.account_no AS entity_id,
       a.bank_name AS entity_name,
       num_claims,
       size(customers) AS num_customers,
       [x IN customers | x.customer_id] AS customer_ids
ORDER BY num_customers DESC
"""


def run_hotspots(
    *,
    hospital_min: int = DEFAULT_HOSPITAL_MIN_CUSTOMERS,
    shop_min: int = DEFAULT_SHOP_MIN_CUSTOMERS,
    account_min: int = DEFAULT_ACCOUNT_MIN_CUSTOMERS,
) -> list[dict[str, Any]]:
    """Q2 엔티티 핫스팟 탐지(FR-3.2).

    병원·정비소·계좌에 청구가 임계치 이상(서로 다른 고객 수 기준) 집중된
    엔티티를 반환한다. 정상 대형 병원도 청구가 많으므로 단순 청구 수가 아니라
    distinct 고객 수를 임계로 사용한다.

    Args:
        hospital_min: 병원 핫스팟 임계(서로 다른 고객 수).
        shop_min: 정비소 핫스팟 임계.
        account_min: 계좌 핫스팟 임계(다수 고객 동일 계좌).

    Returns:
        핫스팟 엔티티 dict 리스트. 키: ``entity_type``, ``entity_id``,
        ``entity_name``, ``num_claims``, ``num_customers``, ``customer_ids``.
    """
    results: list[dict[str, Any]] = []
    results.extend(db.run(_Q2_HOSPITAL, hospital_min=hospital_min))
    results.extend(db.run(_Q2_SHOP, shop_min=shop_min))
    results.extend(db.run(_Q2_ACCOUNT, account_min=account_min))
    return results


# ==================================================================
# Q3 — crash-for-cash 순환 (FR-3.3) — 링 탐지 핵심
# ==================================================================
_Q3_PAIRS = """
MATCH (cust1:Customer)-[:FILED]->(c1:Claim)-[:WITNESSED_BY]->(c2:Claim)<-[:FILED]-(cust2:Customer)
WHERE cust1 <> cust2
  AND (c2)-[:WITNESSED_BY]->(c1)
  AND cust1.customer_id < cust2.customer_id
RETURN cust1.customer_id AS customer_a,
       cust2.customer_id AS customer_b,
       c1.claim_id AS claim_a,
       c2.claim_id AS claim_b,
       cust1.ring_id AS ring_a,
       cust2.ring_id AS ring_b
ORDER BY customer_a, customer_b
"""

_Q3_CLUSTERS = """
MATCH (cust:Customer)-[:FILED]->(c1:Claim)-[:WITNESSED_BY]->(c2:Claim)<-[:FILED]-(peer:Customer)
WHERE (c2)-[:WITNESSED_BY]->(c1) AND peer <> cust
WITH cust, collect(DISTINCT peer.customer_id) AS peers
WITH cust, peers, ([cust.customer_id] + peers) AS members
WHERE size(members) >= $min_cluster
RETURN cust.customer_id AS seed_customer,
       cust.ring_id AS ring_id,
       members,
       size(members) AS cluster_size
ORDER BY cluster_size DESC, seed_customer
"""


def run_crash_rings(
    *,
    min_cluster: int = DEFAULT_MIN_CLUSTER,
) -> list[dict[str, Any]]:
    """Q3 crash-for-cash 순환 탐지(FR-3.3) — 링 군집 반환.

    상호 WITNESSED_BY(서로의 사고를 양방향 교차 목격)하는 고객을 군집으로
    묶어 반환한다. 각 seed 고객과 그가 교차 목격으로 연결된 동료 고객 목록을
    포함한다.

    Args:
        min_cluster: 군집 최소 고객 수(seed 포함).

    Returns:
        군집 dict 리스트. 키: ``seed_customer``, ``ring_id``, ``members``,
        ``cluster_size``.
    """
    return db.run(_Q3_CLUSTERS, min_cluster=min_cluster)


def run_crash_ring_pairs() -> list[dict[str, Any]]:
    """Q3 보조 — 상호 교차 목격 고객 쌍(설명가능성 경로용).

    Returns:
        쌍 dict 리스트. 키: ``customer_a``, ``customer_b``, ``claim_a``,
        ``claim_b``, ``ring_a``, ``ring_b``.
    """
    return db.run(_Q3_PAIRS)
