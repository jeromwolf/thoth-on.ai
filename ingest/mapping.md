# 데이터 사전 — 소스 컬럼 → 그래프 속성 매핑

| 항목 | 내용 |
|---|---|
| 대상 프로젝트 | THOTH-ON 보험 사기탐지 지식그래프 플랫폼 |
| 문서 버전 | **v0.1** |
| 범위 | 자동차보험 PoC (배치 ingest, FR-1.x) |
| 최종 수정 | 2026-06-16 |
| 관련 FR | FR-1.1 (배치 적재), FR-1.2 (엔티티 해소), FR-1.3 (가명처리) |
| 관련 결정 | Q4 (결정적 완전일치 자동 병합) |

---

## 목차

1. [소스 테이블 목록](#1-소스-테이블-목록)
2. [소스별 컬럼 정의](#2-소스별-컬럼-정의)
   - 2.1 claims (청구)
   - 2.2 customers (고객)
   - 2.3 policies (계약)
   - 2.4 vehicles (차량)
   - 2.5 accounts (계좌)
   - 2.6 hospitals (의료기관)
   - 2.7 repair_shops (정비소)
3. [소스 → 노드 매핑](#3-소스--노드-매핑)
4. [소스 → 엣지 매핑 (11종)](#4-소스--엣지-매핑-11종)
5. [엔티티 해소 키 및 정규화 규칙](#5-엔티티-해소-키-및-정규화-규칙)
6. [가명처리(PII 해시) 대상](#6-가명처리pii-해시-대상)
7. [적재 멱등성 키 요약](#7-적재-멱등성-키-요약)

---

## 1. 소스 테이블 목록

| # | 소스 테이블 | 파일 형식 | 그래프 노드 생성 | 그래프 엣지 생성 | 설명 |
|---|---|---|---|---|---|
| 1 | `claims` | CSV / Parquet | Claim | FILED, INVOLVES, TREATED_AT, REPAIRED_AT, PAID_TO, WITNESSED_BY | 사고 청구 마스터. 사기탐지의 핵심 소스 |
| 2 | `customers` | CSV / Parquet | Customer, Address, Phone | LIVES_AT, HAS_PHONE | 고객 기본정보 및 연락처·주소 |
| 3 | `policies` | CSV / Parquet | Policy | HOLDS, COVERS | 보험계약(보험증권) |
| 4 | `vehicles` | CSV / Parquet | Vehicle | OWNS | 차량 등록정보 |
| 5 | `accounts` | CSV / Parquet | Account | PAID_TO | 지급 계좌 |
| 6 | `hospitals` | CSV / Parquet | Hospital | TREATED_AT | 병원·의원·한의원 등 의료기관 |
| 7 | `repair_shops` | CSV / Parquet | RepairShop | REPAIRED_AT | 자동차 정비소 |

> **참고:** `WITNESSED_BY` 엣지는 `claims` 소스의 목격자 관계 컬럼에서 생성된다(→ §4 참조).

---

## 2. 소스별 컬럼 정의

### 2.1 claims (청구)

> 사고 1건 = 행 1개. 가장 많은 노드·엣지를 생성하는 소스.

| 컬럼명 | 타입 | 설명 | 예시 | PII | 비고 |
|---|---|---|---|---|---|
| `claim_id` | STRING | 청구 고유 ID (멱등키) | `CLM-2024-000001` | N | 노드 PRIMARY KEY |
| `customer_id` | STRING | 청구 고객 ID (FK → customers) | `CUST-00123` | N | FILED 엣지 소스 |
| `policy_id` | STRING | 계약 ID (FK → policies) | `POL-2023-007` | N | COVERS 엣지 소스 |
| `vehicle_id` | STRING | 차량 ID (FK → vehicles) | `VEH-00456` | N | INVOLVES 엣지 소스 |
| `hospital_id` | STRING | 치료받은 병원 ID (FK → hospitals), nullable | `HOSP-0012` | N | TREATED_AT 엣지 소스 |
| `repair_shop_id` | STRING | 수리한 정비소 ID (FK → repair_shops), nullable | `RSH-0034` | N | REPAIRED_AT 엣지 소스 |
| `account_id` | STRING | 보험금 지급 계좌 ID (FK → accounts) | `ACC-00789` | N | PAID_TO 엣지 소스 |
| `incident_date` | DATE | 사고 발생일 | `2024-03-15` | N | Claim 속성 |
| `report_date` | DATE | 접수 신고일 | `2024-03-16` | N | Claim 속성 |
| `incident_type` | STRING | 사고 유형 | `collision`, `theft`, `fire` | N | Claim 속성 |
| `incident_location` | STRING | 사고 발생 위치(주소 문자열) | `서울시 강남구 테헤란로 123` | N | Claim 속성(V2에서 Address 노드화) |
| `claimed_amount` | FLOAT | 청구 금액 (원) | `2500000.00` | N | Claim 속성 |
| `paid_amount` | FLOAT | 실제 지급 금액 (원), nullable | `2100000.00` | N | Claim 속성 |
| `claim_status` | STRING | 처리 상태 | `approved`, `pending`, `denied`, `under_review` | N | Claim 속성 |
| `fraud_label` | BOOLEAN | 사기 확정 라벨 (합성데이터 시 주입) | `true` / `false` | N | 탐지 평가용. 실운영 시 NULL 가능 |
| `witness_claim_ids` | STRING[] | 이 사고를 목격 관계로 연결할 다른 청구 ID 목록 | `["CLM-2024-000002","CLM-2024-000003"]` | N | **WITNESSED_BY 엣지 소스** — crash-for-cash 핵심 |
| `created_at` | TIMESTAMP | 레코드 생성 시각 | `2024-03-16T09:12:00Z` | N | 적재 메타 |

### 2.2 customers (고객)

| 컬럼명 | 타입 | 설명 | 예시 | PII | 비고 |
|---|---|---|---|---|---|
| `customer_id` | STRING | 고객 고유 ID (멱등키) | `CUST-00123` | N | Customer 노드 PRIMARY KEY |
| `name` | STRING | 고객 실명 | `홍길동` | **Y** | 가명처리 — sha256(salt + name) 저장 |
| `id_number` | STRING | 주민등록번호 (13자리) | `800101-1234567` | **Y** | 가명처리 — sha256(salt + id_number) 저장. 평문 금지 |
| `birth_date` | DATE | 생년월일 | `1980-01-01` | Y (준PII) | 해시 불필요하나 내부 접근 통제 대상 |
| `gender` | STRING | 성별 | `M` / `F` | N | Customer 속성 |
| `address` | STRING | 현재 주소 (정규화 전 원문) | `서울시 강남구 테헤란로 123 2층` | Y (준PII) | LIVES_AT 엣지 → Address 노드 생성 |
| `address_normalized` | STRING | 정규화 주소 (ETL 산출) | `서울시강남구테헤란로123` | N | Address 노드 병합 키 (공백·특수문자 제거, 소문자화) |
| `phone_number` | STRING | 연락처 (정규화 전) | `010-1234-5678` | **Y** | 가명처리 — sha256(salt + normalized_phone) 저장 |
| `phone_normalized` | STRING | 정규화 전화번호 (ETL 산출) | `01012345678` | N | Phone 노드 병합 키. **엔티티 해소 자동 병합 대상** |
| `email` | STRING | 이메일 주소 | `hong@example.com` | **Y** | 가명처리 — sha256(salt + email) 저장 |
| `created_at` | TIMESTAMP | 고객 등록일 | `2020-05-10T00:00:00Z` | N | Customer 속성 |

### 2.3 policies (계약)

| 컬럼명 | 타입 | 설명 | 예시 | PII | 비고 |
|---|---|---|---|---|---|
| `policy_id` | STRING | 계약 고유 ID (멱등키) | `POL-2023-007` | N | Policy 노드 PRIMARY KEY |
| `customer_id` | STRING | 계약자 고객 ID (FK → customers) | `CUST-00123` | N | HOLDS 엣지 소스 |
| `vehicle_id` | STRING | 피보험 차량 ID (FK → vehicles) | `VEH-00456` | N | COVERS 엣지 소스 |
| `product_code` | STRING | 상품 코드 | `AUTO-STANDARD-V3` | N | Policy 속성 |
| `coverage_type` | STRING | 보장 유형 | `comprehensive`, `liability_only` | N | Policy 속성 |
| `start_date` | DATE | 보장 시작일 | `2023-01-01` | N | Policy 속성, COVERS 엣지 속성 |
| `end_date` | DATE | 보장 종료일 | `2024-01-01` | N | Policy 속성, COVERS 엣지 속성 |
| `premium_amount` | FLOAT | 보험료 (원/연) | `850000.00` | N | Policy 속성 |
| `coverage_limit` | FLOAT | 최대 보장금액 (원) | `50000000.00` | N | Policy 속성 |
| `status` | STRING | 계약 상태 | `active`, `expired`, `cancelled` | N | Policy 속성 |
| `created_at` | TIMESTAMP | 계약 체결일시 | `2023-01-01T10:00:00Z` | N | Policy 속성 |

### 2.4 vehicles (차량)

| 컬럼명 | 타입 | 설명 | 예시 | PII | 비고 |
|---|---|---|---|---|---|
| `vehicle_id` | STRING | 차량 내부 ID (멱등키) | `VEH-00456` | N | Vehicle 노드 PRIMARY KEY |
| `customer_id` | STRING | 소유자 고객 ID (FK → customers) | `CUST-00123` | N | OWNS 엣지 소스 |
| `vin` | STRING | 차대번호(Vehicle Identification Number, 17자리) | `KMHDN41BP3U123456` | N | **엔티티 해소 자동 병합 키** — 정규화(대문자, 하이픈 제거) |
| `license_plate` | STRING | 차량 번호판 | `12가3456` | N | Vehicle 속성 (보조 식별자) |
| `make` | STRING | 제조사 | `Hyundai` | N | Vehicle 속성 |
| `model` | STRING | 모델명 | `Sonata` | N | Vehicle 속성 |
| `year` | INTEGER | 연식 | `2020` | N | Vehicle 속성 |
| `color` | STRING | 차량 색상 | `흰색` | N | Vehicle 속성 |
| `registered_at` | DATE | 차량 등록일 | `2020-06-01` | N | Vehicle 속성 |

### 2.5 accounts (계좌)

| 컬럼명 | 타입 | 설명 | 예시 | PII | 비고 |
|---|---|---|---|---|---|
| `account_id` | STRING | 계좌 내부 ID (멱등키) | `ACC-00789` | N | Account 노드 PRIMARY KEY |
| `account_no` | STRING | 실제 계좌번호 (은행코드-번호) | `011-1234-567890` | **Y** | 가명처리 — sha256(salt + normalized_account_no) 저장. **엔티티 해소 자동 병합 키** — 정규화(하이픈·공백 제거) |
| `account_no_normalized` | STRING | 정규화 계좌번호 (ETL 산출) | `0111234567890` | N | Account 노드 병합 키 |
| `bank_code` | STRING | 은행 기관 코드 | `011` (농협) | N | Account 속성 |
| `bank_name` | STRING | 은행명 | `농협은행` | N | Account 속성 |
| `account_holder` | STRING | 예금주명 | `홍길동` | **Y** | 가명처리 — sha256(salt + account_holder) 저장 |
| `account_type` | STRING | 계좌 유형 | `checking`, `savings` | N | Account 속성 |
| `created_at` | TIMESTAMP | 계좌 등록일 | `2021-03-20T00:00:00Z` | N | Account 속성 |

### 2.6 hospitals (의료기관)

| 컬럼명 | 타입 | 설명 | 예시 | PII | 비고 |
|---|---|---|---|---|---|
| `hospital_id` | STRING | 의료기관 내부 ID (멱등키) | `HOSP-0012` | N | Hospital 노드 PRIMARY KEY |
| `institution_code` | STRING | 요양기관 기호 (건보 코드) | `B1234567` | N | 외부 기준 식별자 (V2 연계 시 활용) |
| `name` | STRING | 기관명 | `강남정형외과의원` | N | Hospital 속성 |
| `type` | STRING | 기관 유형 | `clinic`, `hospital`, `oriental_medicine` | N | Hospital 속성 |
| `address` | STRING | 소재지 주소 | `서울시 강남구 역삼동 678` | N | Hospital 속성 |
| `phone` | STRING | 대표 전화 | `02-1234-5678` | N | Hospital 속성 |
| `license_no` | STRING | 개설 허가 번호 | `서울강남2024-001` | N | Hospital 속성 |
| `specialties` | STRING[] | 표방 진료과목 | `["정형외과","재활의학과"]` | N | Hospital 속성 |
| `created_at` | TIMESTAMP | 등록일 | `2020-01-15T00:00:00Z` | N | Hospital 속성 |

### 2.7 repair_shops (정비소)

| 컬럼명 | 타입 | 설명 | 예시 | PII | 비고 |
|---|---|---|---|---|---|
| `repair_shop_id` | STRING | 정비소 내부 ID (멱등키) | `RSH-0034` | N | RepairShop 노드 PRIMARY KEY |
| `business_reg_no` | STRING | 사업자등록번호 | `123-45-67890` | N | 외부 기준 식별자 |
| `name` | STRING | 상호명 | `빠른카정비` | N | RepairShop 속성 |
| `type` | STRING | 정비소 유형 | `authorized`, `independent`, `body_shop` | N | RepairShop 속성 |
| `address` | STRING | 소재지 주소 | `경기도 성남시 분당구 판교로 55` | N | RepairShop 속성 |
| `phone` | STRING | 대표 전화 | `031-9876-5432` | N | RepairShop 속성 |
| `license_no` | STRING | 정비업 등록 번호 | `경기성남-정비-0099` | N | RepairShop 속성 |
| `rating` | FLOAT | 보험사 평가 등급 (0–5) | `3.8` | N | RepairShop 속성 (핫스팟 탐지 보조) |
| `created_at` | TIMESTAMP | 등록일 | `2019-07-01T00:00:00Z` | N | RepairShop 속성 |

---

## 3. 소스 → 노드 매핑

| 그래프 노드 | 생성 소스 | 노드 ID 속성 | 소스 PRIMARY KEY 컬럼 | 주요 속성 (소스 컬럼 → 노드 속성) |
|---|---|---|---|---|
| `Customer` | customers | `customer_id` | `customer_id` | `gender`, `birth_date`, `created_at` / `name`·`id_number`·`email` → 해시 저장 |
| `Claim` | claims | `claim_id` | `claim_id` | `incident_date`, `report_date`, `incident_type`, `incident_location`, `claimed_amount`, `paid_amount`, `claim_status`, `fraud_label` |
| `Policy` | policies | `policy_id` | `policy_id` | `product_code`, `coverage_type`, `start_date`, `end_date`, `premium_amount`, `coverage_limit`, `status` |
| `Vehicle` | vehicles | `vehicle_id` | `vehicle_id` | `vin`, `license_plate`, `make`, `model`, `year`, `color`, `registered_at` |
| `Account` | accounts | `account_id` | `account_id` | `account_no_normalized`(병합키), `bank_code`, `bank_name`, `account_type` / `account_no`·`account_holder` → 해시 저장 |
| `Hospital` | hospitals | `hospital_id` | `hospital_id` | `institution_code`, `name`, `type`, `address`, `phone`, `license_no`, `specialties` |
| `RepairShop` | repair_shops | `repair_shop_id` | `repair_shop_id` | `business_reg_no`, `name`, `type`, `address`, `phone`, `license_no`, `rating` |
| `Address` | customers (ETL 파생) | `address_id` = hash(address_normalized) | `address_normalized` | `raw_address`, `address_normalized` |
| `Phone` | customers (ETL 파생) | `phone_id` = hash(phone_normalized) | `phone_normalized` | `phone_normalized` / `phone_number` → 해시 저장 |

> **Address·Phone 노드**는 `customers` 소스에서 ETL이 파생 생성한다. 동일 정규화 값이면 단일 노드로 병합되어 복수 고객이 주소/전화를 공유하는 관계가 그래프에 드러난다.

---

## 4. 소스 → 엣지 매핑 (11종)

| # | 엣지 타입 | 방향 | 소스 테이블 | 소스 컬럼 (FROM → TO) | 엣지 속성 | 탐지 활용 |
|---|---|---|---|---|---|---|
| 1 | `FILED` | `(Customer)-[:FILED]->(Claim)` | claims | `customer_id` → `claim_id` | `filed_at` = `claims.report_date` | 고객별 청구 빈도, 링 멤버 식별 |
| 2 | `HOLDS` | `(Customer)-[:HOLDS]->(Policy)` | policies | `customer_id` → `policy_id` | `since` = `policies.start_date` | 동일 고객의 다중 계약 탐지 |
| 3 | `COVERS` | `(Policy)-[:COVERS]->(Vehicle)` | policies | `policy_id` → `vehicle_id` | `start_date`, `end_date`, `coverage_type` | 계약·차량 연결 경로 확인 |
| 4 | `INVOLVES` | `(Claim)-[:INVOLVES]->(Vehicle)` | claims | `claim_id` → `vehicle_id` | `role` = `"accident_vehicle"` | 동일 차량 반복 청구 탐지 |
| 5 | `TREATED_AT` | `(Claim)-[:TREATED_AT]->(Hospital)` | claims | `claim_id` → `hospital_id` | `treatment_date` = `claims.incident_date` | 병원 핫스팟 탐지 (FR-3.2) |
| 6 | `REPAIRED_AT` | `(Claim)-[:REPAIRED_AT]->(RepairShop)` | claims | `claim_id` → `repair_shop_id` | `repair_date` = `claims.incident_date` | 정비소 핫스팟 탐지 (FR-3.2) |
| 7 | `PAID_TO` | `(Claim)-[:PAID_TO]->(Account)` | claims | `claim_id` → `account_id` | `amount` = `claims.paid_amount`, `paid_at` | 계좌 공유 탐지 — 서로 다른 고객 청구금이 동일 계좌로 지급 (FR-3.1) |
| 8 | `LIVES_AT` | `(Customer)-[:LIVES_AT]->(Address)` | customers (ETL) | `customer_id` → hash(`address_normalized`) | `since` = `customers.created_at` | 주소 공유 고객 군집 탐지 (FR-3.1) |
| 9 | `OWNS` | `(Customer)-[:OWNS]->(Vehicle)` | vehicles | `customer_id` → `vehicle_id` | `registered_at` | 차량 소유 관계, 공유 차량 탐지 |
| 10 | `HAS_PHONE` | `(Customer)-[:HAS_PHONE]->(Phone)` | customers (ETL) | `customer_id` → hash(`phone_normalized`) | `since` = `customers.created_at` | 전화 공유 고객 탐지 (FR-3.1) |
| 11 | `WITNESSED_BY` | `(Claim)-[:WITNESSED_BY]->(Claim)` | claims | `claim_id` → `witness_claim_ids[]` (각 원소 1엣지) | `witness_type` = `"cross_witness"` | **crash-for-cash 교차 목격 탐지 핵심** (FR-3.3) |

### 4.1 WITNESSED_BY 상세 — crash-for-cash 핵심 엣지

crash-for-cash(고의 추돌 사기)에서 사기 링 구성원들은 **서로의 사고를 교차 목격**한다. 즉, A가 B의 사고 목격자로 청구서에 등재되고, B가 A의 다른 사고를 목격하는 순환 구조다.

```
(Claim A) -[:WITNESSED_BY]-> (Claim B)
(Claim B) -[:WITNESSED_BY]-> (Claim A)
           ↕                  ↕
     (Customer X)       (Customer Y)
         via FILED           via FILED
```

**생성 로직:**
1. `claims.witness_claim_ids` 배열의 각 원소를 순회
2. `(source_claim_id, target_claim_id)` 쌍마다 엣지 1개 생성
3. 멱등 병합 키: `(claim_id, witness_claim_id)` 조합의 유니크 제약

**탐지 쿼리 패턴 (FR-3.3):**
```cypher
MATCH (c1:Claim)-[:WITNESSED_BY]->(c2:Claim)
      <-[:FILED]-(cust2:Customer),
      (c1)<-[:FILED]-(cust1:Customer)
WHERE cust1 <> cust2
  AND (c2)-[:WITNESSED_BY]->(c1)   // 교차 (양방향 목격)
RETURN cust1, cust2, c1, c2
```

---

## 5. 엔티티 해소 키 및 정규화 규칙

FR-1.2 / Q4: **결정적 완전일치(정규화 후)만 자동 병합**. 퍼지 매칭은 조사관 확인 큐로 보낸다.

### 5.1 자동 병합 대상 속성

| 엔티티 | 병합 키 속성 | 소스 컬럼 | 그래프 노드 |
|---|---|---|---|
| 계좌번호 | `Account.account_no_normalized` | `accounts.account_no_normalized` | `Account` |
| 차대번호 | `Vehicle.vin` | `vehicles.vin` | `Vehicle` |
| 전화번호 | `Phone.number` | `customers.phone_normalized` | `Phone` |
| 주소 | `Address.address_normalized` | `customers.address_normalized` | `Address` |

### 5.2 정규화 규칙

| 필드 | 정규화 규칙 | 예시 전 | 예시 후 |
|---|---|---|---|
| **계좌번호** (`account_no`) | 하이픈·공백·슬래시 제거 | `011-1234-567890` | `0111234567890` |
| **차대번호** (`vin`) | 대문자 변환, 공백·하이픈 제거 | `kmhdn41bp3u-123456` | `KMHDN41BP3U123456` |
| **전화번호** (`phone_number`) | 숫자만 추출 (비숫자 전부 제거) | `010-1234-5678` / `010 1234 5678` | `01012345678` |
| **주소** (`address`) | 공백 제거, 소문자화, 특수문자 제거 | `서울시 강남구 테헤란로 123 2층` | `서울시강남구테헤란로123` |

> **ETL 구현 위치:** `ingest/normalize.py` — `normalize_account_no()`, `normalize_vin()`, `normalize_phone()`, `normalize_address()` 함수로 분리 구현.

### 5.3 병합 방식 (Neo4j MERGE)

```cypher
// 예: Account 노드 병합 (계좌번호 기준)
MERGE (a:Account {account_no_normalized: $account_no_normalized})
ON CREATE SET a.account_id    = $account_id,
              a.bank_code     = $bank_code,
              a.bank_name     = $bank_name,
              a.account_type  = $account_type,
              a.account_no_hash = $account_no_hash,
              a.created_at    = $created_at
```

**멱등성 보장:** `MERGE`로 동일 정규화 키가 이미 존재하면 노드를 새로 만들지 않고 기존 노드에 연결한다. 재실행 시 중복 노드 0 (FR-1.1 AC).

---

## 6. 가명처리(PII 해시) 대상

FR-1.3: **PII 원문은 그래프에 평문 저장 금지.** 저장 방식은 `sha256(SALT + 원문)`, SALT는 환경변수 `THOTH_PII_SALT`로 주입.

| 소스 | 컬럼 | PII 유형 | 저장 속성명 | 처리 방식 |
|---|---|---|---|---|
| customers | `name` | 실명 | `Customer.name_hash` | sha256(salt + name) |
| customers | `id_number` | 주민등록번호 | `Customer.id_hash` | sha256(salt + id_number), **평문 완전 금지** |
| customers | `phone_number` | 연락처 | `Phone.number_hash` | sha256(salt + phone_normalized) |
| customers | `email` | 이메일 | `Customer.email_hash` | sha256(salt + email) |
| accounts | `account_no` | 계좌번호 | `Account.account_no_hash` | sha256(salt + account_no_normalized) |
| accounts | `account_holder` | 예금주명 | `Account.holder_hash` | sha256(salt + account_holder) |

> **주의사항:**
> - `id_number`는 어떠한 형태로도 평문·부분 마스킹으로 저장하지 않는다.
> - 생년월일(`birth_date`)은 해시 불필요하나 내부 접근 통제(RBAC) 대상이다.
> - 병원·정비소 개인 소유자 정보가 포함될 경우 해시 처리를 별도 추가한다.
> - 검증 AC: `pytest tests/test_pii.py` — 원문 PII가 Neo4j 그래프 어디에도 저장되지 않음을 단언.

---

## 7. 적재 멱등성 키 요약

FR-1.1 AC: 동일 소스 재실행 시 중복 노드·엣지 0.

| 노드 / 엣지 | 멱등 병합 키 | Neo4j 제약 |
|---|---|---|
| `Customer` | `customer_id` | `UNIQUE` |
| `Claim` | `claim_id` | `UNIQUE` |
| `Policy` | `policy_id` | `UNIQUE` |
| `Vehicle` | `vehicle_id` (내부) / `vin` (해소) | `UNIQUE` on `vin` |
| `Account` | `account_no_normalized` | `UNIQUE` |
| `Hospital` | `hospital_id` | `UNIQUE` |
| `RepairShop` | `repair_shop_id` | `UNIQUE` |
| `Address` | `address_normalized` | `UNIQUE` |
| `Phone` | `phone_normalized` | `UNIQUE` |
| `(Customer)-[:FILED]->(Claim)` | `(customer_id, claim_id)` | 엣지 중복 방지 — `MERGE` |
| `(Claim)-[:WITNESSED_BY]->(Claim)` | `(claim_id, witness_claim_id)` | 엣지 중복 방지 — `MERGE` |

> 나머지 엣지 유형도 동일하게 `MERGE`로 삽입하며 소스 FK 조합을 키로 사용한다.

---

*이 문서는 `ingest/` 배치 파이프라인 구현의 기준 명세다. 스키마 변경 시 반드시 이 문서를 먼저 수정한 후 `graph/01_schema.cypher`와 `ingest/` 코드에 반영한다 (FR-2.2 온톨로지 버전관리).*
