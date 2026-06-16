"""GDS 군집·중심성 파이프라인 (WP3 · FR-3.4).

Neo4j GDS in-memory 그래프를 투영(projection)해 **군집 탐지(WCC·Louvain)**와
**중심성(Degree·PageRank)**을 실행하고, 그 결과를 노드 속성으로 write 한다.
ground truth(``ring_id``)는 알고리즘 입력에 쓰지 않으며(평가 전용), 알고리즘은
순수 그래프 구조만으로 사기 네트워크를 드러낸다.

[프로젝션 설계 근거 — ingest/synth_generator.py 링 주입 패턴]
    각 crash-for-cash 링 멤버는
        · 동일 Account(account_no) 로 보험금 지급(PAID_TO) — **링당 1개 공유 계좌**
        · 상호 WITNESSED_BY 교차 목격 — 링 내부 청구가 양방향 목격
        · 동일 Hospital/RepairShop 집중 이용(TREATED_AT/REPAIRED_AT)
    을 공유한다.

    군집 프로젝션(``thoth-fraud-graph``)은 **Customer·Claim·Account** 노드와
    ``FILED``·``PAID_TO``·``WITNESSED_BY`` 관계를 무방향으로 투영한다.
    공유 Account 가 링 멤버를 한 연결요소로 묶고, 정상 고객은 각자 고유 계좌를
    가져 분리된다(실측: 15개 링 전부 단일 WCC 컴포넌트). Hospital·RepairShop 은
    수백 건의 정상 청구가 함께 물려 컴포넌트를 과병합(전체가 1덩어리)시키므로
    **군집 프로젝션에서 제외**한다.

    중심성 프로젝션(``thoth-centrality-graph``)은 반대로 Hospital·RepairShop·
    Account 허브를 포함한다. 다수 고객이 공유하는 계좌·정비소·병원(핫스팟)이
    Degree·PageRank 상위에 랭크되도록 청구가 모이는 엔티티를 모두 투영한다.

write 결과 속성:
    Customer/Claim/Account.wcc_id            — WCC 연결요소 ID
    Customer/Claim/Account.louvain_community — Louvain 커뮤니티 ID
    (중심성) *.degree_score, *.pagerank_score — 중심성 점수

멱등성:
    프로젝션 이름은 고정 상수. 재실행 시 ``gds.graph.exists`` 확인 후 drop·재생성,
    write 도 동일 속성을 덮어쓴다.

CLI:
    python -m detection.gds_pipeline run        # 전체 파이프라인 실행 + 리포트
    python -m detection.gds_pipeline cohesion   # 링 커뮤니티 응집도만 출력
    python -m detection.gds_pipeline centrality # 중심성 상위 N 출력
    python -m detection.gds_pipeline drop        # 프로젝션 정리
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from typing import Any

from thoth import db

# ------------------------------------------------------------------
# 고정 프로젝션 이름 (멱등 관리)
# ------------------------------------------------------------------
COMMUNITY_GRAPH = "thoth-fraud-graph"        # WCC/Louvain 용 (링 응집 프로젝션)
CENTRALITY_GRAPH = "thoth-centrality-graph"  # Degree/PageRank 용 (허브 포함)

# 군집 프로젝션: 링 멤버를 묶는 binder(공유 계좌 + 교차 목격)만 포함.
_COMMUNITY_NODES = ["Customer", "Claim", "Account"]
_COMMUNITY_RELS = {
    "FILED": {"orientation": "UNDIRECTED"},
    "PAID_TO": {"orientation": "UNDIRECTED"},
    "WITNESSED_BY": {"orientation": "UNDIRECTED"},
}

# 중심성 프로젝션: 청구가 모이는 허브(계좌·병원·정비소)를 포함해 핫스팟 식별.
_CENTRALITY_NODES = ["Customer", "Claim", "Account", "Hospital", "RepairShop"]
_CENTRALITY_RELS = {
    "FILED": {"orientation": "UNDIRECTED"},
    "PAID_TO": {"orientation": "UNDIRECTED"},
    "TREATED_AT": {"orientation": "UNDIRECTED"},
    "REPAIRED_AT": {"orientation": "UNDIRECTED"},
    "WITNESSED_BY": {"orientation": "UNDIRECTED"},
}

DEFAULT_TOP_N = 20


# ==================================================================
# 프로젝션 멱등 관리
# ==================================================================
def graph_exists(name: str) -> bool:
    """GDS in-memory 그래프 존재 여부."""
    rows = db.run("CALL gds.graph.exists($name) YIELD exists RETURN exists", name=name)
    return bool(rows and rows[0]["exists"])


def drop_graph(name: str) -> None:
    """존재하면 GDS in-memory 그래프를 제거한다(멱등)."""
    if graph_exists(name):
        db.run("CALL gds.graph.drop($name) YIELD graphName RETURN graphName", name=name)


def project_community_graph() -> dict[str, Any]:
    """군집 탐지용 프로젝션을 (재)생성한다. 노드/관계 수 반환."""
    drop_graph(COMMUNITY_GRAPH)
    rows = db.run(
        "CALL gds.graph.project($name, $nodes, $rels) "
        "YIELD nodeCount, relationshipCount "
        "RETURN nodeCount, relationshipCount",
        name=COMMUNITY_GRAPH,
        nodes=_COMMUNITY_NODES,
        rels=_COMMUNITY_RELS,
    )
    return rows[0]


def project_centrality_graph() -> dict[str, Any]:
    """중심성용 프로젝션을 (재)생성한다. 노드/관계 수 반환."""
    drop_graph(CENTRALITY_GRAPH)
    rows = db.run(
        "CALL gds.graph.project($name, $nodes, $rels) "
        "YIELD nodeCount, relationshipCount "
        "RETURN nodeCount, relationshipCount",
        name=CENTRALITY_GRAPH,
        nodes=_CENTRALITY_NODES,
        rels=_CENTRALITY_RELS,
    )
    return rows[0]


# ==================================================================
# 군집 탐지: WCC + Louvain (write)
# ==================================================================
def run_wcc() -> dict[str, Any]:
    """WCC(연결요소)를 실행하고 ``wcc_id`` 속성으로 write 한다.

    Returns:
        ``componentCount``, ``nodePropertiesWritten`` 등 write 요약.
    """
    rows = db.run(
        "CALL gds.wcc.write($name, {writeProperty: 'wcc_id'}) "
        "YIELD componentCount, nodePropertiesWritten, computeMillis "
        "RETURN componentCount, nodePropertiesWritten, computeMillis",
        name=COMMUNITY_GRAPH,
    )
    return rows[0]


def run_louvain() -> dict[str, Any]:
    """Louvain(커뮤니티)을 실행하고 ``louvain_community`` 속성으로 write 한다.

    Returns:
        ``communityCount``, ``modularity``, ``nodePropertiesWritten`` 등 요약.
    """
    rows = db.run(
        "CALL gds.louvain.write($name, {writeProperty: 'louvain_community'}) "
        "YIELD communityCount, modularity, nodePropertiesWritten, computeMillis "
        "RETURN communityCount, modularity, nodePropertiesWritten, computeMillis",
        name=COMMUNITY_GRAPH,
    )
    return rows[0]


# ==================================================================
# 중심성: Degree + PageRank (write)
# ==================================================================
def run_degree() -> dict[str, Any]:
    """Degree 중심성을 실행하고 ``degree_score`` 속성으로 write 한다."""
    rows = db.run(
        "CALL gds.degree.write($name, {writeProperty: 'degree_score'}) "
        "YIELD centralityDistribution, nodePropertiesWritten, computeMillis "
        "RETURN nodePropertiesWritten, computeMillis",
        name=CENTRALITY_GRAPH,
    )
    return rows[0]


def run_pagerank() -> dict[str, Any]:
    """PageRank 중심성을 실행하고 ``pagerank_score`` 속성으로 write 한다."""
    rows = db.run(
        "CALL gds.pageRank.write($name, "
        "{writeProperty: 'pagerank_score', maxIterations: 50}) "
        "YIELD ranIterations, didConverge, nodePropertiesWritten, computeMillis "
        "RETURN ranIterations, didConverge, nodePropertiesWritten, computeMillis",
        name=CENTRALITY_GRAPH,
    )
    return rows[0]


# ==================================================================
# 평가: 링 커뮤니티 응집도 (ground truth 는 측정에만 사용)
# ==================================================================
@dataclass
class RingCohesion:
    """링 1개의 커뮤니티 응집도 측정 결과."""

    ring_id: str
    members: int
    wcc_components: int        # 멤버가 흩어진 WCC 컴포넌트 수 (이상적 1)
    louvain_communities: int   # 멤버가 흩어진 Louvain 커뮤니티 수 (이상적 1)
    wcc_majority_ratio: float       # 최대 WCC 컴포넌트가 차지하는 멤버 비율
    louvain_majority_ratio: float   # 최대 Louvain 커뮤니티가 차지하는 멤버 비율

    @property
    def wcc_single(self) -> bool:
        return self.wcc_components == 1

    @property
    def louvain_single(self) -> bool:
        return self.louvain_communities == 1


@dataclass
class CohesionReport:
    """전체 링 응집도 요약."""

    rings: list[RingCohesion] = field(default_factory=list)

    @property
    def total_rings(self) -> int:
        return len(self.rings)

    @property
    def wcc_single_rings(self) -> int:
        return sum(1 for r in self.rings if r.wcc_single)

    @property
    def louvain_single_rings(self) -> int:
        return sum(1 for r in self.rings if r.louvain_single)

    @property
    def avg_wcc_majority(self) -> float:
        if not self.rings:
            return 0.0
        return sum(r.wcc_majority_ratio for r in self.rings) / len(self.rings)

    @property
    def avg_louvain_majority(self) -> float:
        if not self.rings:
            return 0.0
        return sum(r.louvain_majority_ratio for r in self.rings) / len(self.rings)


_COHESION_QUERY = """
MATCH (c:Customer)
WHERE c.ring_id IS NOT NULL AND c.ring_id <> ''
WITH c.ring_id AS ring, c.wcc_id AS wcc, c.louvain_community AS lvn
WITH ring,
     count(*) AS members,
     collect(wcc) AS wccs,
     collect(lvn) AS lvns
