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

# ------------------------------------------------------------------
# 정상 공유 구분 / 시간 군집 임계 — data/synthetic_test 실측 근거.
#   [공유 군집 주소 동일성 — 가족 vs 사기]
#     · 정상 가족: 같은 계좌/전화/차량 공유 시 **주소도 같다**(distinct_addr/n ≈ 0.45,
#       소수 인원·같은 주소). 약한(=양성) 공유 신호로 취급.
#     · 사기 링: 같은 계좌 공유하나 **주소는 모두 다르다**(distinct_addr/n = 1.0,
#       서로 무관한 다수 인원). 강한 공유 신호.
#   [청구 시간 군집 — 가족 vs 사기]
#     · 가족 공유 계좌 군집의 청구 incident_date span ≈ 262일(연중 분산).
#     · 사기 링 청구 span ≈ 10.7일(짧은 기간 집중) — base_date+0~18일 구조.
#     → span 이 좁을수록 강한 사기 신호(crash-for-cash 의 동시 청구 특성).
# ------------------------------------------------------------------
DEFAULT_TIME_CLUSTER_DAYS = 30        # 공유 군집 청구가 이 일수 이내면 "시간 군집"(강)
DEFAULT_HOTSPOT_CLUSTER_DAYS = 21     # 집중 핫스팟(병원+정비소) 시간 군집 창
DEFAULT_HOTSPOT_MAX_CUSTOMERS = 8     # 집중 핫스팟 군집 최대 고객(소수 집중만)
# 담합(collusion) 핫스팟 — 집중 핫스팟보다 엄격한 조건(고정밀 신호):
#   동일 (병원+정비소) + 소수(2~4) + 매우 짧은 기간(<=14일) + 사고유형 단일(collision).
#   실측: 이 조건의 군집은 fraud:normal ≈ 26:6 (정밀 ~81%) — 약신호 링(hotspot_only/
#   weak) 회수에 유효. crash-for-cash 의 "동일 충돌 동시 청구" 특성을 포착.
DEFAULT_COLLUSION_CLUSTER_DAYS = 14
DEFAULT_COLLUSION_MAX_CUSTOMERS = 4
# 인기 대형 병원/정비소(정상 운영량 baseline) — synth_generator 가 상위 N 곳에
# 정상 청구를 자연 집중시킨다(병원 6곳, 정비소 8곳). 이들은 단순 건수가 많아도
# 정상이므로 "집중 핫스팟" 판정에서 제외해 기대치 대비 편차만 본다.
POPULAR_HOSPITAL_IDS = [f"HOSP-{i:04d}" for i in range(1, 7)]
POPULAR_SHOP_IDS = [f"RSH-{i:04d}" for i in range(1, 9)]

# ------------------------------------------------------------------
# WP-KR 한국 실제 사기 수법 탐지 임계 — synth_generator 주입 구조 근거.
#   fake_admission_star: 비인기 병원 1곳에 소수~다수 환자(>=5) 짧은 기간 집중 +
#       브로커 알선 허브. → 병원 star 신호 + 브로커 허브 신호.
#   agent_fraud        : 설계사 1명이 다수 계약 모집(>=5) + 다수 고객 청구금이
#       소수 공통 계좌로 집중. → 설계사 허브 신호.
#   repair_overbill    : 비인기 정비소 1곳에 다수 고객(>=4) + 청구금액 이상(p99↑).
# ------------------------------------------------------------------
DEFAULT_STAR_MIN_PATIENTS = 10        # 허위입원 star: 시간창 내 환자 집중 최소
DEFAULT_STAR_CLUSTER_DAYS = 28        # star 시간창(동시 모객 — 0~24일 주입 구조)
STAR_EXCESS_FACTOR = 3.0              # 시간창 환자수가 연중 기대밀도의 N배 초과 시 star
                                      #   (병원 단독 burst 신호는 corroborating — 주
                                      #    신호는 브로커 허브. 정상 트래픽이 균등해
                                      #    병원 단독 분리는 한계가 있음 — 정직 보고)
