"""대량·성능 벤치마크 (상용화 검증 2단계 · 스케일/속도).

실제 보험사 규모(수천만 노드)를 향한 스케일·속도 검증을 위해, 합성 데이터를
단계적으로 키워 Neo4j 그래프의 성능 한계를 **실측**한다.

측정 항목(각 규모별):
    ① 배치 적재 시간(초) + 초/만건 환산 + 멱등 재적재 확인
    ② 탐지 쿼리 Q1~Q3 실행 시간(detection/detect.py)
    ③ 케이스 그래프 탐색(customer_subgraph) p50/p95/p99 — 다수 고객 샘플 반복
    ④ 전건 스코어링 시간(detection/scoring.score_customers)

NFR 목표: 케이스 그래프 탐색 p95 < 2초.

[메모리 한계 인지]
    docker-compose.yml 의 Neo4j heap=2G, pagecache=512M 가 상한. 대량 적재/쿼리
    시 OOM 또는 과도한 적재시간이 발생하면 그 규모를 마지막 측정점으로 보고하고
    멈춘다(비현실적 규모로 OOM 유발 금지).

CLI:
    # 데이터 생성(별도 디렉토리) — seed 고정
    python -m detection.benchmark gen --out data/synthetic_large/s50 \\
        --customers 50000 --claims 200000

    # 적재만(시간 측정) — 기존 그래프 reset 후 적재
    python -m detection.benchmark load data/synthetic_large/s50 --reset

    # 성능 측정(현재 적재된 그래프 대상)
    python -m detection.benchmark measure --samples 200 --label s50

    # 전체 파이프라인(생성→적재→측정) 한 규모
    python -m detection.benchmark run --out data/synthetic_large/s50 \\
        --customers 50000 --claims 200000 --samples 200 --label s50 --reset
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from thoth import db


# ==================================================================
# 측정 결과 모델
# ==================================================================
@dataclass
class GraphSize:
    """그래프 규모 스냅샷."""

    nodes: int = 0
    rels: int = 0
    by_label: dict[str, int] = field(default_factory=dict)


@dataclass
class LatencyStats:
    """반복 측정 지연 통계(밀리초)."""

    n: int = 0
    p50_ms: float = 0.0
    p95_ms: float = 0.0
    p99_ms: float = 0.0
    min_ms: float = 0.0
    max_ms: float = 0.0
    mean_ms: float = 0.0

    @classmethod
    def from_samples(cls, samples_ms: list[float]) -> "LatencyStats":
        if not samples_ms:
            return cls()
        s = sorted(samples_ms)
        return cls(
            n=len(s),
            p50_ms=round(_pct(s, 50), 2),
            p95_ms=round(_pct(s, 95), 2),
            p99_ms=round(_pct(s, 99), 2),
            min_ms=round(s[0], 2),
            max_ms=round(s[-1], 2),
            mean_ms=round(statistics.fmean(s), 2),
        )


def _pct(sorted_vals: list[float], pct: float) -> float:
    """정렬된 값에서 백분위(선형 보간) 계산."""
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    k = (len(sorted_vals) - 1) * (pct / 100.0)
    lo = int(k)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = k - lo
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * frac


# ==================================================================
# 그래프 규모 측정
# ==================================================================
def graph_size() -> GraphSize:
    """현재 그래프의 노드/엣지 수와 라벨별 분포를 조회."""
    n = db.run("MATCH (n) RETURN count(n) AS c")[0]["c"]
    r = db.run("MATCH ()-[x]->() RETURN count(x) AS c")[0]["c"]
    by_label: dict[str, int] = {}
    for row in db.run(
        "MATCH (n) RETURN labels(n)[0] AS label, count(*) AS c ORDER BY c DESC"
    ):
        by_label[row["label"] or "?"] = row["c"]
    return GraphSize(nodes=n, rels=r, by_label=by_label)


# ==================================================================
# 적재(시간 측정)
# ==================================================================
def measure_load(
    data_dir: str | Path,
    *,
    reset: bool = False,
    apply_schema: bool = True,
    batch_size: int = 2000,
) -> dict[str, Any]:
    """``data_dir`` 적재 시간을 측정. 옵션으로 reset/스키마 적용.

    Returns:
        ``{"reset", "schema_sec", "load_sec", "counts", "rows_loaded",
           "sec_per_10k"}``.
    """
    from ingest import loader

    out: dict[str, Any] = {"data_dir": str(data_dir)}

    if reset:
        t0 = time.perf_counter()
        db.reset_graph()
        out["reset_sec"] = round(time.perf_counter() - t0, 2)

    if apply_schema:
        t0 = time.perf_counter()
        schema = Path(__file__).resolve().parent.parent / "graph" / "01_schema.cypher"
        db.apply_file(schema)
        out["schema_sec"] = round(time.perf_counter() - t0, 2)

    t0 = time.perf_counter()
    counts = loader.load(data_dir, batch_size=batch_size)
    load_sec = time.perf_counter() - t0
    out["load_sec"] = round(load_sec, 2)
    out["counts"] = counts

    rows_loaded = sum(v for k, v in counts.items())
    nodes = sum(v for k, v in counts.items() if k.startswith("node:"))
    edges = sum(v for k, v in counts.items() if k.startswith("edge:"))
    out["nodes_loaded"] = nodes
    out["edges_loaded"] = edges
    out["rows_loaded"] = rows_loaded
    out["sec_per_10k_rows"] = round(load_sec / max(rows_loaded, 1) * 10000, 3)
    return out


def measure_idempotency(data_dir: str | Path, *, batch_size: int = 2000) -> dict[str, Any]:
    """멱등성 확인 — 재적재 후 노드/엣지 수 불변인지 검증."""
    from ingest import loader

    before = graph_size()
    t0 = time.perf_counter()
    loader.load(data_dir, batch_size=batch_size)
    reload_sec = time.perf_counter() - t0
    after = graph_size()
    return {
        "reload_sec": round(reload_sec, 2),
        "nodes_before": before.nodes,
        "nodes_after": after.nodes,
        "rels_before": before.rels,
        "rels_after": after.rels,
        "idempotent": before.nodes == after.nodes and before.rels == after.rels,
    }


# ==================================================================
# 타이밍 헬퍼
# ==================================================================
def _time_call(fn: Callable[[], Any]) -> tuple[float, Any]:
    """함수 1회 실행 경과시간(ms)과 반환값."""
    t0 = time.perf_counter()
    result = fn()
    return (time.perf_counter() - t0) * 1000.0, result


# ==================================================================
# 탐지 쿼리(Q1~Q3) 측정
# ==================================================================
def measure_detection_queries(*, repeat: int = 3) -> dict[str, Any]:
    """Q1(공유)·Q2(핫스팟)·Q3(crash-ring) 실행 시간 측정(최소값 사용).

    각 쿼리를 ``repeat`` 회 실행해 최소 시간(warm cache)을 보고한다.
    """
    from detection import detect

    def _bench(name: str, fn: Callable[[], list]) -> dict[str, Any]:
        times: list[float] = []
        n_rows = 0
        for _ in range(repeat):
            ms, rows = _time_call(fn)
            times.append(ms)
            n_rows = len(rows)
        return {
            "min_ms": round(min(times), 2),
            "max_ms": round(max(times), 2),
            "mean_ms": round(statistics.fmean(times), 2),
            "n_rows": n_rows,
        }

    return {
        "Q1_shared_entities": _bench("Q1", lambda: detect.run_shared_entities()),
        "Q2_hotspots": _bench("Q2", lambda: detect.run_hotspots()),
        "Q3_crash_rings": _bench("Q3", lambda: detect.run_crash_rings()),
    }


# ==================================================================
# 케이스 그래프 탐색(customer_subgraph) p50/p95/p99
# ==================================================================
def _sample_customer_ids(n: int, *, seed: int = 42) -> list[str]:
    """그래프에서 무작위 고객 ID ``n`` 개 샘플(존재하는 것 중)."""
    total = db.run("MATCH (c:Customer) RETURN count(c) AS c")[0]["c"]
    if total == 0:
        return []
    # 전부 가져오기보다 skip 무작위 추출 — total 이 커도 ID 만 일부.
    # 단순/재현 가능하게: 전체 ID 를 받아 seed 로 샘플(고객 수십만이어도 ID 만은 가볍다).
    rows = db.run("MATCH (c:Customer) RETURN c.customer_id AS cid")
    ids = [r["cid"] for r in rows]
    rng = random.Random(seed)
    if n >= len(ids):
        return ids
    return rng.sample(ids, n)


def measure_subgraph_latency(
    *, samples: int = 200, seed: int = 42, warmup: int = 5
) -> dict[str, Any]:
    """customer_subgraph(케이스 그래프 탐색) 지연을 다수 고객 샘플로 측정.

    NFR(p95 < 2초) 충족 여부를 함께 판정한다.
    """
    from api import service

    ids = _sample_customer_ids(samples, seed=seed)
    if not ids:
        return {"error": "no customers in graph"}

    # warmup — 캐시/플랜 예열(측정 제외)
    for cid in ids[:warmup]:
        service.customer_subgraph(cid)

    latencies: list[float] = []
    node_counts: list[int] = []
    edge_counts: list[int] = []
    for cid in ids:
        ms, result = _time_call(lambda c=cid: service.customer_subgraph(c))
        latencies.append(ms)
        node_counts.append(result.get("node_count", 0))
        edge_counts.append(result.get("edge_count", 0))

    stats = LatencyStats.from_samples(latencies)
    return {
        "samples": len(latencies),
        "latency": asdict(stats),
        "nfr_p95_under_2s": stats.p95_ms < 2000.0,
        "avg_node_count": round(statistics.fmean(node_counts), 1) if node_counts else 0,
        "max_node_count": max(node_counts) if node_counts else 0,
        "avg_edge_count": round(statistics.fmean(edge_counts), 1) if edge_counts else 0,
        "max_edge_count": max(edge_counts) if edge_counts else 0,
    }


# ==================================================================
# 전건 스코어링 시간
# ==================================================================
def measure_scoring(*, use_gds: bool = False) -> dict[str, Any]:
    """전 고객 리스크 스코어링(scoring.score_customers) 시간 측정."""
    from detection import scoring

    ms, risks = _time_call(lambda: scoring.score_customers(use_gds=use_gds))
    flagged = [r for r in risks.values() if r.score >= scoring.DEFAULT_ALERT_THRESHOLD]
    return {
        "scoring_sec": round(ms / 1000.0, 2),
        "scored_customers": len(risks),
        "flagged_customers": len(flagged),
        "use_gds": use_gds,
    }


# ==================================================================
# EXPLAIN/PROFILE — 풀스캔 점검
# ==================================================================
def measure_subgraph_plan(sample_cid: str | None = None) -> dict[str, Any]:
    """customer_subgraph 핵심 쿼리들의 실행계획을 점검(풀스캔 여부).

    인덱스 사용 여부(NodeIndexSeek vs AllNodesScan/NodeByLabelScan)를 보고한다.
    """
    if sample_cid is None:
        ids = _sample_customer_ids(1)
        sample_cid = ids[0] if ids else "CUST-00001"

    queries = {
        "center": (
            "MATCH (c:Customer {customer_id: $cid}) "
            "RETURN c.customer_id AS cid"
        ),
        "direct": (
            "MATCH (c:Customer {customer_id: $cid}) "
            "OPTIONAL MATCH (c)-[r]-(n) "
            "WHERE n:Account OR n:Phone OR n:Vehicle OR n:Address "
            "   OR n:Hospital OR n:RepairShop OR n:Claim "
            "RETURN count(*) AS c"
        ),
        "peers": (
            "MATCH (c:Customer {customer_id: $cid})-[r1]->(shared) "
            "WHERE shared:Account OR shared:Phone OR shared:Vehicle OR shared:Address "
            "MATCH (peer:Customer)-[r2]->(shared) "
            "WHERE peer.customer_id <> $cid "
            "RETURN count(*) AS c"
        ),
    }
    out: dict[str, Any] = {"sample_cid": sample_cid}
    for name, q in queries.items():
        rows = db.run(f"EXPLAIN {q}", cid=sample_cid)  # noqa: F841 — plan in summary
        # PROFILE 로 실제 db hits / 연산자 추출
        prof = _profile_operators(q, sample_cid)
        out[name] = prof
    return out


def _profile_operators(cypher: str, cid: str) -> dict[str, Any]:
    """PROFILE 실행해 연산자 트리에서 스캔 유형과 db hits 추출."""
    with db.session() as sess:
        result = sess.run(f"PROFILE {cypher}", cid=cid)
        summary = result.consume()  # 결과 소진 + summary 획득
        plan = summary.profile
    operators: list[str] = []
    total_db_hits = 0

    def _walk(p: dict) -> None:
        nonlocal total_db_hits
        if not p:
            return
        op = p.get("operatorType", "")
        operators.append(op)
        total_db_hits += int(p.get("dbHits", 0) or 0)
        for child in p.get("children", []) or []:
            _walk(child)

    _walk(plan or {})
    full_scans = [o for o in operators if "AllNodesScan" in o or "NodeByLabelScan" in o]
    index_seeks = [o for o in operators if "IndexSeek" in o or "NodeUniqueIndexSeek" in o
                   or "NodeIndexSeek" in o]
    return {
        "operators": operators,
        "total_db_hits": total_db_hits,
        "has_full_scan": bool(full_scans),
        "full_scan_ops": full_scans,
        "index_seek_ops": index_seeks,
    }


# ==================================================================
# 컨테이너 메모리 스냅샷(선택적)
# ==================================================================
def container_mem(container: str = "thoth-neo4j") -> dict[str, Any]:
    """docker stats 로 컨테이너 메모리 사용량 스냅샷(실패 시 빈 dict)."""
    import subprocess

    try:
        out = subprocess.run(
            ["docker", "stats", container, "--no-stream",
             "--format", "{{.MemUsage}}|{{.MemPerc}}"],
            capture_output=True, text=True, timeout=15,
        )
        if out.returncode == 0 and out.stdout.strip():
            usage, perc = out.stdout.strip().split("|")
            return {"mem_usage": usage.strip(), "mem_perc": perc.strip()}
    except Exception as exc:  # pragma: no cover
        return {"error": str(exc)}
    return {}


# ==================================================================
# 측정 오케스트레이션
# ==================================================================
def measure_all(
    *, label: str, samples: int = 200, seed: int = 42, use_gds: bool = False
) -> dict[str, Any]:
    """현재 적재된 그래프에 대해 전 항목 측정."""
    print(f"[measure:{label}] 그래프 규모 조회...", flush=True)
    size = graph_size()
    print(f"  nodes={size.nodes:,} rels={size.rels:,}", flush=True)

    print(f"[measure:{label}] 실행계획(풀스캔) 점검...", flush=True)
    plan = measure_subgraph_plan()

    print(f"[measure:{label}] 탐지 쿼리 Q1~Q3...", flush=True)
    queries = measure_detection_queries()
    for k, v in queries.items():
        print(f"  {k}: min={v['min_ms']}ms rows={v['n_rows']}", flush=True)

    print(f"[measure:{label}] 그래프 탐색 p50/p95/p99 (samples={samples})...", flush=True)
    subgraph = measure_subgraph_latency(samples=samples, seed=seed)
    lat = subgraph.get("latency", {})
    print(f"  p50={lat.get('p50_ms')}ms p95={lat.get('p95_ms')}ms "
          f"p99={lat.get('p99_ms')}ms NFR(p95<2s)={subgraph.get('nfr_p95_under_2s')}",
          flush=True)

    print(f"[measure:{label}] 전건 스코어링...", flush=True)
    scoring_r = measure_scoring(use_gds=use_gds)
    print(f"  scoring={scoring_r['scoring_sec']}s flagged={scoring_r['flagged_customers']}",
          flush=True)

    mem = container_mem()

    return {
        "label": label,
        "graph_size": asdict(size),
        "subgraph_plan": plan,
        "detection_queries": queries,
        "subgraph_latency": subgraph,
        "scoring": scoring_r,
        "container_mem": mem,
    }


# ==================================================================
# 데이터 생성
# ==================================================================
def gen_data(
    *, out: str, customers: int, claims: int, rings: int, families: int, seed: int
) -> dict[str, Any]:
    """합성 데이터 생성(별도 디렉토리). 규모 인자 전달."""
    from ingest import synth_generator as sg

    t0 = time.perf_counter()
    res = sg.generate(
        out_dir=out, n_customers=customers, n_claims=claims,
        n_rings=rings, n_families=families, seed=seed,
    )
    gen_sec = time.perf_counter() - t0
    return {
        "out_dir": str(res.out_dir),
        "gen_sec": round(gen_sec, 2),
        "n_customers": res.n_customers,
        "n_claims": res.n_claims,
        "n_rings": res.n_rings,
        "n_fraud_customers": res.n_fraud_customers,
    }


# ==================================================================
# CLI
# ==================================================================
def _save_report(report: dict[str, Any], path: str | Path) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[report] 저장: {p}", flush=True)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="THOTH-ON 대량·성능 벤치마크")
    sub = p.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("gen", help="합성 데이터 생성(별도 디렉토리)")
    g.add_argument("--out", required=True)
    g.add_argument("--customers", type=int, default=50000)
    g.add_argument("--claims", type=int, default=200000)
    g.add_argument("--rings", type=int, default=30)
    g.add_argument("--families", type=int, default=250)
    g.add_argument("--seed", type=int, default=42)

    ld = sub.add_parser("load", help="적재(시간 측정)")
    ld.add_argument("data_dir")
    ld.add_argument("--reset", action="store_true")
    ld.add_argument("--no-schema", action="store_true")
    ld.add_argument("--batch-size", type=int, default=2000)
    ld.add_argument("--check-idempotent", action="store_true")
    ld.add_argument("--report")

    m = sub.add_parser("measure", help="성능 측정(현재 그래프)")
    m.add_argument("--label", default="current")
    m.add_argument("--samples", type=int, default=200)
    m.add_argument("--seed", type=int, default=42)
    m.add_argument("--gds", action="store_true")
    m.add_argument("--report")

    r = sub.add_parser("run", help="생성→적재→측정 (한 규모)")
    r.add_argument("--out", required=True)
    r.add_argument("--customers", type=int, default=50000)
    r.add_argument("--claims", type=int, default=200000)
    r.add_argument("--rings", type=int, default=30)
    r.add_argument("--families", type=int, default=250)
    r.add_argument("--seed", type=int, default=42)
    r.add_argument("--samples", type=int, default=200)
    r.add_argument("--label", default="run")
    r.add_argument("--reset", action="store_true")
    r.add_argument("--batch-size", type=int, default=2000)
    r.add_argument("--report")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    if args.cmd == "gen":
        res = gen_data(out=args.out, customers=args.customers, claims=args.claims,
                       rings=args.rings, families=args.families, seed=args.seed)
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0

    if args.cmd == "load":
        res = measure_load(args.data_dir, reset=args.reset,
                           apply_schema=not args.no_schema, batch_size=args.batch_size)
        if args.check_idempotent:
            res["idempotency"] = measure_idempotency(args.data_dir,
                                                     batch_size=args.batch_size)
        res["container_mem"] = container_mem()
        print(json.dumps(res, ensure_ascii=False, indent=2))
        if args.report:
            _save_report(res, args.report)
        return 0

    if args.cmd == "measure":
        res = measure_all(label=args.label, samples=args.samples,
                          seed=args.seed, use_gds=args.gds)
        print(json.dumps(res, ensure_ascii=False, indent=2))
        if args.report:
            _save_report(res, args.report)
        return 0

    if args.cmd == "run":
        report: dict[str, Any] = {"label": args.label}
        print(f"=== [{args.label}] 1) 데이터 생성 ===", flush=True)
        report["gen"] = gen_data(out=args.out, customers=args.customers,
                                 claims=args.claims, rings=args.rings,
                                 families=args.families, seed=args.seed)
        print(json.dumps(report["gen"], ensure_ascii=False, indent=2), flush=True)

        print(f"=== [{args.label}] 2) 적재 ===", flush=True)
        report["load"] = measure_load(args.out, reset=args.reset,
                                      batch_size=args.batch_size)
        print(f"  load_sec={report['load']['load_sec']} "
              f"sec/10k={report['load']['sec_per_10k_rows']}", flush=True)

        print(f"=== [{args.label}] 3) 측정 ===", flush=True)
        report["measure"] = measure_all(label=args.label, samples=args.samples,
                                        seed=args.seed)
        if args.report:
            _save_report(report, args.report)
        else:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
