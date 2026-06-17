"""자동차보험 합성 데이터 생성기 + 사기 링 주입 (WP-KR, 한국 실제 수법판).

mapping.md §2 컬럼 스키마를 따라 9종 소스 CSV 를 생성한다:
    customers, policies, claims, vehicles, accounts, hospitals, repair_shops,
    brokers, agents

핵심: 금감원(FSS)·KIRI 가 보고한 **한국 실제 자동차보험 사기 수법 5종**을
ground truth(ring_id + ring_pattern) 로 의도적 주입한다. 과거의 임의 패턴
(perfect/account_only/witness_only/hotspot_only/weak)을 폐기하고, 현실 수법으로
교체해 파일럿 데모의 현실성을 높인다.

[한국 실제 사기 수법 5종 — ring_pattern 라벨]
  · fake_admission_star : 허위입원 조직형. 브로커 1 → 특정 병원 1 → 환자 10~30명
        방사형(star). 환자들이 같은 병원에 집중 청구(나이롱 환자). 브로커가
        각 환자를 BROKERED 알선. (난이도: 중 — 병원 허브/브로커 허브로 탐지)
  · collision_ring      : 고의충돌 공모(crash-for-cash). 운전자 3~5명 + 공통
        RepairShop + 공통 Account + 상호 WITNESSED_BY 교차목격. (난이도: 쉬움/중)
  · repair_overbill     : 정비비 과다청구. 특정 정비소 ↔ 다수 고객 반복 연결 +
        청구금액 이상(정상 대비 2~4배). (난이도: 중 — 정비소 허브 + 금액 이상)
  · agent_fraud         : 설계사 개입. 설계사 1 → 다수 Policy/Customer + 공통 수취
        Account(설계사가 보험금 가로채기). (난이도: 어려움 — 설계사 허브 신호)
  · driver_swap         : 운전자 교체/동승자 공모. 같은 Vehicle·사고에 운전자가
        바뀌고 동승자 다수가 청구(공유 차량 vin + 동시 청구). (난이도: 어려움)

[FSS 통계분포 반영]
  · 사기 가담 연령 30~40대 중심(피크), 수도권·부산경남 집중.
  · 자동차보험 사기 적발률 ≈ 2~3% (전체 고객 대비 링 멤버 소수 유지).
  · 허위/고의 사고가 적발 사기의 다수 — collision/fake_admission 비중 큼.

[정상 노이즈(오탐 유발) — 유지/강화]
  · 정상 가족 단위 공유(계좌/주소/전화/차량) — 사기 아님인데 공유 신호 발생.
  · 대형 병원·인기 정비소에 정상 청구가 자연 집중(정상 핫스팟).
  · 정상 단방향 목격(우연) — 상호 교차목격 아님.
  · 정상 설계사/브로커도 다수 고객을 정상 모집/알선(허브 신호의 정상 배경).

PII(name/id_number/phone_number/email/account_no/account_holder)는 평문으로
CSV 에 생성한다(가명처리는 적재 단계 loader 가 담당). 재현성을 위해 random
seed 를 고정한다.

CLI:
    python -m ingest.synth_generator [--out DIR] [--customers N] [--claims N]
                                     [--rings N] [--ring-size MIN MAX] [--seed S]
                                     [--families N]
"""
from __future__ import annotations

import argparse
import csv
import json
import random
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

# ------------------------------------------------------------------
# 기본 규모 (mapping/PRD NFR: 고객 ~5000, 청구 ~20000)
# ------------------------------------------------------------------
DEFAULT_CUSTOMERS = 5000
DEFAULT_CLAIMS = 20000
DEFAULT_RINGS = 30          # 한국 수법 5종을 충분히 담기 위한 링 개수(라벨 비율 소수 유지)
DEFAULT_RING_SIZE = (2, 6)  # collision_ring/repair_overbill 등 소규모 링 기본 크기
DEFAULT_SEED = 42
DEFAULT_OUT = "data/synthetic"
DEFAULT_FAMILIES = 250      # 정상 가족 공유 클러스터 수(오탐 유발 노이즈)

# 보조 마스터 규모 (엔티티 합계 ~5만 노드 목표에 맞춰 조정)
N_HOSPITALS = 120
N_REPAIR_SHOPS = 200
# WP-KR: 브로커/설계사 마스터 — 정상 배경(다수 정상 모집/알선)을 충분히 둬
# 허브 신호가 사기에만 특이적이지 않게 한다(오탐 유발 노이즈).
N_BROKERS = 40              # 정상 알선자 배경(보험대리·렌터카 제휴 등)
N_AGENTS = 150             # 정상 보험설계사 배경

KOREAN_SURNAMES = list("김이박최정강조윤장임한오서신권황안송류전홍고문양손배백허유남")
KOREAN_GIVEN = ["민준", "서준", "도윤", "예준", "시우", "하준", "지호", "준우", "지후", "준서",
                "서연", "서윤", "지우", "하은", "하윤", "민서", "지유", "윤서", "지민", "채원"]
BANKS = [("011", "농협은행"), ("088", "신한은행"), ("004", "국민은행"),
         ("020", "우리은행"), ("081", "하나은행"), ("003", "기업은행")]
MAKES_MODELS = [("Hyundai", ["Sonata", "Avante", "Grandeur", "Tucson"]),
                ("Kia", ["K5", "K7", "Sorento", "Sportage"]),
                ("Genesis", ["G70", "G80", "GV70"]),
                ("Chevrolet", ["Malibu", "Spark"]),
                ("BMW", ["320i", "520i", "X3"])]
COLORS = ["흰색", "검정", "회색", "은색", "파랑", "빨강"]
CITIES = ["서울시 강남구", "서울시 송파구", "경기도 성남시 분당구", "인천시 남동구",
          "부산시 해운대구", "대구시 수성구", "경기도 수원시 영통구", "서울시 마포구"]
STREETS = ["테헤란로", "올림픽로", "판교로", "논현로", "강남대로", "양재대로", "월드컵로"]
HOSPITAL_TYPES = ["clinic", "hospital", "oriental_medicine"]
SHOP_TYPES = ["authorized", "independent", "body_shop"]
INCIDENT_TYPES_NORMAL = ["collision", "theft", "fire", "weather", "vandalism"]

# FSS 통계분포 — 사기 가담자는 수도권·부산경남에 집중된다(허위/고의 사고 본거지).
# 정상 고객은 전국 분포(CITIES)를 따르되, 사기 링 거점 주소는 아래 가중 도시에서
# 뽑아 현실 분포를 모사한다.
FRAUD_HOTSPOT_CITIES = [
    "서울시 강남구", "서울시 송파구", "서울시 마포구",        # 수도권(서울)
    "경기도 성남시 분당구", "경기도 수원시 영통구", "인천시 남동구",  # 수도권(경인)
    "부산시 해운대구", "부산시 사상구", "경상남도 창원시 성산구",   # 부산·경남
]

