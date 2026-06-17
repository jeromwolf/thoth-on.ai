"""배치 적재 파이프라인 — CSV/Parquet → Neo4j MERGE 멱등 적재 (WP1-4).

FR-1.1 (멱등 적재): 모든 노드·엣지를 MERGE 로 삽입하여 재실행 시 중복 0.
FR-1.2 (엔티티 해소): 계좌·차대번호·전화·주소를 정규화 후 병합키로 사용.
FR-1.3 (가명처리): PII(name/id/email/phone/account_no/holder)는 salt 해시 후 저장.

병합키는 graph/01_schema.cypher 의 UNIQUE 제약과 일치한다:
    Customer.customer_id, Claim.claim_id, Policy.policy_id, Vehicle.vin,
    Account.account_no(정규화값 저장), Phone.number_hash(sha256 해시 — WP1-6 가명처리),
    Address.address_id(=hash(정규화주소)), Hospital.hospital_id, RepairShop.shop_id

엣지 13종: FILED, HOLDS, COVERS, INVOLVES, TREATED_AT, REPAIRED_AT,
           PAID_TO, LIVES_AT, OWNS, HAS_PHONE, WITNESSED_BY,
           BROKERED, SOLD_POLICY (WP-KR 한국 조직형 사기 확장)
모두 MERGE 로 삽입하여 엣지 중복도 0.

WP-KR 확장 노드: Broker(broker_id), Agent(agent_id) — 한국 실제 사기 수법
(허위입원 조직형·설계사 개입)의 허브를 표현한다. 소스 brokers.csv/agents.csv 와
관계 소스 brokered.csv/sold_policy.csv 가 있을 때만 적재한다(없으면 graceful skip).

CLI:
    python -m ingest.loader load <data_dir> [--batch-size N]
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

from ingest.normalize import (
    normalize_account_no,
    normalize_address,
    normalize_phone,
    normalize_vin,
)
from ingest.pii import hash_pii
from thoth import db

BATCH_SIZE = 1000


# ==================================================================
# 소스 로딩 (CSV / Parquet)
# ==================================================================
def _read_source(data_dir: Path, name: str) -> list[dict]:
    """``<name>.csv`` 또는 ``<name>.parquet`` 를 읽어 dict 리스트 반환.

    CSV 우선. 표준 csv 모듈로 읽어 의존성을 최소화한다. Parquet 은 pandas 가
    설치된 경우에만 지원한다.
    """
    csv_path = data_dir / f"{name}.csv"
    parquet_path = data_dir / f"{name}.parquet"
    if csv_path.exists():
        with csv_path.open(newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))
    if parquet_path.exists():
        try:
            import pandas as pd  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                f"{parquet_path} 읽기에 pandas 가 필요합니다. pip install pandas pyarrow"
            ) from exc
        return pd.read_parquet(parquet_path).to_dict(orient="records")
    raise FileNotFoundError(f"소스 파일 없음: {csv_path} / {parquet_path}")


def _read_source_optional(data_dir: Path, name: str) -> list[dict]:
    """선택적 소스(없으면 빈 리스트) — WP-KR 브로커/설계사 등 신규 소스 graceful."""
    try:
        return _read_source(data_dir, name)
    except FileNotFoundError:
        return []


def _parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "1", "yes", "y")


def _parse_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _parse_json_list(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(v) for v in value]
    try:
        parsed = json.loads(value)
        return [str(v) for v in parsed] if isinstance(parsed, list) else []
    except (TypeError, ValueError, json.JSONDecodeError):
        return []


def _nz(value: Any) -> str | None:
    """빈 문자열을 None 으로 변환(없는 FK 처리)."""
    if value is None:
        return None
    s = str(value).strip()
    return s or None


# ==================================================================
# 배치 실행 헬퍼
# ==================================================================
def _run_batched(
    sess: Any,
    cypher: str,
    rows: list[dict],
    *,
    batch_size: int = BATCH_SIZE,
) -> int:
    """UNWIND $rows 패턴 쿼리를 배치 단위로 실행. 처리 행 수 반환."""
    total = 0
    for start in range(0, len(rows), batch_size):
        chunk = rows[start:start + batch_size]
        if not chunk:
            continue
        sess.run(cypher, rows=chunk)
        total += len(chunk)
    return total


# ==================================================================
# 노드 적재
# ==================================================================
def _load_customers(sess: Any, rows: list[dict], salt: str, batch_size: int) -> int:
    payload = []
    for r in rows:
        payload.append({
            "customer_id": r["customer_id"],
            "name_hash": hash_pii(r.get("name"), salt=salt),
            "id_hash": hash_pii(r.get("id_number"), salt=salt),
            "email_hash": hash_pii(r.get("email"), salt=salt),
            "dob": _nz(r.get("birth_date")),
            "gender": _nz(r.get("gender")),
            "created_at": _nz(r.get("created_at")),
            "is_fraud_ring": _parse_bool(r.get("is_fraud_ring")),
            "ring_id": r.get("ring_id") or "",
            "ring_pattern": r.get("ring_pattern") or "",
        })
    cypher = """
    UNWIND $rows AS row
    MERGE (c:Customer {customer_id: row.customer_id})
    SET c.name_hash = row.name_hash,
        c.id_hash = row.id_hash,
        c.email_hash = row.email_hash,
        c.dob = row.dob,
        c.gender = row.gender,
        c.created_at = row.created_at,
        c.is_fraud_ring = row.is_fraud_ring,
        c.ring_id = row.ring_id,
        c.ring_pattern = row.ring_pattern
    """
    return _run_batched(sess, cypher, payload, batch_size=batch_size)


def _load_claims(sess: Any, rows: list[dict], batch_size: int) -> int:
    payload = []
    for r in rows:
        payload.append({
            "claim_id": r["claim_id"],
            "incident_date": _nz(r.get("incident_date")),
            "report_date": _nz(r.get("report_date")),
            "filed_at": _nz(r.get("report_date")),  # 인덱스(claim_filed_at)
            "incident_type": _nz(r.get("incident_type")),
            "incident_location": _nz(r.get("incident_location")),
            "claimed_amount": _parse_float(r.get("claimed_amount")),
            "paid_amount": _parse_float(r.get("paid_amount")),
            "status": _nz(r.get("claim_status")),
            "fraud_label": _parse_bool(r.get("fraud_label")),
            "is_fraud_ring": _parse_bool(r.get("is_fraud_ring")),
            "ring_id": r.get("ring_id") or "",
            "ring_pattern": r.get("ring_pattern") or "",
            "created_at": _nz(r.get("created_at")),
        })
    cypher = """
    UNWIND $rows AS row
    MERGE (c:Claim {claim_id: row.claim_id})
    SET c.incident_date = row.incident_date,
        c.report_date = row.report_date,
        c.filed_at = row.filed_at,
        c.incident_type = row.incident_type,
        c.incident_location = row.incident_location,
        c.claimed_amount = row.claimed_amount,
        c.paid_amount = row.paid_amount,
        c.status = row.status,
        c.fraud_label = row.fraud_label,
        c.is_fraud_ring = row.is_fraud_ring,
        c.ring_id = row.ring_id,
        c.ring_pattern = row.ring_pattern,
        c.created_at = row.created_at
    """
    return _run_batched(sess, cypher, payload, batch_size=batch_size)


def _load_policies(sess: Any, rows: list[dict], batch_size: int) -> int:
    payload = []
    for r in rows:
        payload.append({
            "policy_id": r["policy_id"],
            "product_code": _nz(r.get("product_code")),
            "policy_type": _nz(r.get("coverage_type")),  # 인덱스(policy_type)
            "coverage_type": _nz(r.get("coverage_type")),
            "start_date": _nz(r.get("start_date")),
            "end_date": _nz(r.get("end_date")),
            "premium_amount": _parse_float(r.get("premium_amount")),
            "coverage_limit": _parse_float(r.get("coverage_limit")),
            "status": _nz(r.get("status")),
            "created_at": _nz(r.get("created_at")),
        })
    cypher = """
    UNWIND $rows AS row
    MERGE (p:Policy {policy_id: row.policy_id})
    SET p.product_code = row.product_code,
        p.policy_type = row.policy_type,
        p.coverage_type = row.coverage_type,
        p.start_date = row.start_date,
        p.end_date = row.end_date,
        p.premium_amount = row.premium_amount,
        p.coverage_limit = row.coverage_limit,
        p.status = row.status,
        p.created_at = row.created_at
    """
    return _run_batched(sess, cypher, payload, batch_size=batch_size)


def _load_vehicles(sess: Any, rows: list[dict], batch_size: int) -> int:
    """차량 적재 — 병합키는 정규화 VIN (schema: Vehicle.vin UNIQUE)."""
    payload = []
    for r in rows:
        vin_norm = normalize_vin(r.get("vin"))
        payload.append({
            "vin": vin_norm,
            "vehicle_id": _nz(r.get("vehicle_id")),
            "plate_no": _nz(r.get("license_plate")),
            "make": _nz(r.get("make")),
            "model": _nz(r.get("model")),
            "year": _parse_int(r.get("year")),
            "color": _nz(r.get("color")),
            "registered_at": _nz(r.get("registered_at")),
        })
    cypher = """
    UNWIND $rows AS row
    MERGE (v:Vehicle {vin: row.vin})
    SET v.vehicle_id = row.vehicle_id,
        v.plate_no = row.plate_no,
        v.make = row.make,
        v.model = row.model,
        v.year = row.year,
        v.color = row.color,
        v.registered_at = row.registered_at
    """
    return _run_batched(sess, cypher, payload, batch_size=batch_size)


def _load_accounts(sess: Any, rows: list[dict], salt: str, batch_size: int) -> int:
    """계좌 적재 — 병합키는 정규화 계좌번호 (schema: Account.account_no UNIQUE).

    account_no 속성에는 정규화 값을 저장(병합키), 평문 계좌번호는 해시만 저장.
    """
    payload = []
    for r in rows:
        acc_norm = normalize_account_no(r.get("account_no"))
        payload.append({
            "account_no": acc_norm,  # 병합키 = 정규화 계좌번호
            "account_id": _nz(r.get("account_id")),
            "account_no_hash": hash_pii(acc_norm, salt=salt),
            "holder_hash": hash_pii(r.get("account_holder"), salt=salt),
            "bank_code": _nz(r.get("bank_code")),
            "bank_name": _nz(r.get("bank_name")),
            "account_type": _nz(r.get("account_type")),
            "created_at": _nz(r.get("created_at")),
        })
    cypher = """
    UNWIND $rows AS row
    MERGE (a:Account {account_no: row.account_no})
    SET a.account_id = row.account_id,
        a.account_no_hash = row.account_no_hash,
        a.holder_hash = row.holder_hash,
        a.bank_code = row.bank_code,
        a.bank_name = row.bank_name,
        a.account_type = row.account_type,
        a.created_at = row.created_at
    """
    return _run_batched(sess, cypher, payload, batch_size=batch_size)


def _load_hospitals(sess: Any, rows: list[dict], batch_size: int) -> int:
    payload = []
    for r in rows:
        payload.append({
            "hospital_id": r["hospital_id"],
            "institution_code": _nz(r.get("institution_code")),
            "name": _nz(r.get("name")),
            "type": _nz(r.get("type")),
            "address": _nz(r.get("address")),
            "phone": _nz(r.get("phone")),
            "license_no": _nz(r.get("license_no")),
            "specialties": _parse_json_list(r.get("specialties")),
            "created_at": _nz(r.get("created_at")),
        })
    cypher = """
    UNWIND $rows AS row
    MERGE (h:Hospital {hospital_id: row.hospital_id})
    SET h.institution_code = row.institution_code,
        h.name = row.name,
        h.type = row.type,
        h.address = row.address,
        h.phone = row.phone,
        h.license_no = row.license_no,
        h.specialties = row.specialties,
        h.created_at = row.created_at
    """
    return _run_batched(sess, cypher, payload, batch_size=batch_size)


def _load_repair_shops(sess: Any, rows: list[dict], batch_size: int) -> int:
    """정비소 적재 — 병합키는 shop_id (schema: RepairShop.shop_id UNIQUE)."""
    payload = []
    for r in rows:
        payload.append({
            "shop_id": r["repair_shop_id"],
            "business_reg_no": _nz(r.get("business_reg_no")),
            "name": _nz(r.get("name")),
            "type": _nz(r.get("type")),
            "address": _nz(r.get("address")),
            "phone": _nz(r.get("phone")),
            "license_no": _nz(r.get("license_no")),
            "rating": _parse_float(r.get("rating")),
            "created_at": _nz(r.get("created_at")),
        })
    cypher = """
    UNWIND $rows AS row
    MERGE (s:RepairShop {shop_id: row.shop_id})
    SET s.business_reg_no = row.business_reg_no,
        s.name = row.name,
        s.type = row.type,
        s.address = row.address,
        s.phone = row.phone,
        s.license_no = row.license_no,
        s.rating = row.rating,
        s.created_at = row.created_at
    """
    return _run_batched(sess, cypher, payload, batch_size=batch_size)


def _load_brokers(sess: Any, rows: list[dict], batch_size: int) -> int:
    """브로커/알선자 적재(WP-KR) — 병합키 broker_id."""
    payload = []
    for r in rows:
        payload.append({
            "broker_id": r["broker_id"],
            "name": _nz(r.get("name")),
            "business_reg_no": _nz(r.get("business_reg_no")),
            "phone": _nz(r.get("phone")),
            "region": _nz(r.get("region")),
            "created_at": _nz(r.get("created_at")),
        })
    cypher = """
    UNWIND $rows AS row
    MERGE (b:Broker {broker_id: row.broker_id})
    SET b.name = row.name,
        b.business_reg_no = row.business_reg_no,
        b.phone = row.phone,
        b.region = row.region,
        b.created_at = row.created_at
    """
    return _run_batched(sess, cypher, payload, batch_size=batch_size)


def _load_agents(sess: Any, rows: list[dict], batch_size: int) -> int:
    """보험설계사 적재(WP-KR) — 병합키 agent_id."""
    payload = []
    for r in rows:
        payload.append({
            "agent_id": r["agent_id"],
            "name": _nz(r.get("name")),
            "license_no": _nz(r.get("license_no")),
            "agency": _nz(r.get("agency")),
            "phone": _nz(r.get("phone")),
            "created_at": _nz(r.get("created_at")),
        })
    cypher = """
    UNWIND $rows AS row
    MERGE (a:Agent {agent_id: row.agent_id})
    SET a.name = row.name,
        a.license_no = row.license_no,
        a.agency = row.agency,
        a.phone = row.phone,
        a.created_at = row.created_at
    """
    return _run_batched(sess, cypher, payload, batch_size=batch_size)


def _load_addresses_and_phones(
    sess: Any, customers: list[dict], salt: str, batch_size: int
) -> tuple[int, int]:
    """customers 소스에서 Address·Phone 노드 파생 생성 (ETL).

    동일 정규화 값은 단일 노드로 병합되어 공유 주소/전화 관계가 드러난다.
    Address 병합키 = address_id(=hash(정규화주소)).
    Phone  병합키 = number_hash(=sha256(salt+phone_normalized)) — WP1-6 (FR-1.3).
      - 동일 번호 → 동일 해시 → 동일 노드 병합(엔티티 해소 WP1-5 보장).
      - 평문 전화번호는 그래프에 일절 저장하지 않는다.

    Hospital.phone / RepairShop.phone 은 기관 대표번호이므로 개인 PII 가 아니어서
    가명처리 대상에서 제외하고 평문 유지한다 (mapping.md §6 주의사항 참조).
    """
    addr_seen: dict[str, dict] = {}
    # WP1-6 (FR-1.3): Phone 병합키 = number_hash (sha256).
    # 평문 phone_normalized 는 HAS_PHONE 엣지 연결 시에만 임시 참조하고 그래프에 저장하지 않는다.
    # 동일 전화번호 → 동일 hash → 동일 노드로 MERGE — 엔티티 해소(WP1-5)도 함께 보장.
    phone_seen: dict[str, dict] = {}  # key = number_hash (병합키)
    for r in customers:
        addr_norm = normalize_address(r.get("address"))
        if addr_norm:
            addr_id = hash_pii(addr_norm, salt=salt)
            addr_seen.setdefault(addr_id, {
                "address_id": addr_id,
                "address_normalized": addr_norm,
                "raw_address": _nz(r.get("address")),
            })
        phone_norm = normalize_phone(r.get("phone_number"))
        if phone_norm:
            ph_hash = hash_pii(phone_norm, salt=salt)
            phone_seen.setdefault(ph_hash, {
                # number_hash = 병합키 겸 유일 저장 속성 (평문 number 미저장)
                "number_hash": ph_hash,
            })

    addr_rows = list(addr_seen.values())
    phone_rows = list(phone_seen.values())

    n_addr = _run_batched(sess, """
    UNWIND $rows AS row
    MERGE (a:Address {address_id: row.address_id})
    SET a.address_normalized = row.address_normalized,
        a.raw_address = row.raw_address
    """, addr_rows, batch_size=batch_size)

    # Phone 노드: number_hash 만 저장 (FR-1.3 준수, 제약: phone_number_hash_unique)
    n_phone = _run_batched(sess, """
    UNWIND $rows AS row
    MERGE (p:Phone {number_hash: row.number_hash})
    """, phone_rows, batch_size=batch_size)

    return n_addr, n_phone


# ==================================================================
# 엣지 적재 (11종) — 모두 MERGE (멱등)
# ==================================================================
def _load_edges_filed(sess: Any, claims: list[dict], batch_size: int) -> int:
    rows = [{"customer_id": r["customer_id"], "claim_id": r["claim_id"],
             "filed_at": _nz(r.get("report_date"))}
            for r in claims if _nz(r.get("customer_id"))]
    cypher = """
    UNWIND $rows AS row
    MATCH (c:Customer {customer_id: row.customer_id})
    MATCH (cl:Claim {claim_id: row.claim_id})
    MERGE (c)-[f:FILED]->(cl)
    SET f.filed_at = row.filed_at
    """
    return _run_batched(sess, cypher, rows, batch_size=batch_size)


def _load_edges_holds(sess: Any, policies: list[dict], batch_size: int) -> int:
    rows = [{"customer_id": r["customer_id"], "policy_id": r["policy_id"],
             "since": _nz(r.get("start_date"))}
            for r in policies if _nz(r.get("customer_id"))]
    cypher = """
    UNWIND $rows AS row
    MATCH (c:Customer {customer_id: row.customer_id})
    MATCH (p:Policy {policy_id: row.policy_id})
    MERGE (c)-[h:HOLDS]->(p)
    SET h.since = row.since
    """
    return _run_batched(sess, cypher, rows, batch_size=batch_size)


def _load_edges_covers(sess: Any, policies: list[dict], vin_by_vehicle: dict[str, str],
                       batch_size: int) -> int:
    rows = []
    for r in policies:
        vid = _nz(r.get("vehicle_id"))
        vin = vin_by_vehicle.get(vid) if vid else None
        if not vin:
            continue
        rows.append({
            "policy_id": r["policy_id"], "vin": vin,
            "start_date": _nz(r.get("start_date")),
            "end_date": _nz(r.get("end_date")),
            "coverage_type": _nz(r.get("coverage_type")),
        })
    cypher = """
    UNWIND $rows AS row
    MATCH (p:Policy {policy_id: row.policy_id})
    MATCH (v:Vehicle {vin: row.vin})
    MERGE (p)-[c:COVERS]->(v)
    SET c.start_date = row.start_date,
        c.end_date = row.end_date,
        c.coverage_type = row.coverage_type
    """
    return _run_batched(sess, cypher, rows, batch_size=batch_size)


def _load_edges_involves(sess: Any, claims: list[dict], vin_by_vehicle: dict[str, str],
                         batch_size: int) -> int:
    rows = []
    for r in claims:
        vid = _nz(r.get("vehicle_id"))
        vin = vin_by_vehicle.get(vid) if vid else None
        if not vin:
            continue
        rows.append({"claim_id": r["claim_id"], "vin": vin})
    cypher = """
    UNWIND $rows AS row
    MATCH (cl:Claim {claim_id: row.claim_id})
    MATCH (v:Vehicle {vin: row.vin})
    MERGE (cl)-[i:INVOLVES]->(v)
    SET i.role = 'accident_vehicle'
    """
    return _run_batched(sess, cypher, rows, batch_size=batch_size)


def _load_edges_treated_at(sess: Any, claims: list[dict], batch_size: int) -> int:
    rows = [{"claim_id": r["claim_id"], "hospital_id": _nz(r.get("hospital_id")),
             "treatment_date": _nz(r.get("incident_date"))}
            for r in claims if _nz(r.get("hospital_id"))]
    cypher = """
    UNWIND $rows AS row
    MATCH (cl:Claim {claim_id: row.claim_id})
    MATCH (h:Hospital {hospital_id: row.hospital_id})
    MERGE (cl)-[t:TREATED_AT]->(h)
    SET t.treatment_date = row.treatment_date
    """
    return _run_batched(sess, cypher, rows, batch_size=batch_size)


def _load_edges_repaired_at(sess: Any, claims: list[dict], batch_size: int) -> int:
    rows = [{"claim_id": r["claim_id"], "shop_id": _nz(r.get("repair_shop_id")),
             "repair_date": _nz(r.get("incident_date"))}
            for r in claims if _nz(r.get("repair_shop_id"))]
    cypher = """
    UNWIND $rows AS row
    MATCH (cl:Claim {claim_id: row.claim_id})
    MATCH (s:RepairShop {shop_id: row.shop_id})
    MERGE (cl)-[rp:REPAIRED_AT]->(s)
    SET rp.repair_date = row.repair_date
    """
    return _run_batched(sess, cypher, rows, batch_size=batch_size)


def _load_edges_paid_to(sess: Any, claims: list[dict], accno_by_account: dict[str, str],
                        batch_size: int) -> int:
    rows = []
    for r in claims:
        aid = _nz(r.get("account_id"))
        accno = accno_by_account.get(aid) if aid else None
        if not accno:
            continue
        rows.append({
            "claim_id": r["claim_id"], "account_no": accno,
            "amount": _parse_float(r.get("paid_amount")),
            "paid_at": _nz(r.get("report_date")),
        })
    cypher = """
    UNWIND $rows AS row
    MATCH (cl:Claim {claim_id: row.claim_id})
    MATCH (a:Account {account_no: row.account_no})
    MERGE (cl)-[p:PAID_TO]->(a)
    SET p.amount = row.amount,
        p.paid_at = row.paid_at
    """
    return _run_batched(sess, cypher, rows, batch_size=batch_size)


def _load_edges_lives_at(sess: Any, customers: list[dict], salt: str, batch_size: int) -> int:
    rows = []
    for r in customers:
        addr_norm = normalize_address(r.get("address"))
        if not addr_norm:
            continue
        rows.append({
            "customer_id": r["customer_id"],
            "address_id": hash_pii(addr_norm, salt=salt),
            "since": _nz(r.get("created_at")),
        })
    cypher = """
    UNWIND $rows AS row
    MATCH (c:Customer {customer_id: row.customer_id})
    MATCH (a:Address {address_id: row.address_id})
    MERGE (c)-[l:LIVES_AT]->(a)
    SET l.since = row.since
    """
    return _run_batched(sess, cypher, rows, batch_size=batch_size)


def _load_edges_owns(sess: Any, vehicles: list[dict], batch_size: int) -> int:
    rows = []
    for r in vehicles:
        vin = normalize_vin(r.get("vin"))
        cid = _nz(r.get("customer_id"))
        if not vin or not cid:
            continue
        rows.append({"customer_id": cid, "vin": vin,
                     "registered_at": _nz(r.get("registered_at"))})
    cypher = """
    UNWIND $rows AS row
    MATCH (c:Customer {customer_id: row.customer_id})
    MATCH (v:Vehicle {vin: row.vin})
    MERGE (c)-[o:OWNS]->(v)
    SET o.registered_at = row.registered_at
    """
    return _run_batched(sess, cypher, rows, batch_size=batch_size)


def _load_edges_has_phone(
    sess: Any, customers: list[dict], salt: str, batch_size: int
) -> int:
    """HAS_PHONE 엣지: Phone 조회 키를 number_hash 로 사용 (WP1-6, FR-1.3).

    Phone 노드의 병합키가 number_hash 로 변경되었으므로, 엣지 연결도
    hash_pii(normalize_phone(phone_number)) 를 통해 해시 기반으로 수행한다.
    """
    rows = []
    for r in customers:
        phone_norm = normalize_phone(r.get("phone_number"))
        if not phone_norm:
            continue
        rows.append({
            "customer_id": r["customer_id"],
            "number_hash": hash_pii(phone_norm, salt=salt),
            "since": _nz(r.get("created_at")),
        })
    cypher = """
    UNWIND $rows AS row
    MATCH (c:Customer {customer_id: row.customer_id})
    MATCH (p:Phone {number_hash: row.number_hash})
    MERGE (c)-[hp:HAS_PHONE]->(p)
    SET hp.since = row.since
    """
    return _run_batched(sess, cypher, rows, batch_size=batch_size)


def _load_edges_witnessed_by(sess: Any, claims: list[dict], batch_size: int) -> int:
    """WITNESSED_BY (Claim)->(Claim) — crash-for-cash 교차 목격 핵심.

    claims.witness_claim_ids 배열의 각 원소마다 엣지 1개. 멱등 병합키는
    (claim_id, witness_claim_id) 조합 — MERGE 가 보장.
    """
    rows = []
    for r in claims:
        src = r["claim_id"]
        for tgt in _parse_json_list(r.get("witness_claim_ids")):
            if tgt and tgt != src:
                rows.append({"src": src, "tgt": tgt})
    cypher = """
    UNWIND $rows AS row
    MATCH (a:Claim {claim_id: row.src})
    MATCH (b:Claim {claim_id: row.tgt})
    MERGE (a)-[w:WITNESSED_BY]->(b)
    SET w.witness_type = 'cross_witness'
    """
    return _run_batched(sess, cypher, rows, batch_size=batch_size)


def _load_edges_brokered(sess: Any, brokered: list[dict], batch_size: int) -> int:
    """BROKERED (Broker)->(Customer) — 브로커가 고객을 알선(허위입원 조직형 허브)."""
    rows = [{"broker_id": _nz(r.get("broker_id")), "customer_id": _nz(r.get("customer_id"))}
            for r in brokered if _nz(r.get("broker_id")) and _nz(r.get("customer_id"))]
    cypher = """
    UNWIND $rows AS row
    MATCH (b:Broker {broker_id: row.broker_id})
    MATCH (c:Customer {customer_id: row.customer_id})
    MERGE (b)-[r:BROKERED]->(c)
    """
    return _run_batched(sess, cypher, rows, batch_size=batch_size)


def _load_edges_sold_policy(sess: Any, sold: list[dict], batch_size: int) -> int:
    """SOLD_POLICY (Agent)->(Policy) — 설계사가 계약을 모집(설계사 개입 허브)."""
    rows = [{"agent_id": _nz(r.get("agent_id")), "policy_id": _nz(r.get("policy_id"))}
            for r in sold if _nz(r.get("agent_id")) and _nz(r.get("policy_id"))]
    cypher = """
    UNWIND $rows AS row
    MATCH (a:Agent {agent_id: row.agent_id})
    MATCH (p:Policy {policy_id: row.policy_id})
    MERGE (a)-[r:SOLD_POLICY]->(p)
    """
    return _run_batched(sess, cypher, rows, batch_size=batch_size)


# ==================================================================
# 오케스트레이션
# ==================================================================
def load(data_dir: str | Path, *, batch_size: int = BATCH_SIZE) -> dict[str, int]:
    """``data_dir`` 의 CSV/Parquet 소스를 Neo4j 에 멱등 적재한다.

    Returns:
        적재 건수 dict — ``node:<Label>`` 과 ``edge:<TYPE>`` 키.
    """
    data_path = Path(data_dir)
    from thoth.config import get_settings
    salt = get_settings().pii_salt

    # 소스 로딩
    customers = _read_source(data_path, "customers")
    policies = _read_source(data_path, "policies")
    vehicles = _read_source(data_path, "vehicles")
    accounts = _read_source(data_path, "accounts")
    hospitals = _read_source(data_path, "hospitals")
    repair_shops = _read_source(data_path, "repair_shops")
    claims = _read_source(data_path, "claims")
    # WP-KR 선택적 소스(없으면 빈 리스트 — 구버전 데이터 호환)
    brokers = _read_source_optional(data_path, "brokers")
    agents = _read_source_optional(data_path, "agents")
    brokered = _read_source_optional(data_path, "brokered")
    sold_policy = _read_source_optional(data_path, "sold_policy")

    # FK → 병합키 매핑 (vehicle_id→vin, account_id→정규화 account_no)
    vin_by_vehicle = {
        _nz(v.get("vehicle_id")): normalize_vin(v.get("vin"))
        for v in vehicles if _nz(v.get("vehicle_id")) and normalize_vin(v.get("vin"))
    }
    accno_by_account = {
        _nz(a.get("account_id")): normalize_account_no(a.get("account_no"))
        for a in accounts if _nz(a.get("account_id")) and normalize_account_no(a.get("account_no"))
    }

    counts: dict[str, int] = {}
    print("=" * 60)
    print(" 배치 적재 시작 (WP1-4) — MERGE 멱등")
    print(f"   소스: {data_path}")
    print("=" * 60)

    with db.session() as sess:
        # --- 노드 ---
        print("[노드 적재]")
        counts["node:Customer"] = _load_customers(sess, customers, salt, batch_size)
        print(f"  Customer    : {counts['node:Customer']:,}")
        counts["node:Claim"] = _load_claims(sess, claims, batch_size)
        print(f"  Claim       : {counts['node:Claim']:,}")
        counts["node:Policy"] = _load_policies(sess, policies, batch_size)
        print(f"  Policy      : {counts['node:Policy']:,}")
        counts["node:Vehicle"] = _load_vehicles(sess, vehicles, batch_size)
        print(f"  Vehicle     : {counts['node:Vehicle']:,}")
        counts["node:Account"] = _load_accounts(sess, accounts, salt, batch_size)
        print(f"  Account     : {counts['node:Account']:,}")
        counts["node:Hospital"] = _load_hospitals(sess, hospitals, batch_size)
        print(f"  Hospital    : {counts['node:Hospital']:,}")
        counts["node:RepairShop"] = _load_repair_shops(sess, repair_shops, batch_size)
        print(f"  RepairShop  : {counts['node:RepairShop']:,}")
        if brokers:
            counts["node:Broker"] = _load_brokers(sess, brokers, batch_size)
            print(f"  Broker      : {counts['node:Broker']:,} (WP-KR)")
        if agents:
            counts["node:Agent"] = _load_agents(sess, agents, batch_size)
            print(f"  Agent       : {counts['node:Agent']:,} (WP-KR)")
        n_addr, n_phone = _load_addresses_and_phones(sess, customers, salt, batch_size)
        counts["node:Address"] = n_addr
        counts["node:Phone"] = n_phone
        print(f"  Address     : {n_addr:,} (ETL 파생)")
        print(f"  Phone       : {n_phone:,} (ETL 파생)")

        # --- 엣지 ---
        print("[엣지 적재]")
        counts["edge:FILED"] = _load_edges_filed(sess, claims, batch_size)
        print(f"  FILED        : {counts['edge:FILED']:,}")
        counts["edge:HOLDS"] = _load_edges_holds(sess, policies, batch_size)
        print(f"  HOLDS        : {counts['edge:HOLDS']:,}")
        counts["edge:COVERS"] = _load_edges_covers(sess, policies, vin_by_vehicle, batch_size)
        print(f"  COVERS       : {counts['edge:COVERS']:,}")
        counts["edge:INVOLVES"] = _load_edges_involves(sess, claims, vin_by_vehicle, batch_size)
        print(f"  INVOLVES     : {counts['edge:INVOLVES']:,}")
        counts["edge:TREATED_AT"] = _load_edges_treated_at(sess, claims, batch_size)
        print(f"  TREATED_AT   : {counts['edge:TREATED_AT']:,}")
        counts["edge:REPAIRED_AT"] = _load_edges_repaired_at(sess, claims, batch_size)
        print(f"  REPAIRED_AT  : {counts['edge:REPAIRED_AT']:,}")
        counts["edge:PAID_TO"] = _load_edges_paid_to(sess, claims, accno_by_account, batch_size)
        print(f"  PAID_TO      : {counts['edge:PAID_TO']:,}")
        counts["edge:LIVES_AT"] = _load_edges_lives_at(sess, customers, salt, batch_size)
        print(f"  LIVES_AT     : {counts['edge:LIVES_AT']:,}")
        counts["edge:OWNS"] = _load_edges_owns(sess, vehicles, batch_size)
        print(f"  OWNS         : {counts['edge:OWNS']:,}")
        counts["edge:HAS_PHONE"] = _load_edges_has_phone(sess, customers, salt, batch_size)
        print(f"  HAS_PHONE    : {counts['edge:HAS_PHONE']:,}")
        counts["edge:WITNESSED_BY"] = _load_edges_witnessed_by(sess, claims, batch_size)
        print(f"  WITNESSED_BY : {counts['edge:WITNESSED_BY']:,}")
        if brokered:
            counts["edge:BROKERED"] = _load_edges_brokered(sess, brokered, batch_size)
            print(f"  BROKERED     : {counts['edge:BROKERED']:,} (WP-KR)")
        if sold_policy:
            counts["edge:SOLD_POLICY"] = _load_edges_sold_policy(sess, sold_policy, batch_size)
            print(f"  SOLD_POLICY  : {counts['edge:SOLD_POLICY']:,} (WP-KR)")

    print("-" * 60)
    print(" 적재 완료.")
    return counts


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="THOTH-ON 배치 적재 파이프라인 (WP1-4)")
    sub = p.add_subparsers(dest="cmd", required=True)
    load_p = sub.add_parser("load", help="소스 디렉토리를 Neo4j 에 멱등 적재")
    load_p.add_argument("data_dir", help="CSV/Parquet 소스 디렉토리")
    load_p.add_argument("--batch-size", type=int, default=BATCH_SIZE, help="UNWIND 배치 크기")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.cmd == "load":
        load(args.data_dir, batch_size=args.batch_size)
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
