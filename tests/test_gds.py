"""WP3 GDS 파이프라인 수용기준(AC) 테스트 (FR-3.4).

검증 대상:
    1. 프로젝션 멱등 생성(재실행 시 drop 후 재생성).
    2. WCC·Louvain·Degree·PageRank write 성공.
    3. **주입 링(ring_id 동일) 멤버가 단일 커뮤니티로 묶이는가** — ground truth 는
       측정에만 사용(알고리즘 입력 아님). 각 링이 단일 WCC/Louvain 군집을 이루는
       비율이 임계 이상이어야 한다.
    4. **중심성 상위에 핫스팟 허브(공유 정비소/병원·계좌)가 포함되는가**.
    5. GDS 신호 반영 후 detection.evaluate 재현율이 유지/향상되는가.

ground truth(ring_id)는 GDS 알고리즘 입력에 쓰지 않는다 — 프로젝션은 순수 구조
(Customer-Claim-Account + WITNESSED_BY)만 투영한다.

integration 마커 — 실제 Neo4j + GDS 적재 데이터 필요. 미가용 시 skip.
"""
from __future__ import annotations

import pytest

from detection import evaluate, gds_pipeline
from thoth import db

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def gds_available(neo4j_available) -> bool:
    """Neo4j/GDS 미가용 시 모듈 전체 skip."""
    if not neo4j_available:
        pytest.skip("Neo4j 미가용 — `make up && make wait-neo4j` 후 재실행")
    if not db.has_gds():
        pytest.skip("GDS 플러그인 미가용 — gds.version() 응답 없음")
    return True


@pytest.fixture(scope="module")
def pipeline_run(gds_available):
    """모듈 1회 파이프라인 실행(프로젝션+WCC+Louvain+Degree+PageRank write)."""
    return gds_pipeline.run_pipeline()


# ---------------------------------------------------------------------------
# 프로젝션 멱등 + write 성공
# ---------------------------------------------------------------------------
def test_projections_created_and_idempotent(pipeline_run) -> None:
    """프로젝션이 생성되고, 재실행 시 멱등(drop 후 재생성)이어야 한다."""
    res = pipeline_run
    assert res.community_projection["nodeCount"] > 0
    assert res.community_projection["relationshipCount"] > 0
    assert res.centrality_projection["nodeCount"] > 0

    # 멱등: 다시 한 번 프로젝션해도 예외 없이 동일 규모로 재생성.
    assert gds_pipeline.graph_exists(gds_pipeline.COMMUNITY_GRAPH)
    again = gds_pipeline.project_community_graph()
    assert again["nodeCount"] == res.community_projection["nodeCount"]
    assert again["relationshipCount"] == res.community_projection["relationshipCount"]


def test_wcc_louvain_centrality_written(pipeline_run) -> None:
    """WCC/Louvain/Degree/PageRank 가 노드 속성으로 write 되어야 한다."""
    res = pipeline_run
    assert res.wcc["nodePropertiesWritten"] > 0
    assert res.louvain["nodePropertiesWritten"] > 0
    assert res.degree["nodePropertiesWritten"] > 0
    assert res.pagerank["nodePropertiesWritten"] > 0

    # Customer 노드에 실제 속성이 기록됐는지 직접 확인.
    n = db.run(
        "MATCH (c:Customer) WHERE c.wcc_id IS NOT NULL "
        "AND c.louvain_community IS NOT NULL RETURN count(*) AS n"
    )[0]["n"]
    assert n > 0, "Customer 에 wcc_id/louvain_community 가 write 되지 않음"


# ---------------------------------------------------------------------------
# 링 응집도 — 주입 링이 단일 커뮤니티로 묶이는가 (FR-3.4 핵심 AC)
# ---------------------------------------------------------------------------
def test_rings_form_single_wcc_component(pipeline_run) -> None:
    """주입 링 멤버가 단일 WCC 연결요소로 묶여야 한다(대다수 >= 0.8 비율)."""
    report = gds_pipeline.measure_ring_cohesion()
    assert report.total_rings > 0, "측정된 링이 없습니다(ground truth 부재)"

    single_ratio = report.wcc_single_rings / report.total_rings
    assert single_ratio >= 0.8, (
        f"단일 WCC 컴포넌트 링 비율 미달: {single_ratio:.2f} "
        f"({report.wcc_single_rings}/{report.total_rings})"
    )
    # 평균 다수 비율(최대 컴포넌트가 차지하는 멤버 비율)도 높아야 함.
    assert report.avg_wcc_majority >= 0.9, (
        f"평균 WCC 다수 비율 미달: {report.avg_wcc_majority:.2f}"
    )


def test_rings_form_single_louvain_community(pipeline_run) -> None:
    """주입 링 멤버가 단일 Louvain 커뮤니티로 묶여야 한다(대다수 >= 0.8 비율)."""
    report = gds_pipeline.measure_ring_cohesion()
    assert report.total_rings > 0

    single_ratio = report.louvain_single_rings / report.total_rings
    assert single_ratio >= 0.8, (
        f"단일 Louvain 커뮤니티 링 비율 미달: {single_ratio:.2f} "
        f"({report.louvain_single_rings}/{report.total_rings})"
    )
    assert report.avg_louvain_majority >= 0.9, (
        f"평균 Louvain 다수 비율 미달: {report.avg_louvain_majority:.2f}"
    )