// WCC 분포
WITH ring, members, wccs, lvns,
     [w IN apoc.coll.toSet(wccs) | size([x IN wccs WHERE x = w])] AS wcc_sizes,
     [l IN apoc.coll.toSet(lvns) | size([x IN lvns WHERE x = l])] AS lvn_sizes
RETURN ring AS ring_id,
       members,
       size(apoc.coll.toSet(wccs)) AS wcc_components,
       size(apoc.coll.toSet(lvns)) AS louvain_communities,
       reduce(m = 0, s IN wcc_sizes | CASE WHEN s > m THEN s ELSE m END) AS wcc_majority,
       reduce(m = 0, s IN lvn_sizes | CASE WHEN s > m THEN s ELSE m END) AS lvn_majority
ORDER BY ring_id
"""

# apoc 미설치 환경 대비 순수 cypher 폴백 (그룹핑으로 최빈 크기 계산).
_COHESION_QUERY_FALLBACK = """
MATCH (c:Customer)
WHERE c.ring_id IS NOT NULL AND c.ring_id <> ''
WITH c.ring_id AS ring, c.wcc_id AS wcc, count(*) AS cnt
WITH ring, count(*) AS wcc_components, max(cnt) AS wcc_majority, sum(cnt) AS members_w
WITH ring, wcc_components, wcc_majority, members_w
MATCH (c2:Customer)
WHERE c2.ring_id = ring
WITH ring, wcc_components, wcc_majority, members_w, c2.louvain_community AS lvn, count(*) AS lcnt
WITH ring, wcc_components, wcc_majority, members_w,
     count(*) AS louvain_communities, max(lcnt) AS lvn_majority
