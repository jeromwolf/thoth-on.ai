"""조사관 판정 피드백 재학습 단위 테스트 (WP4-3 · FR-4.3).

smoke 테스트는 Neo4j/sklearn 없이 순수 로직만 검증한다:
    · collect_verdict_labels — 최근 판정 채택 + 고객 매핑.
    · merge_labels — override/provenance 집계 정확성.
    · 멀티 케이스 동일 고객 — 가장 최근 ts 채택.

integration 테스트(retrain_with_feedback)는 Neo4j+sklearn 필요 → 미가용 시 skip.
"""
from __future__ import annotations

import pytest

from detection.feedback import (
    LabelProvenance,
    collect_verdict_labels,
    merge_labels,
)


@pytest.fixture()
def store(tmp_path, monkeypatch):
    """임시 SQLite 케이스 저장소. 감사로그도 tmp 로 격리.

    판정 ts 를 결정적으로 통제하기 위해 cases._now_iso 를 단조증가 카운터로 패치한다
    (실제 datetime 은 동일 호출 내에서 같은 ts 가 나올 수 있어 '최근 판정' 검증이
    불안정해진다).
    """
    monkeypatch.setenv("THOTH_AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    from core import cases as cases_mod

    counter = {"n": 0}

    def _fake_now() -> str:
        counter["n"] += 1
        # 사전식 정렬이 시간순과 일치하도록 zero-pad(ISO 유사).
        return f"2026-01-01T00:00:{counter['n']:02d}+00:00"

    monkeypatch.setattr(cases_mod, "_now_iso", _fake_now)
    return cases_mod.CaseStore(db_path=tmp_path / "cases.db")


@pytest.mark.smoke
def test_collect_verdict_labels_basic(store):
    """단일 케이스/단일 판정 — 고객→라벨 매핑(FRAUD=1/NORMAL=0)."""
    store.create_case(case_id="C-1", customer_id="CUST-1", score=90.0)
    store.create_case(case_id="C-2", customer_id="CUST-2", score=80.0)
    store.record_verdict("C-1", "FRAUD", actor="inv")
    store.record_verdict("C-2", "NORMAL", actor="inv")

    labels = collect_verdict_labels(store)
    assert labels == {"CUST-1": 1, "CUST-2": 0}


@pytest.mark.smoke
def test_collect_verdict_labels_excludes_unjudged(store):
    """판정 없는 케이스는 제외된다."""
    store.create_case(case_id="C-1", customer_id="CUST-1", score=90.0)
    store.create_case(case_id="C-2", customer_id="CUST-2", score=70.0)
    store.record_verdict("C-1", "FRAUD", actor="inv")

    labels = collect_verdict_labels(store)
    assert labels == {"CUST-1": 1}
    assert "CUST-2" not in labels


@pytest.mark.smoke
def test_collect_verdict_labels_latest_wins_same_case(store):
    """한 케이스에 재판정이 쌓이면 가장 최근(ts 최대) 판정을 채택.

    FRAUD 판정 후 재오픈(INVESTIGATING) → NORMAL 재판정 → 최종 NORMAL(0).
    """
    from core.cases import CaseStatus

    store.create_case(case_id="C-1", customer_id="CUST-1", score=90.0)
    store.record_verdict("C-1", "FRAUD", actor="inv")          # ts 작음
    store.transition("C-1", CaseStatus.INVESTIGATING, actor="inv")  # 재오픈
    store.record_verdict("C-1", "NORMAL", actor="inv")         # ts 큼(최근)

    labels = collect_verdict_labels(store)
    assert labels == {"CUST-1": 0}


@pytest.mark.smoke
def test_collect_verdict_labels_latest_wins_multi_case(store):
    """동일 고객이 두 케이스를 가지면 전체 판정 중 ts 최대를 채택.

    먼저 NORMAL(이른 ts), 나중에 FRAUD(늦은 ts) → 최종 FRAUD(1).
    """
    store.create_case(case_id="C-1", customer_id="CUST-1", score=90.0)
    store.create_case(case_id="C-2", customer_id="CUST-1", score=85.0)
    store.record_verdict("C-1", "NORMAL", actor="inv")   # 이른 ts
    store.record_verdict("C-2", "FRAUD", actor="inv")    # 늦은 ts(최근)

    labels = collect_verdict_labels(store)
    assert labels == {"CUST-1": 1}


@pytest.mark.smoke
def test_merge_labels_override_and_provenance():
    """override/유지 + provenance(n_feedback/n_overrides/n_agree/n_base) 정확성.

    구성(고객 5명):
        CUST-1 base=0, verdict=1  → override(뒤집힘)
        CUST-2 base=1, verdict=1  → agree(재확인)
        CUST-3 base=0, verdict=0  → agree(재확인)
        CUST-4 base=1, verdict=0  → override(뒤집힘)
        CUST-5 base=0, verdict 없음 → base 유지
    """
    customer_ids = ["CUST-1", "CUST-2", "CUST-3", "CUST-4", "CUST-5"]
    base_labels = [0, 1, 0, 1, 0]
    verdict_labels = {"CUST-1": 1, "CUST-2": 1, "CUST-3": 0, "CUST-4": 0}

    merged, prov = merge_labels(customer_ids, base_labels, verdict_labels)

    assert merged == [1, 1, 0, 0, 0]
    assert isinstance(prov, LabelProvenance)
    assert prov.n_total == 5
    assert prov.n_feedback == 4        # 판정으로 결정된 고객
    assert prov.n_overrides == 2       # CUST-1, CUST-4
    assert prov.n_agree == 2           # CUST-2, CUST-3
    assert prov.n_base == 1            # CUST-5
    # 출처 합 = 전체.
    assert prov.n_overrides + prov.n_agree + prov.n_base == prov.n_total


@pytest.mark.smoke
def test_merge_labels_no_verdicts_keeps_base():
    """판정이 전혀 없으면 base 그대로, 전부 n_base."""
    customer_ids = ["A", "B", "C"]
    base_labels = [0, 1, 0]
    merged, prov = merge_labels(customer_ids, base_labels, {})

    assert merged == base_labels
    assert prov.n_feedback == 0
    assert prov.n_overrides == 0
    assert prov.n_agree == 0
    assert prov.n_base == 3


@pytest.mark.smoke
def test_merge_labels_length_mismatch_raises():
    """customer_ids 와 base_labels 길이 불일치 시 ValueError."""
    with pytest.raises(ValueError):
        merge_labels(["A", "B"], [0], {})


@pytest.mark.smoke
def test_collect_then_merge_pipeline(store):
    """collect → merge 파이프라인 통합(여전히 외부 의존성 없음)."""
    store.create_case(case_id="C-1", customer_id="CUST-1", score=90.0)
    store.create_case(case_id="C-2", customer_id="CUST-2", score=80.0)
    store.record_verdict("C-1", "FRAUD", actor="inv")    # CUST-1 -> 1
    store.record_verdict("C-2", "NORMAL", actor="inv")   # CUST-2 -> 0

    verdict_labels = collect_verdict_labels(store)
    customer_ids = ["CUST-1", "CUST-2", "CUST-3"]
    base_labels = [0, 0, 1]  # CUST-1 뒤집힘, CUST-2 재확인, CUST-3 판정없음 유지

    merged, prov = merge_labels(customer_ids, base_labels, verdict_labels)
    assert merged == [1, 0, 1]
    assert prov.n_overrides == 1   # CUST-1
    assert prov.n_agree == 1       # CUST-2
    assert prov.n_base == 1        # CUST-3


# ==================================================================
# integration — 실제 Neo4j + sklearn 필요(가볍게 1개, 미가용 시 skip)
# ==================================================================
@pytest.mark.integration
def test_retrain_with_feedback_smoke(store):
    """retrain_with_feedback 가 RetrainResult 를 만든다(Neo4j+sklearn 필요)."""
    from thoth import db

    if not db.healthcheck():
        pytest.skip("Neo4j 미가용 — `make up && make wait-neo4j` 후 재실행")
    try:
        import sklearn  # noqa: F401
    except Exception:
        pytest.skip("scikit-learn 미설치")

    from detection.feedback import RetrainResult, retrain_with_feedback

    # 판정이 비어 양성 부족이면 ValueError(정상 경로)도 허용.
    try:
        res = retrain_with_feedback(model_kind="rf", n_folds=3, store=store)
    except ValueError as e:
        assert "재학습 불가" in str(e)
        return
    assert isinstance(res, RetrainResult)
    assert res.provenance.n_total > 0