DEFAULT_BROKER_MIN_CUSTOMERS = 12     # 브로커 허브: 한 병원 집중 알선 고객 최소 수
                                      #   (정상 브로커 병원당 최대 ~11명과 분리)
DEFAULT_AGENT_MIN_SHARED_CUSTOMERS = 4  # 설계사+공통계좌로 묶인 고객 최소 수(가로채기)
DEFAULT_OVERBILL_MIN_CUSTOMERS = 4    # 정비비 과다청구: 정비소 반복 고객 최소
DEFAULT_OVERBILL_AMOUNT = 11_000_000  # 정비비 과다청구 금액 임계(정상 0.8~8M 상회)


# ==================================================================
# Q1 — 공유 엔티티 탐지 (FR-3.1)  — 정상(가족) 공유 구분 컨텍스트 포함
# ------------------------------------------------------------------
# 각 공유 군집에 대해 두 가지 구분 컨텍스트를 함께 산출한다:
#   · distinct_addresses / num_customers  → 주소 동일성(가족=낮음, 사기=1.0)
#   · time_span_days = 군집 청구 incident_date 의 최대-최소 일수(시간 군집)
# 이로써 "공유=의심"의 단순함을 깨고, 같은 계좌라도 가족(같은 주소·연중 분산)과
# 사기(다른 주소·짧은 기간 집중)를 점수 단계에서 차등화한다.
# ==================================================================

# 군집의 주소 동일성 + 청구 incident_date span(일)을 계산하는 공통 꼬리.
# 진입 시점에 ``shared_type``, ``shared_key``, ``customers`` 가 스코프에 있어야 한다.
#   · distinct_addresses: 군집 고객의 서로 다른 주소 수(가족=낮음, 사기=num_customers).
#   · time_span_days: 군집 청구 incident_date 의 (최대-최소) 일수(청구<2면 -1).
_SHARE_CONTEXT_TAIL = """
OPTIONAL MATCH (ac:Customer)-[:LIVES_AT]->(ad:Address) WHERE ac IN customers
WITH shared_type, shared_key, customers,
     count(DISTINCT ad.address_id) AS distinct_addresses
OPTIONAL MATCH (cc:Customer)-[:FILED]->(cl:Claim)
WHERE cc IN customers AND cl.incident_date IS NOT NULL
WITH shared_type, shared_key, customers, distinct_addresses,
     collect(date(cl.incident_date)) AS dts
RETURN shared_type,
       shared_key,
       [x IN customers | x.customer_id] AS customer_ids,
       size(customers) AS num_customers,
       [x IN customers | x.ring_id] AS ring_ids,
       distinct_addresses,
       CASE WHEN size(dts) >= 2 THEN duration.inDays(
              reduce(mn=date('2099-12-31'), d IN dts | CASE WHEN d<mn THEN d ELSE mn END),
              reduce(mx=date('1900-01-01'), d IN dts | CASE WHEN d>mx THEN d ELSE mx END)
            ).days
            ELSE -1 END AS time_span_days
ORDER BY num_customers DESC
"""

_Q1_ACCOUNT = """
MATCH (c:Customer)-[:FILED]->(:Claim)-[:PAID_TO]->(a:Account)
WITH a, collect(DISTINCT c) AS customers
WHERE size(customers) >= $min_customers
WITH 'ACCOUNT' AS shared_type, a.account_no AS shared_key, customers
""" + _SHARE_CONTEXT_TAIL

_Q1_PHONE = """
MATCH (c:Customer)-[:HAS_PHONE]->(p:Phone)
WITH p, collect(DISTINCT c) AS customers
WHERE size(customers) >= $min_customers
WITH 'PHONE' AS shared_type, p.number_hash AS shared_key, customers
""" + _SHARE_CONTEXT_TAIL

_Q1_VEHICLE = """
MATCH (c:Customer)-[:OWNS]->(v:Vehicle)
WITH v, collect(DISTINCT c) AS customers
WHERE size(customers) >= $min_customers
WITH 'VEHICLE' AS shared_type, v.vin AS shared_key, customers
""" + _SHARE_CONTEXT_TAIL