# 한국 실제 사기 수법 5종 — (라벨은 모두 사기). ring_pattern 라벨로 사용.
RING_PATTERNS = [
    "fake_admission_star",  # 허위입원 조직형(브로커→병원→환자 방사형)
    "collision_ring",       # 고의충돌 공모(crash-for-cash 교차목격+공통계좌/정비소)
    "repair_overbill",      # 정비비 과다청구(정비소 허브 + 금액 이상)
    "agent_fraud",          # 설계사 개입(설계사 허브 + 공통 수취계좌)
    "driver_swap",          # 운전자 교체/동승자 공모(공유 차량 + 동시 청구)
]


def _fraud_age_birthdate(rng: random.Random) -> date:
    """FSS 분포 — 사기 가담 연령 30~40대 중심의 생년월일을 뽑는다.

    오늘(2026 기준) 30~49세 가중 피크, 일부 20대 후반/50대 꼬리.
    """
    age = rng.choices(
        population=[27, 32, 37, 42, 47, 53],
        weights=[6, 22, 28, 24, 14, 6],
        k=1,
    )[0]
    age += rng.randint(-2, 2)
    year = 2026 - age
    month = rng.randint(1, 12)
    day = rng.randint(1, 28)
    return date(year, month, day)


def _fraud_address(rng: random.Random) -> str:
    """FSS 분포 — 수도권·부산경남 집중 사기 거점 주소."""
    return f"{rng.choice(FRAUD_HOTSPOT_CITIES)} {rng.choice(STREETS)} {rng.randint(1, 999)}"


@dataclass
class _Ctx:
    """생성 컨텍스트 — 누적 레코드 보관."""

    customers: list[dict] = field(default_factory=list)
    policies: list[dict] = field(default_factory=list)
    vehicles: list[dict] = field(default_factory=list)
    accounts: list[dict] = field(default_factory=list)
    hospitals: list[dict] = field(default_factory=list)
    repair_shops: list[dict] = field(default_factory=list)
    claims: list[dict] = field(default_factory=list)
    brokers: list[dict] = field(default_factory=list)   # WP-KR 브로커/알선자
    agents: list[dict] = field(default_factory=list)    # WP-KR 보험설계사
    # 정상 핫스팟 후보(인기 대형 병원/정비소) — 정상 청구가 자연 집중되는 곳
    popular_hospitals: list[str] = field(default_factory=list)
    popular_shops: list[str] = field(default_factory=list)
    # ETL 엣지 파생용 매핑: 브로커→알선 고객, 설계사→모집 계약
    brokered: list[dict] = field(default_factory=list)   # {broker_id, customer_id}
    sold_policy: list[dict] = field(default_factory=list)  # {agent_id, policy_id}


def _ts(d: date) -> str:
    return datetime(d.year, d.month, d.day).isoformat() + "Z"


def _rand_date(rng: random.Random, start: date, end: date) -> date:
    delta = (end - start).days
    return start + timedelta(days=rng.randint(0, max(delta, 0)))


def _make_name(rng: random.Random) -> str:
    return rng.choice(KOREAN_SURNAMES) + rng.choice(KOREAN_GIVEN)


def _make_ssn(rng: random.Random, birth: date) -> str:
    yy = f"{birth.year % 100:02d}"
    mm = f"{birth.month:02d}"
    dd = f"{birth.day:02d}"
    gender_digit = rng.choice([1, 2, 3, 4])
    rest = f"{rng.randint(0, 999999):06d}"
    return f"{yy}{mm}{dd}-{gender_digit}{rest}"


def _make_phone(rng: random.Random) -> str:
    return f"010-{rng.randint(1000, 9999)}-{rng.randint(1000, 9999)}"


def _make_account_no(rng: random.Random, bank_code: str) -> str:
    return f"{bank_code}-{rng.randint(1000, 9999)}-{rng.randint(100000, 999999)}"


def _make_vin(rng: random.Random) -> str:
    chars = "ABCDEFGHJKLMNPRSTUVWXYZ0123456789"
    return "".join(rng.choice(chars) for _ in range(17))


def _make_plate(rng: random.Random) -> str:
    return f"{rng.randint(10, 99)}{rng.choice('가나다라마바사아자하')}{rng.randint(1000, 9999)}"


def _make_address(rng: random.Random) -> str:
    return f"{rng.choice(CITIES)} {rng.choice(STREETS)} {rng.randint(1, 999)}"


# ------------------------------------------------------------------
# 마스터 데이터 (병원·정비소)
# ------------------------------------------------------------------
def _gen_hospitals(rng: random.Random, ctx: _Ctx) -> None:
    for i in range(1, N_HOSPITALS + 1):
        ctx.hospitals.append({
            "hospital_id": f"HOSP-{i:04d}",
            "institution_code": f"B{rng.randint(1000000, 9999999)}",
            "name": f"{rng.choice(CITIES).split()[-1]}{rng.choice(['정형외과', '한방병원', '재활의학과', '연합의원'])}",
            "type": rng.choice(HOSPITAL_TYPES),
            "address": _make_address(rng),
            "phone": f"02-{rng.randint(1000, 9999)}-{rng.randint(1000, 9999)}",
            "license_no": f"서울{rng.randint(2018, 2024)}-{rng.randint(1, 999):03d}",
            "specialties": json.dumps(rng.sample(["정형외과", "재활의학과", "한방", "신경외과"], k=2),
                                      ensure_ascii=False),
            "created_at": _ts(_rand_date(rng, date(2018, 1, 1), date(2022, 1, 1))),
        })
    # 인기 대형 병원(정상 핫스팟) — 상위 몇 곳에 정상 청구가 자연 집중된다.
    ctx.popular_hospitals = [h["hospital_id"] for h in ctx.hospitals[:6]]


def _gen_repair_shops(rng: random.Random, ctx: _Ctx) -> None:
    for i in range(1, N_REPAIR_SHOPS + 1):
        ctx.repair_shops.append({
            "repair_shop_id": f"RSH-{i:04d}",
            "business_reg_no": f"{rng.randint(100, 999)}-{rng.randint(10, 99)}-{rng.randint(10000, 99999)}",
            "name": f"{rng.choice(['빠른', '으뜸', '신속', '명품', '대박'])}카정비{i}",
            "type": rng.choice(SHOP_TYPES),
            "address": _make_address(rng),
            "phone": f"031-{rng.randint(1000, 9999)}-{rng.randint(1000, 9999)}",
            "license_no": f"경기-정비-{i:04d}",
            "rating": round(rng.uniform(2.5, 5.0), 1),
            "created_at": _ts(_rand_date(rng, date(2017, 1, 1), date(2021, 1, 1))),
        })
    # 인기 정비소(정상 핫스팟) — 평점 좋은 체인점에 정상 청구가 자연 집중.
    ctx.popular_shops = [s["repair_shop_id"] for s in ctx.repair_shops[:8]]


