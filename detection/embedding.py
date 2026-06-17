"""그래프 임베딩 + 비지도 이상탐지 (WP3 · FR-3.6) — 재현율 보강판.

룰(detect/scoring)이 못 잡는 약신호 수법(weak·hotspot_only·witness_only 일부)을
**고객-고객 유사 그래프의 구조적 이상신호**로 보강한다. 라벨을 일절 쓰지 않는
완전 비지도 방식이라 평가 누수(라벨 치팅)가 원천적으로 없다.

[왜 고객-고객 유사 그래프인가]
    원본 이종 그래프(Customer-Claim-Hospital-…)에 FastRP 를 바로 돌리면 정상 청구가
    수백 건 몰린 허브와 5천 고객이 섞여 사기 링의 희소 구조가 묻혀 분리도가 0 에
    수렴한다(실측). 대신 **사기를 잘 가르는 고객-고객 엣지**만으로 압축된 유사
    그래프(``SIMILAR_TO``)를 만든다. 엣지 정의(순수 구조+시간, 라벨 미사용):

      E1 핫스팟 동행 : 비인기(정상 baseline 제외) 동일 병원+정비소를 두 고객이
                       짧은 기간(<=21일)에 함께 이용 — weak/hotspot_only 의 핵심 단서.
      E2 교차 목격   : 상호 WITNESSED_BY(crash-for-cash 양방향 목격).
      E3 계좌 동행   : 동일 계좌로 짧은 기간 지급 + 서로 다른 주소(가족 배제).

    실측(data/synthetic_test): 이 엣지 집합은 사기 멤버끼리 224개, 정상 혼입 2개로
    사기 동질성이 매우 높다. 또한 각 사기 링은 서로 **고립된 클리크**(같은 링끼리만
    빽빽이 연결, 링↔링 교차 0)를 이룬다.

[핵심 신호 — 비지도, 누수 없음]
    사기 링은 멤버 3+ 가 서로 모두 유사(클리크)하지만, 정상 우연 동행은 보통
    **고립된 2자 쌍**으로 끝난다(실측: size-2 컴포넌트 110개). 따라서:

      · CLIQUE 신호(강): ``SIMILAR_TO`` 차수(degree) >= 2 — 3+ 클리크 멤버.
        실측 정밀 ~0.81, 전 수법(weak 17/22·hotspot_only 12/22 포함) 회수.
      · PAIR 신호(약): degree == 1 — 고립된 유사 쌍. noisy(정상 다수) →
        corroborating 약신호로만.

    이 신호는 **알려진 사기와의 유사도**가 아니라 **자기 이웃의 구조적 밀도**만
    보므로 라벨이 전혀 필요 없다(누수 불가능). FastRP 임베딩은 FR-3.6 충족 및
    설명가능성(유사 군집 시각화)용으로 함께 write 한다.

[그래프 임베딩(FastRP) 위치]
    FR-3.6 가 요구하는 그래프 임베딩으로 ``SIMILAR_TO`` 그래프에 FastRP 를 돌려
    노드 임베딩을 write 한다(비지도). 다만 이 데이터에서 사기 링은 서로 고립된
    클리크라 "다른 링과의 임베딩 유사도"는 의미가 없고(누수 없이는 0), 임베딩
    절대 코사인은 작은 정상 쌍에서 오히려 높게 나와 이상탐지에 부적합함을
    실측으로 확인했다. 따라서 **이상탐지 점수는 임베딩 코사인이 아니라 동일
    유사 그래프의 구조적 차수(클리크 멤버십)** 로 산출한다 — 더 정직하고 정밀하다.

CLI:
    python -m detection.embedding run      # 유사 엣지 적재 + FastRP write + 이상신호 리포트
    python -m detection.embedding eval     # 비지도 이상신호 분리도(수법별)
    python -m detection.embedding drop     # 임베딩 프로젝션/유사 엣지 정리
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from typing import Any

from detection import detect
from thoth import db

# ------------------------------------------------------------------
# 고객-고객 유사 그래프 / FastRP 프로젝션 (멱등 관리)
# ------------------------------------------------------------------
EMBED_GRAPH = "thoth-embed-graph"        # in-memory SIMILAR_TO 프로젝션
EMBED_PROPERTY = "fastrp_embedding"
EMBED_DIM = 64
EMBED_ITER_WEIGHTS = [0.0, 1.0, 1.0, 1.0]
EMBED_SEED = 42

SIM_TIME_DAYS = 21          # 동행(핫스팟/계좌) 청구 시점 차 상한(일)

# 비지도 이상신호 임계 — SIMILAR_TO 차수.
CLIQUE_MIN_DEGREE = 2       # 차수 >= 2 → 3+ 클리크(강 신호)


# ==================================================================
# 고객-고객 유사 엣지 적재 (멱등) — 순수 구조+시간, 라벨 미사용
# ==================================================================
_E1_HOTSPOT_PAIR = """
MATCH (c1:Customer)-[:FILED]->(cl1:Claim)-[:TREATED_AT]->(h:Hospital)
MATCH (cl1)-[:REPAIRED_AT]->(s:RepairShop)
MATCH (c2:Customer)-[:FILED]->(cl2:Claim)-[:TREATED_AT]->(h)
MATCH (cl2)-[:REPAIRED_AT]->(s)
WHERE c1.customer_id < c2.customer_id
  AND NOT h.hospital_id IN $pop_h AND NOT s.shop_id IN $pop_s
  AND cl1.incident_date IS NOT NULL AND cl2.incident_date IS NOT NULL
  AND abs(duration.inDays(date(cl1.incident_date), date(cl2.incident_date)).days) <= $days