# 주소 공유는 그 자체가 "같은 주소" 군집이므로 distinct_addresses=1,
# 시간 군집 컨텍스트만 부착(가족 다수 → 약 신호).
_Q1_ADDRESS = """
MATCH (c:Customer)-[:LIVES_AT]->(a:Address)
WITH a, collect(DISTINCT c) AS customers
WHERE size(customers) >= $min_customers
OPTIONAL MATCH (cc:Customer)-[:FILED]->(cl:Claim)
WHERE cc IN customers AND cl.incident_date IS NOT NULL
WITH a, customers, collect(date(cl.incident_date)) AS dts
RETURN 'ADDRESS' AS shared_type,
       a.address_id AS shared_key,
       [x IN customers | x.customer_id] AS customer_ids,
       size(customers) AS num_customers,
       [x IN customers | x.ring_id] AS ring_ids,
       1 AS distinct_addresses,
       CASE WHEN size(dts) >= 2 THEN duration.inDays(
              reduce(mn=date('2099-12-31'), d IN dts | CASE WHEN d<mn THEN d ELSE mn END),
              reduce(mx=date('1900-01-01'), d IN dts | CASE WHEN d>mx THEN d ELSE mx END)
            ).days
            ELSE -1 END AS time_span_days
ORDER BY num_customers DESC
"""


def run_shared_entities(
    *,
    min_customers: int = DEFAULT_MIN_CUSTOMERS,
    include_address: bool = True,
) -> list[dict[str, Any]]:
    """Q1 공유 엔티티 탐지(FR-3.1) — 정상(가족) 공유 구분 컨텍스트 포함.

    동일 Account / Phone / Address / Vehicle 를 ``min_customers`` 명 이상
    공유하는 고객 군집을 반환한다. 각 군집에는 정상 공유(가족)와 사기 공유를
    점수 단계에서 차등화하기 위한 컨텍스트를 부착한다:

        · ``distinct_addresses``: 군집 고객의 서로 다른 주소 수. 가족은 같은
          주소(낮음), 사기 링은 모두 다른 주소(= num_customers).
        · ``time_span_days``: 군집 고객 청구의 incident_date 최대-최소 일수.
          가족은 연중 분산(큼), 사기 링은 짧은 기간 집중(작음). 청구가 1건
          이하면 -1.

    Args:
        min_customers: 공유로 간주할 최소 고객 수.
        include_address: 주소 공유(약한 신호)를 결과에 포함할지 여부.

    Returns:
        각 군집을 나타내는 dict 리스트. 키: ``shared_type``, ``shared_key``,
        ``customer_ids``, ``num_customers``, ``ring_ids``,
        ``distinct_addresses``, ``time_span_days``.
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


# ------------------------------------------------------------------
# Q2b — 집중(focused) 핫스팟 (FR-3.2 정밀판)
# ------------------------------------------------------------------
# 단순 청구 건수가 아니라 "기대치 대비 이상 집중"을 잡는다:
#   · 인기 대형 병원/정비소(정상 baseline)는 제외 — 정상 운영량은 신호 아님.
#   · 동일 (병원 AND 정비소) 쌍을 **소수(2~MAX)** distinct 고객이 공유하고,
#     그 청구들이 **짧은 기간(<= cluster_days)** 에 집중될 때만 의심.
#   · crash-for-cash 링은 같은 병원+정비소로 동시 청구가 몰린다(실측 span≈10일).
#   정상 배경(연중 무작위로 같은 병원+정비소를 우연히 함께 쓴 고객)은 시간
#   분산되므로 걸러진다. 단, 완전 분리는 안 되는 약 신호 → corroborating.
_Q2_FOCUSED = """
MATCH (c:Customer)-[:FILED]->(cl:Claim)-[:TREATED_AT]->(h:Hospital)
MATCH (cl)-[:REPAIRED_AT]->(s:RepairShop)
WHERE NOT h.hospital_id IN $popular_hospitals
  AND NOT s.shop_id IN $popular_shops
  AND cl.incident_date IS NOT NULL
