"""재학습 모델 영속화·로드·추론 (WP4-3 · FR-4.3) — 피드백 루프 닫기.

조사관 판정 피드백으로 재학습한 모델을 디스크에 **영속화**하고, 라이브
스코어링(detection.scoring)에서 **로드해 추론**하게 하는 서빙 레이어다. 이로써
"판정 → 재학습 → 배포 → 라이브 스코어 반영"의 피드백 루프를 끝까지 닫는다.

[평가 누수 절대 금지 — 최우선 제약]
    1) 학습은 라벨(y)로 하되, **피처(X)는 detection.features(라벨-free)만** 쓴다.
       라벨이 피처 행렬에 섞이지 않도록 구조적으로 분리한다(features 가 차단).
    2) ``train_and_persist`` 가 받는 ``labels`` 는 학습 타깃일 뿐이며, ``fm.rows``
       (피처)는 라벨을 일절 참조하지 않는다.

[서빙 vs 일반화 성능 — 혼동 금지]
    ``predict_proba`` 는 **배포 모델이 자기 학습 모집단을 스코어링**하는 서빙 경로라
    본질상 in-sample 이다(라이브 운영 점수 산출용). 정직한 일반화 성능은
    detection.ml_model 의 out-of-fold 교차검증으로 **별도** 측정한다. 둘을 같은
    척도로 비교하지 말 것.

[컬럼 정렬 — 학습/추론 피처 순서 불일치 방어]
    추론 시 ``fm.feature_names`` 와 학습 시 저장한 ``feature_names`` 의 순서/구성이
    다를 수 있으므로, **이름 기준으로 학습 순서대로 열을 재배열**한다(없는 컬럼은
    0.0). 이름 매칭이 누락되면 0 으로 채워 안전하게 추론한다.
"""
from __future__ import annotations

import dataclasses
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import numpy as np

from detection import features as featmod
from detection import ml_model

# joblib — sklearn 과 함께 설치돼 있을 것. 미설치 환경에서도 모듈 임포트는 가능하게
# 지연 임포트 가드(ml_model._SKLEARN 패턴 모사).
try:
    import joblib

    _JOBLIB = True
except Exception:  # pragma: no cover - 미설치 가드
    _JOBLIB = False


# 모델 파일 기본 경로 — 환경변수로 재정의 가능(테스트·배포 격리).
DEFAULT_MODEL_PATH = Path(
    os.getenv("THOTH_MODEL_PATH", "data/models/feedback_model.joblib")
)


def _meta_path(path: Path) -> Path:
    """모델 경로의 메타 사이드카 경로(.meta.json)를 만든다."""
    return Path(str(path) + ".meta.json")


@dataclass
class ModelMeta:
    """영속화 모델의 메타데이터(사이드카 .meta.json 에 저장).

    Attributes:
        trained_at: 학습 시각(ISO8601 UTC 문자열).
        model_kind: 모델 종류('lr'/'rf'/'gb').
        n_samples: 학습 표본 수.
        n_positive: 양성(사기=1) 표본 수.
        feature_names: 학습에 사용한 피처 컬럼 순서.
        provenance: 라벨 출처 분해(feedback.LabelProvenance dict, 없으면 {}).
        path: 모델 파일 경로(문자열).
    """

    trained_at: str
    model_kind: str
    n_samples: int
    n_positive: int
    feature_names: list[str]
    provenance: dict[str, Any]
    path: str