RETURN ring AS ring_id,
       members_w AS members,
       wcc_components,
       louvain_communities,
       wcc_majority,
       lvn_majority AS lvn_majority
ORDER BY ring_id
"""


def measure_ring_cohesion() -> CohesionReport:
    """주입 링별 WCC/Louvain 응집도를 측정한다(ground truth=ring_id, 평가 전용).

    각 링 멤버(Customer)가 몇 개의 WCC 컴포넌트·Louvain 커뮤니티에 흩어졌는지와,
    최대(다수) 군집이 차지하는 멤버 비율을 계산한다. 이상적으로 컴포넌트/커뮤니티
    수 = 1, 다수 비율 = 1.0.

    Returns:
        ``CohesionReport`` — 링별 ``RingCohesion`` 리스트.
    """
    has_apoc = _has_apoc()
    query = _COHESION_QUERY if has_apoc else _COHESION_QUERY_FALLBACK
    rows = db.run(query)
    report = CohesionReport()
    for r in rows:
        members = int(r["members"])
        wcc_maj = int(r["wcc_majority"])
        lvn_maj = int(r["lvn_majority"])
        report.rings.append(
            RingCohesion(
                ring_id=r["ring_id"],
                members=members,
                wcc_components=int(r["wcc_components"]),
                louvain_communities=int(r["louvain_communities"]),
                wcc_majority_ratio=(wcc_maj / members) if members else 0.0,
                louvain_majority_ratio=(lvn_maj / members) if members else 0.0,
            )
        )
    return report


def _has_apoc() -> bool:
    """apoc 플러그인 사용 가능 여부(응집도 쿼리 분기용)."""
    try:
        rows = db.run("RETURN apoc.version() AS v")
        return bool(rows and rows[0].get("v"))
    except Exception:
        return False


# ==================================================================
# 평가: 중심성 상위 N
# ==================================================================
_TOP_CENTRALITY_QUERY = """
MATCH (n)
WHERE n.pagerank_score IS NOT NULL
  AND any(l IN labels(n) WHERE l IN ['Account', 'Hospital', 'RepairShop'])
