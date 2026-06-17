# THOTH-ON 온톨로지 명세 (Ontology Specification)

> 보험 사기탐지 지식그래프 — 자동차보험 PoC
> 관련 요구사항: **FR-2.2 (P1)** 온톨로지 정의 문서화·버전관리 + (옵션) SHACL 무결성 게이트
> 본 문서는 `graph/01_schema.cypher` 의 implicit 스키마를 사람이 읽는 **명문 온톨로지**로 승격한 것이다.

| 항목 | 내용 |
|---|---|
| 대상 | THOTH-ON 보험 사기탐지 지식그래프 (Neo4j 5 + GDS) |
| 온톨로지 버전 | **v0.3** |
| 범위 | 자동차보험 사기 PoC (한국 실제 수법 5종 중심) |
| 최종 수정 | 2026-06-17 |
| 권위 소스(SSOT) | 노드/엣지/제약의 **기계 판본**은 `graph/01_schema.cypher` 이다. 본 문서는 그 의미론(semantics) 판본이며 두 문서는 항상 일치해야 한다. |
| 관련 문서 | `ingest/mapping.md`(속성 매핑·정규화·가명처리), `detection/03_fraud_queries.cypher`(탐지 의미) |

---

## 목차
1. [개요 & 범위](#1-개요--범위)
2. [온톨로지 버전 & 변경관리 규칙](#2-온톨로지-버전--변경관리-규칙)
3. [클래스 (노드 9종)](#3-클래스-노드-9종)
4. [관계 (엣지 11종)](#4-관계-엣지-11종)
5. [속성·제약 (facet)](#5-속성제약-facet)
6. [표준 매핑 (재사용 검토)](#6-표준-매핑-재사용-검토)
7. [SHACL 무결성 게이트 로드맵](#7-shacl-무결성-게이트-로드맵)
8. [변경관리 규칙 (요약)](#8-변경관리-규칙-요약)

---

## 1. 개요 & 범위

### 1.1 목적
THOTH-ON 은 자동차보험 사기를 **관계(그래프)** 로 포착하는 지식그래프 플랫폼이다. 본 온톨로지는 사기탐지에 필요한 **개념(클래스)·관계(엣지)·식별자·제약**을 정의하여:

- ETL 적재(`ingest/`)와 탐지 쿼리(`detection/`)가 동일한 의미 모델을 공유하게 하고,
- 설명가능성(FR-5.2) 근거문 생성 시 **온톨로지 컨텍스트**를 제공하며,
- 스키마 진화를 버전·마이그레이션으로 추적(FR-2.2 AC)한다.

### 1.2 범위 (In scope)
- 자동차보험 1개 라인의 청구·고객·계약·차량·결제·치료·수리 도메인.
- crash-for-cash(고의 추돌 보험사기 링) 탐지에 필요한 9개 클래스 / 11개 관계.
- 결정적 엔티티 해소 키(`account_no`·`vin`·`number_hash`·`address_normalized`).

### 1.3 비범위 (Non-goals, PoC 제외)
| 항목 | 사유 / 차후 계획 |
|---|---|
| 실손·장기·일반보험 라인 | V2 다중 라인 확장 시 추가 |
| 알선자/설계사 클래스 (`Broker`, `Agent`) | V2 — 조직형 사기 확장 시 (PRD §9 확장 항목) |
| `MedicalProvider`·`IncidentLocation`·`Device/IP` | V2 |
| 완전한 OWL/RDF 트리플 스토어 | PoC 는 **속성그래프(LPG)** 기반. OWL 표준은 *개념 차용*만 하고 추론 엔진은 도입하지 않음 |
| 온라인 추론(reasoning)·SWRL 룰 | 탐지는 Cypher/GDS 로 수행. 온톨로지 추론은 비범위 |

> **PoC 경량성 원칙:** 본 온톨로지는 LPG(Labeled Property Graph) 위의 *경량 도메인 어휘*다. 외부 표준(FIBO 등)은 **개념·명명 정렬**에만 차용하며, OWL import·트리플 변환은 무리하게 채택하지 않는다(§6 참조).

---

## 2. 온톨로지 버전 & 변경관리 규칙

### 2.1 버전 정책
- 온톨로지 버전은 `MAJOR.MINOR` 로 관리한다.
  - **MINOR**: 속성 추가, 인덱스 추가, 비파괴적 변경.
  - **MAJOR**: 클래스/관계 추가·삭제, 식별자(병합키) 변경, 카디널리티 의미 변경 등 파괴적 변경.
- 현재 버전: **v0.2** (WP0~WP5 적용 상태).

### 2.2 변경 이력

| 버전 | 일자 | 변경 내용 | 마이그레이션 근거 |
|---|---|---|---|
| v0.1 | 2026-06-16 | 최초 implicit 스키마. 노드 9종 / 엣지 11종. `Phone` 병합키 = 평문 `number`, `Customer.name` 평문 인덱스 | `graph/01_schema.cypher` 초안 |
| **v0.2** | 2026-06-16 | **(1)** `Phone` 병합키를 `number_hash`(sha256)로 변경, 평문 `number` 그래프 저장 금지 (FR-1.3). **(2)** `Customer.name` 평문 인덱스 → `name_hash` 인덱스 대체. **(3)** 본 명문 온톨로지 문서(FR-2.2) 신설 | `graph/01_schema.cypher` L51–54, L78–80 (`phone_number_hash_unique`, `customer_name_hash`) |
| **v0.3** | 2026-06-17 | **(MAJOR)** WP-KR 한국 실제 사기 수법 확장 — 노드 2종 추가(`Broker`/`Agent`), 엣지 2종 추가(`BROKERED`/`SOLD_POLICY`). 합성데이터를 금감원·KIRI 보고 한국 수법 5종(허위입원 조직형·고의충돌 공모·정비비 과다청구·설계사 개입·운전자 교체)으로 교체. 노드 11종 / 엣지 13종 | `graph/01_schema.cypher` (`broker_id_unique`, `agent_id_unique`, `broker_name`, `agent_name`); `ingest/synth_generator.py` RING_PATTERNS |

### 2.3 추적 규칙 (FR-2.2 AC — *온톨로지 변경이 마이그레이션으로 추적됨*)

> **모든 온톨로지 변경은 `graph/01_schema.cypher` 마이그레이션(멱등 `CREATE CONSTRAINT/INDEX ... IF NOT EXISTS`)으로 추적한다.**

변경 절차(반드시 이 순서):
1. **본 문서(`graph/ontology.md`)** 에 변경 의도·버전·근거를 먼저 기록한다.
2. **`graph/01_schema.cypher`** 에 멱등 마이그레이션 구문을 반영한다(제약/인덱스).
3. **`ingest/mapping.md`** 및 ETL 코드(`ingest/`)에 속성 매핑을 반영한다.
4. 검증: 스키마 적용 후 `SHOW CONSTRAINTS` / `SHOW INDEXES` 결과가 본 문서 §5 와 일치하는지 확인한다.

→ Git 커밋 단위로 위 세 파일이 함께 변경되므로, **온톨로지 변경 = cypher 마이그레이션 diff** 로 1:1 추적된다.

---

## 3. 클래스 (노드 11종)

`graph/01_schema.cypher` 의 노드 온톨로지와 정확히 일치한다(기존 9종 + WP-KR 2종).

| 클래스 | 정의 | 식별자 (UNIQUE 병합키) | 핵심 속성 | 데이터 등급 (PII) |
|---|---|---|---|---|
| **Customer** | 보험 계약자·피보험자·청구인(자연인) | `customer_id` | `gender`, `birth_date`, `dob`, `created_at`, `name_hash`, `id_hash`, `email_hash`, `ring_id`/`is_fraud_ring`(ground truth) | **PII 보유** — `name`/`id_number`/`email` 은 해시만 저장. `birth_date`(dob)는 준PII(RBAC 통제) |
| **Claim** | 보험 청구 1건 (사고 1건 = 노드 1개), 리스크 점수 부여 대상 | `claim_id` | `incident_date`, `report_date`(=`filed_at`), `incident_type`, `incident_location`, `claimed_amount`, `paid_amount`, `claim_status`(=`status`), `risk_score`, `fraud_label`, `ring_id`(ground truth) | 비PII (사고/금액 사실) |
| **Policy** | 보험 계약(증권) | `policy_id` | `product_code`, `coverage_type`(=`policy_type`), `start_date`, `end_date`, `premium_amount`, `coverage_limit`, `status` | 비PII |
| **Vehicle** | 차량. **VIN 기반 엔티티 해소 핵심** | `vin` (정규화: 대문자, 하이픈·공백 제거) | `vehicle_id`(내부), `plate_no`/`license_plate`, `make`, `model`, `year`, `color`, `registered_at` | 비PII (단, `plate_no`는 준식별자) |
| **Address** | 주소(공유 주소 링크 탐지). ETL 파생 노드 | `address_id` = hash(`address_normalized`) | `raw_address`, `address_normalized` | 준PII — `raw_address` 는 내부 접근 통제 대상 |
| **Account** | 금융 계좌(공유 계좌 탐지). **crash-for-cash 링이 공유** | `account_no` (정규화: 하이픈·공백·슬래시 제거) | `account_no_normalized`(병합키 원천), `bank_code`, `bank_name`, `account_type`, `account_no_hash`, `holder_hash` | **PII 보유** — `account_no`/`account_holder` 는 해시 저장 |
| **Phone** | 전화번호(공유 전화 탐지). ETL 파생 노드 | `number_hash` = sha256(salt + `phone_normalized`) | `phone_id`, (평문 `number` **저장 금지**) | **PII** — v0.2부터 병합키 자체가 해시. 평문 전화번호 미저장 |
| **Hospital** | 의료기관(병원·의원·한의원). 핫스팟 탐지 대상 | `hospital_id` | `institution_code`, `name`, `type`, `address`, `phone`, `license_no`, `specialties` | 비PII (기관 정보) |
| **RepairShop** | 자동차 정비소. 핫스팟 탐지 대상 | `shop_id` (소스 `repair_shop_id`) | `business_reg_no`, `name`, `type`, `address`, `phone`, `license_no`, `rating` | 비PII (사업체 정보) |
| **Broker** ⭐WP-KR | 사기 알선자/브로커. 허위입원 조직형(나이롱 환자 모객) 허브 | `broker_id` | `name`, `business_reg_no`, `phone`, `region` | 준PII (사업자 정보) |
| **Agent** ⭐WP-KR | 보험설계사. 설계사 개입 사기(가공계약·보험금 가로채기) 허브 | `agent_id` | `name`, `license_no`, `agency`, `phone` | 준PII (설계사 정보) |

> **명명 주의:** 일부 속성은 소스 컬럼명(`ingest/mapping.md`)과 그래프 속성명이 다르다. 인덱스가 거는 그래프 속성명을 기준으로 한다 — 예: `report_date`→`filed_at`, `coverage_type`→`policy_type`, `claim_status`→`status`, `license_plate`→`plate_no`, `repair_shop_id`→`shop_id`. (출처: `01_schema.cypher` 인덱스 정의 L69–98)

---

## 4. 관계 (엣지 13종)

`graph/01_schema.cypher` 의 엣지 온톨로지와 정확히 일치한다(기존 11종 + WP-KR 2종). 카디널리티는 PoC 도메인 규칙 기준 표기(`1`=정확히 1, `0..1`=선택, `*`=다수).

| 엣지 | 정의 | 도메인 (FROM) | 레인지 (TO) | 카디널리티 | 사기탐지 의미 |
|---|---|---|---|---|---|
| **FILED** | 청구인이 청구를 접수 | Customer | Claim | Customer `1` → Claim `*` | 고객별 청구 빈도, 링 멤버 식별 기준 엣지 |
| **HOLDS** | 계약자가 보험 계약 보유 | Customer | Policy | Customer `1` → Policy `*` | 동일 고객 다중 계약 탐지 |
| **COVERS** | 계약이 차량을 피보험 | Policy | Vehicle | Policy `1` → Vehicle `1` (PoC) | 계약·차량 연결 경로 |
| **INVOLVES** | 청구가 사고 차량을 포함 | Claim | Vehicle | Claim `1` → Vehicle `1` | 동일 차량 반복 청구 탐지 |
| **TREATED_AT** | 청구가 치료 병원에서 발생 | Claim | Hospital | Claim `0..1` → Hospital `*` | **병원 핫스팟** (FR-3.2) — distinct 청구인 집중 |
| **REPAIRED_AT** | 청구가 정비소에서 수리 | Claim | RepairShop | Claim `0..1` → RepairShop `*` | **정비소 핫스팟** (FR-3.2) — 링 멤버 수리 집중 |
| **PAID_TO** | 청구 보험금이 계좌로 지급 | Claim | Account | Claim `1` → Account `*` | **계좌 공유 탐지** (FR-3.1) — 서로 다른 고객 청구금이 동일 계좌로 (링 핵심 신호) |
| **LIVES_AT** | 고객이 주소에 거주 | Customer | Address | Customer `1` → Address `*` (병합 시) | 주소 공유 군집 탐지 (FR-3.1, 약한 신호) |
| **OWNS** | 고객이 차량 소유 | Customer | Vehicle | Customer `*` → Vehicle `*` | 공유 차량(대포차/명의도용) 탐지 (FR-3.1) |
| **HAS_PHONE** | 고객이 전화번호 보유 | Customer | Phone | Customer `1` → Phone `*` (병합 시) | 전화 공유 고객 탐지 (FR-3.1) |
| **WITNESSED_BY** ⭐ | 청구가 다른 청구에 의해 교차 목격됨 | Claim | **Claim** | Claim `*` → Claim `*` | **★ crash-for-cash 탐지 핵심 ★** |
| **BROKERED** ⭐WP-KR | 브로커가 고객을 알선 | Broker | Customer | Broker `1` → Customer `*` | **허위입원 조직형** — 한 브로커가 다수 환자를 한 병원에 알선(브로커 허브 신호, 정밀 ~1.0) |
| **SOLD_POLICY** ⭐WP-KR | 설계사가 계약을 모집 | Agent | Policy | Agent `1` → Policy `*` | **설계사 개입** — 모집 고객 청구금이 한 공통계좌로 집중(가로채기) 시 설계사 허브 신호 |

### 4.1 WITNESSED_BY — crash-for-cash 핵심 관계 (강조)

`01_schema.cypher` L29 의 주석은 `(Claim)-[:WITNESSED_BY]->(Customer)` 로 표기되어 있으나, **실 구현·탐지 의미는 `(Claim)-[:WITNESSED_BY]->(Claim)`** 이다(`ingest/mapping.md` §4 #11, `detection/03_fraud_queries.cypher` Q3 기준 — `witness_claim_ids[]` 의 각 청구 ID로 청구↔청구 엣지 생성).

> **명세 정정 노트:** 권위 의미론은 **Claim→Claim** 이다. `01_schema.cypher` L29 의 주석 레인지(`Customer`)는 v0.3 마이그레이션에서 `Claim` 으로 문구를 정정해야 한다(§2.3 추적 규칙). 제약/인덱스에는 영향 없음.

crash-for-cash 사기 링은 **서로의 사고를 양방향 교차 목격**한다:

```
(Claim A) -[:WITNESSED_BY]-> (Claim B)
(Claim B) -[:WITNESSED_BY]-> (Claim A)   ← 상호(양방향)일 때만 사기 순환 신호
     ↑                            ↑
(Customer X) -[:FILED]->     (Customer Y) -[:FILED]->
```

- **단방향** 목격은 정상 사고에서도 발생(노이즈) → 신호 아님.
- **양방향(상호) 교차 목격** + (동일 Account·Hospital·RepairShop 공유)가 결합될 때 링으로 확정(`detection/03_fraud_queries.cypher` Q3 + Q1/Q2 결합).
- 멱등 병합키: `(claim_id, witness_claim_id)` 조합(`ingest/mapping.md` §4.1).

---

## 5. 속성·제약 (facet)

### 5.1 UNIQUE 제약 (엔티티 해소 병합키) — `01_schema.cypher` L36–63 과 일치

| 클래스 | UNIQUE 속성 | 제약명 (cypher) |
|---|---|---|
| Customer | `customer_id` | `customer_id_unique` |
| Claim | `claim_id` | `claim_id_unique` |
| Policy | `policy_id` | `policy_id_unique` |
| Vehicle | `vin` | `vehicle_vin_unique` |
| Account | `account_no` | `account_no_unique` |
| Phone | `number_hash` | `phone_number_hash_unique` |
| Address | `address_id` | `address_id_unique` |
| Hospital | `hospital_id` | `hospital_id_unique` |
| RepairShop | `shop_id` | `repairshop_id_unique` |
| Broker (WP-KR) | `broker_id` | `broker_id_unique` |
| Agent (WP-KR) | `agent_id` | `agent_id_unique` |

### 5.2 인덱스 — `01_schema.cypher` L69–98 과 일치

| 클래스 | 인덱스 속성 | 인덱스명 |
|---|---|---|
| Claim | `filed_at`, `risk_score`, `status` | `claim_filed_at`, `claim_risk_score`, `claim_status` |
| Customer | `name_hash`, `dob` | `customer_name_hash`, `customer_dob` |
| Policy | `start_date`, `policy_type` | `policy_start_date`, `policy_type` |
| Vehicle | `plate_no` | `vehicle_plate` |
| Hospital | `name` | `hospital_name` |
| RepairShop | `name` | `repairshop_name` |
| Broker (WP-KR) | `name` | `broker_name` |
| Agent (WP-KR) | `name` | `agent_name` |

### 5.3 결정적 엔티티 해소 (자동 병합) — FR-1.2 / Q4

> 정책: **정규화 후 완전일치(deterministic exact match)만 자동 병합.** 퍼지 매칭은 조사관 확인 큐로 보낸다.

| 엔티티 | 병합키 | 정규화 규칙 (`ingest/normalize.py`) |
|---|---|---|
| Account | `account_no` (← `account_no_normalized`) | 하이픈·공백·슬래시 제거 |
| Vehicle | `vin` | 대문자 변환, 하이픈·공백 제거 |
| Phone | `number_hash` (← sha256(salt + `phone_normalized`)) | 숫자만 추출 후 해시 |
| Address | `address_id` (← hash(`address_normalized`)) | 공백·특수문자 제거, 소문자화 |

### 5.4 가명처리 대상 (PII 해시) — FR-1.3

> **PII 원문은 그래프에 평문 저장 금지.** 저장 방식 `sha256(THOTH_PII_SALT + 정규화원문)`.

| 클래스 | 평문 컬럼 | 저장 속성 |
|---|---|---|
| Customer | `name` / `id_number` / `email` | `name_hash` / `id_hash` / `email_hash` |
| Phone | `phone_number` | `number_hash` (병합키 겸용) |
| Account | `account_no` / `account_holder` | `account_no_hash` / `holder_hash` |

> `id_number`(주민번호)는 부분 마스킹조차 금지. 검증: `pytest tests/test_pii.py`.

---

## 6. 표준 매핑 (재사용 검토)

**스탠포드 7-step 온톨로지 개발법 Step 2(기존 온톨로지 재사용 검토)** 에 따라, 보험·금융 도메인 표준에서 차용 가능한 개념을 조사하여 THOTH-ON 클래스에 정렬했다. PoC 경량성 원칙상 **개념·명명 정렬(align)** 수준만 채용하며, OWL import/트리플 변환 같은 무거운 채택은 미루었다.

### 6.1 FIBO (Financial Industry Business Ontology)
출처: <https://spec.edmcouncil.org/fibo/> · <https://github.com/edmcouncil/fibo>

| THOTH-ON 클래스/관계 | FIBO 대응 개념 | 권고 시점 | 비고 |
|---|---|---|---|
| Customer | `fibo-fnd-pty-pty:IndependentParty` / `fibo-fnd-aap-ppl:Person` (Party-in-Role 패턴) | **V1** | FIBO 의 *Party-in-Role* 로 「계약자/피보험자/청구인」역할 분리 시 차용 |
| Policy | `fibo-fbc-pas-fpas` 계열 보험·금융상품 계약 | **V1** | 계약 클래스 명명·속성 정렬 |
| Account | `fibo-fbc-dae-dbt:Account` (계좌) | **V1** | 계좌 표준 어휘 정렬 |
| HOLDS / COVERS | FIBO 계약-당사자/피보험목적물 관계 | V2 | 다중 보험라인 확장 시 |

권고: **PoC=차용 안 함**(LPG 경량 유지). FIBO 는 OWL/DL 기반으로 무겁다 — V1에서 *명명·역할 패턴*만 선별 정렬.

### 6.2 schema.org
출처: <https://schema.org/Person> · <https://schema.org/PostalAddress> · <https://schema.org/Vehicle> · <https://schema.org/InsuranceAgency> · <https://schema.org/docs/financial.html>

| THOTH-ON 클래스 | schema.org 대응 | 권고 시점 | 비고 |
|---|---|---|---|
| Customer | `schema:Person` | **지금(문서 정렬)** | 가벼움. 외부 노출(API/JSON-LD)시 즉시 정렬 가능 |
| Address | `schema:PostalAddress` | **지금(문서 정렬)** | `addressLocality` 등 정렬 |
| Vehicle | `schema:Vehicle` | **지금(문서 정렬)** | `vehicleIdentificationNumber`↔`vin` 정렬 |
| Hospital | `schema:Hospital` (`MedicalOrganization` 하위) | V1 | |
| RepairShop | `schema:AutoRepair` | V1 | |
| Account | `schema:BankAccount` (`FinancialProduct` 하위) | V1 | |
| (V2) Broker/Agent | `schema:InsuranceAgency` | V2 | 알선자/설계사 도입 시 |

권고: **schema.org 는 경량이라 지금부터 문서 레벨 정렬 권장.** API JSON-LD 출력·외부 상호운용에 유리. 그래프 저장 모델은 그대로 유지.

### 6.3 W3C PROV-O (Provenance Ontology)
출처: <https://www.w3.org/TR/prov-o/> (네임스페이스 `http://www.w3.org/ns/prov#`)

| THOTH-ON 개념 | PROV-O 대응 | 권고 시점 | 비고 |
|---|---|---|---|
| Claim / 탐지 결과(risk_score) | `prov:Entity` | **V1** | 판정·점수 산출물의 출처 추적 |
| 탐지 실행(`detect.py` 잡), 적재 배치 | `prov:Activity` | **V1** | "어떤 잡이 이 점수를 생성?" |
| 조사관 / ETL 파이프라인 / LLM provider | `prov:Agent` | **V1** | 감사·판정 책임 주체 |
| 점수/케이스 ↔ 산출 잡 | `prov:wasGeneratedBy` | **V1** | 감사로그(NFR 감사) + 설명가능성(FR-5) 근거 추적 |
| 조사관 판정(FR-4.3 피드백) | `prov:wasAttributedTo` | **V1** | 판정 주체 추적 |

권고: **PROV-O 는 THOTH-ON 의 감사/판정 추적(NFR 보안·감사, FR-7 피드백)과 정렬도 높음.** V1에서 *감사로그 스키마*에 PROV 어휘(Activity/Agent/wasGeneratedBy)를 차용하면 설명가능성·규제 대응에 직접 기여. PoC 단계는 별도 `audit` 로그로 충분.

### 6.4 W3C Time Ontology (OWL-Time)
출처: <https://www.w3.org/TR/owl-time/> (네임스페이스 `http://www.w3.org/2006/time#`)

| THOTH-ON 개념 | OWL-Time 대응 | 권고 시점 | 비고 |
|---|---|---|---|
| `incident_date`, `report_date`/`filed_at`, `treatment_date`, `paid_at` | `time:Instant` | **V2** | 단일 시점 |
| Policy `start_date`~`end_date` (보장 기간) | `time:Interval` (`hasBeginning`/`hasEnd`) | **V2** | 보장기간 vs 사고시점 정합성 검증에 유용 |
| 시간축 시각화(FR-6.2) | OWL-Time 관계(`time:before`/`after`) | V2 | Allen interval algebra 차용 가능 |

권고: **V2 보류.** PoC 는 ISO-8601 문자열/날짜 속성으로 충분. 시간 추론(보장기간 외 사고 등) 본격화 시 OWL-Time 정렬.

### 6.5 종합 권고
| 표준 | 권고 |
|---|---|
| schema.org | **지금** 문서 레벨 정렬(API/JSON-LD 상호운용). 저비용·고효용 |
| PROV-O | **V1** 감사·판정 추적 스키마에 차용(규제·설명가능성 직접 기여) |
| FIBO | **V1** 명명·Party-in-Role 패턴만 선별 정렬(전면 OWL import 지양) |
| OWL-Time | **V2** 시간 추론 본격화 시 |

> **공통 원칙:** 네 표준 모두 *전면 채택(OWL import/트리플 변환)은 하지 않는다.* THOTH-ON 은 Neo4j LPG 위 경량 도메인 어휘를 유지하고, 표준은 **개념·명명·외부 출력 정렬**로만 차용한다.

---

## 7. SHACL 무결성 게이트 로드맵

SHACL(Shapes Constraint Language, RDF 그래프 검증 표준)은 RDF/트리플 기반이다. THOTH-ON 은 LPG(Neo4j)이므로, **단계적**으로 무결성 게이트를 강화한다.

### 7.1 PoC (현재 v0.2) — 제약·인덱스 + 적재 검증
- Neo4j `UNIQUE CONSTRAINT` (§5.1) 로 식별자 무결성 보장.
- 적재 멱등성·ER·PII 단언은 `tests/`(`test_idempotency`, `test_entity_resolution`, `test_pii`)로 게이트.
- **SHACL 미도입** (LPG 위에서 과도).

### 7.2 V1 — 개념적 SHACL shape (참고 명세, RDF 익스포트 시 적용)
RDF 익스포트(neosemantics/n10s) 시 적용할 shape 예시:

**Shape 1 — Customer 필수 식별자 + PII 평문 금지**
```turtle
@prefix sh:   <http://www.w3.org/ns/shacl#> .
@prefix thoth:<https://thoth-on.ai/onto#> .

thoth:CustomerShape a sh:NodeShape ;
  sh:targetClass thoth:Customer ;
  sh:property [ sh:path thoth:customer_id ; sh:minCount 1 ; sh:maxCount 1 ;
                sh:datatype xsd:string ] ;
  sh:property [ sh:path thoth:name_hash ; sh:pattern "^[a-f0-9]{64}$" ;  # sha256만 허용
                sh:message "Customer.name 평문 저장 금지 — name_hash(sha256)만 허용" ] ;
  sh:closed false .
```

**Shape 2 — WITNESSED_BY 레인지 무결성 (Claim→Claim 만 허용)**
```turtle
thoth:WitnessedByShape a sh:NodeShape ;
  sh:targetSubjectsOf thoth:WITNESSED_BY ;
  sh:property [ sh:path thoth:WITNESSED_BY ;
                sh:class thoth:Claim ;                # 레인지는 반드시 Claim
                sh:message "WITNESSED_BY 레인지는 Claim 이어야 함 (crash-for-cash 의미 보존)" ] .
```

### 7.3 V2 — SHACL 게이트 CI 통합
- RDF 익스포트 → `pySHACL` 등으로 CI 검증, 위반 시 적재 차단(무결성 게이트).
- OWL-Time/PROV-O 정렬 후 시간·출처 제약을 shape 로 표현.

---

## 8. 변경관리 규칙 (요약)

1. **SSOT 분리:** 기계 판본 = `graph/01_schema.cypher`, 의미 판본 = 본 문서. **두 문서는 항상 일치**(§2.3 검증 절차).
2. **변경 순서:** `ontology.md`(의도 기록) → `01_schema.cypher`(멱등 마이그레이션) → `ingest/mapping.md`·ETL → 테스트 검증.
3. **버전 증가:** 비파괴 변경=MINOR, 파괴 변경(클래스/관계/병합키)=MAJOR. §2.2 이력표에 근거(cypher 라인) 명시.
4. **추적성(FR-2.2 AC):** 온톨로지 변경 ↔ cypher 마이그레이션 diff 1:1 대응. Git 커밋에 세 파일 동반.
5. **미해결 정정 항목:** `WITNESSED_BY` 의 `01_schema.cypher` L29 주석 레인지(`Customer`)를 차기(v0.3) 마이그레이션에서 `Claim` 으로 정정(§4.1).

---

## 부록. 출처 (Sources)
- FIBO 명세: <https://spec.edmcouncil.org/fibo/> · GitHub: <https://github.com/edmcouncil/fibo>
- schema.org: <https://schema.org/Person>, <https://schema.org/PostalAddress>, <https://schema.org/Vehicle>, <https://schema.org/InsuranceAgency>, <https://schema.org/docs/financial.html>
- W3C PROV-O: <https://www.w3.org/TR/prov-o/>
- W3C OWL-Time: <https://www.w3.org/TR/owl-time/>
- W3C SHACL: <https://www.w3.org/TR/shacl/>
- 내부 근거: `graph/01_schema.cypher`, `ingest/mapping.md`, `detection/03_fraud_queries.cypher`, `PRD.md` §8.2(FR-2.2)·§9

---
*본 문서(`graph/ontology.md`)는 FR-2.2 의 온톨로지 명문 산출물이다. 스키마 변경 시 §2.3·§8 의 절차를 반드시 따른다.*