def train_and_persist(
    *,
    labels: list[int],
    model_kind: str = "rf",
    fm: Optional[featmod.FeatureMatrix] = None,
    provenance: Optional[dict[str, Any]] = None,
    path: Optional[Path] = None,
) -> ModelMeta:
    """라벨(y)로 분류기를 적합해 디스크에 영속화하고 메타를 반환한다.

    학습은 라벨로 하되 피처(X)는 ``fm.rows``(라벨-free)만 쓴다(누수 차단). 적합
    방식은 detection.ml_model 의 ``_make_classifier``/``_fit_with_balance`` 를 그대로
    재사용해 불균형 처리를 일관되게 유지한다.

    Args:
        labels: ``fm.customer_ids`` 와 같은 순서/길이의 0/1 라벨(학습 타깃).
        model_kind: 모델 종류('lr'/'rf'/'gb').
        fm: 미리 만든 FeatureMatrix(재사용). None 이면 ``build_features()``.
        provenance: 라벨 출처 dict(feedback.LabelProvenance asdict). None 이면 {}.
        path: 모델 저장 경로. None 이면 ``DEFAULT_MODEL_PATH``.

    Returns:
        ``ModelMeta`` — 저장된 모델 메타데이터.

    Raises:
        RuntimeError: scikit-learn 또는 joblib 미설치.
    """
    if not ml_model._SKLEARN:
        raise RuntimeError(
            "scikit-learn 미설치 — `.venv/bin/pip install scikit-learn`"
        )
    if not _JOBLIB:
        raise RuntimeError("joblib 미설치 — `.venv/bin/pip install joblib`")

    if fm is None:
        fm = featmod.build_features()

    X = np.asarray(fm.rows, dtype=float)
    y = np.asarray(labels, dtype=int)

    clf = ml_model._make_classifier(model_kind)
    ml_model._fit_with_balance(clf, model_kind, X, y)

    if path is None:
        path = DEFAULT_MODEL_PATH
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    joblib.dump(
        {
            "estimator": clf,
            "feature_names": list(fm.feature_names),
            "model_kind": model_kind,
        },
        path,
    )

    meta = ModelMeta(
        trained_at=datetime.now(timezone.utc).isoformat(),
        model_kind=model_kind,
        n_samples=int(len(y)),
        n_positive=int(y.sum()),
        feature_names=list(fm.feature_names),
        provenance=dict(provenance) if provenance else {},
        path=str(path),
    )
    _meta_path(path).write_text(
        json.dumps(dataclasses.asdict(meta), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return meta


def load_model(path: Optional[Path] = None) -> Optional[tuple[Any, ModelMeta]]:
    """영속화 모델과 메타를 로드한다. 파일/메타 없으면 None.

    Args:
        path: 모델 경로. None 이면 ``DEFAULT_MODEL_PATH``.

    Returns:
        ``(estimator, ModelMeta)`` 또는 None(미존재).

    Raises:
        RuntimeError: joblib 미설치(파일은 있으나 로드 불가).
    """
    if path is None:
        path = DEFAULT_MODEL_PATH
    path = Path(path)
    meta_p = _meta_path(path)
    if not path.exists() or not meta_p.exists():
        return None
    if not _JOBLIB:
        raise RuntimeError("joblib 미설치 — `.venv/bin/pip install joblib`")

    payload = joblib.load(path)
    estimator = payload["estimator"]
    meta_dict = json.loads(meta_p.read_text(encoding="utf-8"))
    meta = ModelMeta(**meta_dict)
    return estimator, meta


def active_model_meta(path: Optional[Path] = None) -> Optional[ModelMeta]:
    """사이드카 메타(.meta.json)만 읽어 ModelMeta 를 반환한다(없으면 None).

    estimator 로드 없이 "현재 배포된 모델이 무엇인가"만 빠르게 조회할 때 쓴다.
    """
    if path is None:
        path = DEFAULT_MODEL_PATH
    meta_p = _meta_path(Path(path))
    if not meta_p.exists():
        return None
    meta_dict = json.loads(meta_p.read_text(encoding="utf-8"))
    return ModelMeta(**meta_dict)


def predict_proba(
    *,
    customer_ids: Optional[list[str]] = None,
    fm: Optional[featmod.FeatureMatrix] = None,
    path: Optional[Path] = None,
) -> dict[str, float]:
    """배포 모델로 고객별 사기확률을 산출한다(서빙 경로 — 본질상 in-sample).

    **누수 주의**: 이 함수는 라이브 운영 점수 산출용이며, 정직한 일반화 성능은
    detection.ml_model 의 out-of-fold 로 별도 측정한다(혼동 금지).

    컬럼 정렬: 학습 시 저장한 ``meta.feature_names`` 순서대로 ``fm`` 의 열을 이름
    기준으로 재배열한다(이름 불일치 컬럼은 0.0). 피처 추출 순서가 학습과 달라도
    안전하게 추론한다.

    Args:
        customer_ids: 결과를 이 고객 집합으로만 필터(None 이면 전체).
        fm: 미리 만든 FeatureMatrix. None 이면 ``build_features()``.
        path: 모델 경로. None 이면 ``DEFAULT_MODEL_PATH``.

    Returns:
        ``{customer_id: 사기확률(0~1)}``. 모델 미존재면 빈 dict.
    """
    loaded = load_model(path)
    if loaded is None:
        return {}
    estimator, meta = loaded

    if fm is None:
        fm = featmod.build_features()

    # 학습 피처 순서(meta.feature_names)대로 열 재배열 — 이름 기준(누락은 0.0).
    name_to_idx = {name: i for i, name in enumerate(fm.feature_names)}
    rows = np.asarray(fm.rows, dtype=float)
    if rows.size == 0:
        return {}
    n = rows.shape[0]
    X = np.zeros((n, len(meta.feature_names)), dtype=float)
    for j, name in enumerate(meta.feature_names):
        src = name_to_idx.get(name)
        if src is not None:
            X[:, j] = rows[:, src]

    proba = estimator.predict_proba(X)[:, 1]
    out = {cid: float(p) for cid, p in zip(fm.customer_ids, proba)}
    if customer_ids is not None:
        wanted = set(customer_ids)
        out = {cid: p for cid, p in out.items() if cid in wanted}
    return out


def clear_model(path: Optional[Path] = None) -> None:
    """모델/메타 파일을 삭제한다(테스트·리셋용). 없으면 조용히 통과."""
    if path is None:
        path = DEFAULT_MODEL_PATH
    path = Path(path)
    for p in (path, _meta_path(path)):
        if p.exists():
            p.unlink()
