// ============================================================
// THOTH-ON · 보험 사기탐지 — 탐지 쿼리 (WP2 탐지 코어)
// FR-3.1 (공유 엔티티) · FR-3.2 (핫스팟) · FR-3.3 (crash-for-cash 순환)
// detection/03_fraud_queries.cypher
//
// ※ 이 파일은 사람이 읽는 기준 명세(레퍼런스)이다. 실제 실행은
//    detection/detect.py 가 파라미터화한 동일 쿼리를 사용한다.
//    (db.apply_file 의 단순 세미콜론 분리기로는 파라미터/주석 처리가
//     제한되므로, 운영 실행은 detect.py 를 통해 한다.)
//
// [데이터 사실 — 주입 링 패턴 (ingest/synth_generator.py)]
//   · 각 crash-for-cash 링은 동일 Account(account_no)를 공유한다.
//   · 링 멤버 청구는 동일 Hospital + 동일 RepairShop 으로 집중된다.
//   · 링 멤버는 서로의 사고를 양방향 교차 목격한다(WITNESSED_BY 상호).
//   · ground truth: Customer.is_fraud_ring / Customer.ring_id,
//                   Claim.is_fraud_ring / Claim.ring_id.
// ============================================================


// ------------------------------------------------------------
// Q1 — 공유 엔티티 탐지 (FR-3.1) — 정상(가족) 공유 구분 컨텍스트 포함
// 동일 Account / Phone(number_hash) / Address / Vehicle(vin) 를
// 2명 이상 고객이 공유하는 쌍·군집을 반환한다.
// $min_customers : 공유로 간주할 최소 고객 수 (기본 2)
//
// [정밀도 — 정상 공유 구분축 (data/synthetic_test 실측)]
//   각 공유 군집에 두 가지 컨텍스트를 함께 산출해 가족/사기를 점수 단계에서
//   차등화한다(detection/scoring.py):
//     · distinct_addresses : 군집 고객의 서로 다른 주소 수.
//         - 가족: 같은 주소(distinct < n, 실측 addr_ratio≈0.45) → 약 신호.
//         - 사기: 모두 다른 주소(distinct = n, 무관한 다수)           → 강 신호.
//     · time_span_days : 군집 청구 incident_date 의 (최대-최소) 일수.
//         - 가족: 연중 분산(실측 ≈262일)  → 약.
//         - 사기: 짧은 기간 집중(실측 ≈11일) → 강(시간 군집 보너스).
// ------------------------------------------------------------

// Q1-Account: 동일 계좌 공유 (서로 다른 고객의 청구금이 같은 계좌로 지급)
//   + 주소 동일성(distinct_addresses) + 청구 시간 군집(time_span_days)
MATCH (c:Customer)-[:FILED]->(:Claim)-[:PAID_TO]->(a:Account)
WITH a, collect(DISTINCT c) AS customers
WHERE size(customers) >= $min_customers
OPTIONAL MATCH (ac:Customer)-[:LIVES_AT]->(ad:Address) WHERE ac IN customers
WITH a, customers, count(DISTINCT ad.address_id) AS distinct_addresses
OPTIONAL MATCH (cc:Customer)-[:FILED]->(cl:Claim)
WHERE cc IN customers AND cl.incident_date IS NOT NULL
WITH a, customers, distinct_addresses, collect(date(cl.incident_date)) AS dts
RETURN 'ACCOUNT' AS shared_type,
       a.account_no AS shared_key,
       [x IN customers | x.customer_id] AS customer_ids,
       size(customers) AS num_customers,
       [x IN customers | x.ring_id] AS ring_ids,
       distinct_addresses,
       CASE WHEN size(dts) >= 2 THEN duration.inDays(
              reduce(mn=date('2099-12-31'), d IN dts | CASE WHEN d<mn THEN d ELSE mn END),
              reduce(mx=date('1900-01-01'), d IN dts | CASE WHEN d>mx THEN d ELSE mx END)
            ).days ELSE -1 END AS time_span_days
ORDER BY num_customers DESC;
// (Phone/Vehicle 도 동일 패턴으로 distinct_addresses·time_span_days 를 산출한다.
//  Address 공유는 그 자체가 같은 주소이므로 distinct_addresses=1 로 고정.)

