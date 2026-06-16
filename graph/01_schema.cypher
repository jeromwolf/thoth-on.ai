// ============================================================
// THOTH-ON · 보험 사기탐지 지식그래프 — Neo4j 5 스키마
// FR-2.1 (P0)  graph/01_schema.cypher
// 멱등 (IF NOT EXISTS) — 재실행 안전
// ============================================================
//
// [노드 온톨로지]
//   Customer    : 보험 계약자·피보험자·청구인
//   Claim       : 보험 청구 건 (risk_score 부여)
//   Policy      : 보험 계약 (증권)
//   Vehicle     : 차량 (VIN 기반 엔티티 해소 핵심)
//   Address     : 주소 (공유 주소 링크 탐지)
//   Account     : 금융 계좌 (공유 계좌 탐지)
//   Phone       : 전화번호 (공유 전화 탐지)
//   Hospital    : 의료기관
//   RepairShop  : 자동차 정비소
//
// [엣지 온톨로지]
//   (Customer)-[:FILED]->(Claim)          청구인 → 청구 건
//   (Customer)-[:HOLDS]->(Policy)         계약자 → 보험 계약
//   (Policy)-[:COVERS]->(Vehicle)         계약 → 피보험 차량
//   (Claim)-[:INVOLVES]->(Vehicle)        청구 → 사고 차량
//   (Claim)-[:TREATED_AT]->(Hospital)     청구 → 치료 병원
//   (Claim)-[:REPAIRED_AT]->(RepairShop)  청구 → 수리 정비소
//   (Claim)-[:PAID_TO]->(Account)         청구 → 지급 계좌
//   (Customer)-[:LIVES_AT]->(Address)     고객 → 거주 주소
//   (Customer)-[:OWNS]->(Vehicle)         고객 → 소유 차량
//   (Customer)-[:HAS_PHONE]->(Phone)      고객 → 전화번호
//   (Claim)-[:WITNESSED_BY]->(Customer)   청구 → 목격자(제3자 고객)
// ============================================================

// ------------------------------------------------------------
// UNIQUE 제약 — 식별자 (엔티티 해소 병합 키)
// ------------------------------------------------------------

CREATE CONSTRAINT customer_id_unique IF NOT EXISTS
FOR (n:Customer) REQUIRE n.customer_id IS UNIQUE;

CREATE CONSTRAINT claim_id_unique IF NOT EXISTS
FOR (n:Claim) REQUIRE n.claim_id IS UNIQUE;

CREATE CONSTRAINT policy_id_unique IF NOT EXISTS
FOR (n:Policy) REQUIRE n.policy_id IS UNIQUE;

CREATE CONSTRAINT vehicle_vin_unique IF NOT EXISTS
FOR (n:Vehicle) REQUIRE n.vin IS UNIQUE;

CREATE CONSTRAINT account_no_unique IF NOT EXISTS
FOR (n:Account) REQUIRE n.account_no IS UNIQUE;

// WP1-6: 개인 전화번호는 가명처리(FR-1.3) — 병합키를 number_hash(sha256)로 변경.
// 평문 number 는 그래프에 저장하지 않는다.
CREATE CONSTRAINT phone_number_hash_unique IF NOT EXISTS
FOR (n:Phone) REQUIRE n.number_hash IS UNIQUE;

CREATE CONSTRAINT address_id_unique IF NOT EXISTS
FOR (n:Address) REQUIRE n.address_id IS UNIQUE;

CREATE CONSTRAINT hospital_id_unique IF NOT EXISTS
FOR (n:Hospital) REQUIRE n.hospital_id IS UNIQUE;

CREATE CONSTRAINT repairshop_id_unique IF NOT EXISTS
FOR (n:RepairShop) REQUIRE n.shop_id IS UNIQUE;

// ------------------------------------------------------------
// 인덱스 — 자주 조회되는 속성
// ------------------------------------------------------------

CREATE INDEX claim_filed_at IF NOT EXISTS
FOR (n:Claim) ON (n.filed_at);

CREATE INDEX claim_risk_score IF NOT EXISTS
FOR (n:Claim) ON (n.risk_score);

CREATE INDEX claim_status IF NOT EXISTS
FOR (n:Claim) ON (n.status);

// Customer.name 은 평문 미저장(FR-1.3) — name_hash 인덱스로 대체
CREATE INDEX customer_name_hash IF NOT EXISTS
FOR (n:Customer) ON (n.name_hash);

CREATE INDEX customer_dob IF NOT EXISTS
FOR (n:Customer) ON (n.dob);

CREATE INDEX policy_start_date IF NOT EXISTS
FOR (n:Policy) ON (n.start_date);

CREATE INDEX policy_type IF NOT EXISTS
FOR (n:Policy) ON (n.policy_type);

CREATE INDEX vehicle_plate IF NOT EXISTS
FOR (n:Vehicle) ON (n.plate_no);

CREATE INDEX hospital_name IF NOT EXISTS
FOR (n:Hospital) ON (n.name);

CREATE INDEX repairshop_name IF NOT EXISTS
FOR (n:RepairShop) ON (n.name);