WITH h, s, collect(DISTINCT c) AS customers, collect(date(cl.incident_date)) AS dts
WHERE size(customers) >= 2 AND size(customers) <= $max_customers
WITH h, s, customers, dts,
     duration.inDays(
       reduce(mn=date('2099-12-31'), d IN dts | CASE WHEN d<mn THEN d ELSE mn END),
       reduce(mx=date('1900-01-01'), d IN dts | CASE WHEN d>mx THEN d ELSE mx END)
     ).days AS span_days
WHERE span_days <= $cluster_days
RETURN 'FOCUSED_HOTSPOT' AS entity_type,
       h.hospital_id AS entity_id,
       h.name AS entity_name,
       s.shop_id AS shop_id,
       size(customers) AS num_customers,
       [x IN customers | x.customer_id] AS customer_ids,
       span_days
ORDER BY num_customers DESC, span_days ASC
"""


def run_focused_hotspots(
    *,
    max_customers: int = DEFAULT_HOTSPOT_MAX_CUSTOMERS,
    cluster_days: int = DEFAULT_HOTSPOT_CLUSTER_DAYS,
) -> list[dict[str, Any]]:
    """Q2b 집중 핫스팟 탐지(FR-3.2 정밀판) — corroborating 약 신호.

    인기 대형 병원/정비소(정상 baseline)를 제외하고, 동일 (병원, 정비소) 쌍을
    소수 고객이 짧은 기간에 함께 이용한 군집을 반환한다. 정상 대형 엔티티의
    단순 청구량은 신호로 보지 않으며, "기대치 대비 비정상 집중 + 소수 반복 +
    시간 군집"만 잡는다.

    Args:
        max_customers: 군집 최대 고객 수(소수 집중만; 대량은 정상 운영).
        cluster_days: 군집 청구 incident_date span 상한(일).

    Returns:
        dict 리스트. 키: ``entity_type``, ``entity_id``, ``entity_name``,
        ``shop_id``, ``num_customers``, ``customer_ids``, ``span_days``.
    """
    return db.run(
        _Q2_FOCUSED,
        popular_hospitals=POPULAR_HOSPITAL_IDS,
        popular_shops=POPULAR_SHOP_IDS,
        max_customers=max_customers,
        cluster_days=cluster_days,
    )


# ------------------------------------------------------------------
# Q2c — 담합(collusion) 핫스팟 (FR-3.2 고정밀판)
# ------------------------------------------------------------------
# 집중 핫스팟 조건에 더해 (a) 매우 짧은 기간(<=14일), (b) 사고유형이 단일
# 'collision' 인 군집만 잡는다. crash-for-cash 의 "동일 충돌 동시 청구" 특성으로
# 정상 우연 공유(다양한 사고유형·분산된 시점)와 구분한다. 약신호 링(hotspot_only/
# weak) 회수용 고정밀 신호(실측 정밀 ~81%).
_Q2_COLLUSION = """
MATCH (c:Customer)-[:FILED]->(cl:Claim)-[:TREATED_AT]->(h:Hospital)
MATCH (cl)-[:REPAIRED_AT]->(s:RepairShop)
WHERE NOT h.hospital_id IN $popular_hospitals
  AND NOT s.shop_id IN $popular_shops
  AND cl.incident_date IS NOT NULL
WITH h, s, collect(DISTINCT c) AS customers,
     collect(DISTINCT cl.incident_type) AS types,
     collect(date(cl.incident_date)) AS dts
WHERE size(customers) >= 2 AND size(customers) <= $max_customers
  AND size(types) = 1 AND types[0] = 'collision'
WITH h, s, customers,
     duration.inDays(
       reduce(mn=date('2099-12-31'), d IN dts | CASE WHEN d<mn THEN d ELSE mn END),
       reduce(mx=date('1900-01-01'), d IN dts | CASE WHEN d>mx THEN d ELSE mx END)
     ).days AS span_days