def test_ring_members_share_community_id(pipeline_run) -> None:
    """한 링의 멤버 다수가 동일한 louvain_community 값을 갖는지 직접 확인.

    ground truth(ring_id)는 결과 그룹핑에만 쓰며 알고리즘 입력에는 쓰지 않는다.
    """
    rows = db.run(
        """
        MATCH (c:Customer)
        WHERE c.ring_id IS NOT NULL AND c.ring_id <> ''
        WITH c.ring_id AS ring,
             c.louvain_community AS comm,
             count(*) AS cnt
        WITH ring, max(cnt) AS dominant, sum(cnt) AS members
        RETURN ring, dominant, members,
               toFloat(dominant) / members AS ratio
        ORDER BY ratio ASC
        """
    )
    assert rows, "링 멤버 커뮤니티 분포를 조회하지 못함"
    # 가장 분산된 링조차 멤버의 80% 이상이 한 커뮤니티에 모여야 한다.
    worst = rows[0]
    assert worst["ratio"] >= 0.8, (
        f"링 {worst['ring']} 커뮤니티 응집도 미달: {worst['ratio']:.2f} "
        f"({worst['dominant']}/{worst['members']})"
    )


# ---------------------------------------------------------------------------
# 중심성 상위에 핫스팟 허브 포함 (FR-3.4 AC: 허브 상위 랭크)
# ---------------------------------------------------------------------------
def test_centrality_top_contains_hub_entities(pipeline_run) -> None:
    """중심성 상위 N 에 허브 엔티티(병원/정비소/계좌)가 포함되어야 한다."""
    top = gds_pipeline.top_centrality(gds_pipeline.DEFAULT_TOP_N)
    assert top, "중심성 상위 결과가 비었습니다"

    labels = {t["label"] for t in top}
    assert labels & {"Hospital", "RepairShop", "Account"}, (
        f"상위 {gds_pipeline.DEFAULT_TOP_N} 에 허브 엔티티 유형이 없음: {labels}"
    )
    # 상위는 PageRank 내림차순이어야 한다.
    scores = [t["pagerank_score"] for t in top]
    assert scores == sorted(scores, reverse=True), "PageRank 내림차순 정렬 위반"


def test_hub_repair_shops_rank_high(pipeline_run) -> None:
    """다수 고객이 공유하는 정비소(핫스팟)가 정비소 중심성 상위에 랭크되어야 한다.

    정비소 degree 상위 = 가장 많은 청구가 모인 정비소 = 핫스팟. 청구가 집중된
    정비소가 실제로 높은 Degree 를 갖는지(핫스팟 = 허브) 확인한다.
    """
    rows = db.run(
        """
        MATCH (c:Customer)-[:FILED]->(:Claim)-[:REPAIRED_AT]->(s:RepairShop)
        WHERE s.degree_score IS NOT NULL
        WITH s, count(DISTINCT c) AS customers, s.degree_score AS deg
        RETURN s.shop_id AS shop_id, customers, deg
        ORDER BY deg DESC
        LIMIT 5
        """
    )
    assert rows, "정비소 중심성 결과가 비었습니다"
    # 중심성 상위 정비소는 많은 고객이 공유(핫스팟)해야 한다.
    for r in rows:
        assert r["customers"] >= 50, (
            f"중심성 상위 정비소 {r['shop_id']} 공유 고객 부족: {r['customers']}"
        )


# ---------------------------------------------------------------------------
# GDS 신호가 재현율을 깨지 않는가 (스코어링 반영 검증)
# ---------------------------------------------------------------------------
def test_gds_scoring_preserves_recall(pipeline_run) -> None:
    """GDS 신호 반영 후에도 재현율/정밀도가 유지(>= 0.8)되어야 한다."""
    base = evaluate.evaluate(use_gds=False)
    with_gds = evaluate.evaluate(use_gds=True)

    # GDS 반영이 재현율을 떨어뜨리면 안 됨.
    assert with_gds.recall >= base.recall, (
        f"GDS 반영 후 재현율 하락: {base.recall:.3f} -> {with_gds.recall:.3f}"
    )
    assert with_gds.recall >= 0.8, f"GDS 재현율 미달: {with_gds.recall:.3f}"
    # 정밀도(오탐 억제)도 유지되어야 함 — GDS 가 정상 고객을 잘못 올리면 안 됨.
    assert with_gds.precision >= base.precision - 1e-9, (
        f"GDS 반영 후 정밀도 하락: {base.precision:.3f} -> {with_gds.precision:.3f}"
    )


def test_gds_community_signal_only_on_ring_members(pipeline_run) -> None:
    """다수 멤버 Louvain 커뮤니티 신호는 링 멤버에만 붙어야 한다(오탐 방지).

    정상 고객은 단독(size 1) 커뮤니티로 분리되므로 GDS_COMMUNITY 신호를
    받지 않아야 한다. ground truth 는 검증에만 사용.
    """
    from detection import scoring

    risks = scoring.score_customers(use_gds=True)
    normal_with_community = [
        r for r in risks.values()
        if not r.is_fraud_ring
        and any(s["type"] == "GDS_COMMUNITY" for s in r.signals)
    ]
    assert not normal_with_community, (
        f"정상 고객 {len(normal_with_community)}명이 GDS_COMMUNITY 신호를 받음(오탐)"
    )
