"""캐글 실데이터 적용 테스트 — ① 분포 분석 + 합성 prior 반영, ② 격리 그래프 PoC."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ingest import kaggle_analysis, kaggle_graph, synth_generator
from thoth import db

CSV = "data/kaggle/fraud_oracle.csv"
_HAS_CSV = Path(CSV).exists()


# ---------------------------------------------------------------------------
# ① 분포 분석 (Neo4j 불필요)
# ---------------------------------------------------------------------------
@pytest.mark.smoke
@pytest.mark.skipif(not _HAS_CSV, reason="캐글 CSV 부재")
def test_kaggle_analysis_overall_rate() -> None:
    dist = kaggle_analysis.analyze(CSV)
    assert dist["total_claims"] == 15420
    assert dist["fraud_claims"] == 923
    # 캐글 실 사기율 5.99%
    assert abs(dist["overall_fraud_rate"] - 0.0599) < 0.0005


@pytest.mark.smoke
@pytest.mark.skipif(not _HAS_CSV, reason="캐글 CSV 부재")
def test_kaggle_analysis_conditional_rates() -> None:
    dist = kaggle_analysis.analyze(CSV)
    cat = dist["categorical"]
    # 본인과실 > 제3자 (실데이터 7.9% vs 0.9%)
    assert cat["Fault"]["Policy Holder"]["fraud_rate"] > \
        cat["Fault"]["Third Party"]["fraud_rate"]
    # BasePolicy: All Perils > Collision > Liability
    bp = cat["BasePolicy"]
    assert bp["All Perils"]["fraud_rate"] > bp["Collision"]["fraud_rate"]
    assert bp["Collision"]["fraud_rate"] > bp["Liability"]["fraud_rate"]
    # VehicleCategory: Utility 최고, Sport 최저
    vc = cat["VehicleCategory"]
    assert vc["Utility"]["fraud_rate"] > vc["Sedan"]["fraud_rate"]
    assert vc["Sedan"]["fraud_rate"] > vc["Sport"]["fraud_rate"]


@pytest.mark.smoke
@pytest.mark.skipif(not _HAS_CSV, reason="캐글 CSV 부재")
def test_kaggle_analysis_writes_json(tmp_path: Path) -> None:
    dist = kaggle_analysis.analyze(CSV)
    out = kaggle_analysis.write_json(dist, tmp_path / "dist.json")
    loaded = json.loads(out.read_text(encoding="utf-8"))
    assert loaded["overall_fraud_rate"] == dist["overall_fraud_rate"]
    assert "VehicleCategory" in loaded["categorical"]


# ---------------------------------------------------------------------------
# ① 합성 generator 가 캐글 prior 를 반영 (Neo4j 불필요)
# ---------------------------------------------------------------------------
@pytest.mark.smoke
def test_synth_priors_load_with_fallback() -> None:
    # 존재하지 않는 경로 → BAKED fallback 사용(예외 없이 동작)
    priors = synth_generator._load_kaggle_priors("does/not/exist.json")
    assert abs(priors.overall_fraud_rate - 0.0599) < 0.001
    cats, weights = priors.categories("fault")
    assert "Policy Holder" in cats
    # 본인과실 조건부 사기율 > 제3자
    assert priors.fraud_rate("fault", "Policy Holder") > \
        priors.fraud_rate("fault", "Third Party")


@pytest.mark.smoke
def test_synth_generates_kaggle_attrs_and_6pct(tmp_path: Path) -> None:
    res = synth_generator.generate(
        out_dir=tmp_path, n_customers=800, n_claims=3000,
        n_rings=10, n_families=40, seed=42,
    )
    # 청구 단위 전체 사기율이 캐글(5.99%) 부근으로 현실화
    assert abs(res.claim_fraud_rate - 0.06) < 0.01
    assert res.n_opportunistic_claims > 0

    import csv as _csv
    rows = list(_csv.DictReader(
        (tmp_path / "claims.csv").open(encoding="utf-8")
    ))
    # 모든 청구에 캐글 속성 4축이 부여됨
    assert all(r["fault"] for r in rows)
    assert all(r["base_policy"] for r in rows)
    # 본인과실 사기율 > 제3자 사기율(실데이터 서열 보존)
    def _rate(axis: str, val: str) -> float:
        sub = [r for r in rows if r[axis] == val]
        if not sub:
            return 0.0
        return sum(1 for r in sub if r["fraud_label"] == "True") / len(sub)
    assert _rate("fault", "Policy Holder") > _rate("fault", "Third Party")


@pytest.mark.smoke
def test_synth_seed_reproducible(tmp_path: Path) -> None:
    a = synth_generator.generate(out_dir=tmp_path / "a", n_customers=500,
                                 n_claims=1500, n_rings=5, n_families=20, seed=7)
    b = synth_generator.generate(out_dir=tmp_path / "b", n_customers=500,
                                 n_claims=1500, n_rings=5, n_families=20, seed=7)
    assert a.claim_fraud_rate == b.claim_fraud_rate
    assert a.n_claim_fraud_total == b.n_claim_fraud_total
    assert (tmp_path / "a" / "claims.csv").read_text(encoding="utf-8") == \
        (tmp_path / "b" / "claims.csv").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# ② 격리 그래프 PoC (Neo4j 필요) — 적재/분석/격리/정리
# ---------------------------------------------------------------------------
@pytest.mark.integration
@pytest.mark.skipif(not _HAS_CSV, reason="캐글 CSV 부재")
def test_kaggle_graph_load_isolated_and_correlation(graph) -> None:
    """소량 적재 → 노드/엣지·공유허브 연관 측정·기존 한국형 그래프 격리 확인."""
    # 기존 한국형 그래프 규모(보존 검증 기준).
    kor_before = db.run(
        "MATCH (n) WHERE NOT any(l IN labels(n) WHERE l STARTS WITH 'Kaggle') "
        "RETURN count(n) AS c"
    )[0]["c"]

    # 테스트는 기존 Kaggle* 노드를 정리 후 소량 적재.
    kaggle_graph.clear()
    counts = kaggle_graph.load(CSV, limit=500)
    assert counts["KaggleClaim"] == 500

    stats = kaggle_graph.node_edge_stats()
    assert stats["nodes"]["KaggleClaim"] == 500
    # 7개 공유 엣지 타입 모두 적재
    assert len(stats["edges"]) == 7

    # 공유 허브-사기 연관: base_rate 와 축별 결과 존재
    corr = kaggle_graph.shared_hub_correlation()
    assert "_base_rate" in corr
    assert "KaggleBasePolicy" in corr

    # 격리 검증: Kaggle ↔ 한국형 교차 엣지 0, 한국형 노드 수 불변.
    cross = db.run(
        "MATCH (a)-[r]-(b) "
        "WHERE any(l IN labels(a) WHERE l STARTS WITH 'Kaggle') "
        "AND NOT any(l IN labels(b) WHERE l STARTS WITH 'Kaggle') "
        "RETURN count(r) AS c"
    )[0]["c"]
    assert cross == 0
    kor_after = db.run(
        "MATCH (n) WHERE NOT any(l IN labels(n) WHERE l STARTS WITH 'Kaggle') "
        "RETURN count(n) AS c"
    )[0]["c"]
    assert kor_after == kor_before

    # 정리(Kaggle* 만 삭제) 후에도 한국형 그래프 보존.
    kaggle_graph.clear()
    assert db.run("MATCH (n:KaggleClaim) RETURN count(n) AS c")[0]["c"] == 0
    kor_final = db.run(
        "MATCH (n) WHERE NOT any(l IN labels(n) WHERE l STARTS WITH 'Kaggle') "
        "RETURN count(n) AS c"
    )[0]["c"]
    assert kor_final == kor_before

    # 테스트 후 정식 Kaggle 그래프(전량)를 복원해 운영 상태를 유지한다.
    kaggle_graph.load(CSV)