WHERE span_days <= $cluster_days
RETURN 'COLLUSION_HOTSPOT' AS entity_type,
       h.hospital_id AS entity_id,
       h.name AS entity_name,
       s.shop_id AS shop_id,
       size(customers) AS num_customers,
       [x IN customers | x.customer_id] AS customer_ids,
       span_days
ORDER BY num_customers DESC, span_days ASC
"""


def run_collusion_hotspots(
    *,
    max_customers: int = DEFAULT_COLLUSION_MAX_CUSTOMERS,
    cluster_days: int = DEFAULT_COLLUSION_CLUSTER_DAYS,
) -> list[dict[str, Any]]:
    """Q2c 담합 핫스팟 탐지(FR-3.2 고정밀판) — 약신호 링 회수용.

    비인기 (병원+정비소)를 소수가 매우 짧은 기간에 **단일 사고유형(collision)** 으로
    함께 청구한 군집을 반환한다. crash-for-cash 의 동시 충돌 청구 특성을 포착해
    hotspot_only/weak 수법을 정밀하게 회수한다.

    Args:
        max_customers: 군집 최대 고객 수.
        cluster_days: 군집 청구 incident_date span 상한(일).

    Returns:
        dict 리스트. 키: ``entity_type``, ``entity_id``, ``entity_name``,
        ``shop_id``, ``num_customers``, ``customer_ids``, ``span_days``.
    """
    return db.run(
        _Q2_COLLUSION,
        popular_hospitals=POPULAR_HOSPITAL_IDS,
        popular_shops=POPULAR_SHOP_IDS,
        max_customers=max_customers,
        cluster_days=cluster_days,
    )


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


# ==================================================================
# WP-KR — 한국 실제 사기 수법 탐지 (Q4~Q7)
# ==================================================================
# Q4 허위입원 star : 비인기 병원에 소수~다수 환자가 짧은 기간 집중(나이롱 환자).
# Q5 브로커 허브   : 한 브로커가 다수 고객을 알선(BROKERED).
# Q6 설계사 허브   : 한 설계사가 다수 계약 모집(SOLD_POLICY) + 고객 청구금이 소수
#                    공통 계좌로 집중(보험금 가로채기).
# Q7 정비비 과다청구: 비인기 정비소에 다수 고객 + 청구금액 이상(정상 상한 초과).
# 모두 distinct 고객 기준 + 비인기(정상 baseline 제외) 조건으로 정상 운영량을 배제.
# ==================================================================

# 비인기 병원별 (환자, 청구일) 목록을 가져와 Python 에서 "가장 빽빽한 시간창"을
# 찾는다. 정상 청구는 연중 분산되어 어떤 21~28일 창에도 소수만 모이지만, 허위입원
# star 는 0~24일에 10~30명이 몰리므로 한 시간창에 비정상적으로 많은 distinct 환자가
# 잡힌다(나이롱 환자 동시 모객). 단순 "병원 총 환자수"는 정상 병원도 100+ 이라
# 신호가 안 되므로 반드시 시간창 집중으로 본다.
_Q4_STAR_RAW = """
MATCH (c:Customer)-[:FILED]->(cl:Claim)-[:TREATED_AT]->(h:Hospital)
WHERE NOT h.hospital_id IN $popular_hospitals
  AND cl.incident_date IS NOT NULL
RETURN h.hospital_id AS hospital_id, h.name AS name,
       c.customer_id AS customer_id, date(cl.incident_date) AS dt
