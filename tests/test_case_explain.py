"""WP4 케이스 관리 + 설명가능성 수용기준(AC) 테스트 (FR-4.x, FR-5.x).

· 케이스 큐 생성·상태전이(유효/무효)·배정·이력 (FR-4.1)
· 케이스에 근거 경로·기여 신호 첨부 (FR-5.1)
· 환각 가드: 가짜 인용 거부 / 정상 소명문 통과 (FR-5.2 AC) — 차별화 핵심
· 조사관 판정 피드백 기록 (FR-4.3)

대부분 mock provider + SQLite 인메모리/임시파일이라 Neo4j 불필요 → smoke 마커.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from core.cases import (
    CaseNotFound,
    CaseStatus,
    CaseStore,
    InvalidTransition,
    is_valid_transition,
)
from detection import paths as path_builder
from explain import explainer
from explain.provider import MockProvider, get_provider

pytestmark = pytest.mark.smoke


# ---------------------------------------------------------------------------
# 픽스처
# ---------------------------------------------------------------------------
@pytest.fixture()
def store(tmp_path: Path) -> CaseStore:
    return CaseStore(db_path=tmp_path / "cases_test.db")


@pytest.fixture()
def sample_signals() -> list[dict]:
    """공유 계좌 + 교차 목격 + 핫스팟 기여 신호(scoring 출력 형태)."""
    return [
        {"type": "SHARED_ACCOUNT", "weight": 57.0, "shared_key": "0118569172667",
         "num_customers": 3, "shared_with": ["CUST-05015", "CUST-05013"]},
        {"type": "CROSS_WITNESS", "weight": 48.0, "cluster_size": 3,
         "witnessed_with": ["CUST-05015", "CUST-05013"]},
        {"type": "HOTSPOT_REPAIR_SHOP", "weight": 8.0, "entity_id": "RSH-0089",
         "entity_name": "명품카정비89", "num_customers": 104},
        {"type": "_alert_threshold", "value": 50.0},
    ]


# ===========================================================================
# 케이스 큐 / 상태전이 / 배정 / 이력 (FR-4.1)
# ===========================================================================
def test_create_and_queue_sorted_by_score(store: CaseStore) -> None:
    store.create_case(case_id="C1", customer_id="CUST-1", score=40.0)
    store.create_case(case_id="C2", customer_id="CUST-2", score=90.0)
    store.create_case(case_id="C3", customer_id="CUST-3", score=70.0)

    q = store.queue()
    assert [c.case_id for c in q] == ["C2", "C3", "C1"], "점수 내림차순 큐 정렬 실패"
    assert all(c.status == CaseStatus.UNASSIGNED for c in q)


def test_create_is_idempotent(store: CaseStore) -> None:
    a = store.create_case(case_id="C1", customer_id="CUST-1", score=40.0)
    b = store.create_case(case_id="C1", customer_id="CUST-1", score=99.0)
    assert a.case_id == b.case_id
    assert len(store.queue()) == 1


def test_assign_transitions_to_investigating_and_records_history(store: CaseStore) -> None:
    store.create_case(case_id="C1", customer_id="CUST-1", score=80.0)
    case = store.assign("C1", assignee="analyst-kim", actor="admin")
    assert case.assignee == "analyst-kim"
    assert case.status == CaseStatus.INVESTIGATING

    hist = store.history("C1")
    assert len(hist) == 1
    assert hist[0].from_status == CaseStatus.UNASSIGNED.value
    assert hist[0].to_status == CaseStatus.INVESTIGATING.value


def test_valid_transition_path(store: CaseStore) -> None:
    store.create_case(case_id="C1", customer_id="CUST-1", score=80.0)
    store.assign("C1", "analyst-kim")          # UNASSIGNED -> INVESTIGATING
    store.transition("C1", CaseStatus.HOLD, actor="analyst-kim", note="자료대기")
    c = store.transition("C1", CaseStatus.FRAUD, actor="analyst-kim", note="확정")
    assert c.status == CaseStatus.FRAUD

    hist = store.history("C1")
    transitions = [(h.from_status, h.to_status) for h in hist]
    assert ("UNASSIGNED", "INVESTIGATING") in transitions
    assert ("INVESTIGATING", "HOLD") in transitions
    assert ("HOLD", "FRAUD") in transitions


def test_invalid_transition_rejected(store: CaseStore) -> None:
    store.create_case(case_id="C1", customer_id="CUST-1", score=80.0)
    # UNASSIGNED -> FRAUD 는 허용되지 않음(조사중/보류를 거쳐야 함).
    with pytest.raises(InvalidTransition):
        store.transition("C1", CaseStatus.FRAUD, actor="x")
    # 상태가 변하지 않아야 함.
    assert store.get_case("C1").status == CaseStatus.UNASSIGNED


def test_transition_table_consistency() -> None:
    assert is_valid_transition(CaseStatus.UNASSIGNED, CaseStatus.INVESTIGATING)
    assert not is_valid_transition(CaseStatus.UNASSIGNED, CaseStatus.FRAUD)
    assert is_valid_transition(CaseStatus.INVESTIGATING, CaseStatus.FRAUD)
    assert is_valid_transition(CaseStatus.FRAUD, CaseStatus.INVESTIGATING)  # 재오픈
    assert not is_valid_transition(CaseStatus.NORMAL, CaseStatus.FRAUD)


def test_missing_case_raises(store: CaseStore) -> None:
    with pytest.raises(CaseNotFound):
        store.transition("NOPE", CaseStatus.INVESTIGATING)


def test_update_score_changes_score_and_audits(
    store: CaseStore, monkeypatch
) -> None:
    """update_score 는 점수·updated_at 만 갱신하고 상태/이력은 불변, 감사 기록."""
    import core.cases as cases_mod

    events: list[tuple] = []
    monkeypatch.setattr(
        cases_mod, "audit_event",
        lambda action, actor, **kw: events.append((action, actor, kw)),
    )

    created = store.create_case(case_id="C1", customer_id="CUST-1", score=60.0)
    updated = store.update_score("C1", 85.0, actor="rescore-bot")

    # 점수 갱신.
    assert updated.score == 85.0
    fresh = store.get_case("C1")
    assert fresh.score == 85.0
    # 상태 불변(미배정 유지), updated_at 변경.
    assert fresh.status == CaseStatus.UNASSIGNED
    assert fresh.updated_at >= created.created_at
    # 이력 미생성(점수만 변경이므로 상태변경 이력 없음).
    assert store.history("C1") == []
    # 감사 기록(case.rescore) 발생 + old/new 점수 메타.
    rescore_events = [e for e in events if e[0] == "case.rescore"]
    assert len(rescore_events) == 1
    assert rescore_events[0][1] == "rescore-bot"
    assert rescore_events[0][2]["meta"]["old_score"] == 60.0
    assert rescore_events[0][2]["meta"]["new_score"] == 85.0


def test_update_score_missing_case_raises(store: CaseStore) -> None:
    with pytest.raises(CaseNotFound):
        store.update_score("NOPE", 50.0)


# ===========================================================================
# 근거 경로·기여 신호 첨부 (FR-5.1)
# ===========================================================================
def test_paths_attached_per_signal(sample_signals: list[dict]) -> None:
    paths = path_builder.build_paths("CUST-05014", sample_signals)
    # _alert_threshold(내부 메타)는 경로로 변환되지 않음 → 3개 신호만.
    assert len(paths) == 3
    types = {p["signal_type"] for p in paths}
    assert types == {"SHARED_ACCOUNT", "CROSS_WITNESS", "HOTSPOT_REPAIR_SHOP"}

    shared = next(p for p in paths if p["signal_type"] == "SHARED_ACCOUNT")
    assert "0118569172667" in shared["entities"]
    assert "CUST-05014" in shared["entities"]
    assert "CUST-05015" in shared["entities"]
    # 노드/엣지 시퀀스가 구성되어야(시각화 입력).
    assert shared["nodes"] and shared["edges"]


def test_collect_entities_union(sample_signals: list[dict]) -> None:
    paths = path_builder.build_paths("CUST-05014", sample_signals)
    ents = path_builder.collect_entities(paths)
    assert {"CUST-05014", "CUST-05015", "CUST-05013",
            "0118569172667", "RSH-0089"} <= ents


# ===========================================================================
# 환각 가드 (FR-5.2 AC) — 차별화 핵심
# ===========================================================================
def test_grounding_accepts_real_explanation(sample_signals: list[dict]) -> None:
    paths = path_builder.build_paths("CUST-05014", sample_signals)
    good = ("고객 CUST-05014·CUST-05015·CUST-05013은 동일 계좌(011***67)를 "
            "공유하며 서로의 사고를 교차 목격했고, 정비소 RSH-0089를 이용했습니다.")
    res = explainer.verify_grounding(good, paths)
    assert res.grounded, f"정상 소명문이 거부됨. 환각={res.hallucinated}"
    assert res.hallucinated == []


def test_grounding_rejects_fake_customer(sample_signals: list[dict]) -> None:
    paths = path_builder.build_paths("CUST-05014", sample_signals)
    # CUST-99999 는 경로에 없는 가짜 고객.
    bad = "고객 CUST-05014와 CUST-99999가 공모했습니다."
    res = explainer.verify_grounding(bad, paths)
    assert not res.grounded, "가짜 고객 인용이 통과되면 안 됨"
    assert "CUST-99999" in res.hallucinated


def test_grounding_rejects_fake_entity(sample_signals: list[dict]) -> None:
    paths = path_builder.build_paths("CUST-05014", sample_signals)
    # RSH-7777 / 계좌 8888888888 모두 경로에 없음.
    bad = ("고객 CUST-05014은 정비소 RSH-7777과 계좌 8888888888을 공유합니다.")
    res = explainer.verify_grounding(bad, paths)
    assert not res.grounded
    assert "RSH-7777" in res.hallucinated
    assert "8888888888" in res.hallucinated


def test_grounding_rejects_fake_masked_account(sample_signals: list[dict]) -> None:
    paths = path_builder.build_paths("CUST-05014", sample_signals)
    # 실재 계좌 마스킹은 011***67. 999***99 는 존재하지 않는 마스킹.
    bad = "고객 CUST-05014은 계좌 999***99를 사용합니다."
    res = explainer.verify_grounding(bad, paths)
    assert not res.grounded
    assert "999***99" in res.hallucinated


def test_mock_provider_output_is_grounded(sample_signals: list[dict]) -> None:
    """MockProvider 가 생성한 소명문은 항상 환각 가드를 통과해야 한다(결정적)."""
    exp = explainer.explain_case("CUST-05014", sample_signals, provider=MockProvider())
    assert exp.text
    assert exp.accepted, f"Mock 소명문이 환각 가드에 걸림: {exp.grounding.hallucinated}"
    # 실제 엔티티가 인용되었는지(빈 서술이 아님).
    assert "CUST-05014" in exp.text


def test_mock_provider_is_deterministic(sample_signals: list[dict]) -> None:
    a = explainer.explain_case("CUST-05014", sample_signals, provider=MockProvider())
    b = explainer.explain_case("CUST-05014", sample_signals, provider=MockProvider())
    assert a.text == b.text


def test_get_provider_defaults_to_mock() -> None:
    # env 기본(THOTH_LLM_PROVIDER=mock) → MockProvider.
    prov = get_provider("mock")
    assert prov.name == "mock"
    # 알 수 없는 provider 도 mock 으로 fallback.
    assert get_provider("does-not-exist").name == "mock"


# ===========================================================================
# 판정 피드백 (FR-4.3)
# ===========================================================================
def test_record_verdict_and_status(store: CaseStore) -> None:
    store.create_case(case_id="C1", customer_id="CUST-1", score=80.0)
    store.assign("C1", "analyst-kim")  # -> INVESTIGATING
    v = store.record_verdict("C1", "fraud", actor="analyst-kim", comment="공모 확인")
    assert v.label == "FRAUD"

    # 판정이 케이스 상태를 FRAUD 로 전이.
    assert store.get_case("C1").status == CaseStatus.FRAUD
    verdicts = store.verdicts("C1")
    assert len(verdicts) == 1
    assert verdicts[0].comment == "공모 확인"


def test_verdict_invalid_label_rejected(store: CaseStore) -> None:
    store.create_case(case_id="C1", customer_id="CUST-1", score=80.0)
    with pytest.raises(ValueError):
        store.record_verdict("C1", "MAYBE", actor="x")