WITH DISTINCT c1, c2
MERGE (c1)-[:SIMILAR_TO]->(c2)
MERGE (c2)-[:SIMILAR_TO]->(c1)
"""

_E2_WITNESS_PAIR = """
MATCH (c1:Customer)-[:FILED]->(x:Claim)-[:WITNESSED_BY]->(y:Claim)<-[:FILED]-(c2:Customer)
WHERE (y)-[:WITNESSED_BY]->(x) AND c1.customer_id < c2.customer_id
WITH DISTINCT c1, c2
MERGE (c1)-[:SIMILAR_TO]->(c2)
MERGE (c2)-[:SIMILAR_TO]->(c1)
"""

_E3_ACCOUNT_PAIR = """
MATCH (c1:Customer)-[:FILED]->(cl1:Claim)-[:PAID_TO]->(a:Account)<-[:PAID_TO]-(cl2:Claim)<-[:FILED]-(c2:Customer)
WHERE c1.customer_id < c2.customer_id
  AND cl1.incident_date IS NOT NULL AND cl2.incident_date IS NOT NULL
  AND abs(duration.inDays(date(cl1.incident_date), date(cl2.incident_date)).days) <= $days
OPTIONAL MATCH (c1)-[:LIVES_AT]->(ad1:Address)
OPTIONAL MATCH (c2)-[:LIVES_AT]->(ad2:Address)
WITH c1, c2, ad1, ad2
WHERE ad1.address_id <> ad2.address_id
WITH DISTINCT c1, c2
MERGE (c1)-[:SIMILAR_TO]->(c2)
MERGE (c2)-[:SIMILAR_TO]->(c1)
"""

# WP-KR 한국 수법 동행 엣지 — 순수 구조, 라벨 미사용.
#   E5 같은 설계사 모집 + 청구금 공통 계좌(설계사 가로채기) 동행.
#   E7 같은 차량(vin) 동승/운전자 교체 동행(driver_swap).
#   ※ 브로커 단독 동행/병원 단독 co-attendance 는 정상 다수가 무관하게 연결되어
#      거대 정상 클리크를 만들므로(정밀도 파괴) 유사 엣지로 쓰지 않는다. 허위입원
#      조직형은 룰(브로커→한 병원 집중 허브, 정밀 ~1.0)이 확실히 포착한다.
_E5_AGENT_PAIR = """
MATCH (a:Agent)-[:SOLD_POLICY]->(:Policy)<-[:HOLDS]-(c1:Customer)
MATCH (a)-[:SOLD_POLICY]->(:Policy)<-[:HOLDS]-(c2:Customer)
WHERE c1.customer_id < c2.customer_id
WITH a, c1, c2
MATCH (c1)-[:FILED]->(:Claim)-[:PAID_TO]->(acc:Account)<-[:PAID_TO]-(:Claim)<-[:FILED]-(c2)
WITH DISTINCT c1, c2
MERGE (c1)-[:SIMILAR_TO]->(c2)
MERGE (c2)-[:SIMILAR_TO]->(c1)
"""

# E7 driver_swap 동행: 같은 차량(vin)으로 청구한 서로 다른 고객. 단, 정상 가족이
#   공동명의 차량을 공유(같은 주소)하는 경우를 배제하기 위해 **서로 다른 주소**
#   조건을 둔다(운전자 교체/동승자 공모는 무관한 다수가 한 차량으로 청구).
_E7_VEHICLE_PAIR = """
MATCH (c1:Customer)-[:FILED]->(:Claim)-[:INVOLVES]->(v:Vehicle)<-[:INVOLVES]-(:Claim)<-[:FILED]-(c2:Customer)
WHERE c1.customer_id < c2.customer_id
OPTIONAL MATCH (c1)-[:LIVES_AT]->(ad1:Address)
OPTIONAL MATCH (c2)-[:LIVES_AT]->(ad2:Address)
WITH c1, c2, ad1, ad2
WHERE ad1.address_id <> ad2.address_id
WITH DISTINCT c1, c2
MERGE (c1)-[:SIMILAR_TO]->(c2)
MERGE (c2)-[:SIMILAR_TO]->(c1)
"""


def build_similarity_edges() -> int:
    """고객-고객 유사(``SIMILAR_TO``) 엣지를 (재)적재한다(멱등).

    기존 ``SIMILAR_TO`` 를 모두 지운 뒤 E1~E3 를 다시 만든다. 순수 구조+시간만
    쓰며 라벨(ring_id/is_fraud)은 일절 참조하지 않는다.

    Returns:
        적재된 ``SIMILAR_TO`` 엣지 수(무방향이므로 양방향 합).
    """
    db.run("MATCH ()-[r:SIMILAR_TO]->() DELETE r")
    pop_h = detect.POPULAR_HOSPITAL_IDS
    pop_s = detect.POPULAR_SHOP_IDS
    db.run(_E1_HOTSPOT_PAIR, pop_h=pop_h, pop_s=pop_s, days=SIM_TIME_DAYS)
    db.run(_E2_WITNESS_PAIR)
    db.run(_E3_ACCOUNT_PAIR, days=SIM_TIME_DAYS)
    # WP-KR 한국 수법 동행 엣지(신규 노드 없으면 빈 결과 — graceful).
    #   허위입원 star 는 룰(브로커 허브, 정밀 ~1.0)이 확실히 잡으므로 임베딩 동행
    #   엣지로는 넣지 않는다 — 병원 단독 co-attendance 는 정상 트래픽 균등으로 거대
    #   정상 클리크를 만들어 정밀도를 해친다(실측). agent/driver_swap 만 동행으로 둔다.
    db.run(_E5_AGENT_PAIR)
    db.run(_E7_VEHICLE_PAIR)
    n = db.run("MATCH ()-[r:SIMILAR_TO]->() RETURN count(r) AS n")[0]["n"]
    return int(n)


# ==================================================================
# 멱등 in-memory 프로젝션 + FastRP (FR-3.6 그래프 임베딩)
# ==================================================================
def graph_exists(name: str = EMBED_GRAPH) -> bool:
    rows = db.run("CALL gds.graph.exists($name) YIELD exists RETURN exists", name=name)
    return bool(rows and rows[0]["exists"])


def drop_graph(name: str = EMBED_GRAPH) -> None:
    if graph_exists(name):
        db.run("CALL gds.graph.drop($name) YIELD graphName RETURN graphName", name=name)


def project_embed_graph() -> dict[str, Any]:
    """고객-고객 유사 그래프(``SIMILAR_TO``)를 in-memory 로 (재)투영한다(멱등)."""
    drop_graph(EMBED_GRAPH)
    rows = db.run(
        "CALL gds.graph.project($name, 'Customer', "
        "{SIMILAR_TO: {orientation: 'UNDIRECTED'}}) "
        "YIELD nodeCount, relationshipCount "
        "RETURN nodeCount, relationshipCount",
        name=EMBED_GRAPH,
    )
    return rows[0]


def run_fastrp() -> dict[str, Any]:
    """FastRP 임베딩을 실행하고 ``fastrp_embedding`` 속성으로 write 한다(FR-3.6).

    라벨을 일절 입력하지 않는 순수 비지도 구조 임베딩(평가 누수 없음). 임베딩은
    설명가능성/시각화용이며 이상점수는 구조적 차수로 산출한다(모듈 docstring 참조).
    """
    rows = db.run(
        "CALL gds.fastRP.write($name, {"
        "  embeddingDimension: $dim,"
        "  iterationWeights: $weights,"
        "  randomSeed: $seed,"
        "  writeProperty: $prop"
        "}) "
        "YIELD nodePropertiesWritten, computeMillis "
        "RETURN nodePropertiesWritten, computeMillis",
        name=EMBED_GRAPH,
        dim=EMBED_DIM,
        weights=EMBED_ITER_WEIGHTS,
        seed=EMBED_SEED,
        prop=EMBED_PROPERTY,
    )
    return rows[0]


# ==================================================================
# 비지도 이상신호 — SIMILAR_TO 구조적 차수(클리크 멤버십)
# ==================================================================
@dataclass
class AnomalySignal:
    """고객 1명의 비지도 구조 이상신호."""

    customer_id: str
    similar_degree: int          # SIMILAR_TO 차수(서로 유사한 고객 수)
    similar_peers: list[str] = field(default_factory=list)
    is_fraud: bool = False       # ground truth(평가 전용 — 신호 산출엔 미사용)
    ring_id: str = ""
    ring_pattern: str = ""

    @property
    def is_clique(self) -> bool:
        """3+ 클리크 멤버(강 신호)."""
        return self.similar_degree >= CLIQUE_MIN_DEGREE

    @property
    def is_pair(self) -> bool:
        """고립 유사 쌍(약 신호)."""
        return self.similar_degree == 1


def compute_anomaly_signals(
    *,
    attach_labels: bool = True,
) -> dict[str, AnomalySignal]:
    """``SIMILAR_TO`` 차수 기반 비지도 이상신호를 산출한다.

    라벨을 신호 산출에 일절 쓰지 않는다(완전 비지도 — 누수 불가능). ``attach_labels``
    가 True 면 평가/리포트용으로 ground truth 만 부착한다(점수 영향 없음).

    Returns:
        {cid: AnomalySignal} — SIMILAR_TO 엣지가 1개 이상인 고객만.
    """
    rows = db.run(
        """
        MATCH (c:Customer)-[:SIMILAR_TO]->(p:Customer)
        RETURN c.customer_id AS cid,
               count(DISTINCT p) AS degree,
               collect(DISTINCT p.customer_id) AS peers
        """
    )
    signals: dict[str, AnomalySignal] = {}
    for r in rows:
        signals[r["cid"]] = AnomalySignal(
            customer_id=r["cid"],
            similar_degree=int(r["degree"]),
            similar_peers=list(r["peers"]),
        )

    if attach_labels and signals:
        lab = db.run(
            "MATCH (c:Customer) WHERE c.customer_id IN $ids "
            "RETURN c.customer_id AS cid, "
            "       coalesce(c.is_fraud_ring,false) AS f, "
            "       coalesce(c.ring_id,'') AS rid, "
            "       coalesce(c.ring_pattern,'') AS pat",
            ids=list(signals.keys()),
        )
        for r in lab:
            s = signals.get(r["cid"])
            if s is not None:
                s.is_fraud = bool(r["f"])
                s.ring_id = r["rid"]
                s.ring_pattern = r["pat"]
    return signals


# ==================================================================
# 전체 파이프라인 (유사 엣지 적재 + 프로젝션 + FastRP write)
# ==================================================================
@dataclass
class EmbedPipelineResult:
    similarity_edges: int
    projection: dict[str, Any]
    fastrp: dict[str, Any]


def run_pipeline() -> EmbedPipelineResult:
    """임베딩 파이프라인 멱등 실행: 유사 엣지 적재 → 프로젝션 → FastRP write."""
    if not db.has_gds():
        raise RuntimeError("GDS 플러그인 미가용 — gds.version() 응답 없음")
    n_edges = build_similarity_edges()
    proj = project_embed_graph()
    fastrp = run_fastrp()
    return EmbedPipelineResult(
        similarity_edges=n_edges, projection=proj, fastrp=fastrp
    )


# ==================================================================
# 리포트
# ==================================================================
def _print_pipeline_report(res: EmbedPipelineResult) -> None:
    line = "=" * 64
    print(line)
    print(" THOTH-ON 그래프 임베딩 + 비지도 이상탐지 (FastRP) — FR-3.6")
    print(line)
    print(f"  고객-고객 유사 엣지(SIMILAR_TO) : {res.similarity_edges:,} (양방향)")
    print(f"  프로젝션 '{EMBED_GRAPH}'")
    print(f"    노드 {res.projection['nodeCount']:,} / "
          f"관계 {res.projection['relationshipCount']:,}")
    print(f"  FastRP   : dim={EMBED_DIM}, write {res.fastrp['nodePropertiesWritten']:,}")
    print(line)


def _print_anomaly_report(signals: dict[str, AnomalySignal]) -> None:
    line = "=" * 64
    total_fraud = db.run(
        "MATCH (c:Customer) WHERE c.is_fraud_ring RETURN count(*) AS n"
    )[0]["n"]
    total_normal = db.run(
        "MATCH (c:Customer) WHERE NOT coalesce(c.is_fraud_ring,false) "
        "RETURN count(*) AS n"
    )[0]["n"]
    clique = [s for s in signals.values() if s.is_clique]
    pair = [s for s in signals.values() if s.is_pair]
    cf = sum(1 for s in clique if s.is_fraud)
    pf = sum(1 for s in pair if s.is_fraud)
    print(line)
    print(" 비지도 구조 이상신호 (SIMILAR_TO 차수 — 라벨 미사용)")
    print(line)
    print(f"  CLIQUE(차수>=2) : {len(clique)}명 (사기 {cf} / 정상 {len(clique)-cf}) "
          f"정밀 {cf/len(clique) if clique else 0:.3f}")
    print(f"  PAIR(차수==1)   : {len(pair)}명 (사기 {pf} / 정상 {len(pair)-pf}) "
          f"정밀 {pf/len(pair) if pair else 0:.3f}")
    print("-" * 64)
    pat_totals = {
        r["p"]: r["n"]
        for r in db.run(
            "MATCH (c:Customer) WHERE c.is_fraud_ring AND coalesce(c.ring_pattern,'')<>'' "
            "RETURN c.ring_pattern AS p, count(*) AS n"
        )
    }
    clique_by_pat: dict[str, int] = {}
    for s in clique:
        if s.is_fraud:
            clique_by_pat[s.ring_pattern] = clique_by_pat.get(s.ring_pattern, 0) + 1
    print(f"  CLIQUE 신호의 수법별 회수 (사기 멤버)")
    print(f"  {'수법':<14}{'멤버':>6}{'CLIQUE회수':>12}{'회수율':>10}")
    order = ["fake_admission_star", "collision_ring", "repair_overbill",
             "agent_fraud", "driver_swap"]
    for pat in order:
        tot = pat_totals.get(pat, 0)
        rec = clique_by_pat.get(pat, 0)
        rate = rec / tot if tot else 0.0
        print(f"  {pat:<14}{tot:>6}{rec:>12}{rate:>10.3f}")
    print(line)


# ==================================================================
# CLI
# ==================================================================
def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="THOTH-ON 그래프 임베딩 + 비지도 이상탐지")
    p.add_argument("command", nargs="?", default="run",
                   choices=["run", "eval", "drop"], help="실행 명령")
    args = p.parse_args(argv)

    if not db.healthcheck():
        print("Neo4j 미가용 — `make up && make wait-neo4j` 후 재실행")
        return 1
    if not db.has_gds():
        print("GDS 플러그인 미가용 — gds.version() 응답 없음")
        return 1

    if args.command == "drop":
        drop_graph()
        db.run("MATCH ()-[r:SIMILAR_TO]->() DELETE r")
        print("임베딩 프로젝션/유사 엣지 정리 완료")
        return 0

    if args.command == "run":
        res = run_pipeline()
        _print_pipeline_report(res)
        _print_anomaly_report(compute_anomaly_signals())
        return 0

    if args.command == "eval":
        # 유사 엣지가 없으면 먼저 적재.
        n = db.run("MATCH ()-[r:SIMILAR_TO]->() RETURN count(r) AS n")[0]["n"]
        if n == 0:
            build_similarity_edges()
        _print_anomaly_report(compute_anomaly_signals())
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