// Q1-Phone: 동일 전화번호(number_hash) 공유
MATCH (c:Customer)-[:HAS_PHONE]->(p:Phone)
WITH p, collect(DISTINCT c) AS customers
WHERE size(customers) >= $min_customers
RETURN 'PHONE' AS shared_type,
       p.number_hash AS shared_key,
       [x IN customers | x.customer_id] AS customer_ids,
       size(customers) AS num_customers,
       [x IN customers | x.ring_id] AS ring_ids
ORDER BY num_customers DESC;

// Q1-Address: 동일 정규화 주소 공유 (배경 노이즈 많음 — 약한 신호)
MATCH (c:Customer)-[:LIVES_AT]->(a:Address)
WITH a, collect(DISTINCT c) AS customers
WHERE size(customers) >= $min_customers
RETURN 'ADDRESS' AS shared_type,
       a.address_id AS shared_key,
       [x IN customers | x.customer_id] AS customer_ids,
       size(customers) AS num_customers,
       [x IN customers | x.ring_id] AS ring_ids
ORDER BY num_customers DESC;

// Q1-Vehicle: 동일 차량(vin)을 복수 고객이 소유 (명의 도용/대포차 신호)
MATCH (c:Customer)-[:OWNS]->(v:Vehicle)
WITH v, collect(DISTINCT c) AS customers
WHERE size(customers) >= $min_customers
RETURN 'VEHICLE' AS shared_type,
       v.vin AS shared_key,
       [x IN customers | x.customer_id] AS customer_ids,
       size(customers) AS num_customers,
       [x IN customers | x.ring_id] AS ring_ids
ORDER BY num_customers DESC;


// ------------------------------------------------------------
// Q2 — 엔티티 핫스팟 (FR-3.2)
// 병원·정비소·계좌에 청구가 임계치 이상 집중된 엔티티를 반환한다.
// 정상 운영량(대형 병원 100건+)과 구분하기 위해, 단순 청구 수가 아니라
// "서로 다른 고객 수(distinct claimants)" 기준으로도 측정한다.
// $hospital_min / $shop_min / $account_min : 종류별 임계치(고객 수 기준)
// ------------------------------------------------------------

// Q2-Hospital 핫스팟
MATCH (c:Customer)-[:FILED]->(cl:Claim)-[:TREATED_AT]->(h:Hospital)
WITH h, count(DISTINCT cl) AS num_claims, collect(DISTINCT c) AS customers
WHERE size(customers) >= $hospital_min
RETURN 'HOSPITAL' AS entity_type,
       h.hospital_id AS entity_id,
       h.name AS entity_name,
       num_claims,
       size(customers) AS num_customers,
       [x IN customers | x.customer_id] AS customer_ids
ORDER BY num_customers DESC;

// Q2-RepairShop 핫스팟
MATCH (c:Customer)-[:FILED]->(cl:Claim)-[:REPAIRED_AT]->(s:RepairShop)
WITH s, count(DISTINCT cl) AS num_claims, collect(DISTINCT c) AS customers
WHERE size(customers) >= $shop_min
RETURN 'REPAIR_SHOP' AS entity_type,
       s.shop_id AS entity_id,
       s.name AS entity_name,
       num_claims,
       size(customers) AS num_customers,
       [x IN customers | x.customer_id] AS customer_ids
ORDER BY num_customers DESC;

// Q2-Account 핫스팟 (한 계좌에 다수 고객 청구금 집중)
MATCH (c:Customer)-[:FILED]->(cl:Claim)-[:PAID_TO]->(a:Account)
WITH a, count(DISTINCT cl) AS num_claims, collect(DISTINCT c) AS customers
WHERE size(customers) >= $account_min
RETURN 'ACCOUNT' AS entity_type,
       a.account_no AS entity_id,
       a.bank_name AS entity_name,
       num_claims,
       size(customers) AS num_customers,
       [x IN customers | x.customer_id] AS customer_ids
ORDER BY num_customers DESC;

// ------------------------------------------------------------
// Q2b/Q2c — 집중·담합 핫스팟 (FR-3.2 정밀판) — baseline 정규화
// 단순 청구 건수가 아니라 "기대치 대비 이상 집중"을 잡는다. 인기 대형 병원/
// 정비소(정상 운영 baseline)는 제외하고, 비인기 (병원+정비소) 쌍을 소수 고객이
// 짧은 기간에 함께 이용하는 군집만 의심한다. 정상 대형 엔티티가 건수만 많다고
// 핫스팟이 되지 않게 한다.
//   $popular_hospitals / $popular_shops : 정상 baseline 엔티티(제외)
//   $max_customers : 소수 집중 상한
//   $cluster_days  : 청구 시간 군집 창
// ------------------------------------------------------------

