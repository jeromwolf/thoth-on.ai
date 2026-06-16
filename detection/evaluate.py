"""재현율/정밀도/분리도 측정 CLI (WP2 · KPI).

ground truth(``Customer.is_fraud_ring`` / ``ring_id``)를 정답지로 삼아,
리스크 스코어 임계치 기준 탐지 결과의 **재현율(recall)·정밀도(precision)·
F1·정상 대비 점수 분리도**를 계산해 출력한다. PoC 영업 근거 수치다(PRD §12).

CLI:
    python -m detection.evaluate [--threshold 50] [--no-address]
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Any

from detection import scoring
from thoth import db


@dataclass
class EvalResult:
    """평가 지표 요약."""

    threshold: float
    total_fraud_customers: int      # ground truth 링 멤버 총수
    total_rings: int                # ground truth 링 총수
    detected_fraud: int             # 임계 이상으로 잡은 링 멤버 수 (TP)
    detected_normal: int            # 임계 이상으로 잡은 정상 고객 수 (FP)
    rings_covered: int              # 멤버 1명 이상 탐지된 링 수
    recall: float                   # 고객 단위 재현율
    precision: float                # 고객 단위 정밀도
    f1: float
    ring_recall: float              # 링 단위 재현율(링 1개라도 잡으면 적발)
    fraud_avg_score: float
    fraud_min_score: float
    normal_avg_score: float
    normal_max_score: float
    separation: float               # 링 평균 − 정상 평균

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


def evaluate(
    *,
    threshold: float = scoring.DEFAULT_ALERT_THRESHOLD,
    include_address: bool = True,
    use_gds: bool = False,
) -> EvalResult:
    """탐지 성능을 평가한다.

    Args:
        threshold: 알림(탐지) 판정 점수 임계치.
        include_address: 주소 공유(약 신호) 포함 여부.
        use_gds: GDS 군집·중심성 신호(WP3)를 corroborating 가산으로 반영할지.

    Returns:
        재현율/정밀도/분리도 등을 담은 ``EvalResult``.
    """
    total_fraud, total_rings = _ground_truth()

    risks = scoring.score_customers(include_address=include_address, use_gds=use_gds)
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
    #   scored 에 없는 정상 고객은 0점이므로, 전체 정상 모집단 평균을 별도 산출.
    total_customers = db.run("MATCH (c:Customer) RETURN count(*) AS n")[0]["n"]
    total_normal_pop = total_customers - total_fraud
    normal_score_sum = sum(r.score for r in normal)
    normal_avg = normal_score_sum / total_normal_pop if total_normal_pop else 0.0

    fraud_scores = [r.score for r in fraud]
    fraud_avg = sum(fraud_scores) / len(fraud_scores) if fraud_scores else 0.0
    fraud_min = min(fraud_scores) if fraud_scores else 0.0
    normal_max = max((r.score for r in normal), default=0.0)

    return EvalResult(
        threshold=threshold,
        total_fraud_customers=total_fraud,
        total_rings=total_rings,
        detected_fraud=tp,
        detected_normal=fp,
        rings_covered=rings_covered,
        recall=recall,
        precision=precision,
        f1=f1,
        ring_recall=ring_recall,
        fraud_avg_score=fraud_avg,
        fraud_min_score=fraud_min,
        normal_avg_score=normal_avg,
        normal_max_score=normal_max,
        separation=fraud_avg - normal_avg,
    )


def _print_report(res: EvalResult) -> None:
    line = "=" * 60
    print(line)
    print(" THOTH-ON WP2 탐지 성능 평가 (주입 링 재현율)")
    print(line)
    print(f"  점수 임계치              : {res.threshold:.1f}")
    print(f"  ground truth 링 멤버     : {res.total_fraud_customers}명")
    print(f"  ground truth 링 개수     : {res.total_rings}개")
    print("-" * 60)
    print(f"  탐지된 링 멤버 (TP)      : {res.detected_fraud}명")
    print(f"  오탐 정상 고객 (FP)      : {res.detected_normal}명")
    print(f"  적발된 링                : {res.rings_covered}/{res.total_rings}개")
    print("-" * 60)
    print(f"  재현율(recall)           : {res.recall:.3f}")
    print(f"  정밀도(precision)        : {res.precision:.3f}")
    print(f"  F1                       : {res.f1:.3f}")
    print(f"  링 단위 재현율           : {res.ring_recall:.3f}")
    print("-" * 60)
    print(f"  링 멤버 평균 점수        : {res.fraud_avg_score:.1f}")
    print(f"  링 멤버 최저 점수        : {res.fraud_min_score:.1f}")
    print(f"  정상 고객 평균 점수      : {res.normal_avg_score:.2f}")
    print(f"  정상 고객 최고 점수      : {res.normal_max_score:.1f}")
    print(f"  점수 분리도(링평균-정상평균): {res.separation:.1f}")
    print(line)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="THOTH-ON WP2 탐지 성능 평가")
    p.add_argument("--threshold", type=float,
                   default=scoring.DEFAULT_ALERT_THRESHOLD,
                   help="탐지 판정 점수 임계치")
    p.add_argument("--no-address", action="store_true",
                   help="주소 공유(약 신호)를 제외하고 평가")
    p.add_argument("--gds", action="store_true",
                   help="GDS 군집·중심성 신호(WP3)를 corroborating 으로 반영")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if not db.healthcheck():
        print("Neo4j 미가용 — `make up && make wait-neo4j` 후 재실행")
        return 1
    res = evaluate(
        threshold=args.threshold,
        include_address=not args.no_address,
        use_gds=args.gds,
    )
    _print_report(res)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
