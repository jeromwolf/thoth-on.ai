"""재학습 모델 영속화·로드·추론 단위 테스트 (WP4-3 · FR-4.3).

sklearn/joblib 가 있으면 실행, 없으면 importorskip 으로 skip. **Neo4j 불필요** —
featmod.FeatureMatrix 를 손으로 구성해 fm 인자로 주입한다(라이브 그래프 불필요).

검증:
    · train_and_persist  — ModelMeta(n_samples/n_positive) + 파일·메타 생성.
    · load_model         — 라운드트립 + active_model_meta 일치.
    · predict_proba      — fm 주입 추론, 0~1 확률, 길이 일치.
    · clear_model        — 삭제 후 load None / predict_proba {}.
"""
from __future__ import annotations

import pytest

from detection import features as featmod


def _make_fm() -> tuple[featmod.FeatureMatrix, list[int]]:
    """손제작 FeatureMatrix + 라벨(Neo4j 불필요).

    양성 4·음성 4. 분리 가능하도록 'rule_score' 컬럼에 양성=강신호/음성=약신호를
    심어 분류기가 클래스를 구분할 수 있게 한다(누수 없음 — rule_score 는 라벨-free
    구조 신호 컬럼이며, 여기선 테스트 분리용 더미값).
    """
    names = list(featmod.FEATURE_NAMES)
    rs_idx = names.index("rule_score")
    customer_ids = [f"C{i}" for i in range(1, 9)]
    labels = [1, 1, 1, 1, 0, 0, 0, 0]

    rows: list[list[float]] = []
    for lbl in labels:
        vec = [0.0] * len(names)
        # 양성은 rule_score 높게, 음성은 낮게 — 단일 컬럼으로 선형 분리 가능.
        vec[rs_idx] = 90.0 if lbl == 1 else 5.0
        rows.append(vec)

    fm = featmod.FeatureMatrix(
        customer_ids=customer_ids, rows=rows, feature_names=names
    )
    return fm, labels


@pytest.mark.smoke
def test_train_and_persist_creates_files(tmp_path):
    """train_and_persist → ModelMeta(n_samples=8,n_positive=4) + 파일·메타 생성."""
    pytest.importorskip("sklearn")
    pytest.importorskip("joblib")
    from detection import model_store

    fm, labels = _make_fm()
    path = tmp_path / "model.joblib"

    meta = model_store.train_and_persist(
        labels=labels, model_kind="rf", fm=fm, path=path
    )

    assert meta.n_samples == 8
    assert meta.n_positive == 4
    assert meta.model_kind == "rf"
    assert meta.feature_names == list(featmod.FEATURE_NAMES)
    assert path.exists()
    assert (tmp_path / "model.joblib.meta.json").exists()
    assert meta.trained_at  # ISO 문자열 채워짐


@pytest.mark.smoke
def test_load_model_roundtrip(tmp_path):
    """load_model 라운드트립 not None + active_model_meta 메타 일치."""
    pytest.importorskip("sklearn")
    pytest.importorskip("joblib")
    from detection import model_store

    fm, labels = _make_fm()
    path = tmp_path / "model.joblib"
    saved = model_store.train_and_persist(
        labels=labels, model_kind="rf", fm=fm, path=path
    )

    loaded = model_store.load_model(path=path)
    assert loaded is not None
    estimator, meta = loaded
    assert hasattr(estimator, "predict_proba")
    assert meta.n_samples == saved.n_samples
    assert meta.feature_names == saved.feature_names

    active = model_store.active_model_meta(path=path)
    assert active is not None
    assert active.trained_at == saved.trained_at
    assert active.model_kind == "rf"


@pytest.mark.smoke
def test_predict_proba_returns_probabilities(tmp_path):
    """predict_proba(fm=fm) 모든 cid 에 0~1 확률, 길이 8."""
    pytest.importorskip("sklearn")
    pytest.importorskip("joblib")
    from detection import model_store

    fm, labels = _make_fm()
    path = tmp_path / "model.joblib"
    model_store.train_and_persist(labels=labels, model_kind="rf", fm=fm, path=path)

    probs = model_store.predict_proba(fm=fm, path=path)
    assert len(probs) == 8
    assert set(probs.keys()) == set(fm.customer_ids)
    for p in probs.values():
        assert 0.0 <= p <= 1.0

    # 양성(C1)이 음성(C8)보다 높은 확률 — 분리 학습 확인.
    assert probs["C1"] > probs["C8"]

    # customer_ids 필터 동작.
    filtered = model_store.predict_proba(
        customer_ids=["C1", "C2"], fm=fm, path=path
    )
    assert set(filtered.keys()) == {"C1", "C2"}


@pytest.mark.smoke
def test_clear_model_removes_and_returns_empty(tmp_path):
    """clear_model 후 load_model None, predict_proba {}."""
    pytest.importorskip("sklearn")
    pytest.importorskip("joblib")
    from detection import model_store

    fm, labels = _make_fm()
    path = tmp_path / "model.joblib"
    model_store.train_and_persist(labels=labels, model_kind="rf", fm=fm, path=path)

    model_store.clear_model(path=path)
    assert not path.exists()
    assert not (tmp_path / "model.joblib.meta.json").exists()
    assert model_store.load_model(path=path) is None
    assert model_store.predict_proba(fm=fm, path=path) == {}

    # 멱등성 — 없는 파일 clear 도 에러 없음.
    model_store.clear_model(path=path)