def _gen_brokers(rng: random.Random, ctx: _Ctx) -> None:
    """브로커/알선자 마스터 생성(WP-KR).

    대부분은 정상 알선자(렌터카·보험대리점 제휴 등)이며, 사기 수법(허위입원
    조직형)에서 일부가 환자를 방사형으로 알선한다. 정상 배경을 충분히 둬서
    "브로커 연결 다수" 신호가 사기에만 특이적이지 않게 한다(오탐 유발 노이즈).
    """
    for i in range(1, N_BROKERS + 1):
        ctx.brokers.append({
            "broker_id": f"BRK-{i:04d}",
            "name": f"{rng.choice(['한길', '대성', '믿음', '으뜸', '하나'])}알선{i}",
            "business_reg_no": f"{rng.randint(100, 999)}-{rng.randint(10, 99)}-{rng.randint(10000, 99999)}",
            "phone": _make_phone(rng),
            "region": rng.choice(FRAUD_HOTSPOT_CITIES).split()[0],
            "created_at": _ts(_rand_date(rng, date(2018, 1, 1), date(2022, 1, 1))),
        })


def _gen_agents(rng: random.Random, ctx: _Ctx) -> None:
    """보험설계사 마스터 생성(WP-KR).

    대부분 정상 설계사이며, 설계사 개입 사기 수법에서 일부가 다수 계약을
    모집하고 보험금을 공통 계좌로 가로챈다. 정상 배경(다수 정상 모집)을 둬서
    "설계사 허브"가 사기에만 특이적이지 않게 한다.
    """
    for i in range(1, N_AGENTS + 1):
        ctx.agents.append({
            "agent_id": f"AGT-{i:05d}",
            "name": _make_name(rng),
            "license_no": f"설계-{rng.randint(2015, 2024)}-{rng.randint(1, 99999):05d}",
            "agency": rng.choice(["삼성화재", "DB손보", "현대해상", "KB손보", "메리츠"]),
            "phone": _make_phone(rng),
            "created_at": _ts(_rand_date(rng, date(2016, 1, 1), date(2022, 1, 1))),
        })


# ------------------------------------------------------------------
# 고객 + 종속 엔티티(차량·계좌·계약)
# ------------------------------------------------------------------
def _gen_customer_bundle(
    rng: random.Random,
    ctx: _Ctx,
    idx: int,
    *,
    is_fraud: bool = False,
    ring_id: str = "",
    ring_pattern: str = "",
    shared_account_no: str | None = None,
    shared_phone: str | None = None,
    shared_address: str | None = None,
    shared_vin: str | None = None,
    fraud_demo: bool = False,
) -> dict:
    """고객 1명과 그에 딸린 차량·계좌·계약을 생성하고 customer 레코드 반환.

    shared_* 인자가 주어지면 해당 식별자를 강제(사기 링/정상 가족 공유).
    fraud_demo=True 이면 FSS 분포(30~40대·수도권/부산경남)를 적용한다.
    반환 dict 에 내부 참조용 키(_vehicle_id, _policy_id, _account_id)를 부착한다.
    """
    cust_id = f"CUST-{idx:05d}"
    birth = _fraud_age_birthdate(rng) if fraud_demo else \
        _rand_date(rng, date(1960, 1, 1), date(2002, 12, 31))
    created = _rand_date(rng, date(2019, 1, 1), date(2023, 6, 1))

    default_addr = _fraud_address(rng) if fraud_demo else _make_address(rng)
    customer = {
        "customer_id": cust_id,
        "name": _make_name(rng),
        "id_number": _make_ssn(rng, birth),
        "birth_date": birth.isoformat(),
        "gender": rng.choice(["M", "F"]),
        "address": shared_address if shared_address is not None else default_addr,
        "phone_number": shared_phone if shared_phone is not None else _make_phone(rng),
        "email": f"user{idx}@example.com",
        "created_at": _ts(created),
        # ground truth 라벨 (정상은 빈값/False)
        "is_fraud_ring": is_fraud,
        "ring_id": ring_id,
        "ring_pattern": ring_pattern,
    }
    ctx.customers.append(customer)

    # 차량
    veh_id = f"VEH-{idx:05d}"
    make, models = rng.choice(MAKES_MODELS)
    ctx.vehicles.append({
        "vehicle_id": veh_id,
        "customer_id": cust_id,
        "vin": shared_vin if shared_vin is not None else _make_vin(rng),
        "license_plate": _make_plate(rng),
        "make": make,
        "model": rng.choice(models),
        "year": rng.randint(2012, 2024),
        "color": rng.choice(COLORS),
        "registered_at": _rand_date(rng, date(2018, 1, 1), date(2023, 1, 1)).isoformat(),
    })

    # 계좌
    acc_id = f"ACC-{idx:05d}"
    bank_code, bank_name = rng.choice(BANKS)
    if shared_account_no is not None:
        account_no = shared_account_no
        # 공유 계좌번호의 은행코드와 일치시킴
        bank_code = account_no.split("-")[0]
        bank_name = next((b[1] for b in BANKS if b[0] == bank_code), bank_name)
    else:
        account_no = _make_account_no(rng, bank_code)
    ctx.accounts.append({
        "account_id": acc_id,
        "account_no": account_no,
        "bank_code": bank_code,
        "bank_name": bank_name,
        "account_holder": customer["name"],
        "account_type": rng.choice(["checking", "savings"]),
        "created_at": _ts(_rand_date(rng, date(2019, 1, 1), date(2023, 1, 1))),
    })

    # 계약
    pol_id = f"POL-{idx:05d}"
    start = _rand_date(rng, date(2022, 1, 1), date(2023, 6, 1))
    end = start + timedelta(days=365)
    ctx.policies.append({
        "policy_id": pol_id,
        "customer_id": cust_id,
        "vehicle_id": veh_id,
        "product_code": rng.choice(["AUTO-STANDARD-V3", "AUTO-PREMIUM-V2", "AUTO-BASIC-V1"]),
        "coverage_type": rng.choice(["comprehensive", "liability_only"]),
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "premium_amount": round(rng.uniform(500000, 1500000), 2),
        "coverage_limit": float(rng.choice([30000000, 50000000, 100000000])),
        "status": rng.choice(["active", "active", "active", "expired"]),
        "created_at": _ts(start),
    })

    customer["_vehicle_id"] = veh_id
    customer["_policy_id"] = pol_id
    customer["_account_id"] = acc_id
    return customer


