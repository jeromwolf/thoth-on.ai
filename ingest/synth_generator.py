"""자동차보험 합성 데이터 생성기 + 사기 링 주입 (WP1-3).

mapping.md §2 컬럼 스키마를 따라 7종 소스 CSV 를 생성한다:
    customers, policies, claims, vehicles, accounts, hospitals, repair_shops

핵심: crash-for-cash 사기 링(ground truth) 을 의도적으로 N개 주입한다.
각 링 멤버는 동일 Account / RepairShop / Hospital 을 공유하고, 서로의 사고를
교차 목격(WITNESSED_BY)하며, 짧은 기간에 집중 청구한다. 사기 청구·고객에는
ground truth 라벨(``is_fraud_ring``, ``ring_id``)을 부여하여 WP2 재현율 측정의
정답지로 사용한다.

PII(name/id_number/phone_number/email/account_no/account_holder)는 평문으로
CSV 에 생성한다(가명처리는 적재 단계 loader 가 담당). 재현성을 위해 random
seed 를 고정한다.

CLI:
    python -m ingest.synth_generator [--out DIR] [--customers N] [--claims N]
                                     [--rings N] [--ring-size MIN MAX] [--seed S]
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
DEFAULT_RINGS = 15
DEFAULT_RING_SIZE = (3, 6)
DEFAULT_SEED = 42
DEFAULT_OUT = "data/synthetic"

# 보조 마스터 규모 (엔티티 합계 ~5만 노드 목표에 맞춰 조정)
N_HOSPITALS = 120
N_REPAIR_SHOPS = 200

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
    shared_account_no: str | None = None,
) -> dict:
    """고객 1명과 그에 딸린 차량·계좌·계약을 생성하고 customer 레코드 반환.

    shared_account_no 가 주어지면 해당 고객 계좌번호를 강제(사기 링 계좌 공유).
    반환 dict 에 내부 참조용 키(_vehicle_id, _policy_id, _account_id)를 부착한다.
    """
    cust_id = f"CUST-{idx:05d}"
    birth = _rand_date(rng, date(1960, 1, 1), date(2002, 12, 31))
    created = _rand_date(rng, date(2019, 1, 1), date(2023, 6, 1))

    customer = {
        "customer_id": cust_id,
        "name": _make_name(rng),
        "id_number": _make_ssn(rng, birth),
        "birth_date": birth.isoformat(),
        "gender": rng.choice(["M", "F"]),
        "address": _make_address(rng),
        "phone_number": _make_phone(rng),
        "email": f"user{idx}@example.com",
        "created_at": _ts(created),
        # ground truth 라벨 (정상은 빈값/False)
        "is_fraud_ring": is_fraud,
        "ring_id": ring_id,
    }
    ctx.customers.append(customer)

    # 차량
    veh_id = f"VEH-{idx:05d}"
    make, models = rng.choice(MAKES_MODELS)
    ctx.vehicles.append({
        "vehicle_id": veh_id,
        "customer_id": cust_id,
        "vin": _make_vin(rng),
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
    hospital_id: str | None = None,
    repair_shop_id: str | None = None,
    account_id: str | None = None,
    incident_type: str | None = None,
    witness_claim_ids: list[str] | None = None,
) -> dict:
    """청구 1건 생성. 사기 링이면 공유 hospital/shop/account 및 라벨 부여."""
    claim_id = f"CLM-2024-{claim_idx:06d}"
    report = incident_date + timedelta(days=rng.randint(0, 5))
    claimed = round(rng.uniform(800000, 8000000), 2)
    status = rng.choice(["approved", "approved", "pending", "under_review", "denied"])
    paid = round(claimed * rng.uniform(0.7, 1.0), 2) if status == "approved" else None

    claim = {
        "claim_id": claim_id,
        "customer_id": customer["customer_id"],
        "policy_id": customer["_policy_id"],
        "vehicle_id": customer["_vehicle_id"],
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
    }
    ctx.claims.append(claim)
    return claim


# ------------------------------------------------------------------
# 사기 링 주입 (crash-for-cash)
# ------------------------------------------------------------------
def _inject_fraud_rings(
    rng: random.Random,
    ctx: _Ctx,
    *,
    n_rings: int,
    ring_size: tuple[int, int],
    next_cust_idx: int,
    next_claim_idx: int,
) -> tuple[int, int, int, int]:
    """crash-for-cash 사기 링 주입.

    각 링: 공유 Account/RepairShop/Hospital + 교차 목격(WITNESSED_BY) + 집중 청구.
    Returns: (다음 고객 idx, 다음 청구 idx, 주입 청구 수, 주입 고객 수).
    """
    fraud_claims = 0
    fraud_customers = 0
    cust_idx = next_cust_idx
    claim_idx = next_claim_idx

    for r in range(1, n_rings + 1):
        ring_id = f"RING-{r:03d}"
        size = rng.randint(ring_size[0], ring_size[1])

        # 링 전용 공유 엔티티
        shared_bank_code, _ = rng.choice(BANKS)
        shared_account_no = _make_account_no(rng, shared_bank_code)
        shared_hospital = rng.choice(ctx.hospitals)["hospital_id"]
        shared_shop = rng.choice(ctx.repair_shops)["repair_shop_id"]

        # 링 멤버 생성 (동일 계좌번호 공유 → 엔티티 해소로 단일 Account 병합)
        members: list[dict] = []
        for _ in range(size):
            member = _gen_customer_bundle(
                rng, ctx, cust_idx,
                is_fraud=True, ring_id=ring_id,
                shared_account_no=shared_account_no,
            )
            members.append(member)
            cust_idx += 1
            fraud_customers += 1

        # 짧은 기간(2~3주) 집중 청구 — 멤버당 1건
        base_date = _rand_date(rng, date(2024, 2, 1), date(2024, 10, 1))
        ring_claims: list[dict] = []
        for m in members:
            inc = base_date + timedelta(days=rng.randint(0, 18))
            claim = _gen_claim(
                rng, ctx, claim_idx, m,
                incident_date=inc,
                is_fraud=True, ring_id=ring_id,
                hospital_id=shared_hospital,
                repair_shop_id=shared_shop,
                account_id=m["_account_id"],  # 각 멤버 계좌는 동일 account_no 보유
                incident_type="collision",
            )
            ring_claims.append(claim)
            claim_idx += 1
            fraud_claims += 1

        # 교차 목격(WITNESSED_BY): 링을 원형으로 — 각 청구가 다음 청구를 목격,
        # 그리고 다음 청구도 해당 청구를 목격(양방향 교차)하도록 구성.
        ids = [c["claim_id"] for c in ring_claims]
        for i, claim in enumerate(ring_claims):
            others = [ids[j] for j in range(len(ids)) if j != i]
            # 링이 작으면 전원 교차, 크면 인접 2명과 교차하여 밀집 순환 형성
            if len(others) <= 2:
                witnesses = others
            else:
                nxt = ids[(i + 1) % len(ids)]
                prv = ids[(i - 1) % len(ids)]
                witnesses = [nxt, prv]
            claim["witness_claim_ids"] = json.dumps(witnesses, ensure_ascii=False)

    return cust_idx, claim_idx, fraud_claims, fraud_customers


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
                    "is_fraud_ring", "ring_id"]
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
                 "witness_claim_ids", "created_at", "is_fraud_ring", "ring_id"]


@dataclass
class GenResult:
    """생성 결과 요약."""

    out_dir: Path
    n_customers: int
    n_claims: int
    n_rings: int
    n_fraud_claims: int
    n_fraud_customers: int


def generate(
    *,
    out_dir: str | Path = DEFAULT_OUT,
    n_customers: int = DEFAULT_CUSTOMERS,
    n_claims: int = DEFAULT_CLAIMS,
    n_rings: int = DEFAULT_RINGS,
    ring_size: tuple[int, int] = DEFAULT_RING_SIZE,
    seed: int = DEFAULT_SEED,
) -> GenResult:
    """합성 데이터를 생성해 CSV 7종을 ``out_dir`` 에 저장한다.

    Args:
        out_dir: 출력 디렉토리 (기본 ``data/synthetic``).
        n_customers: 정상 고객 수 목표(사기 링 멤버는 추가됨).
        n_claims: 정상 청구 수 목표(사기 청구는 추가됨).
        n_rings: 주입할 crash-for-cash 링 개수.
        ring_size: 링당 멤버 수 (min, max).
        seed: random seed (재현성).
    """
    rng = random.Random(seed)
    ctx = _Ctx()
    out = Path(out_dir)

    # 1) 마스터 (병원·정비소)
    _gen_hospitals(rng, ctx)
    _gen_repair_shops(rng, ctx)

    # 2) 정상 고객 번들
    for idx in range(1, n_customers + 1):
        _gen_customer_bundle(rng, ctx, idx)

    # 3) 정상 청구 — 기존 고객에 분산
    claim_idx = 1
    for _ in range(n_claims):
        cust = rng.choice(ctx.customers)
        inc = _rand_date(rng, date(2024, 1, 1), date(2024, 12, 1))
        _gen_claim(rng, ctx, claim_idx, cust, incident_date=inc)
        claim_idx += 1

    # 4) 사기 링 주입
    next_cust_idx = n_customers + 1
    _, _, n_fraud_claims, n_fraud_customers = _inject_fraud_rings(
        rng, ctx,
        n_rings=n_rings, ring_size=ring_size,
        next_cust_idx=next_cust_idx, next_claim_idx=claim_idx,
    )

    # 5) CSV 직렬화
    _write_csv(out / "customers.csv", ctx.customers, _CUSTOMER_FIELDS)
    _write_csv(out / "policies.csv", ctx.policies, _POLICY_FIELDS)
    _write_csv(out / "vehicles.csv", ctx.vehicles, _VEHICLE_FIELDS)
    _write_csv(out / "accounts.csv", ctx.accounts, _ACCOUNT_FIELDS)
    _write_csv(out / "hospitals.csv", ctx.hospitals, _HOSPITAL_FIELDS)
    _write_csv(out / "repair_shops.csv", ctx.repair_shops, _SHOP_FIELDS)
    _write_csv(out / "claims.csv", ctx.claims, _CLAIM_FIELDS)

    return GenResult(
        out_dir=out,
        n_customers=len(ctx.customers),
        n_claims=len(ctx.claims),
        n_rings=n_rings,
        n_fraud_claims=n_fraud_claims,
        n_fraud_customers=n_fraud_customers,
    )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="THOTH-ON 합성 데이터 생성기 (WP1-3)")
    p.add_argument("--out", default=DEFAULT_OUT, help="출력 디렉토리")
    p.add_argument("--customers", type=int, default=DEFAULT_CUSTOMERS, help="정상 고객 수")
    p.add_argument("--claims", type=int, default=DEFAULT_CLAIMS, help="정상 청구 수")
    p.add_argument("--rings", type=int, default=DEFAULT_RINGS, help="사기 링 개수")
    p.add_argument("--ring-size", type=int, nargs=2, default=list(DEFAULT_RING_SIZE),
                   metavar=("MIN", "MAX"), help="링당 멤버 수 범위")
    p.add_argument("--seed", type=int, default=DEFAULT_SEED, help="random seed")
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
    )
    print("=" * 56)
    print(" 합성 데이터 생성 완료 (WP1-3)")
    print("=" * 56)
    print(f"  출력 디렉토리 : {res.out_dir}")
    print(f"  고객(전체)    : {res.n_customers:,}")
    print(f"  청구(전체)    : {res.n_claims:,}")
    print(f"  사기 링       : {res.n_rings}")
    print(f"  사기 청구     : {res.n_fraud_claims:,}")
    print(f"  사기 고객     : {res.n_fraud_customers:,}")
    print("-" * 56)
    print("  생성 파일: customers, policies, vehicles, accounts,")
    print("            hospitals, repair_shops, claims (.csv)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
