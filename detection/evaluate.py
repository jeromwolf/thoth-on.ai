"""재현율/정밀도/분리도 측정 + 임계치 스윕 CLI (WP2 · KPI, 상용화 검증판).

ground truth(``Customer.is_fraud_ring`` / ``ring_id`` / ``ring_pattern``)를
정답지로 삼아, 리스크 스코어 임계치 기준 탐지 결과의 **재현율(recall)·
정밀도(precision)·F1·FPR·정상 대비 점수 분리도**를 계산해 출력한다.

상용화 검증을 위해 다음을 추가로 산출한다:
    · 임계치 스윕 — 여러 임계치에서 precision/recall/F1/FPR 트레이드오프 표.
    · 수법(ring_pattern)별 탐지율 — 어떤 사기 패턴을 잘/못 잡는지.
    · GDS 신호(use_gds) 사용 시 개선 여부 비교.

PoC 영업 근거 수치다(PRD §12). 과장 없이 측정한다.

CLI:
    python -m detection.evaluate [--threshold 50] [--no-address] [--gds]
                                 [--sweep 30 40 50 60 70]
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from typing import Any

from detection import scoring
from thoth import db


@dataclass
class EvalResult:
    """평가 지표 요약."""

    threshold: float
    total_fraud_customers: int      # ground truth 링 멤버 총수
    total_rings: int                # ground truth 링 총수
    total_normal: int               # 정상 고객 모집단 총수
    detected_fraud: int             # 임계 이상으로 잡은 링 멤버 수 (TP)
    detected_normal: int            # 임계 이상으로 잡은 정상 고객 수 (FP)
    rings_covered: int              # 멤버 1명 이상 탐지된 링 수
    recall: float                   # 고객 단위 재현율
    precision: float                # 고객 단위 정밀도
    f1: float
    fpr: float                      # 위양성률 = FP / 정상 모집단
    ring_recall: float              # 링 단위 재현율(링 1개라도 잡으면 적발)
    fraud_avg_score: float
    fraud_min_score: float
    normal_avg_score: float
    normal_max_score: float
    separation: float               # 링 평균 − 정상 평균
    # 수법별 탐지율 — {pattern: {"total": n, "detected": m, "recall": r}}
    pattern_recall: dict[str, dict[str, float]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def _ground_truth() -> tuple[int, int]:
    """(링 멤버 고객 총수, 링 총수) 반환."""
    n = db.run("MATCH (c:Customer) WHERE c.is_fraud_ring RETURN count(*) AS n")[0]["n"]
    rings = db.run(
        "MATCH (c:Customer) WHERE c.ring_id <> '' "
        "RETURN count(DISTINCT c.ring_id) AS r"
    )[0]["r"]
    return int(n), int(rings)


def _pattern_membership() -> dict[str, list[str]]:
    """{ring_pattern: [customer_id, ...]} — 수법별 링 멤버 매핑.

    ``ring_pattern`` 속성이 없는(구버전 데이터) 경우 빈 dict 반환.
    """
    try:
        rows = db.run(
            """
            MATCH (c:Customer)
            WHERE c.is_fraud_ring AND coalesce(c.ring_pattern, '') <> ''
            RETURN c.ring_pattern AS pattern, collect(c.customer_id) AS cids
            """
        )
    except Exception:
        return {}
    return {r["pattern"]: r["cids"] for r in rows}


def evaluate(
    *,
    threshold: float = scoring.DEFAULT_ALERT_THRESHOLD,
    include_address: bool = True,
    use_gds: bool = False,
    use_embedding: bool = False,
    _risks: dict[str, scoring.CustomerRisk] | None = None,
) -> EvalResult:
    """탐지 성능을 평가한다.

    Args:
        threshold: 알림(탐지) 판정 점수 임계치.
        include_address: 주소 공유(약 신호) 포함 여부.
        use_gds: GDS 군집·중심성 신호(WP3 · FR-3.4)를 corroborating 가산으로 반영할지.
        use_embedding: 그래프 임베딩/비지도 이상신호(WP3 · FR-3.6)를 반영할지.
        _risks: 이미 산출된 risk 맵 재사용(임계치 스윕 시 재계산 방지). None 이면 새로 계산.

    Returns:
        재현율/정밀도/FPR/분리도/수법별 탐지율을 담은 ``EvalResult``.
    """
    total_fraud, total_rings = _ground_truth()

    risks = _risks if _risks is not None else scoring.score_customers(
        include_address=include_address, use_gds=use_gds, use_embedding=use_embedding
    )
    scored = list(risks.values())

    fraud = [r for r in scored if r.is_fraud_ring]
    normal = [r for r in scored if not r.is_fraud_ring]

    # 임계 이상 탐지
    detected = [r for r in scored if r.score >= threshold]
    tp = sum(1 for r in detected if r.is_fraud_ring)
    fp = sum(1 for r in detected if not r.is_fraud_ring)

    recall = tp / total_fraud if total_fraud else 0.0
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0

    rings_covered = len(
        {r.ring_id for r in detected if r.is_fraud_ring and r.ring_id}
    )
    ring_recall = rings_covered / total_rings if total_rings else 0.0

    # 점수 분리도 — 신호 없는 정상 고객(점수 0)을 포함해 전체 정상 평균을 추정.
    total_customers = db.run("MATCH (c:Customer) RETURN count(*) AS n")[0]["n"]
    total_normal_pop = total_customers - total_fraud
    normal_score_sum = sum(r.score for r in normal)
    normal_avg = normal_score_sum / total_normal_pop if total_normal_pop else 0.0
    fpr = fp / total_normal_pop if total_normal_pop else 0.0

    fraud_scores = [r.score for r in fraud]
    fraud_avg = sum(fraud_scores) / len(fraud_scores) if fraud_scores else 0.0
    fraud_min = min(fraud_scores) if fraud_scores else 0.0
    normal_max = max((r.score for r in normal), default=0.0)

    # 수법(ring_pattern)별 탐지율
    pattern_members = _pattern_membership()
    detected_ids = {r.customer_id for r in detected if r.is_fraud_ring}
    pattern_recall: dict[str, dict[str, float]] = {}
    for pat, cids in pattern_members.items():
        det = sum(1 for cid in cids if cid in detected_ids)
        pattern_recall[pat] = {
            "total": float(len(cids)),
            "detected": float(det),
            "recall": (det / len(cids)) if cids else 0.0,
        }

    return EvalResult(
        threshold=threshold,
        total_fraud_customers=total_fraud,
        total_rings=total_rings,
        total_normal=total_normal_pop,
        detected_fraud=tp,
        detected_normal=fp,
        rings_covered=rings_covered,
        recall=recall,
        precision=precision,
        f1=f1,
        fpr=fpr,
        ring_recall=ring_recall,
        fraud_avg_score=fraud_avg,
        fraud_min_score=fraud_min,
        normal_avg_score=normal_avg,
        normal_max_score=normal_max,
        separation=fraud_avg - normal_avg,
        pattern_recall=pattern_recall,
    )


def sweep(
    thresholds: list[float],
    *,
    include_address: bool = True,
    use_gds: bool = False,
    use_embedding: bool = False,
) -> list[EvalResult]:
    """임계치 스윕 — risk 를 한 번만 계산하고 여러 임계치로 평가한다.

    Args:
        thresholds: 평가할 임계치 목록.
        include_address: 주소 공유 신호 포함 여부.
        use_gds: GDS 신호 반영 여부.
        use_embedding: 임베딩/비지도 이상신호 반영 여부.

    Returns:
        임계치별 ``EvalResult`` 리스트(입력 순서 유지).
    """
    risks = scoring.score_customers(
        include_address=include_address, use_gds=use_gds, use_embedding=use_embedding
    )
    return [
        evaluate(threshold=t, include_address=include_address,
                 use_gds=use_gds, use_embedding=use_embedding, _risks=risks)
        for t in thresholds
    ]


def recommend_threshold(results: list[EvalResult]) -> EvalResult:
    """스윕 결과에서 F1 최대 임계치를 운영 권장값으로 선택한다."""
    return max(results, key=lambda r: (r.f1, r.recall))


def _print_report(res: EvalResult, *, label: str = "") -> None:
    line = "=" * 60
    print(line)
    title = " THOTH-ON WP2 탐지 성능 평가 (현실 데이터)"
    if label:
        title += f" — {label}"
    print(title)
    print(line)
    print(f"  점수 임계치              : {res.threshold:.1f}")
    print(f"  ground truth 링 멤버     : {res.total_fraud_customers}명")
    print(f"  ground truth 링 개수     : {res.total_rings}개")
    print(f"  정상 고객 모집단         : {res.total_normal}명")
    print("-" * 60)
    print(f"  탐지된 링 멤버 (TP)      : {res.detected_fraud}명")
    print(f"  오탐 정상 고객 (FP)      : {res.detected_normal}명")
    print(f"  적발된 링                : {res.rings_covered}/{res.total_rings}개")
    print("-" * 60)
    print(f"  재현율(recall)           : {res.recall:.3f}")
    print(f"  정밀도(precision)        : {res.precision:.3f}")
    print(f"  F1                       : {res.f1:.3f}")
    print(f"  위양성률(FPR)            : {res.fpr:.4f}")
    print(f"  링 단위 재현율           : {res.ring_recall:.3f}")
    print("-" * 60)
    print(f"  링 멤버 평균 점수        : {res.fraud_avg_score:.1f}")
    print(f"  링 멤버 최저 점수        : {res.fraud_min_score:.1f}")
    print(f"  정상 고객 평균 점수      : {res.normal_avg_score:.2f}")
    print(f"  정상 고객 최고 점수      : {res.normal_max_score:.1f}")
    print(f"  점수 분리도(링평균-정상평균): {res.separation:.1f}")
    print(line)


def _print_pattern_recall(res: EvalResult) -> None:
    if not res.pattern_recall:
        return
    print()
    print(" 수법(ring_pattern)별 탐지율 — 임계치 %.0f 기준" % res.threshold)
    print("-" * 60)
    print(f"  {'수법':<22} {'멤버':>6} {'탐지':>6} {'재현율':>8}")
    print("-" * 60)
    # 보기 좋은 순서(한국 수법 5종)
    order = ["fake_admission_star", "collision_ring", "repair_overbill",
             "agent_fraud", "driver_swap"]
    keys = [k for k in order if k in res.pattern_recall]
    keys += [k for k in res.pattern_recall if k not in order]
    for pat in keys:
        d = res.pattern_recall[pat]
        print(f"  {pat:<22} {int(d['total']):>6} {int(d['detected']):>6} {d['recall']:>8.3f}")
    print("-" * 60)


def _print_sweep(results: list[EvalResult]) -> None:
    print()
    print(" 임계치 스윕 — precision/recall/F1/FPR 트레이드오프")
    print("-" * 60)
    print(f"  {'임계':>5} {'recall':>8} {'prec':>8} {'F1':>8} {'FPR':>8} {'TP':>5} {'FP':>5}")
    print("-" * 60)
    for r in results:
        print(f"  {r.threshold:>5.0f} {r.recall:>8.3f} {r.precision:>8.3f} "
              f"{r.f1:>8.3f} {r.fpr:>8.4f} {r.detected_fraud:>5} {r.detected_normal:>5}")
    print("-" * 60)
    best = recommend_threshold(results)
    print(f"  운영 권장 임계치(F1 최대): {best.threshold:.0f} "
          f"(recall {best.recall:.3f}, precision {best.precision:.3f}, F1 {best.f1:.3f})")
    print("-" * 60)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="THOTH-ON WP2 탐지 성능 평가 + 임계치 스윕")
    p.add_argument("--threshold", type=float,
                   default=scoring.DEFAULT_ALERT_THRESHOLD,
                   help="탐지 판정 점수 임계치")
    p.add_argument("--no-address", action="store_true",
                   help="주소 공유(약 신호)를 제외하고 평가")
    p.add_argument("--gds", action="store_true",
                   help="GDS 군집·중심성 신호(WP3 · FR-3.4)를 corroborating 으로 반영")
    p.add_argument("--embedding", action="store_true",
                   help="그래프 임베딩/비지도 이상신호(WP3 · FR-3.6)를 반영")
    p.add_argument("--sweep", type=float, nargs="*",
                   default=[30.0, 40.0, 50.0, 60.0, 70.0],
                   help="임계치 스윕 목록(기본 30 40 50 60 70). 빈 값이면 스윕 생략")
    p.add_argument("--compare-gds", action="store_true",
                   help="GDS 미사용 vs 사용 재현율/정밀도 비교 출력")
    p.add_argument("--compare-embedding", action="store_true",
                   help="룰만 vs 룰+임베딩(FR-3.6) 재현율/정밀도/수법별 비교 출력")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if not db.healthcheck():
        print("Neo4j 미가용 — `make up && make wait-neo4j` 후 재실행")
        return 1

    include_address = not args.no_address

    # 1) 기본 임계치 단일 평가
    res = evaluate(threshold=args.threshold, include_address=include_address,
                   use_gds=args.gds, use_embedding=args.embedding)
    labels = []
    if args.gds:
        labels.append("GDS")
    if args.embedding:
        labels.append("임베딩")
    _print_report(res, label="+".join(labels) if labels else "룰")
    _print_pattern_recall(res)

    # 2) 임계치 스윕
    if args.sweep:
        results = sweep(args.sweep, include_address=include_address,
                        use_gds=args.gds, use_embedding=args.embedding)
        _print_sweep(results)

    # 3) GDS 비교
    if args.compare_gds:
        base = evaluate(threshold=args.threshold, include_address=include_address,
                        use_gds=False)
        gds = evaluate(threshold=args.threshold, include_address=include_address,
                       use_gds=True)
        print()
        print(" GDS 신호 반영 전후 비교 (임계치 %.0f)" % args.threshold)
        print("-" * 60)
        print(f"  {'':<8} {'recall':>8} {'prec':>8} {'F1':>8} {'FPR':>8}")
        print(f"  {'룰만':<8} {base.recall:>8.3f} {base.precision:>8.3f} "
              f"{base.f1:>8.3f} {base.fpr:>8.4f}")
        print(f"  {'룰+GDS':<8} {gds.recall:>8.3f} {gds.precision:>8.3f} "
              f"{gds.f1:>8.3f} {gds.fpr:>8.4f}")
        print("-" * 60)

    # 4) 임베딩(FR-3.6) 비교 — 룰만 vs 룰+임베딩
    if args.compare_embedding:
        base = evaluate(threshold=args.threshold, include_address=include_address,
                        use_gds=False, use_embedding=False)
        emb = evaluate(threshold=args.threshold, include_address=include_address,
                       use_gds=False, use_embedding=True)
        print()
        print(" 임베딩/비지도 이상신호(FR-3.6) 반영 전후 비교 (임계치 %.0f)"
              % args.threshold)
        print("-" * 60)
        print(f"  {'':<10} {'recall':>8} {'prec':>8} {'F1':>8} {'FPR':>8} "
              f"{'TP':>5} {'FP':>5}")
        print(f"  {'룰만':<10} {base.recall:>8.3f} {base.precision:>8.3f} "
              f"{base.f1:>8.3f} {base.fpr:>8.4f} {base.detected_fraud:>5} "
              f"{base.detected_normal:>5}")
        print(f"  {'룰+임베딩':<10} {emb.recall:>8.3f} {emb.precision:>8.3f} "
              f"{emb.f1:>8.3f} {emb.fpr:>8.4f} {emb.detected_fraud:>5} "
              f"{emb.detected_normal:>5}")
        print("-" * 60)
        # 수법별 회수 변화
        print(f"  {'수법':<22} {'룰만':>8} {'룰+임베딩':>10} {'증감':>6}")
        order = ["fake_admission_star", "collision_ring", "repair_overbill",
                 "agent_fraud", "driver_swap"]
        for pat in order:
            b = base.pattern_recall.get(pat, {}).get("recall", 0.0)
            e = emb.pattern_recall.get(pat, {}).get("recall", 0.0)
            print(f"  {pat:<22} {b:>8.3f} {e:>10.3f} {e - b:>+6.3f}")
        print("-" * 60)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