def _gen_claim(
    rng: random.Random,
    ctx: _Ctx,
    claim_idx: int,
    customer: dict,
    *,
    incident_date: date,
    is_fraud: bool = False,
    ring_id: str = "",
    ring_pattern: str = "",
    hospital_id: str | None = None,
    repair_shop_id: str | None = None,
    account_id: str | None = None,
    vehicle_id: str | None = None,
    incident_type: str | None = None,
    witness_claim_ids: list[str] | None = None,
    claimed_amount: float | None = None,
) -> dict:
    """청구 1건 생성. 사기 링이면 공유 hospital/shop/account 및 라벨 부여.

    claimed_amount 가 주어지면 정비비 과다청구(repair_overbill) 같은 금액 이상을
    표현하기 위해 강제한다. vehicle_id 가 주어지면(운전자 교체) 청구의 사고 차량을
    고객 본인 차량이 아닌 공유 차량으로 강제한다.
    """
    claim_id = f"CLM-2024-{claim_idx:06d}"
    report = incident_date + timedelta(days=rng.randint(0, 5))
    claimed = claimed_amount if claimed_amount is not None \
        else round(rng.uniform(800000, 8000000), 2)
    status = rng.choice(["approved", "approved", "pending", "under_review", "denied"])
    paid = round(claimed * rng.uniform(0.7, 1.0), 2) if status == "approved" else None

    claim = {
        "claim_id": claim_id,
        "customer_id": customer["customer_id"],
        "policy_id": customer["_policy_id"],
        "vehicle_id": vehicle_id if vehicle_id is not None else customer["_vehicle_id"],
        "hospital_id": hospital_id if hospital_id is not None
        else (rng.choice(ctx.hospitals)["hospital_id"] if rng.random() < 0.7 else ""),
        "repair_shop_id": repair_shop_id if repair_shop_id is not None
        else (rng.choice(ctx.repair_shops)["repair_shop_id"] if rng.random() < 0.85 else ""),
        "account_id": account_id if account_id is not None else customer["_account_id"],
        "incident_date": incident_date.isoformat(),
        "report_date": report.isoformat(),
        "incident_type": incident_type or rng.choice(INCIDENT_TYPES_NORMAL),
        "incident_location": _make_address(rng),
        "claimed_amount": claimed,
        "paid_amount": paid if paid is not None else "",
        "claim_status": status,
        "fraud_label": is_fraud,
        "witness_claim_ids": json.dumps(witness_claim_ids or [], ensure_ascii=False),
        "created_at": _ts(report),
        # ground truth 라벨
        "is_fraud_ring": is_fraud,
        "ring_id": ring_id,
        "ring_pattern": ring_pattern,
    }
    ctx.claims.append(claim)
    return claim


# ------------------------------------------------------------------
# 정상 가족 공유 클러스터 (노이즈 — 오탐 유발)
# ------------------------------------------------------------------
def _inject_normal_families(
    rng: random.Random,
    ctx: _Ctx,
    *,
    n_families: int,
    next_cust_idx: int,
) -> int:
    """정상 가족 단위 공유 클러스터 주입(사기 아님 — 오탐 유발 노이즈).

    현실: 가족은 같은 주소에 살고(주소 공유), 종종 같은 계좌로 보험금을 받거나
    (가장 명의 계좌), 같은 전화(집전화→대표번호), 같은 차량(부부 공동명의)을
    공유한다. 이는 정상인데 Q1 공유 신호를 유발한다. 라벨은 정상(is_fraud=False).

    각 가족은 2~4인, 공유 신호 1~2종을 무작위로 보유한다. **교차목격(Q3)은
    절대 만들지 않는다** — 가족이라도 서로의 사고를 상호 목격하지는 않는다.

    Returns:
        다음 고객 idx.
    """
    cust_idx = next_cust_idx
    for f in range(1, n_families + 1):
        size = rng.randint(2, 4)

        # 공유 신호 종류 선택(1~2종). 가족 공유 현실 분포:
        #   주소는 거의 항상 공유, 계좌/전화/차량은 일부.
        share_address = rng.random() < 0.85
        share_account = rng.random() < 0.45   # 대표 계좌로 수령
        share_phone = rng.random() < 0.25     # 대표 연락처
        share_vehicle = rng.random() < 0.12   # 공동명의 차량(드묾)
        # 최소 1종은 공유하도록 보정
        if not (share_address or share_account or share_phone or share_vehicle):
            share_address = True

        shared_addr = _make_address(rng) if share_address else None
        bank_code, _ = rng.choice(BANKS)
        shared_acc = _make_account_no(rng, bank_code) if share_account else None
        shared_ph = _make_phone(rng) if share_phone else None
        shared_v = _make_vin(rng) if share_vehicle else None

        for _ in range(size):
            _gen_customer_bundle(
                rng, ctx, cust_idx,
                is_fraud=False, ring_id="",
                shared_account_no=shared_acc,
                shared_phone=shared_ph,
                shared_address=shared_addr,
                shared_vin=shared_v,
            )
            cust_idx += 1
    return cust_idx


# ------------------------------------------------------------------
# 한국 실제 사기 수법 5종 주입 (WP-KR)
# ------------------------------------------------------------------
# 각 수법은 (ring_id, ring_pattern) ground truth 를 부여한다. 수법별 신호 구성과
# 난이도를 달리해 탐지 파이프라인의 현실적 성능을 측정할 수 있게 한다.
#
# 공통 헬퍼 _RingBuild 는 멤버 생성·청구 인덱스·통계를 누적한다.
@dataclass
class _RingBuild:
    """사기 링 주입 진행 상태(고객/청구 인덱스 + 누적 통계)."""

    cust_idx: int
    claim_idx: int
    fraud_customers: int = 0
    fraud_claims: int = 0
    ring_pattern: dict[str, str] = field(default_factory=dict)


def _ring_base_date(rng: random.Random) -> date:
    """사기 링 집중 청구의 기준일(2024년 내)."""
    return _rand_date(rng, date(2024, 2, 1), date(2024, 10, 1))