// Q2b-Focused: 비인기 (병원+정비소) 소수·시간군집 공유 (corroborating 약 신호)
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
RETURN 'FOCUSED_HOTSPOT' AS entity_type, h.hospital_id AS entity_id,
       h.name AS entity_name, s.shop_id AS shop_id,
       size(customers) AS num_customers,
       [x IN customers | x.customer_id] AS customer_ids, span_days
ORDER BY num_customers DESC, span_days ASC;

// Q2c-Collusion: 위 조건 + 매우 짧은 기간(<=14일) + 단일 사고유형(collision)
//   crash-for-cash 의 "동일 충돌 동시 청구" 담합 특성 — 약신호 링(hotspot_only/
//   weak) 회수용 고정밀 신호(실측 정밀 ~81%).
MATCH (c:Customer)-[:FILED]->(cl:Claim)-[:TREATED_AT]->(h:Hospital)
MATCH (cl)-[:REPAIRED_AT]->(s:RepairShop)
WHERE NOT h.hospital_id IN $popular_hospitals
  AND NOT s.shop_id IN $popular_shops
  AND cl.incident_date IS NOT NULL
WITH h, s, collect(DISTINCT c) AS customers,
     collect(DISTINCT cl.incident_type) AS types, collect(date(cl.incident_date)) AS dts
WHERE size(customers) >= 2 AND size(customers) <= $max_customers
  AND size(types) = 1 AND types[0] = 'collision'
WITH h, s, customers,
     duration.inDays(
       reduce(mn=date('2099-12-31'), d IN dts | CASE WHEN d<mn THEN d ELSE mn END),
       reduce(mx=date('1900-01-01'), d IN dts | CASE WHEN d>mx THEN d ELSE mx END)
     ).days AS span_days
WHERE span_days <= $cluster_days
RETURN 'COLLUSION_HOTSPOT' AS entity_type, h.hospital_id AS entity_id,
       h.name AS entity_name, s.shop_id AS shop_id,
       size(customers) AS num_customers,
       [x IN customers | x.customer_id] AS customer_ids, span_days
ORDER BY num_customers DESC, span_days ASC;


// ------------------------------------------------------------
// Q3 — crash-for-cash 순환 (FR-3.3) — 링 탐지 핵심
// WITNESSED_BY 교차 목격(서로의 사고를 목격)하는 고객 군집을 반환한다.
// 양방향(상호) 목격만 사기 순환 신호로 본다.
// $min_cluster : 군집 최소 고객 수 (기본 2)
// ------------------------------------------------------------

// Q3-pairs: 상호 교차 목격 고객 쌍 (설명가능성 경로용)
MATCH (cust1:Customer)-[:FILED]->(c1:Claim)-[:WITNESSED_BY]->(c2:Claim)<-[:FILED]-(cust2:Customer)
WHERE cust1 <> cust2
  AND (c2)-[:WITNESSED_BY]->(c1)          // 양방향(상호) 교차 목격
  AND cust1.customer_id < cust2.customer_id  // 쌍 중복 제거
RETURN cust1.customer_id AS customer_a,
       cust2.customer_id AS customer_b,
       c1.claim_id AS claim_a,
       c2.claim_id AS claim_b,
       cust1.ring_id AS ring_a,
       cust2.ring_id AS ring_b
ORDER BY customer_a, customer_b;

// Q3-clusters: 교차 목격 연결요소(군집) — 군집별 고객 집합 반환
//   상호 목격하는 청구를 연결요소로 묶어 링 군집을 식별한다.
MATCH (cust:Customer)-[:FILED]->(c1:Claim)-[:WITNESSED_BY]->(c2:Claim)
WHERE (c2)-[:WITNESSED_BY]->(c1)
WITH cust, c1
MATCH (c1)-[:WITNESSED_BY]->(c2:Claim)<-[:FILED]-(peer:Customer)
WHERE (c2)-[:WITNESSED_BY]->(c1) AND peer <> cust
WITH cust, collect(DISTINCT peer.customer_id) AS peers
WITH cust, peers, ([cust.customer_id] + peers) AS members
WHERE size(members) >= $min_cluster
RETURN cust.customer_id AS seed_customer,
       cust.ring_id AS ring_id,
       members,
       size(members) AS cluster_size
ORDER BY cluster_size DESC, seed_customer;