"""

# 브로커 허브(허위입원 조직형) — 핵심 판별: 알선 고객이 **한 병원에 집중**된다.
#   정상 브로커는 무관한 다수 고객을 알선해 병원이 분산되지만(한 병원당 소수),
#   사기 브로커는 환자 전원을 한 병원에 몰아넣는다(나이롱 환자). 따라서 "같은 병원
#   알선 고객 수 >= min_customers" 인 (브로커, 병원) 쌍의 고객만 신호로 본다.
_Q5_BROKER = """
MATCH (b:Broker)-[:BROKERED]->(c:Customer)-[:FILED]->(:Claim)-[:TREATED_AT]->(h:Hospital)
WITH b, h, collect(DISTINCT c) AS customers
WHERE size(customers) >= $min_customers
RETURN b.broker_id AS entity_id,
       b.name AS entity_name,
       h.hospital_id AS hospital_id,
       size(customers) AS num_customers,
       [x IN customers | x.customer_id] AS customer_ids
ORDER BY num_customers DESC
"""

# 설계사 허브(가로채기): 한 설계사가 모집한 다수 계약자(고객)의 청구금이 **하나의
# 공통 계좌로 집중**된다. 정상 설계사는 다수 계약을 모집하지만 청구금은 각 고객
# 고유 계좌로 분산된다(한 계좌당 1~2명). 사기 설계사는 한 계좌가 다수(>=min) 고객의
# 청구금을 수취한다(가로채기). 따라서 "설계사+공통계좌" 로 묶인 고객이 min 이상인
# (설계사, 계좌) 쌍의 고객만 신호로 본다.
_Q6_AGENT = """
MATCH (a:Agent)-[:SOLD_POLICY]->(:Policy)<-[:HOLDS]-(c:Customer)
      -[:FILED]->(:Claim)-[:PAID_TO]->(acc:Account)
WITH a, acc, collect(DISTINCT c) AS customers
WHERE size(customers) >= $min_shared_customers
RETURN a.agent_id AS entity_id,
       a.name AS entity_name,
       acc.account_no AS account_no,
       size(customers) AS num_customers,
       [x IN customers | x.customer_id] AS customer_ids
ORDER BY num_customers DESC
"""

_Q7_OVERBILL = """
MATCH (c:Customer)-[:FILED]->(cl:Claim)-[:REPAIRED_AT]->(s:RepairShop)
WHERE NOT s.shop_id IN $popular_shops
  AND cl.claimed_amount IS NOT NULL
  AND cl.claimed_amount >= $amount
WITH s, collect(DISTINCT c) AS customers,
     avg(cl.claimed_amount) AS avg_amount, count(DISTINCT cl) AS num_claims
WHERE size(customers) >= $min_customers
RETURN s.shop_id AS entity_id,
       s.name AS entity_name,
       size(customers) AS num_customers,
       num_claims,
       avg_amount,
       [x IN customers | x.customer_id] AS customer_ids