RETURN labels(n)[0] AS label,
       coalesce(n.account_no, n.name, n.hospital_id, n.shop_id) AS name,
       n.degree_score AS degree_score,
       n.pagerank_score AS pagerank_score
ORDER BY pagerank_score DESC
LIMIT $top_n
"""


def top_centrality(top_n: int = DEFAULT_TOP_N) -> list[dict[str, Any]]:
    """PageRank 상위 N개 엔티티(허브 후보: 계좌·병원·정비소)를 반환한다.

    Args:
        top_n: 반환할 상위 노드 수.

    Returns:
        ``label``, ``name``, ``degree_score``, ``pagerank_score`` dict 리스트.
    """
    return db.run(_TOP_CENTRALITY_QUERY, top_n=top_n)


_TOP_BY_TYPE_QUERY = """
MATCH (n)
WHERE n.degree_score IS NOT NULL AND $label IN labels(n)
RETURN coalesce(n.account_no, n.name, n.shop_id, n.hospital_id) AS name,
       n.degree_score AS degree_score,
       n.pagerank_score AS pagerank_score
ORDER BY degree_score DESC
LIMIT $top_n
"""


def top_centrality_by_type(label: str, top_n: int = 10) -> list[dict[str, Any]]:
    """특정 유형(Account/Hospital/RepairShop) 중심성 상위 N(Degree 순)."""
    return db.run(_TOP_BY_TYPE_QUERY, label=label, top_n=top_n)


def hotspot_accounts_in_top(top_n: int = DEFAULT_TOP_N) -> list[str]:
    """중심성 상위 N 에 포함된 핫스팟(다수 고객 공유) 계좌의 account_no 목록.

    링 공유 계좌(다수 고객이 동일 계좌로 지급받음)가 상위에 랭크되는지 확인용.
    """
    rows = db.run(
        """
        MATCH (c:Customer)-[:FILED]->(:Claim)-[:PAID_TO]->(a:Account)
        WITH a, count(DISTINCT c) AS nc
        WHERE nc >= 2
        RETURN a.account_no AS account_no
        """
    )
    shared = {r["account_no"] for r in rows}
    top = top_centrality(top_n)
    return [t["name"] for t in top if t["label"] == "Account" and t["name"] in shared]


# ==================================================================
# 전체 파이프라인
# ==================================================================
@dataclass
class PipelineResult:
    """파이프라인 1회 실행 요약."""

    community_projection: dict[str, Any]
    centrality_projection: dict[str, Any]
    wcc: dict[str, Any]
    louvain: dict[str, Any]
    degree: dict[str, Any]
    pagerank: dict[str, Any]


def run_pipeline() -> PipelineResult:
    """WP3 전체 파이프라인을 멱등 실행한다.

    프로젝션(군집·중심성) 재생성 → WCC·Louvain·Degree·PageRank 실행 및 write.

    Returns:
        ``PipelineResult`` — 각 단계 요약.
    """
    if not db.has_gds():
        raise RuntimeError("GDS 플러그인 미가용 — gds.version() 응답 없음")

    comm_proj = project_community_graph()
    cent_proj = project_centrality_graph()

    wcc = run_wcc()
    louvain = run_louvain()
    degree = run_degree()
    pagerank = run_pagerank()

    return PipelineResult(
        community_projection=comm_proj,
        centrality_projection=cent_proj,
        wcc=wcc,
        louvain=louvain,
        degree=degree,
        pagerank=pagerank,
    )


# ==================================================================
# 리포트 출력
# ==================================================================
def _print_pipeline_report(res: PipelineResult) -> None:
    line = "=" * 64
    print(line)
    print(" THOTH-ON WP3 GDS 파이프라인 (군집·중심성)")
    print(line)
    print(f"  [군집 프로젝션 '{COMMUNITY_GRAPH}']")
    print(f"    노드 {res.community_projection['nodeCount']:,} / "
          f"관계 {res.community_projection['relationshipCount']:,}")
    print(f"  [중심성 프로젝션 '{CENTRALITY_GRAPH}']")
    print(f"    노드 {res.centrality_projection['nodeCount']:,} / "
          f"관계 {res.centrality_projection['relationshipCount']:,}")
    print("-" * 64)
    print(f"  WCC      : 컴포넌트 {res.wcc['componentCount']:,}개, "
          f"write {res.wcc['nodePropertiesWritten']:,}")
    print(f"  Louvain  : 커뮤니티 {res.louvain['communityCount']:,}개, "
          f"modularity {res.louvain['modularity']:.4f}, "
          f"write {res.louvain['nodePropertiesWritten']:,}")
    print(f"  Degree   : write {res.degree['nodePropertiesWritten']:,}")
    print(f"  PageRank : 수렴 {res.pagerank['didConverge']} "
          f"({res.pagerank['ranIterations']} iters), "
          f"write {res.pagerank['nodePropertiesWritten']:,}")
    print(line)


def _print_cohesion_report(report: CohesionReport) -> None:
    line = "=" * 64
    print(line)
    print(" 링 커뮤니티 응집도 (ground truth=ring_id, 평가 전용)")
    print(line)
    print(f"  {'링':<10}{'멤버':>5}{'WCC수':>7}{'WCC다수%':>10}"
          f"{'Louvain수':>11}{'Louvain다수%':>13}")
    print("-" * 64)
    for r in report.rings:
        print(f"  {r.ring_id:<10}{r.members:>5}{r.wcc_components:>7}"
              f"{r.wcc_majority_ratio * 100:>9.0f}%"
              f"{r.louvain_communities:>11}"
              f"{r.louvain_majority_ratio * 100:>12.0f}%")
    print("-" * 64)
    print(f"  단일 WCC 컴포넌트로 묶인 링      : "
          f"{report.wcc_single_rings}/{report.total_rings}")
    print(f"  단일 Louvain 커뮤니티로 묶인 링  : "
          f"{report.louvain_single_rings}/{report.total_rings}")
    print(f"  평균 WCC 다수 비율               : {report.avg_wcc_majority * 100:.1f}%")
    print(f"  평균 Louvain 다수 비율           : {report.avg_louvain_majority * 100:.1f}%")
    print(line)


def _print_centrality_report(top_n: int = DEFAULT_TOP_N) -> None:
    line = "=" * 64
    print(line)
    print(f" 중심성 상위 {top_n} (PageRank 순) — 핫스팟 허브 식별")
    print(line)
    rows = top_centrality(top_n)
    print(f"  {'#':>3} {'유형':<12}{'식별자':<22}{'Degree':>10}{'PageRank':>12}")
    print("-" * 64)
    for i, r in enumerate(rows, 1):
        name = str(r["name"])[:20]
        print(f"  {i:>3} {r['label']:<12}{name:<22}"
              f"{r['degree_score']:>10.1f}{r['pagerank_score']:>12.4f}")
    print("-" * 64)
    print(line)
    # 유형별 허브 상위 — 병원이 절대 degree 를 독점하므로 계좌·정비소 핫스팟을
    # 별도로 보여 "다수 고객 공유 엔티티가 각 유형 상위"임을 드러낸다.
    for label in ("RepairShop", "Account"):
        print(f"  [{label} 중심성 상위 5 — Degree 순]")
        for r in top_centrality_by_type(label, 5):
            name = str(r["name"])[:20]
            print(f"    {name:<22}Degree={r['degree_score']:>7.1f} "
                  f"PageRank={r['pagerank_score']:>8.4f}")
    # 공유 계좌(2명 이상 고객)가 Account 중심성 상위에 모이는지.
    shared_top = _shared_accounts_rank()
    print("-" * 64)
    print(f"  Account Degree 상위 15 중 공유 계좌(핫스팟) : "
          f"{shared_top}/15")
    print(line)


def _shared_accounts_rank() -> int:
    """Account Degree 상위 15 중 '2명 이상 고객 공유' 계좌(핫스팟) 개수."""
    shared = {
        r["account_no"]
        for r in db.run(
            """
            MATCH (c:Customer)-[:FILED]->(:Claim)-[:PAID_TO]->(a:Account)
            WITH a, count(DISTINCT c) AS nc WHERE nc >= 2
            RETURN a.account_no AS account_no
            """
        )
    }
    top = db.run(
        """
        MATCH (a:Account) WHERE a.degree_score IS NOT NULL
        RETURN a.account_no AS account_no
        ORDER BY a.degree_score DESC LIMIT 15
        """
    )
    return sum(1 for r in top if r["account_no"] in shared)


# ==================================================================
# CLI
# ==================================================================
def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="THOTH-ON WP3 GDS 파이프라인")
    p.add_argument("command", nargs="?", default="run",
                   choices=["run", "cohesion", "centrality", "drop"],
                   help="실행 명령")
    p.add_argument("--top-n", type=int, default=DEFAULT_TOP_N,
                   help="중심성 상위 N")
    args = p.parse_args(argv)

    if not db.healthcheck():
        print("Neo4j 미가용 — `make up && make wait-neo4j` 후 재실행")
        return 1
    if not db.has_gds():
        print("GDS 플러그인 미가용 — gds.version() 응답 없음")
        return 1

    if args.command == "drop":
        drop_graph(COMMUNITY_GRAPH)
        drop_graph(CENTRALITY_GRAPH)
        print("GDS 프로젝션 정리 완료")
        return 0

    if args.command == "run":
        res = run_pipeline()
        _print_pipeline_report(res)
        _print_cohesion_report(measure_ring_cohesion())
        _print_centrality_report(args.top_n)
        return 0

    if args.command == "cohesion":
        _print_cohesion_report(measure_ring_cohesion())
        return 0

    if args.command == "centrality":
        _print_centrality_report(args.top_n)
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
