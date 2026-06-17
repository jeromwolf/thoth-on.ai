"""캐글 실데이터(fraud_oracle.csv) 분포 분석 → real_distributions.json.

목적(① 피처 분포 → 합성 반영):
    캐글 자동차보험 청구 15,420건의 **실제 분포**를 추출한다. 전체 사기율,
    주요 범주형 컬럼별 사기율(Fault/BasePolicy/VehicleCategory/AccidentArea 등),
    그리고 합성 generator 가 prior 로 사용할 컬럼별 카테고리 비중(marginal)을
    계산해 ``data/kaggle/real_distributions.json`` 에 저장한다.

캐글 데이터셋: Oracle "Vehicle Insurance Fraud Detection" (CC0).
    33개 컬럼 · 라벨 FraudFound_P(0/1) · 사기율 ≈ 5.99%.

stdlib(csv)만 사용한다(pandas 의존 없음 — 본 레포 합성 generator 와 동일 정책).

CLI:
    python -m ingest.kaggle_analysis [--csv PATH] [--out PATH] [--md PATH]
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

DEFAULT_CSV = "data/kaggle/fraud_oracle.csv"
DEFAULT_OUT = "data/kaggle/real_distributions.json"
# 자동 생성 분포표(데이터 부록). 사람이 큐레이션한 docs/kaggle_findings.md 와 분리해
# 재실행 시 큐레이션 문서를 덮어쓰지 않게 한다.
DEFAULT_MD = "docs/kaggle_distributions_auto.md"
LABEL_COL = "FraudFound_P"

# 사기율(조건부)을 측정할 범주형 컬럼 — 합성 prior 로 쓸 핵심 축.
CATEGORICAL_COLS = [
    "Fault",
    "BasePolicy",
    "PolicyType",
    "VehicleCategory",
    "AccidentArea",
    "Make",
    "Sex",
    "MaritalStatus",
    "AgeOfVehicle",
    "AgeOfPolicyHolder",
    "PastNumberOfClaims",
    "PoliceReportFiled",
    "WitnessPresent",
    "AgentType",
    "AddressChange_Claim",
    "NumberOfCars",
    "VehiclePrice",
    "Deductible",
    "DriverRating",
    # ② 듀얼 레이어 — 행동/시계열 신호(속성 ML 의 핵심 피처). 합성 청구에도
    #   이 분포를 prior 로 부여해 속성 ML 이 합성 개인사기를 잡게 한다.
    "Days_Policy_Accident",
    "Days_Policy_Claim",
    "NumberOfSuppliments",
    "MonthClaimed",
]

# 수치형(또는 순서형 수치) 컬럼 — 사기 vs 정상 평균/분포 비교용.
NUMERIC_COLS = ["Age"]


def _read_rows(csv_path: Path) -> list[dict[str, str]]:
    # 캐글 CSV 첫 헤더에 BOM(﻿)이 붙어있어 utf-8-sig 로 연다.
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _column_breakdown(
    rows: list[dict[str, str]], col: str
) -> dict[str, dict[str, Any]]:
    """컬럼의 카테고리별 (건수, 사기건수, 사기율, 전체비중)을 계산."""
    total = len(rows)
    counts: Counter[str] = Counter()
    fraud_counts: Counter[str] = Counter()
    for row in rows:
        val = (row.get(col) or "").strip()
        counts[val] += 1
        if row.get(LABEL_COL) == "1":
            fraud_counts[val] += 1
    out: dict[str, dict[str, Any]] = {}
    for val, n in counts.most_common():
        nf = fraud_counts.get(val, 0)
        out[val] = {
            "count": n,
            "fraud": nf,
            "fraud_rate": round(nf / n, 4) if n else 0.0,
            "share": round(n / total, 4) if total else 0.0,
        }
    return out


def _numeric_summary(
    rows: list[dict[str, str]], col: str
) -> dict[str, Any]:
    """수치형 컬럼의 사기/정상 평균 + 전체 분포 요약."""
    fraud_vals: list[float] = []
    normal_vals: list[float] = []
    for row in rows:
        raw = (row.get(col) or "").strip()
        try:
            v = float(raw)
        except (TypeError, ValueError):
            continue
        if v <= 0:  # Age=0 은 결측 표기 → 제외
            continue
        if row.get(LABEL_COL) == "1":
            fraud_vals.append(v)
        else:
            normal_vals.append(v)
    all_vals = fraud_vals + normal_vals

    def _avg(xs: list[float]) -> float:
        return round(sum(xs) / len(xs), 2) if xs else 0.0

    return {
        "n": len(all_vals),
        "overall_avg": _avg(all_vals),
        "fraud_avg": _avg(fraud_vals),
        "normal_avg": _avg(normal_vals),
        "min": min(all_vals) if all_vals else 0,
        "max": max(all_vals) if all_vals else 0,
    }


def analyze(csv_path: str | Path = DEFAULT_CSV) -> dict[str, Any]:
    """캐글 CSV 분석 → 분포 딕셔너리 반환."""
    rows = _read_rows(Path(csv_path))
    total = len(rows)
    n_fraud = sum(1 for r in rows if r.get(LABEL_COL) == "1")
    overall_rate = round(n_fraud / total, 4) if total else 0.0

    categorical: dict[str, dict[str, dict[str, Any]]] = {}
    for col in CATEGORICAL_COLS:
        if col in (rows[0] if rows else {}):
            categorical[col] = _column_breakdown(rows, col)

    numeric: dict[str, dict[str, Any]] = {}
    for col in NUMERIC_COLS:
        if col in (rows[0] if rows else {}):
            numeric[col] = _numeric_summary(rows, col)

    return {
        "source": str(csv_path),
        "license": "CC0",
        "total_claims": total,
        "fraud_claims": n_fraud,
        "overall_fraud_rate": overall_rate,
        "categorical": categorical,
        "numeric": numeric,
    }


def write_json(dist: dict[str, Any], out_path: str | Path = DEFAULT_OUT) -> Path:
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(dist, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def _md_col_table(name: str, breakdown: dict[str, dict[str, Any]], *, top: int = 12) -> str:
    lines = [f"### {name}", "", "| 값 | 건수 | 비중 | 사기건 | 사기율 |", "|---|---:|---:|---:|---:|"]
    items = sorted(breakdown.items(), key=lambda kv: kv[1]["count"], reverse=True)[:top]
    for val, d in items:
        label = val if val else "(빈값)"
        lines.append(
            f"| {label} | {d['count']} | {d['share']*100:.1f}% | "
            f"{d['fraud']} | {d['fraud_rate']*100:.1f}% |"
        )
    lines.append("")
    return "\n".join(lines)


def write_markdown(dist: dict[str, Any], md_path: str | Path = DEFAULT_MD) -> Path:
    md = Path(md_path)
    md.parent.mkdir(parents=True, exist_ok=True)

    parts: list[str] = []
    parts.append("# 캐글 실데이터 분석 결과 (fraud_oracle.csv)\n")
    parts.append(
        "Oracle *Vehicle Insurance Fraud Detection* (CC0). 자동차보험 청구 "
        f"**{dist['total_claims']:,}건**, 라벨 `FraudFound_P`.\n"
    )
    parts.append(
        f"- **전체 사기율**: {dist['overall_fraud_rate']*100:.2f}% "
        f"(사기 {dist['fraud_claims']:,}건 / 전체 {dist['total_claims']:,}건)\n"
    )

    parts.append("\n## ① 컬럼별 사기율 — 합성 prior 핵심 축\n")
    # 사기 신호가 강한 핵심 축을 앞에 노출
    priority = ["Fault", "BasePolicy", "VehicleCategory", "AccidentArea",
                "PolicyType", "PastNumberOfClaims", "AddressChange_Claim",
                "AgeOfPolicyHolder", "Make"]
    cat = dist["categorical"]
    for col in priority:
        if col in cat:
            parts.append(_md_col_table(col, cat[col]))

    parts.append("\n## ② 수치형(Age) 사기 vs 정상\n")
    for col, d in dist["numeric"].items():
        parts.append(
            f"- **{col}**: 전체평균 {d['overall_avg']}, 사기평균 {d['fraud_avg']}, "
            f"정상평균 {d['normal_avg']} (n={d['n']:,}, 범위 {d['min']}~{d['max']})\n"
        )

    parts.append("\n## ③ 기타 컬럼 분포\n")
    for col in cat:
        if col not in priority:
            parts.append(_md_col_table(col, cat[col], top=8))

    md.write_text("\n".join(parts), encoding="utf-8")
    return md


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="캐글 실데이터 분포 분석 (①)")
    p.add_argument("--csv", default=DEFAULT_CSV, help="입력 캐글 CSV")
    p.add_argument("--out", default=DEFAULT_OUT, help="분포 JSON 출력 경로")
    p.add_argument("--md", default=DEFAULT_MD, help="요약 Markdown 출력 경로")
    p.add_argument("--no-md", action="store_true", help="Markdown 생성 생략")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    dist = analyze(args.csv)
    out = write_json(dist, args.out)
    print("=" * 60)
    print(" 캐글 실데이터 분석 완료 (①)")
    print("=" * 60)
    print(f"  총 청구            : {dist['total_claims']:,}건")
    print(f"  사기 청구          : {dist['fraud_claims']:,}건")
    print(f"  전체 사기율        : {dist['overall_fraud_rate']*100:.2f}%")
    print(f"  분석 컬럼(범주형)  : {len(dist['categorical'])}개")
    print(f"  분포 JSON          : {out}")
    if not args.no_md:
        md = write_markdown(dist, args.md)
        print(f"  요약 Markdown      : {md}")
    print("-" * 60)
    print(" 핵심 컬럼별 사기율(상위 카테고리):")
    for col in ["Fault", "BasePolicy", "VehicleCategory", "AccidentArea"]:
        bd = dist["categorical"].get(col, {})
        top = sorted(bd.items(), key=lambda kv: kv[1]["fraud_rate"], reverse=True)[:3]
        frag = ", ".join(f"{v}={d['fraud_rate']*100:.1f}%" for v, d in top)
        print(f"   {col:<16}: {frag}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
