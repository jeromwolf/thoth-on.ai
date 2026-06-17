"""ML 피처 추출 (WP3 · FR-3.7) — 그래프 신호의 수치 피처 벡터화.

각 고객을 **그래프 구조 신호의 수치 피처 벡터**로 변환한다. 이 피처들은 룰
스코어링(detection.scoring)·GDS(detection.gds_pipeline)·임베딩
(detection.embedding)이 산출하는 신호를 종합한 것으로, scikit-learn 분류기
(detection.ml_model)가 ground truth 라벨로 그래프 신호 가중치를 **자동 학습**하는
입력이 된다.

[평가 누수 절대 금지 — 최우선 제약]
    ground truth(``is_fraud_ring`` / ``ring_id`` / ``ring_pattern``) 또는 그 파생을
    **피처로 절대 넣지 않는다**. 피처는 순수 그래프 구조+시간 신호만 사용한다.
    라벨은 오직 ``extract_labels()`` 가 학습/평가 타깃(y)으로만 별도 추출한다.

    누수 방지 단언(tests/test_ml.py)으로 피처 추출 코드가 라벨 토큰을 참조하지
    않음을 직접 검증한다. 또한 ``FEATURE_NAMES`` 에 ring_id 파생 항목이 없음을
    런타임으로도 확인한다.

[피처 출처 — 모두 라벨 미사용 구조 신호]
    룰 신호 (detection.detect / scoring):
        · n_shared_account_strong   서로 다른 주소 동일 계좌 공유 군집 수(강)
        · n_shared_account_family   같은 주소(가족) 동일 계좌 공유 군집 수(약)
        · n_shared_phone_strong     서로 다른 주소 동일 전화 공유 군집 수
        · n_shared_vehicle_strong   서로 다른 주소 동일 차량 공유 군집 수
        · max_share_cluster_size    참여 공유 군집의 최대 인원
        · min_share_time_span       참여 공유 군집의 최소 청구 시간 span(일)
        · time_clustered_share      시간 군집(span<=30일) 강 공유 보유 여부(0/1)
        · n_cross_witness           상호 교차 목격(crash-for-cash) 군집 수
        · max_witness_cluster_size  교차 목격 군집 최대 인원
        · n_focused_hotspot         집중 핫스팟(비인기 병원+정비소·소수·시간군집) 수
        · n_collusion_hotspot       담합 핫스팟(단일 collision·매우 짧은 기간) 수
        · rule_score                기존 수동 가중합 룰 스코어(0~100) — 종합 신호
        · n_signal_groups           충족한 강신호 그룹 종 수(SHARED/WITNESS/...)
    GDS 신호 (detection.gds_pipeline write 속성):
        · gds_community_size        Louvain 커뮤니티 멤버 수(다수 Customer 소속)
        · gds_community_small_tight 작고 응집된 위험 커뮤니티(2~6인=1, 단독/대형=0)
        · gds_pagerank              PageRank 중심성 점수
    임베딩/비지도 (detection.embedding):
        · embed_similar_degree      SIMILAR_TO 차수(서로 유사한 고객 수)
        · embed_is_clique           3+ 클리크 멤버 여부(0/1) — 비지도 이상신호

    GDS/임베딩 속성이 없으면(파이프라인 미실행) 0 으로 채운다(graceful).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from detection import detect, embedding, scoring
from thoth import db

# ------------------------------------------------------------------
# 피처 이름(순서 고정) — 모델 입력 컬럼. ring_id/라벨 파생 절대 없음.
# ------------------------------------------------------------------
FEATURE_NAMES: list[str] = [
    # 룰 — 공유 엔티티
    "n_shared_account_strong",
    "n_shared_account_family",
    "n_shared_phone_strong",
    "n_shared_vehicle_strong",
    "max_share_cluster_size",
    "min_share_time_span",
    "time_clustered_share",
    # 룰 — 교차 목격(crash-for-cash)
    "n_cross_witness",
    "max_witness_cluster_size",
    # 룰 — 핫스팟
    "n_focused_hotspot",
    "n_collusion_hotspot",
    # 룰 — 종합
    "rule_score",
    "n_signal_groups",
    # GDS
    "gds_community_size",
    "gds_community_small_tight",
    "gds_pagerank",
    # 임베딩/비지도
    "embed_similar_degree",
    "embed_is_clique",
]

# ground truth 라벨 토큰 — 피처에 등장하면 누수. (테스트가 이를 검증)
_LABEL_TOKENS = ("is_fraud_ring", "ring_id", "ring_pattern", "fraud_label")

# 시간 군집 임계(룰과 동일).
_TIME_CLUSTER_DAYS = detect.DEFAULT_TIME_CLUSTER_DAYS


@dataclass
class FeatureMatrix:
    """피처 행렬 + 정렬된 고객 ID. 라벨은 포함하지 않는다(누수 방지)."""

    customer_ids: list[str]
    rows: list[list[float]]
    feature_names: list[str]

    @property
    def n_samples(self) -> int:
        return len(self.customer_ids)

    @property
    def n_features(self) -> int:
        return len(self.feature_names)


def _blank() -> dict[str, float]:
    """0 으로 초기화한 단일 고객 피처 dict."""
    return {name: 0.0 for name in FEATURE_NAMES}


def _all_customer_ids() -> list[str]:
    """전 고객 ID(정렬). 신호 없는 고객도 0 벡터로 포함해 모집단 전체를 학습/평가."""
    rows = db.run("MATCH (c:Customer) RETURN c.customer_id AS cid ORDER BY cid")
    return [r["cid"] for r in rows]


def _community_density() -> tuple[dict[str, float], dict[str, float]]:
    """Louvain 커뮤니티 멤버 수와 '작고 응집된 위험 커뮤니티' 지표를 고객별로 매핑.

    링은 공유 계좌·교차목격으로 **작은 응집 커뮤니티(2~6인)** 를 이루고, 정상 고객은
    각자 고유 계좌로 분리돼 단독(size=1)이거나 허브 병합으로 대형이다. 따라서
    small_tight(2~6인=1, 그 외 0)은 정상 대형/단독을 억제하고 링 규모만 양성화한다.
    ground truth 미사용(순수 구조).

    Returns:
        (size_by_cid, small_tight_by_cid) — 고객별 커뮤니티 크기 / 소수응집 지표.
    """
    rows = db.run(
        """
        MATCH (c:Customer)
        WHERE c.louvain_community IS NOT NULL
        WITH c.louvain_community AS comm, collect(c.customer_id) AS members
        RETURN comm AS community, members, size(members) AS sz
        """
    )
    size_by_cid: dict[str, float] = {}
    tight_by_cid: dict[str, float] = {}
    for r in rows:
        sz = int(r["sz"])
        # 작고 응집된 위험 커뮤니티: 2~6인(링 규모)이면 1, 단독/대형 병합은 0.
        tight = 1.0 if 2 <= sz <= 6 else 0.0
        for cid in r["members"]:
            size_by_cid[cid] = float(sz)
            tight_by_cid[cid] = tight
    return size_by_cid, tight_by_cid


def _gds_props() -> dict[str, dict[str, float]]:
    """고객별 GDS 속성(community_size, pagerank). 미설정 시 빈 dict."""
    try:
        rows = db.run(
            """
            MATCH (c:Customer)
            WHERE c.louvain_community IS NOT NULL OR c.pagerank_score IS NOT NULL
            RETURN c.customer_id AS cid,
                   coalesce(c.pagerank_score, 0.0) AS pagerank
            """
        )
    except Exception:
        return {}
    size_by_cid, tight_by_cid = _community_density()
    out: dict[str, dict[str, float]] = {}
    for r in rows:
        cid = r["cid"]
        out[cid] = {
            "gds_community_size": size_by_cid.get(cid, 0.0),
            "gds_community_small_tight": tight_by_cid.get(cid, 0.0),
            "gds_pagerank": float(r["pagerank"]),
        }
    return out


def build_features(
    *,
    include_address: bool = True,
) -> FeatureMatrix:
    """전 고객의 ML 피처 행렬을 구성한다(라벨 미포함 — 누수 방지).

    룰 신호(detection.detect)·룰 종합 스코어(detection.scoring, GDS+임베딩 corroborating
    포함)·GDS write 속성·임베딩 비지도 신호를 종합해 ``FEATURE_NAMES`` 순서의 수치
    벡터를 만든다. ground truth(ring_id/is_fraud_ring/ring_pattern)는 **일절 참조하지
    않는다**.

    Args:
        include_address: 주소 공유(약 신호) 군집 컨텍스트 포함 여부.

    Returns:
        ``FeatureMatrix`` — customer_ids(정렬) / rows(피처 행렬) / feature_names.
    """
    feats: dict[str, dict[str, float]] = {
        cid: _blank() for cid in _all_customer_ids()
    }

    def _f(cid: str) -> dict[str, float]:
        d = feats.get(cid)
        if d is None:
            d = _blank()
            feats[cid] = d
        return d

    # --- 룰 종합 스코어(기존 수동 가중합) + 강신호 그룹 수 ---
    #   GDS/임베딩 corroborating 을 포함한 종합 룰 점수를 단일 피처로 제공한다.
    #   이는 라벨을 쓰지 않는 점수이며(scoring 은 점수 계산에 라벨 미사용), ML 이
    #   "기존 룰 가중합이 얼마나 신뢰할 만한가"를 자동 가중치로 재조정하게 한다.
    risks = scoring.score_customers(
        include_address=include_address, use_gds=True, use_embedding=True
    )
    for cid, r in risks.items():
        d = _f(cid)
        d["rule_score"] = float(r.score)
        d["n_signal_groups"] = float(len(r._signal_groups))

    # --- 룰 공유 엔티티(가족/강 구분 + 시간 군집 + 군집 규모) ---
    #   min_share_time_span 은 "참여 공유 군집의 최소 청구 span"(작을수록 의심).
    #   미관측/연중분산 고객은 1년(365일)으로 캡해 정상이 자동 억제되게 한다.
    #   (999 같은 극단값은 LR 스케일링에서 오버플로를 유발하므로 365 로 캡.)
    SPAN_SENTINEL = 365.0
    for cid in feats:
        feats[cid]["min_share_time_span"] = SPAN_SENTINEL

    for grp in detect.run_shared_entities(include_address=include_address):
        stype = grp["shared_type"]
        cids = grp["customer_ids"]
        num = int(grp["num_customers"])
        distinct_addr = int(grp.get("distinct_addresses", num))
        time_span = grp.get("time_span_days", -1)
        is_family = scoring._is_family_share(num, distinct_addr)
        time_clustered = (not is_family) and (0 <= time_span <= _TIME_CLUSTER_DAYS)

        for cid in cids:
            d = _f(cid)
            if stype == "ACCOUNT":
                key = "n_shared_account_family" if is_family else "n_shared_account_strong"
                d[key] += 1.0
            elif stype == "PHONE" and not is_family:
                d["n_shared_phone_strong"] += 1.0
            elif stype == "VEHICLE" and not is_family:
                d["n_shared_vehicle_strong"] += 1.0
            # 주소 공유 자체는 가족 노이즈 → 별도 카운트 안 함(약신호).

            if stype in ("ACCOUNT", "PHONE", "VEHICLE") and not is_family:
                d["max_share_cluster_size"] = max(d["max_share_cluster_size"], float(num))
                if 0 <= time_span:
                    capped = min(float(time_span), SPAN_SENTINEL)
                    if capped < d["min_share_time_span"]:
                        d["min_share_time_span"] = capped
                if time_clustered:
                    d["time_clustered_share"] = 1.0

    # sentinel 을 0 이 아닌 큰 값으로 유지(정상=span 큼). 미관측은 sentinel 그대로.

    # --- 룰 교차 목격(crash-for-cash) ---
    for cluster in detect.run_crash_rings():
        seed = cluster["seed_customer"]
        size = float(cluster["cluster_size"])
        d = _f(seed)
        d["n_cross_witness"] += 1.0
        d["max_witness_cluster_size"] = max(d["max_witness_cluster_size"], size)

    # --- 룰 핫스팟(집중/담합) ---
    for hs in detect.run_focused_hotspots():
        for cid in hs["customer_ids"]:
            _f(cid)["n_focused_hotspot"] += 1.0
    for hs in detect.run_collusion_hotspots():
        for cid in hs["customer_ids"]:
            _f(cid)["n_collusion_hotspot"] += 1.0

    # --- GDS 속성(커뮤니티 위험밀도·PageRank) ---
    for cid, props in _gds_props().items():
        d = _f(cid)
        for k, v in props.items():
            d[k] = v

    # --- 임베딩 비지도 신호(SIMILAR_TO 차수·클리크) ---
    try:
        sigs = embedding.compute_anomaly_signals(attach_labels=False)
    except Exception:
        sigs = {}
    for cid, sig in sigs.items():
        d = _f(cid)
        d["embed_similar_degree"] = float(sig.similar_degree)
        d["embed_is_clique"] = 1.0 if sig.is_clique else 0.0

    customer_ids = sorted(feats.keys())
    rows = [[feats[cid][name] for name in FEATURE_NAMES] for cid in customer_ids]
    return FeatureMatrix(
        customer_ids=customer_ids, rows=rows, feature_names=list(FEATURE_NAMES)
    )


def extract_labels(customer_ids: list[str]) -> list[int]:
    """주어진 고객 순서의 ground truth 라벨(사기=1/정상=0)을 반환한다.

    **라벨은 학습/평가 타깃(y)으로만 쓰며 피처(X)에는 절대 들어가지 않는다.**
    피처 추출(build_features)과 분리된 별도 함수로 둬 누수를 구조적으로 차단한다.

    Args:
        customer_ids: 라벨을 매길 고객 ID 순서(FeatureMatrix.customer_ids).

    Returns:
        ``customer_ids`` 와 같은 길이/순서의 0/1 라벨 리스트.
    """
    rows = db.run(
        """
        MATCH (c:Customer)
        WHERE c.customer_id IN $ids
        RETURN c.customer_id AS cid, coalesce(c.is_fraud_ring, false) AS f
        """,
        ids=customer_ids,
    )
    lab = {r["cid"]: (1 if r["f"] else 0) for r in rows}
    return [lab.get(cid, 0) for cid in customer_ids]


def feature_names_are_label_free() -> bool:
    """``FEATURE_NAMES`` 에 라벨 파생 항목이 없음을 런타임 확인(누수 가드)."""
    joined = " ".join(FEATURE_NAMES).lower()
    return not any(tok in joined for tok in _LABEL_TOKENS)


# ==================================================================
# CLI — 피처 요약 출력(빠른 점검)
# ==================================================================
def _print_summary(fm: FeatureMatrix, labels: list[int]) -> None:
    line = "=" * 64
    print(line)
    print(" THOTH-ON ML 피처 추출 요약 (FR-3.7) — 라벨 미사용 구조 신호")
    print(line)
    print(f"  표본 수(고객)   : {fm.n_samples:,}")
    print(f"  피처 수         : {fm.n_features}")
    print(f"  사기(양성) 라벨 : {sum(labels)}  / 정상 {len(labels)-sum(labels)}")
    print(f"  누수 가드       : FEATURE_NAMES label-free = {feature_names_are_label_free()}")
    print("-" * 64)
    # 피처별 사기/정상 평균(설명가능성 미리보기)
    import statistics

    pos_idx = [i for i, y in enumerate(labels) if y == 1]
    neg_idx = [i for i, y in enumerate(labels) if y == 0]
    print(f"  {'피처':<26}{'사기평균':>12}{'정상평균':>12}")
    for j, name in enumerate(fm.feature_names):
        pos = statistics.mean(fm.rows[i][j] for i in pos_idx) if pos_idx else 0.0
        neg = statistics.mean(fm.rows[i][j] for i in neg_idx) if neg_idx else 0.0
        print(f"  {name:<26}{pos:>12.3f}{neg:>12.3f}")
    print(line)


def main(argv: list[str] | None = None) -> int:
    import argparse

    argparse.ArgumentParser(description="THOTH-ON ML 피처 추출 요약").parse_args(argv)
    if not db.healthcheck():
        print("Neo4j 미가용 — `make up && make wait-neo4j` 후 재실행")
        return 1
    fm = build_features()
    labels = extract_labels(fm.customer_ids)
    _print_summary(fm, labels)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
