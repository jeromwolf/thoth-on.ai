"""② 캐글 단건 청구 → 이종 그래프 변환 PoC (격리 적재).

fraud_oracle.csv 의 각 행(청구)을 :KaggleClaim 노드로 만들고, 범주형 컬럼을
**공유 엔티티 노드로 승격**해 같은 Rep/Make/Agent/PolicyType 등을 공유하는 청구들을
묶는 이종 그래프를 구성한다. 그 위에서 "공유 허브"(한 Rep/Agent 에 사기 청구가
집중되는지) 패턴을 집계하고, 실제 사기 라벨(FraudFound_P)과의 연관을 측정한다.

[격리 — 기존 한국형 그래프 절대 미훼손]
    모든 노드 라벨에 ``Kaggle`` 접두어, 모든 관계 타입에 ``K_`` 접두어를 사용한다.
    기존 Customer/Claim/Policy/... 노드·관계와 식별자·라벨이 전혀 겹치지 않으므로
    MERGE 충돌이 발생하지 않는다. (reset 없이 추가 적재 가능 — 기존 그래프 보존)

[승격하는 공유 엔티티]
    RepNumber        → :KaggleRep
    Make             → :KaggleMake
    AgentType        → :KaggleAgent
    PolicyType       → :KagglePolicyType
    VehicleCategory  → :KaggleVehicleCategory
    AccidentArea     → :KaggleAccidentArea
    BasePolicy       → :KaggleBasePolicy

[한계 — 반드시 명시]
    이 그래프의 관계는 "같은 범주 값을 공유"한다는 의미일 뿐, 진짜 공모 네트워크
    (같은 계좌·교차목격·동일 인물)가 **아니다**. 예컨대 "같은 Make(Honda)" 청구
    수천 건이 한 KaggleMake 노드로 묶이는 것은 인위적 군집이다. 따라서 여기서
    찾는 "공유 허브"는 사기 신호의 약한 상관일 뿐 공모 증거가 아니다.

CLI:
    python -m ingest.kaggle_graph load   [--csv PATH] [--limit N]
    python -m ingest.kaggle_graph stats
    python -m ingest.kaggle_graph clear   # Kaggle* 노드만 삭제(기존 그래프 보존)
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
from typing import Any

from thoth import db

DEFAULT_CSV = "data/kaggle/fraud_oracle.csv"
BATCH_SIZE = 2000

# (CSV 컬럼, 공유 노드 라벨, 노드 키 속성, 관계 타입) — 승격 대상 6+1종.
SHARED_ENTITIES = [
    ("RepNumber", "KaggleRep", "rep_number", "K_HANDLED_BY"),
    ("Make", "KaggleMake", "make", "K_OF_MAKE"),
    ("AgentType", "KaggleAgent", "agent_type", "K_VIA_AGENT"),
    ("PolicyType", "KagglePolicyType", "policy_type", "K_OF_POLICY_TYPE"),
    ("VehicleCategory", "KaggleVehicleCategory", "vehicle_category",
     "K_OF_VEHICLE_CATEGORY"),
    ("AccidentArea", "KaggleAccidentArea", "accident_area", "K_IN_AREA"),
    ("BasePolicy", "KaggleBasePolicy", "base_policy", "K_OF_BASE_POLICY"),
]


def _read_rows(csv_path: Path, *, limit: int | None = None) -> list[dict[str, str]]:
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    return rows[:limit] if limit else rows


# ------------------------------------------------------------------
# 적재 — 모두 MERGE (멱등). Kaggle* 라벨만 사용(기존 그래프 격리).
# ------------------------------------------------------------------
def ensure_constraints() -> None:
    """KaggleClaim/공유엔티티 UNIQUE 제약(멱등). 기존 제약 미변경."""
    stmts = [
        "CREATE CONSTRAINT kaggle_claim_id IF NOT EXISTS "
        "FOR (n:KaggleClaim) REQUIRE n.policy_number IS UNIQUE",
    ]
    for _, label, key, _rel in SHARED_ENTITIES:
        stmts.append(
            f"CREATE CONSTRAINT kaggle_{key} IF NOT EXISTS "
            f"FOR (n:{label}) REQUIRE n.value IS UNIQUE"
        )
    with db.session() as sess:
        for s in stmts:
            sess.run(s)


def _load_claims(sess: Any, rows: list[dict], batch_size: int) -> int:
    payload = []
    for r in rows:
        payload.append({
            "policy_number": (r.get("PolicyNumber") or "").strip(),
            "fraud": 1 if r.get("FraudFound_P") == "1" else 0,
            "year": (r.get("Year") or "").strip(),
            "month": (r.get("Month") or "").strip(),
            "sex": (r.get("Sex") or "").strip(),
            "age": (r.get("Age") or "").strip(),
            "fault": (r.get("Fault") or "").strip(),
        })
    cypher = """
    UNWIND $rows AS row
    MERGE (c:KaggleClaim {policy_number: row.policy_number})
    SET c.fraud = row.fraud,
        c.year = row.year,
        c.month = row.month,
        c.sex = row.sex,
        c.age = row.age,
        c.fault = row.fault
    """
    total = 0
    for i in range(0, len(payload), batch_size):
        chunk = payload[i:i + batch_size]
        sess.run(cypher, rows=chunk).consume()
        total += len(chunk)
    return total


def _load_shared_edges(
    sess: Any, rows: list[dict], col: str, label: str, key: str, rel: str,
    batch_size: int,
) -> int:
    payload = []
    for r in rows:
        pn = (r.get("PolicyNumber") or "").strip()
        val = (r.get(col) or "").strip()
        if not pn or not val:
            continue
        payload.append({"pn": pn, "val": val})
    cypher = f"""
    UNWIND $rows AS row
    MATCH (c:KaggleClaim {{policy_number: row.pn}})
    MERGE (e:{label} {{value: row.val}})
    MERGE (c)-[:{rel}]->(e)
    """
    total = 0
    for i in range(0, len(payload), batch_size):
        chunk = payload[i:i + batch_size]
        sess.run(cypher, rows=chunk).consume()
        total += len(chunk)
    return total


def load(csv_path: str | Path = DEFAULT_CSV, *,
         limit: int | None = None, batch_size: int = BATCH_SIZE) -> dict[str, int]:
    """캐글 CSV → 격리 이종 그래프 적재. 노드/엣지 카운트 반환."""
    rows = _read_rows(Path(csv_path), limit=limit)
    ensure_constraints()
    counts: dict[str, int] = {}
    with db.session() as sess:
        counts["KaggleClaim"] = _load_claims(sess, rows, batch_size)
        for col, label, key, rel in SHARED_ENTITIES:
            counts[f"edge:{rel}"] = _load_shared_edges(
                sess, rows, col, label, key, rel, batch_size
            )
    return counts


def clear() -> int:
    """Kaggle* 노드/관계만 삭제(기존 한국형 그래프는 보존). 삭제 노드 수 반환."""
    labels = ["KaggleClaim"] + [lab for _, lab, _k, _r in SHARED_ENTITIES]
    where = " OR ".join(f"n:{lab}" for lab in labels)
    rows = db.run(
        f"MATCH (n) WHERE {where} "
        f"CALL {{ WITH n DETACH DELETE n }} IN TRANSACTIONS OF 5000 ROWS"
    )
    return len(rows)


# ------------------------------------------------------------------
# ② 공유 허브 분석 — 어떤 공유 엔티티가 사기와 상관되나
# ------------------------------------------------------------------
def node_edge_stats() -> dict[str, Any]:
    """Kaggle* 노드/엣지 규모 집계."""
    out: dict[str, Any] = {"nodes": {}, "edges": {}}
    out["nodes"]["KaggleClaim"] = db.run(
        "MATCH (n:KaggleClaim) RETURN count(*) AS n"
    )[0]["n"]
    for _, label, _k, rel in SHARED_ENTITIES:
        out["nodes"][label] = db.run(
            f"MATCH (n:{label}) RETURN count(*) AS n"
        )[0]["n"]
        out["edges"][rel] = db.run(
            f"MATCH ()-[r:{rel}]->() RETURN count(r) AS n"
        )[0]["n"]
    out["claim_fraud"] = db.run(
        "MATCH (c:KaggleClaim) WHERE c.fraud = 1 RETURN count(*) AS n"
    )[0]["n"]
    return out


def shared_hub_correlation() -> dict[str, list[dict[str, Any]]]:
    """공유 엔티티 노드별 (연결 청구수, 사기 청구수, 사기율) 집계.

    각 축에서 사기율이 전체(5.99%) 대비 두드러지게 높은 '공유 허브'를 찾는다.
    이는 "이 범주 값에 사기가 집중"되는지를 보여주지만(상관), **공모 증거가
    아님**(인위적 범주 공유)에 유의.
    """
    overall = db.run("MATCH (c:KaggleClaim) RETURN count(*) AS t, "
                     "sum(c.fraud) AS f")[0]
    base_rate = (overall["f"] / overall["t"]) if overall["t"] else 0.0

    result: dict[str, list[dict[str, Any]]] = {"_base_rate": base_rate}  # type: ignore
    for _, label, _k, rel in SHARED_ENTITIES:
        rows = db.run(
            f"""
            MATCH (e:{label})<-[:{rel}]-(c:KaggleClaim)
            WITH e, count(c) AS claims, sum(c.fraud) AS fraud
            RETURN e.value AS value, claims, fraud,
                   toFloat(fraud) / claims AS fraud_rate
            ORDER BY fraud_rate DESC, claims DESC
            """
        )
        result[label] = [
            {
                "value": r["value"],
                "claims": r["claims"],
                "fraud": r["fraud"],
                "fraud_rate": round(r["fraud_rate"], 4),
                "lift": round(r["fraud_rate"] / base_rate, 2) if base_rate else 0.0,
            }
            for r in rows
        ]
    return result


def top_fraud_hubs(min_claims: int = 30, top: int = 15) -> list[dict[str, Any]]:
    """모든 공유 축을 합쳐 사기율 높은 '공유 허브' 상위 N (최소 청구수 필터)."""
    corr = shared_hub_correlation()
    base = corr.get("_base_rate", 0.0)
    flat: list[dict[str, Any]] = []
    for _, label, _k, _rel in SHARED_ENTITIES:
        for d in corr.get(label, []):
            if d["claims"] >= min_claims:
                flat.append({"entity": label, **d})
    flat.sort(key=lambda d: (d["fraud_rate"], d["claims"]), reverse=True)
    return [{"base_rate": round(base, 4), **d} for d in flat[:top]]


# ------------------------------------------------------------------
# 출력
# ------------------------------------------------------------------
def _print_stats(stats: dict[str, Any]) -> None:
    line = "=" * 60
    print(line)
    print(" ② 캐글 격리 그래프 — 노드/엣지 규모")
    print(line)
    print("  [노드]")
    for k, v in stats["nodes"].items():
        print(f"    {k:<24}: {v:,}")
    print("  [엣지]")
    for k, v in stats["edges"].items():
        print(f"    {k:<24}: {v:,}")
    print("-" * 60)
    print(f"  사기 KaggleClaim : {stats['claim_fraud']:,}")
    print(line)


def _print_hubs(stats: dict[str, Any]) -> None:
    corr = shared_hub_correlation()
    base = corr.get("_base_rate", 0.0)
    print()
    print(" ② 공유 엔티티별 사기율 — 어떤 공유 축이 사기 신호인가")
    print(f" (전체 기준 사기율 base = {base*100:.2f}%, lift = 축사기율/base)")
    print("-" * 60)
    for col, label, _k, _rel in SHARED_ENTITIES:
        rows = corr.get(label, [])
        if not rows:
            continue
        # 청구수 30+ 만(노이즈 제거), 사기율 상위 3
        sig = [r for r in rows if r["claims"] >= 30][:3]
        if not sig:
            sig = rows[:3]
        print(f"  [{label}] (값별 사기율 상위)")
        for r in sig:
            print(f"    {str(r['value']):<14} 청구 {r['claims']:>5} "
                  f"사기 {r['fraud']:>4} 사기율 {r['fraud_rate']*100:5.1f}% "
                  f"(lift {r['lift']}x)")
    print("-" * 60)
    print(" 종합 '공유 허브' 상위(사기율, 청구 30+):")
    for h in top_fraud_hubs(min_claims=30, top=10):
        print(f"    {h['entity']:<22} {str(h['value']):<12} "
              f"사기율 {h['fraud_rate']*100:5.1f}% (lift {h['lift']}x, "
              f"청구 {h['claims']})")
    print("-" * 60)
    print(" [한계] 위 관계는 '같은 범주 값 공유'일 뿐 진짜 공모(같은 계좌·")
    print("  교차목격·동일 인물)가 아니다 — 인위적 군집이다. 따라서 이 '허브'는")
    print("  사기와의 약한 상관 신호일 뿐, 공모 네트워크 증거가 아니다.")
    print("=" * 60)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="② 캐글 청구 → 격리 이종 그래프 PoC")
    sub = p.add_subparsers(dest="cmd", required=True)
    lp = sub.add_parser("load", help="캐글 CSV 격리 적재")
    lp.add_argument("--csv", default=DEFAULT_CSV)
    lp.add_argument("--limit", type=int, default=None, help="상위 N행만(테스트)")
    sub.add_parser("stats", help="노드/엣지 규모 + 공유허브-사기 연관")
    sub.add_parser("clear", help="Kaggle* 노드만 삭제(기존 그래프 보존)")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if not db.healthcheck():
        print("Neo4j 미가용 — `make up && make wait-neo4j` 후 재실행")
        return 1
    if args.cmd == "load":
        counts = load(args.csv, limit=args.limit)
        print("=" * 60)
        print(" ② 캐글 격리 그래프 적재 완료")
        print("=" * 60)
        for k, v in counts.items():
            print(f"  {k:<24}: {v:,}")
        print("-" * 60)
        stats = node_edge_stats()
        _print_stats(stats)
        _print_hubs(stats)
        return 0
    if args.cmd == "stats":
        stats = node_edge_stats()
        _print_stats(stats)
        _print_hubs(stats)
        return 0
    if args.cmd == "clear":
        n = clear()
        print(f"Kaggle* 노드 삭제 완료(기존 한국형 그래프 보존). 배치 처리 {n}회.")
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