ORDER BY num_customers DESC, avg_amount DESC
"""


def run_admission_stars(
    *,
    min_patients: int = DEFAULT_STAR_MIN_PATIENTS,
    cluster_days: int = DEFAULT_STAR_CLUSTER_DAYS,
) -> list[dict[str, Any]]:
    """Q4 허위입원 조직형(star) — 비인기 병원의 시간창 집중 환자 군집을 반환한다.

    인기 대형 병원(정상 baseline)을 제외하고, 각 병원에서 ``cluster_days`` 길이의
    슬라이딩 시간창 중 distinct 환자가 ``min_patients`` 명 이상 몰린 창을 찾아
    그 창의 환자를 star 군집으로 반환한다. 정상 청구는 연중 분산되어 어떤 시간창에도
    소수만 모이므로 정상 병원은 잡히지 않는다(나이롱 환자 동시 모객 특성 포착).

    Returns:
        dict 리스트. 키: ``entity_type``, ``entity_id``, ``entity_name``,
        ``num_customers``, ``customer_ids``, ``span_days``.
    """
    rows = db.run(_Q4_STAR_RAW, popular_hospitals=POPULAR_HOSPITAL_IDS)

    # 병원별로 (ordinal_day, customer_id) 수집
    by_hosp: dict[str, dict[str, Any]] = {}
    for r in rows:
        h = r["hospital_id"]
        d = by_hosp.setdefault(h, {"name": r["name"], "events": []})
        dt = r["dt"]
        # neo4j Date → ordinal
        ordinal = dt.to_native().toordinal() if hasattr(dt, "to_native") else dt.toordinal()
        d["events"].append((ordinal, r["customer_id"]))

    results: list[dict[str, Any]] = []
    for hid, d in by_hosp.items():
        events = sorted(d["events"])
        total = len({c for _, c in events})
        if total < min_patients:
            continue
        # 병원의 연중 기대 밀도(baseline) — 정상은 청구가 연중 균등 분산.
        #   기대 창내 환자 ≈ total * (cluster_days / 365).
        # star 는 이 기대를 크게 초과하는 burst 를 만든다 → 초과분만 신호.
        baseline = total * (cluster_days / 365.0)
        threshold = max(min_patients, baseline * STAR_EXCESS_FACTOR)

        # 슬라이딩 시간창: 각 시작점에서 cluster_days 이내 distinct 환자 최대.
        best_cids: set[str] = set()
        n = len(events)
        for i in range(n):
            window_cids: set[str] = set()
            start_day = events[i][0]
            k = i
            while k < n and events[k][0] - start_day <= cluster_days:
                window_cids.add(events[k][1])
                k += 1
            if len(window_cids) > len(best_cids):
                best_cids = window_cids
        if len(best_cids) >= threshold:
            results.append({
                "entity_type": "FAKE_ADMISSION_STAR",
                "entity_id": hid,
                "entity_name": d["name"],
                "num_customers": len(best_cids),
                "customer_ids": sorted(best_cids),
                "span_days": cluster_days,
            })
    results.sort(key=lambda x: x["num_customers"], reverse=True)
    return results


def run_broker_hubs(
    *,
    min_customers: int = DEFAULT_BROKER_MIN_CUSTOMERS,
) -> list[dict[str, Any]]:
    """Q5 브로커 허브 — 다수 고객을 알선한 브로커를 반환한다(BROKERED 다수).

    Returns:
        dict 리스트. 키: ``entity_id``, ``entity_name``, ``num_customers``,
        ``customer_ids``.
    """
    if not _has_label("Broker"):
        return []
    return db.run(_Q5_BROKER, min_customers=min_customers)


def run_agent_hubs(
    *,
    min_shared_customers: int = DEFAULT_AGENT_MIN_SHARED_CUSTOMERS,
) -> list[dict[str, Any]]:
    """Q6 설계사 허브 — 모집 고객의 청구금이 한 공통 계좌로 집중된 설계사(가로채기).

    정상 설계사(고객별 분산 계좌)는 한 계좌에 묶이는 고객이 소수(1~2명)라 제외된다.
    사기 설계사는 한 계좌가 다수(>=min) 모집 고객의 청구금을 수취한다.

    Returns:
        dict 리스트. 키: ``entity_id``, ``entity_name``, ``account_no``,
        ``num_customers``, ``customer_ids``.
    """
    if not _has_label("Agent"):
        return []
    return db.run(_Q6_AGENT, min_shared_customers=min_shared_customers)


def run_repair_overbills(
    *,
    min_customers: int = DEFAULT_OVERBILL_MIN_CUSTOMERS,
    amount: float = DEFAULT_OVERBILL_AMOUNT,
) -> list[dict[str, Any]]:
    """Q7 정비비 과다청구 — 비인기 정비소에 다수 고객 + 청구금액 이상 군집.

    Returns:
        dict 리스트. 키: ``entity_id``, ``entity_name``, ``num_customers``,
        ``num_claims``, ``avg_amount``, ``customer_ids``.
    """
    return db.run(
        _Q7_OVERBILL,
        popular_shops=POPULAR_SHOP_IDS,
        min_customers=min_customers,
        amount=amount,
    )


def _has_label(label: str) -> bool:
    """그래프에 주어진 노드 레이블이 존재하는지(없으면 신규 노드 미적재 데이터)."""
    try:
        rows = db.run(
            "CALL db.labels() YIELD label RETURN collect(label) AS labels"
        )
    except Exception:
        return False
    return bool(rows and label in (rows[0].get("labels") or []))