def _inject_fake_admission_star(
    rng: random.Random, ctx: _Ctx, st: _RingBuild, ring_id: str,
) -> None:
    """허위입원 조직형(star) — 브로커 1 → 병원 1 → 환자 10~30명 방사형.

    한국 대표 수법(나이롱 환자): 브로커가 환자를 모아 특정 병원에 허위입원시키고
    입원비/합의금을 청구한다. 환자들은 같은 병원에 집중 청구(병원 허브) + 브로커가
    모두를 알선(브로커 허브). 청구는 짧은 기간에 몰린다(동시 모객).
    """
    pattern = "fake_admission_star"
    st.ring_pattern[ring_id] = pattern
    # 10~30명 방사형(과제 명세). 적발률(FSS ≈ 2~3%)을 과도히 넘기지 않도록
    # 링 개수(전체 30개 중 star 6개)로 균형을 맞춘다.
    n_patients = rng.randint(10, 30)

    # 전용 거점: 비인기 병원(정상 baseline 제외)에 집중시켜 허브를 부각.
    hosp = rng.choice([h["hospital_id"] for h in ctx.hospitals[N_HOSPITALS // 4:]])
    # 전용 브로커 — 사기 알선자(정상 브로커 풀 외 신규 ID)
    broker_id = f"BRK-K{len(ctx.brokers) + 1:04d}"
    ctx.brokers.append({
        "broker_id": broker_id,
        "name": f"{rng.choice(['명환', '재호', '동수', '성태'])}브로커",
        "business_reg_no": f"{rng.randint(100, 999)}-{rng.randint(10, 99)}-{rng.randint(10000, 99999)}",
        "phone": _make_phone(rng),
        "region": rng.choice(FRAUD_HOTSPOT_CITIES).split()[0],
        "created_at": _ts(_rand_date(rng, date(2020, 1, 1), date(2023, 1, 1))),
    })

    base = _ring_base_date(rng)
    for _ in range(n_patients):
        member = _gen_customer_bundle(
            rng, ctx, st.cust_idx,
            is_fraud=True, ring_id=ring_id, ring_pattern=pattern,
            fraud_demo=True,
        )
        ctx.brokered.append({"broker_id": broker_id, "customer_id": member["customer_id"]})
        st.cust_idx += 1
        st.fraud_customers += 1

        # 입원형 청구: 같은 병원 집중, 정비소는 보통 없음(차량 손상 부수적).
        inc = base + timedelta(days=rng.randint(0, 24))
        _gen_claim(
            rng, ctx, st.claim_idx, member,
            incident_date=inc,
            is_fraud=True, ring_id=ring_id, ring_pattern=pattern,
            hospital_id=hosp,
            repair_shop_id="" if rng.random() < 0.6 else rng.choice(ctx.repair_shops)["repair_shop_id"],
            account_id=member["_account_id"],
            incident_type="collision",
            claimed_amount=round(rng.uniform(2500000, 9000000), 2),  # 입원비/합의금 큰 편
        )
        st.claim_idx += 1
        st.fraud_claims += 1


def _inject_collision_ring(
    rng: random.Random, ctx: _Ctx, st: _RingBuild, ring_id: str,
    ring_size: tuple[int, int],
) -> None:
    """고의충돌 공모(crash-for-cash) — 운전자 3~5명 + 공통 정비소·계좌 + 상호 교차목격.

    한국 대표 수법: 여러 운전자가 고의로 추돌사고를 내고 서로 목격자가 되어준다
    (상호 WITNESSED_BY). 같은 정비소에서 수리하고(정비소 공유) 보험금을 공통
    계좌로 받기도 한다. 가장 전형적인 그래프 신호(교차목격+공유)를 갖춘다.
    """
    pattern = "collision_ring"
    st.ring_pattern[ring_id] = pattern
    size = rng.randint(max(3, ring_size[0]), max(5, ring_size[1]))

    shared_bank_code, _ = rng.choice(BANKS)
    shared_account_no = _make_account_no(rng, shared_bank_code)
    shared_shop = rng.choice([s["repair_shop_id"]
                              for s in ctx.repair_shops[N_REPAIR_SHOPS // 4:]])
    shared_hosp = rng.choice([h["hospital_id"] for h in ctx.hospitals[N_HOSPITALS // 4:]])
    # 대부분 멤버가 공통 계좌 수령(고의충돌 합의금 집중) — 일부만 자기계좌.
    members: list[dict] = []
    base = _ring_base_date(rng)
    for k in range(size):
        use_shared_acc = (k == 0) or (rng.random() < 0.75)
        member = _gen_customer_bundle(
            rng, ctx, st.cust_idx,
            is_fraud=True, ring_id=ring_id, ring_pattern=pattern,
            shared_account_no=shared_account_no if use_shared_acc else None,
            fraud_demo=True,
        )
        members.append(member)
        st.cust_idx += 1
        st.fraud_customers += 1

    ring_claims: list[dict] = []
    for m in members:
        inc = base + timedelta(days=rng.randint(0, 14))
        claim = _gen_claim(
            rng, ctx, st.claim_idx, m,
            incident_date=inc,
            is_fraud=True, ring_id=ring_id, ring_pattern=pattern,
            hospital_id=shared_hosp,
            repair_shop_id=shared_shop,
            account_id=m["_account_id"],
            incident_type="collision",
        )
        ring_claims.append(claim)
        st.claim_idx += 1
        st.fraud_claims += 1

    # 상호(양방향) 교차 목격 — 인접 멤버끼리 순환 목격.
    ids = [c["claim_id"] for c in ring_claims]
    for i, claim in enumerate(ring_claims):
        others = [ids[j] for j in range(len(ids)) if j != i]
        if len(others) <= 2:
            witnesses = others
        else:
            witnesses = [ids[(i + 1) % len(ids)], ids[(i - 1) % len(ids)]]
        claim["witness_claim_ids"] = json.dumps(witnesses, ensure_ascii=False)


def _inject_repair_overbill(
    rng: random.Random, ctx: _Ctx, st: _RingBuild, ring_id: str,
) -> None:
    """정비비 과다청구 — 특정 정비소 ↔ 다수 고객 반복 연결 + 청구금액 이상.

    한국 수법: 정비소가 경미사고를 과대 수리비로 부풀려 청구한다. 같은 정비소를
    소수~다수 고객이 반복 이용하고, 청구금액이 정상 대비 2~4배로 비정상이다.
    교차목격/공통계좌는 없어(정비소-고객 결탁) 그래프 공유 신호가 약하다(난이도 중).
    """
    pattern = "repair_overbill"
    st.ring_pattern[ring_id] = pattern
    n_cust = rng.randint(6, 14)
    shop = rng.choice([s["repair_shop_id"]
                       for s in ctx.repair_shops[N_REPAIR_SHOPS // 4:]])

    base = _ring_base_date(rng)
    for _ in range(n_cust):
        member = _gen_customer_bundle(
            rng, ctx, st.cust_idx,
            is_fraud=True, ring_id=ring_id, ring_pattern=pattern,
            fraud_demo=True,
        )
        st.cust_idx += 1
        st.fraud_customers += 1
        # 같은 정비소 반복 청구, 기간은 비교적 분산(상시 결탁) → 시간군집 약함.
        inc = base + timedelta(days=rng.randint(0, 120))
        _gen_claim(
            rng, ctx, st.claim_idx, member,
            incident_date=inc,
            is_fraud=True, ring_id=ring_id, ring_pattern=pattern,
            hospital_id="" if rng.random() < 0.7 else rng.choice(ctx.hospitals)["hospital_id"],
            repair_shop_id=shop,
            account_id=member["_account_id"],
            incident_type="collision",
            claimed_amount=round(rng.uniform(12000000, 28000000), 2),  # 정상(0.8~8M) 대비 이상
        )
        st.claim_idx += 1
        st.fraud_claims += 1


def _inject_agent_fraud(
    rng: random.Random, ctx: _Ctx, st: _RingBuild, ring_id: str,
) -> None:
    """설계사 개입 — 설계사 1 → 다수 Policy/Customer + 공통 수취 계좌.

    한국 수법: 보험설계사가 가공/명의도용 계약을 다수 모집하고 보험금을 본인이
    관리하는 공통 계좌로 가로챈다. 설계사 허브(다수 계약 모집) + 공통 계좌가
    핵심 신호이나, 교차목격이 없고 주소도 분산되어 룰 단독으로는 어렵다(난이도↑).
    """
    pattern = "agent_fraud"
    st.ring_pattern[ring_id] = pattern
    n_cust = rng.randint(8, 18)

    # 전용 사기 설계사(정상 풀 외 신규 ID)
    agent_id = f"AGT-K{len(ctx.agents) + 1:05d}"
    ctx.agents.append({
        "agent_id": agent_id,
        "name": _make_name(rng),
        "license_no": f"설계-{rng.randint(2018, 2024)}-{rng.randint(1, 99999):05d}",
        "agency": rng.choice(["삼성화재", "DB손보", "현대해상", "KB손보", "메리츠"]),
        "phone": _make_phone(rng),
        "created_at": _ts(_rand_date(rng, date(2019, 1, 1), date(2023, 1, 1))),
    })
    shared_bank_code, _ = rng.choice(BANKS)
    shared_account_no = _make_account_no(rng, shared_bank_code)

    base = _ring_base_date(rng)
    for _ in range(n_cust):
        # 대부분 공통 계좌(설계사 가로채기), 일부는 자기 계좌(위장).
        use_shared_acc = rng.random() < 0.8
        member = _gen_customer_bundle(
            rng, ctx, st.cust_idx,
            is_fraud=True, ring_id=ring_id, ring_pattern=pattern,
            shared_account_no=shared_account_no if use_shared_acc else None,
            fraud_demo=True,
        )
        # 설계사가 이 고객의 계약을 모집했음(SOLD_POLICY → Policy).
        ctx.sold_policy.append({"agent_id": agent_id, "policy_id": member["_policy_id"]})
        st.cust_idx += 1
        st.fraud_customers += 1

        inc = base + timedelta(days=rng.randint(0, 90))
        _gen_claim(
            rng, ctx, st.claim_idx, member,
            incident_date=inc,
            is_fraud=True, ring_id=ring_id, ring_pattern=pattern,
            hospital_id=rng.choice(ctx.hospitals)["hospital_id"] if rng.random() < 0.5 else "",
            repair_shop_id=rng.choice(ctx.repair_shops)["repair_shop_id"] if rng.random() < 0.6 else "",
            account_id=member["_account_id"],
            incident_type="collision",
        )
        st.claim_idx += 1
        st.fraud_claims += 1


def _inject_driver_swap(
    rng: random.Random, ctx: _Ctx, st: _RingBuild, ring_id: str,
) -> None:
    """운전자 교체/동승자 공모 — 같은 차량·사고에 운전자가 바뀌고 동승자 다수 청구.

    한국 수법: 무면허/음주 운전자를 보험 처리 가능한 명의로 교체하거나, 한 사고의
    동승자 여러 명이 부상으로 청구한다. 핵심 신호는 **공유 차량(vin)** 과 같은 사고에
    대한 **동시 청구**(같은 incident_date·차량). 교차목격/공통계좌가 없어 어렵다.
    """
    pattern = "driver_swap"
    st.ring_pattern[ring_id] = pattern
    size = rng.randint(3, 5)

    shared_vin = _make_vin(rng)
    base = _ring_base_date(rng)
    incident_day = base + timedelta(days=rng.randint(0, 5))  # 동일 사고 — 같은 날 집중

    members: list[dict] = []
    shared_vehicle_id: str | None = None
    for k in range(size):
        # 모든 멤버가 같은 차량(vin) 공유 — 운전자 교체/동승.
        member = _gen_customer_bundle(
            rng, ctx, st.cust_idx,
            is_fraud=True, ring_id=ring_id, ring_pattern=pattern,
            shared_vin=shared_vin,
            fraud_demo=True,
        )
        if shared_vehicle_id is None:
            shared_vehicle_id = member["_vehicle_id"]
        members.append(member)
        st.cust_idx += 1
        st.fraud_customers += 1

    for m in members:
        inc = incident_day + timedelta(days=rng.randint(0, 3))
        _gen_claim(
            rng, ctx, st.claim_idx, m,
            incident_date=inc,
            is_fraud=True, ring_id=ring_id, ring_pattern=pattern,
            hospital_id=rng.choice(ctx.hospitals)["hospital_id"],  # 동승자 부상 치료
            repair_shop_id=rng.choice(ctx.repair_shops)["repair_shop_id"] if rng.random() < 0.5 else "",
            account_id=m["_account_id"],
            vehicle_id=shared_vehicle_id,  # 같은 차량으로 청구(공유 vin)
            incident_type="collision",
        )
        st.claim_idx += 1
        st.fraud_claims += 1


# 수법 → 주입 함수 매핑(난이도 다양화). 순환 배정으로 5종을 골고루 등장시킨다.
def _inject_fraud_rings(
    rng: random.Random,
    ctx: _Ctx,
    *,
    n_rings: int,
    ring_size: tuple[int, int],
    next_cust_idx: int,
    next_claim_idx: int,
) -> tuple[int, int, int, int, dict[str, str]]:
    """한국 실제 사기 수법 5종을 ground truth(ring_id + ring_pattern)로 주입한다.

    수법(RING_PATTERNS) — 난이도 다양화:
        fake_admission_star  허위입원 조직형(브로커→병원→환자 방사형, 병원/브로커 허브)
        collision_ring       고의충돌 공모(교차목격+공통 정비소/계좌) — 전형적 강신호
        repair_overbill      정비비 과다청구(정비소 허브 + 금액 이상) — 공유 신호 약
        agent_fraud          설계사 개입(설계사 허브 + 공통 수취계좌) — 어려움
        driver_swap          운전자 교체/동승자 공모(공유 vin + 동시 청구) — 어려움

    Returns:
        (다음 고객 idx, 다음 청구 idx, 주입 청구 수, 주입 고객 수, ring_id→pattern 맵).
    """
    st = _RingBuild(cust_idx=next_cust_idx, claim_idx=next_claim_idx)

    for r in range(1, n_rings + 1):
        ring_id = f"RING-{r:03d}"
        pattern = RING_PATTERNS[(r - 1) % len(RING_PATTERNS)]
        if pattern == "fake_admission_star":
            _inject_fake_admission_star(rng, ctx, st, ring_id)
        elif pattern == "collision_ring":
            _inject_collision_ring(rng, ctx, st, ring_id, ring_size)
        elif pattern == "repair_overbill":
            _inject_repair_overbill(rng, ctx, st, ring_id)
        elif pattern == "agent_fraud":
            _inject_agent_fraud(rng, ctx, st, ring_id)
        elif pattern == "driver_swap":
            _inject_driver_swap(rng, ctx, st, ring_id)

    return st.cust_idx, st.claim_idx, st.fraud_claims, st.fraud_customers, st.ring_pattern


def _inject_coincidental_witness(
    rng: random.Random,
    ctx: _Ctx,
    *,
    n_pairs: int,
) -> int:
    """정상 단방향 목격 노이즈 주입(우연한 목격 — 교차/상호 아님).

    현실: 실제 사고에는 목격자(다른 사고 당사자)가 있을 수 있다. 하지만 이는
    한 방향(A의 청구가 B를 목격)일 뿐 **상호 교차목격이 아니다**. Q3 는 양방향만
    잡으므로 이 노이즈는 (정상적으로) 탐지되지 않아야 한다 — 견고성 시험.

    이미 생성된 정상 청구 중 무작위 두 건을 골라 한쪽→다른쪽 단방향 엣지만 만든다.

    Returns:
        주입한 단방향 목격 엣지 수.
    """
    # 정상(비사기) 청구만 후보
    normal_claims = [c for c in ctx.claims if not c["is_fraud_ring"]]
    if len(normal_claims) < 2:
        return 0
    added = 0
    for _ in range(n_pairs):
        a, b = rng.sample(normal_claims, 2)
        existing = json.loads(a["witness_claim_ids"]) if a["witness_claim_ids"] else []
        if b["claim_id"] not in existing:
            existing.append(b["claim_id"])
            a["witness_claim_ids"] = json.dumps(existing, ensure_ascii=False)
            added += 1
    return added


def _assign_normal_broker_agent(rng: random.Random, ctx: _Ctx) -> None:
    """정상 브로커/설계사 배경 연결(오탐 유발 노이즈 — 허브 신호의 정상 배경).

    현실: 정상 설계사도 수십~수백 계약을 모집하고, 정상 알선자도 다수 고객을
    연결한다. 따라서 "브로커/설계사 허브" 자체는 사기에 특이적이지 않다. 정상
    고객 일부를 정상 브로커/설계사에 연결해 탐지가 단순 허브 크기로 오탐하지
    않도록 배경 분포를 만든다.

    사기 전용 브로커/설계사(BRK-K*/AGT-K*)는 건드리지 않는다(이미 ring 에서 연결).
    """
    normal_customers = [c for c in ctx.customers if not c["is_fraud_ring"]]
    if not normal_customers or not ctx.brokers or not ctx.agents:
        return

    # 정상 브로커 풀(BRK-NNNN 정상 ID만) — 사기 전용(BRK-K*) 제외.
    normal_brokers = [b["broker_id"] for b in ctx.brokers
                      if not b["broker_id"].startswith("BRK-K")]
    normal_agents = [a["agent_id"] for a in ctx.agents
                     if not a["agent_id"].startswith("AGT-K")]

    # 정상 고객의 ~20% 를 정상 브로커가 알선(렌터카·제휴 모집 등).
    for c in normal_customers:
        if normal_brokers and rng.random() < 0.20:
            ctx.brokered.append({
                "broker_id": rng.choice(normal_brokers),
                "customer_id": c["customer_id"],
            })

    # 정상 계약의 대부분(~85%)을 정상 설계사가 모집(현실: 설계사 채널 비중 큼).
    fraud_cust_ids = {c["customer_id"] for c in ctx.customers if c["is_fraud_ring"]}
    for p in ctx.policies:
        if p["customer_id"] in fraud_cust_ids:
            continue  # 사기 계약은 사기 설계사가 이미 모집(또는 미연결)
        if normal_agents and rng.random() < 0.85:
            ctx.sold_policy.append({
                "agent_id": rng.choice(normal_agents),
                "policy_id": p["policy_id"],
            })


# ------------------------------------------------------------------
# CSV 직렬화
# ------------------------------------------------------------------
def _write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


_CUSTOMER_FIELDS = ["customer_id", "name", "id_number", "birth_date", "gender",
                    "address", "phone_number", "email", "created_at",
                    "is_fraud_ring", "ring_id", "ring_pattern"]
_POLICY_FIELDS = ["policy_id", "customer_id", "vehicle_id", "product_code",
                  "coverage_type", "start_date", "end_date", "premium_amount",
                  "coverage_limit", "status", "created_at"]
_VEHICLE_FIELDS = ["vehicle_id", "customer_id", "vin", "license_plate", "make",
                   "model", "year", "color", "registered_at"]
_ACCOUNT_FIELDS = ["account_id", "account_no", "bank_code", "bank_name",
                   "account_holder", "account_type", "created_at"]
_HOSPITAL_FIELDS = ["hospital_id", "institution_code", "name", "type", "address",
                    "phone", "license_no", "specialties", "created_at"]
_SHOP_FIELDS = ["repair_shop_id", "business_reg_no", "name", "type", "address",
                "phone", "license_no", "rating", "created_at"]
_CLAIM_FIELDS = ["claim_id", "customer_id", "policy_id", "vehicle_id",
                 "hospital_id", "repair_shop_id", "account_id", "incident_date",
                 "report_date", "incident_type", "incident_location",
                 "claimed_amount", "paid_amount", "claim_status", "fraud_label",
                 "witness_claim_ids", "created_at", "is_fraud_ring", "ring_id",
                 "ring_pattern"]
# WP-KR: 브로커/설계사 마스터 + 관계(엣지 소스) CSV
_BROKER_FIELDS = ["broker_id", "name", "business_reg_no", "phone", "region",
                  "created_at"]
_AGENT_FIELDS = ["agent_id", "name", "license_no", "agency", "phone", "created_at"]
_BROKERED_FIELDS = ["broker_id", "customer_id"]
_SOLD_POLICY_FIELDS = ["agent_id", "policy_id"]


@dataclass
class GenResult:
    """생성 결과 요약."""

    out_dir: Path
    n_customers: int
    n_claims: int
    n_rings: int
    n_fraud_claims: int
    n_fraud_customers: int
    n_families: int
    n_brokers: int = 0
    n_agents: int = 0
    n_brokered: int = 0
    n_sold_policy: int = 0
    ring_pattern: dict[str, str] = field(default_factory=dict)


def generate(
    *,
    out_dir: str | Path = DEFAULT_OUT,
    n_customers: int = DEFAULT_CUSTOMERS,
    n_claims: int = DEFAULT_CLAIMS,
    n_rings: int = DEFAULT_RINGS,
    ring_size: tuple[int, int] = DEFAULT_RING_SIZE,
    seed: int = DEFAULT_SEED,
    n_families: int = DEFAULT_FAMILIES,
) -> GenResult:
    """현실적 합성 데이터를 생성해 CSV 9종을 ``out_dir`` 에 저장한다.

    정상 노이즈(가족 공유·정상 핫스팟·우연 단방향 목격·정상 브로커/설계사 배경)와
    한국 실제 사기 수법 5종(fake_admission_star/collision_ring/repair_overbill/
    agent_fraud/driver_swap)을 함께 주입하여 탐지 성능을 과장 없이 측정한다.

    Args:
        out_dir: 출력 디렉토리 (기본 ``data/synthetic``).
        n_customers: 정상 고객 수 목표(가족/사기 링 멤버는 추가됨).
        n_claims: 정상 청구 수 목표(사기 청구는 추가됨).
        n_rings: 주입할 사기 링 개수(한국 수법 5종 순환 배정).
        ring_size: collision_ring 등 소규모 링당 멤버 수 (min, max).
        seed: random seed (재현성).
        n_families: 정상 가족 공유 클러스터 수(오탐 유발 노이즈).
    """
    rng = random.Random(seed)
    ctx = _Ctx()
    out = Path(out_dir)

    # 1) 마스터 (병원·정비소·브로커·설계사)
    _gen_hospitals(rng, ctx)
    _gen_repair_shops(rng, ctx)
    _gen_brokers(rng, ctx)
    _gen_agents(rng, ctx)

    # 2) 정상 고객 번들
    for idx in range(1, n_customers + 1):
        _gen_customer_bundle(rng, ctx, idx)

    # 3) 정상 가족 공유 클러스터(노이즈) — n_customers 이후 인덱스부터
    next_idx = n_customers + 1
    next_idx = _inject_normal_families(
        rng, ctx, n_families=n_families, next_cust_idx=next_idx
    )

    # 4) 정상 청구 — 기존 고객에 분산(인기 병원/정비소로 일부 자연 집중 → 정상 핫스팟)
    claim_idx = 1
    for _ in range(n_claims):
        cust = rng.choice(ctx.customers)
        inc = _rand_date(rng, date(2024, 1, 1), date(2024, 12, 1))
        # 25% 청구는 인기 대형 병원/정비소로 집중(정상 핫스팟 형성)
        hosp = None
        shop = None
        if rng.random() < 0.25:
            hosp = rng.choice(ctx.popular_hospitals)
        if rng.random() < 0.25:
            shop = rng.choice(ctx.popular_shops)
        _gen_claim(rng, ctx, claim_idx, cust, incident_date=inc,
                   hospital_id=hosp, repair_shop_id=shop)
        claim_idx += 1

    # 5) 사기 링 주입(유형 다양화)
    _, claim_idx, n_fraud_claims, n_fraud_customers, ring_pattern = _inject_fraud_rings(
        rng, ctx,
        n_rings=n_rings, ring_size=ring_size,
        next_cust_idx=next_idx, next_claim_idx=claim_idx,
    )

    # 6) 정상 단방향 목격 노이즈(우연 — 상호 아님, Q3 미탐지 기대)
    _inject_coincidental_witness(rng, ctx, n_pairs=max(50, n_claims // 200))

    # 7) 정상 브로커/설계사 배경 연결(허브 신호의 정상 배경 — 오탐 유발 노이즈)
    _assign_normal_broker_agent(rng, ctx)

    # 8) CSV 직렬화 (9종)
    _write_csv(out / "customers.csv", ctx.customers, _CUSTOMER_FIELDS)
    _write_csv(out / "policies.csv", ctx.policies, _POLICY_FIELDS)
    _write_csv(out / "vehicles.csv", ctx.vehicles, _VEHICLE_FIELDS)
    _write_csv(out / "accounts.csv", ctx.accounts, _ACCOUNT_FIELDS)
    _write_csv(out / "hospitals.csv", ctx.hospitals, _HOSPITAL_FIELDS)
    _write_csv(out / "repair_shops.csv", ctx.repair_shops, _SHOP_FIELDS)
    _write_csv(out / "claims.csv", ctx.claims, _CLAIM_FIELDS)
    _write_csv(out / "brokers.csv", ctx.brokers, _BROKER_FIELDS)
    _write_csv(out / "agents.csv", ctx.agents, _AGENT_FIELDS)
    _write_csv(out / "brokered.csv", ctx.brokered, _BROKERED_FIELDS)
    _write_csv(out / "sold_policy.csv", ctx.sold_policy, _SOLD_POLICY_FIELDS)

    return GenResult(
        out_dir=out,
        n_customers=len(ctx.customers),
        n_claims=len(ctx.claims),
        n_rings=n_rings,
        n_fraud_claims=n_fraud_claims,
        n_fraud_customers=n_fraud_customers,
        n_families=n_families,
        n_brokers=len(ctx.brokers),
        n_agents=len(ctx.agents),
        n_brokered=len(ctx.brokered),
        n_sold_policy=len(ctx.sold_policy),
        ring_pattern=ring_pattern,
    )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="THOTH-ON 합성 데이터 생성기 (WP1-3, 현실판)")
    p.add_argument("--out", default=DEFAULT_OUT, help="출력 디렉토리")
    p.add_argument("--customers", type=int, default=DEFAULT_CUSTOMERS, help="정상 고객 수")
    p.add_argument("--claims", type=int, default=DEFAULT_CLAIMS, help="정상 청구 수")
    p.add_argument("--rings", type=int, default=DEFAULT_RINGS, help="사기 링 개수")
    p.add_argument("--ring-size", type=int, nargs=2, default=list(DEFAULT_RING_SIZE),
                   metavar=("MIN", "MAX"), help="링당 멤버 수 범위")
    p.add_argument("--seed", type=int, default=DEFAULT_SEED, help="random seed")
    p.add_argument("--families", type=int, default=DEFAULT_FAMILIES,
                   help="정상 가족 공유 클러스터 수(오탐 유발 노이즈)")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    res = generate(
        out_dir=args.out,
        n_customers=args.customers,
        n_claims=args.claims,
        n_rings=args.rings,
        ring_size=tuple(args.ring_size),
        seed=args.seed,
        n_families=args.families,
    )
    print("=" * 60)
    print(" 합성 데이터 생성 완료 (WP-KR, 한국 실제 수법판)")
    print("=" * 60)
    print(f"  출력 디렉토리 : {res.out_dir}")
    print(f"  고객(전체)    : {res.n_customers:,}")
    print(f"  청구(전체)    : {res.n_claims:,}")
    print(f"  정상 가족 클러스터: {res.n_families}")
    print(f"  브로커/설계사 : {res.n_brokers} / {res.n_agents}")
    print(f"  BROKERED/SOLD : {res.n_brokered:,} / {res.n_sold_policy:,}")
    print(f"  사기 링       : {res.n_rings}")
    print(f"  사기 청구     : {res.n_fraud_claims:,}")
    print(f"  사기 고객     : {res.n_fraud_customers:,} "
          f"(적발률 ≈ {res.n_fraud_customers / max(res.n_customers, 1) * 100:.1f}%)")
    print("-" * 60)
    # 수법별 링/멤버 분포
    from collections import Counter
    dist = Counter(res.ring_pattern.values())
    print("  한국 수법(ring_pattern)별 링 개수:")
    for pat in RING_PATTERNS:
        print(f"    {pat:<22}: {dist.get(pat, 0)}개")
    print("-" * 60)
    print("  생성 파일(9종): customers, policies, vehicles, accounts,")
    print("    hospitals, repair_shops, claims, brokers, agents,")
    print("    brokered, sold_policy (.csv)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
