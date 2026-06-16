"""케이스 관리 + 설명 데모 CLI (WP4 검증).

리스크 스코어 상위 고객으로 케이스 큐를 생성하고, 각 케이스에 기여 신호·근거
경로를 첨부한 뒤(WP4-2), 자연어 소명문(WP4-3)을 출력한다. 환각 가드 시연도 포함.

사용:
    python -m core.case_demo build [--top 10] [--threshold 50]   # 케이스 큐 생성+소명문
    python -m core.case_demo guard                                # 환각 가드 시연
"""
from __future__ import annotations

import argparse
import sys
from typing import Optional

from core.cases import CaseStore
from detection import paths as path_builder
from detection import scoring
from explain import explainer
from explain.provider import MockProvider


def _build_case_queue(
    store: CaseStore,
    *,
    top: int,
    threshold: float,
    actor: str = "demo",
) -> list[str]:
    """리스크 스코어 상위 고객을 케이스로 생성하고 근거·소명문을 첨부·출력."""
    risks = scoring.score_customers(alert_threshold=threshold)
    flagged = scoring.alerts(risks, threshold=threshold)[:top]
    if not flagged:
        print("임계 이상 알림 케이스가 없습니다.")
        return []

    provider = MockProvider()
    created: list[str] = []
    print(f"=== 의심 케이스 큐 (상위 {len(flagged)}건, 임계 {threshold}) ===\n")
    for i, r in enumerate(flagged, 1):
        case_id = f"CASE-{r.customer_id}"
        case = store.create_case(
            case_id=case_id, customer_id=r.customer_id,
            score=r.score, ring_id=r.ring_id, actor=actor,
        )
        case.signals = r.signals
        case.paths = path_builder.build_paths(r.customer_id, r.signals)
        exp = explainer.generate_explanation(
            r.customer_id, case.paths, provider=provider
        )
        created.append(case_id)
        print(f"[{i}] {case_id}  점수={r.score:.0f}  링={r.ring_id or '-'}  "
              f"상태={case.status.value}")
        print(f"    기여 신호: {[s['type'] for s in r.signals if not s['type'].startswith('_')]}")
        print(f"    근거 경로: {len(case.paths)}개")
        print(f"    소명문: {exp.text}")
        print(f"    환각가드: {'통과' if exp.accepted else '거부'} "
              f"(인용 {len(exp.grounding.cited_entities)}개, "
              f"환각 {len(exp.grounding.hallucinated)}개)\n")
    return created


def _demo_guard() -> None:
    """환각 가드 시연: 정상 소명문 통과 vs 가짜 인용 거부."""
    # 실재 경로 1건 구성(공유 계좌).
    signals = [
        {"type": "SHARED_ACCOUNT", "weight": 45.0, "shared_key": "0118569172667",
         "num_customers": 3, "shared_with": ["CUST-05015", "CUST-05013"]},
    ]
    paths = path_builder.build_paths("CUST-05014", signals)

    print("=== 환각 가드 시연 ===\n")
    print("실재 경로 엔티티:",
          sorted(path_builder.collect_entities(paths)), "\n")

    good = ("고객 CUST-05014·CUST-05015·CUST-05013은 동일 계좌(011***67)를 "
            "공유합니다.")
    g_res = explainer.verify_grounding(good, paths)
    print("정상 소명문:", good)
    print(f"  → grounded={g_res.grounded}, 환각={g_res.hallucinated}\n")

    bad = ("고객 CUST-05014·CUST-99999은 동일 정비소(RSH-7777)를 공유하며 "
           "계좌 8888888888을 함께 씁니다.")
    b_res = explainer.verify_grounding(bad, paths)
    print("가짜 소명문(존재하지 않는 엔티티 인용):", bad)
    print(f"  → grounded={b_res.grounded}, 환각={b_res.hallucinated}")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="WP4 케이스+설명 데모")
    sub = parser.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="케이스 큐 생성 + 소명문 출력")
    b.add_argument("--top", type=int, default=10)
    b.add_argument("--threshold", type=float, default=scoring.DEFAULT_ALERT_THRESHOLD)
    b.add_argument("--db", default=None, help="SQLite 케이스 DB 경로")
    sub.add_parser("guard", help="환각 가드 시연")

    args = parser.parse_args(argv)
    if args.cmd == "build":
        store = CaseStore(db_path=args.db)
        _build_case_queue(store, top=args.top, threshold=args.threshold)
        return 0
    if args.cmd == "guard":
        _demo_guard()
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
