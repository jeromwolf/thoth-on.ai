"""듀얼 레이어 스코어러 (조직형 그래프 + 개인형 속성) — 듀얼 레이어 ②.

[왜 듀얼 레이어인가]
    THOTH-ON 그래프 탐지는 **조직형 공모 사기**(같은 계좌·교차목격·브로커 방사형)엔
    강하다(링 단위 F1 ≈ 0.93). 그러나 현실 사기의 76%인 **개인 단발 과장청구**는
    공모 네트워크가 없어 그래프로 거의 못 잡는다(배경 사기 recall ≈ 0.2%,
    ``docs/kaggle_findings.md``). 이 모듈은 두 레이어를 결합한다:

    · 레이어 A — 그래프(조직형) : detection.scoring 의 고객 단위 룰/GDS/임베딩
      종합 점수(0~100). 링/허브/교차목격 같은 **공모 구조 신호**.
    · 레이어 B — 속성(개인형)   : detection.attribute_model 의 청구 단위 속성 ML
      사기확률(0~1). 캐글 fraud_oracle(개인 사기 실데이터)로 독립 학습/검증한
      모델을 합성 청구의 캐글 호환 속성에 적용. 가입직후·본인과실·고액·주소변경
      직후 같은 **개인 사기 신호**.

[결합 방식]
    청구 단위로 두 점수를 0~1 로 정규화해 결합한다. 기본은 **max**(둘 중 하나라도
    강하면 위험) + 가중합(weighted) 옵션. 결합과 함께 **유형 라벨**을 부여한다:
        · "ORGANIZED" : 그래프 점수만 높음(공모형).
        · "INDIVIDUAL": 속성 점수만 높음(개인 단발형).
        · "BOTH"      : 둘 다 높음.
        · "NONE"      : 둘 다 낮음.

[누수 방지 — 핵심]
    · 속성 ML 의 **성능 보고는 캐글 out-of-fold** 로만 한다(attribute_model 모듈).
      여기서 합성 청구에 적용하는 모델은 캐글로 학습된 **별도 도메인** 추론기이며,
      합성 라벨을 일절 보지 않는다(도메인 간 전이 — 합성 자기누수 없음).
    · 그래프 점수(detection.scoring)도 라벨을 점수 계산에 쓰지 않는다(기존 보장).
    · 합성 검증은 ground truth(ring_pattern)로 **재현율만 측정**한다(평가용).

CLI:
    .venv/bin/python -m detection.dual_layer                 # 듀얼 레이어 재현율 측정
    .venv/bin/python -m detection.dual_layer --model lr      # 속성 모델 선택
    .venv/bin/python -m detection.dual_layer --combine max   # 결합 방식(max/weighted)
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from typing import Any

from thoth import db

# 그래프 점수 정규화 기준(룰 스코어 0~100 → 0~1).
GRAPH_SCORE_MAX = 100.0

# 유형 판정 임계(정규화 0~1). 그래프/속성 각각의 "높음" 기준.
GRAPH_HIGH = 0.50        # 룰 임계 50점에 대응(detection.scoring DEFAULT_ALERT_THRESHOLD)
ATTR_HIGH = 0.50         # 속성 ML 사기확률 높음 기준(운영점 — 필요시 비용/ F1 로 조정)

# 가중합 결합 가중(combine='weighted'). max 가 기본(둘 중 하나라도 강하면 위험).
W_GRAPH = 0.5
W_ATTR = 0.5

# 합성 청구 속성(소문자) → 캐글 컬럼명 매핑. attribute_model 인코더가 캐글 컬럼명을
# 기대하므로 변환한다. 값 어휘는 generator 가 캐글 분포로 샘플링해 호환된다.
_SYNTH_TO_KAGGLE = {
    "fault": "Fault",
    "base_policy": "BasePolicy",
    "vehicle_category": "VehicleCategory",
    "accident_area": "AccidentArea",
    "make": "Make",
    "sex": "Sex",
    "marital_status": "MaritalStatus",
    "agent_type": "AgentType",
    "policy_type": "PolicyType",
    "days_policy_accident": "Days_Policy_Accident",
    "days_policy_claim": "Days_Policy_Claim",
    "past_number_of_claims": "PastNumberOfClaims",
    "age_of_vehicle": "AgeOfVehicle",
    "age_of_policy_holder": "AgeOfPolicyHolder",
    "vehicle_price": "VehiclePrice",
    "deductible": "Deductible",
    "driver_rating": "DriverRating",
    "police_report_filed": "PoliceReportFiled",
    "witness_present": "WitnessPresent",
    "number_of_cars": "NumberOfCars",
    "number_of_suppliments": "NumberOfSuppliments",
    "address_change_claim": "AddressChange_Claim",
    "month_claimed": "MonthClaimed",
}


@dataclass
class ClaimRisk:
    """청구 1건의 듀얼 레이어 위험 — 그래프(조직형)+속성(개인형) 결합."""

    claim_id: str
    customer_id: str
    graph_score: float           # 0~1 (조직형 — 고객 그래프 점수 정규화)
    attr_score: float            # 0~1 (개인형 — 속성 ML 사기확률)
    combined: float              # 0~1 (결합 위험)
    risk_type: str               # ORGANIZED / INDIVIDUAL / BOTH / NONE
    # ground truth(평가용 — 점수 계산엔 미사용)
    fraud_label: bool = False
    ring_pattern: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "customer_id": self.customer_id,
            "graph_score": round(self.graph_score, 4),
            "attr_score": round(self.attr_score, 4),
            "combined": round(self.combined, 4),
            "risk_type": self.risk_type,
            "fraud_label": self.fraud_label,
            "ring_pattern": self.ring_pattern,
        }


def _classify(graph_n: float, attr_n: float) -> str:
    """그래프/속성 점수(정규화)로 유형 라벨을 부여한다."""
    g = graph_n >= GRAPH_HIGH
    a = attr_n >= ATTR_HIGH
    if g and a:
        return "BOTH"
    if g:
        return "ORGANIZED"
    if a:
        return "INDIVIDUAL"
    return "NONE"


def _claim_attr_rows() -> list[dict[str, str]]:
    """전 청구의 (claim_id, customer_id, 캐글호환 속성, ground truth)를 읽는다.

    합성 Claim 노드의 소문자 속성을 캐글 컬럼명으로 변환해 attribute_model 인코더
    입력 형식으로 만든다. ground truth(fraud_label/ring_pattern)는 평가용으로만
    함께 가져온다(속성 점수 계산엔 attribute_model 이 라벨 미사용).
    """
    cols = ", ".join(f"c.{k} AS {k}" for k in _SYNTH_TO_KAGGLE)
    rows = db.run(
        f"""
        MATCH (cust:Customer)-[:FILED]->(c:Claim)
        RETURN c.claim_id AS claim_id, cust.customer_id AS customer_id,
               coalesce(c.fraud_label, false) AS fraud_label,
               coalesce(c.ring_pattern, '') AS ring_pattern,
               {cols}
        ORDER BY c.claim_id
        """
    )
    out: list[dict[str, str]] = []
    for r in rows:
        rec: dict[str, str] = {
            "claim_id": r["claim_id"],
            "customer_id": r["customer_id"],
            "_fraud_label": bool(r["fraud_label"]),
            "_ring_pattern": r["ring_pattern"] or "",
        }
        for synth_col, kaggle_col in _SYNTH_TO_KAGGLE.items():
            rec[kaggle_col] = r.get(synth_col) or ""
        out.append(rec)
    return out


def _graph_scores_by_customer() -> dict[str, float]:
    """고객별 그래프 종합 점수(0~100). detection.scoring(룰+GDS+임베딩)."""
    from detection import scoring

    risks = scoring.score_customers(use_gds=True, use_embedding=True)
    return {cid: r.score for cid, r in risks.items()}


@dataclass
class DualLayerResult:
    """듀얼 레이어 청구 위험 + 유형 라벨 모음."""

    claims: list[ClaimRisk]
    combine: str
    attr_model_kind: str

    def alerts(self, threshold: float = 0.5) -> list[ClaimRisk]:
        """결합 위험 임계 이상 청구를 위험 내림차순으로 반환."""
        flagged = [c for c in self.claims if c.combined >= threshold]
        return sorted(flagged, key=lambda c: c.combined, reverse=True)


def score_dual_layer(
    *,
    model_kind: str = "ens",
    combine: str = "max",
) -> DualLayerResult:
    """전 청구에 그래프(조직형)+속성(개인형) 점수를 산출·결합한다.

    Args:
        model_kind: 속성 ML 모델('lr'/'rf'/'gb'). 캐글로 학습된다.
        combine: 결합 방식 — 'max'(둘 중 강한 신호) 또는 'weighted'(가중합).

    Returns:
        ``DualLayerResult`` — 청구별 ClaimRisk(그래프/속성/결합 + 유형 라벨).
    """
    from detection import attribute_model as attrmod

    # 레이어 B — 캐글로 속성 ML 학습(합성 라벨 미사용 — 도메인 전이, 자기누수 없음).
    amodel = attrmod.train_attribute_model(model_kind=model_kind)

    # 레이어 A — 고객 그래프 점수.
    graph_by_cust = _graph_scores_by_customer()

    # 청구 속성 행 + ground truth.
    attr_rows = _claim_attr_rows()
    # 속성 ML 점수(배치). 인코더가 미지/누락은 0/중앙값 처리(graceful).
    kaggle_inputs = [{k: r[k] for k in _SYNTH_TO_KAGGLE.values()} for r in attr_rows]
    attr_proba = amodel.score_rows(kaggle_inputs)

    claims: list[ClaimRisk] = []
    for r, p in zip(attr_rows, attr_proba):
        cid = r["customer_id"]
        graph_raw = graph_by_cust.get(cid, 0.0)
        graph_n = min(max(graph_raw / GRAPH_SCORE_MAX, 0.0), 1.0)
        attr_n = float(min(max(p, 0.0), 1.0))

        if combine == "weighted":
            combined = W_GRAPH * graph_n + W_ATTR * attr_n
        else:  # max
            combined = max(graph_n, attr_n)

        claims.append(ClaimRisk(
            claim_id=r["claim_id"],
            customer_id=cid,
            graph_score=graph_n,
            attr_score=attr_n,
            combined=float(min(max(combined, 0.0), 1.0)),
            risk_type=_classify(graph_n, attr_n),
            fraud_label=bool(r["_fraud_label"]),
            ring_pattern=str(r["_ring_pattern"]),
        ))

    return DualLayerResult(claims=claims, combine=combine, attr_model_kind=model_kind)


# ==================================================================
# 검증 — 조직형(ring) vs 개인형(opportunistic) vs 통합 재현율
# ==================================================================
@dataclass
class RecallBreakdown:
    """청구 단위 재현율 분해(레이어별·유형별)."""

    total_fraud: int
    organized_fraud: int             # ring 공모형 사기 청구 수
    individual_fraud: int            # opportunistic 개인형 사기 청구 수
    # 레이어별 적발(결합 임계 기준)
    graph_only_recall_org: float     # 그래프 단독이 조직형 사기 잡는 비율
    graph_only_recall_ind: float     # 그래프 단독이 개인형 사기 잡는 비율
    attr_only_recall_org: float      # 속성 단독이 조직형 사기 잡는 비율
    attr_only_recall_ind: float      # 속성 단독이 개인형 사기 잡는 비율
    dual_recall_org: float           # 듀얼(결합)이 조직형 잡는 비율
    dual_recall_ind: float           # 듀얼(결합)이 개인형 잡는 비율
    dual_recall_total: float         # 듀얼 전체 재현율
    graph_recall_total: float        # 그래프 단독 전체 재현율
    attr_recall_total: float         # 속성 단독 전체 재현율
    # 정밀도(전체 사기 기준 결합 임계)
    dual_precision: float
    dual_alerts: int
    threshold: float


_ORGANIZED_PATTERNS = {
    "fake_admission_star", "collision_ring", "repair_overbill",
    "agent_fraud", "driver_swap",
}


def measure_recall(
    res: DualLayerResult, *, threshold: float = 0.5, graph_thr: float | None = None,
    attr_thr: float | None = None,
) -> RecallBreakdown:
    """조직형/개인형/통합 재현율을 측정한다(ground truth ring_pattern 기준).

    Args:
        res: 듀얼 레이어 점수 결과.
        threshold: 결합 위험 판정 임계(0~1).
        graph_thr: 그래프 단독 판정 임계(미지정 시 GRAPH_HIGH).
        attr_thr: 속성 단독 판정 임계(미지정 시 결합 threshold).

    Returns:
        ``RecallBreakdown``.
    """
    gthr = GRAPH_HIGH if graph_thr is None else graph_thr
    athr = threshold if attr_thr is None else attr_thr

    fraud = [c for c in res.claims if c.fraud_label]
    org = [c for c in fraud if c.ring_pattern in _ORGANIZED_PATTERNS]
    ind = [c for c in fraud if c.ring_pattern == "opportunistic"]

    def _recall(items: list[ClaimRisk], pred) -> float:
        if not items:
            return 0.0
        hit = sum(1 for c in items if pred(c))
        return hit / len(items)

    graph_pred = lambda c: c.graph_score >= gthr  # noqa: E731
    attr_pred = lambda c: c.attr_score >= athr    # noqa: E731
    dual_pred = lambda c: c.combined >= threshold  # noqa: E731

    # 정밀도(결합 임계 기준 전체).
    alerts = [c for c in res.claims if dual_pred(c)]
    tp = sum(1 for c in alerts if c.fraud_label)
    precision = tp / len(alerts) if alerts else 0.0

    return RecallBreakdown(
        total_fraud=len(fraud),
        organized_fraud=len(org),
        individual_fraud=len(ind),
        graph_only_recall_org=_recall(org, graph_pred),
        graph_only_recall_ind=_recall(ind, graph_pred),
        attr_only_recall_org=_recall(org, attr_pred),
        attr_only_recall_ind=_recall(ind, attr_pred),
        dual_recall_org=_recall(org, dual_pred),
        dual_recall_ind=_recall(ind, dual_pred),
        dual_recall_total=_recall(fraud, dual_pred),
        graph_recall_total=_recall(fraud, graph_pred),
        attr_recall_total=_recall(fraud, attr_pred),
        dual_precision=precision,
        dual_alerts=len(alerts),
        threshold=threshold,
    )


def _type_distribution(res: DualLayerResult) -> dict[str, dict[str, int]]:
    """유형 라벨(ORGANIZED/INDIVIDUAL/BOTH/NONE) × 사기/정상 분포."""
    out: dict[str, dict[str, int]] = {}
    for c in res.claims:
        d = out.setdefault(c.risk_type, {"fraud": 0, "normal": 0})
        d["fraud" if c.fraud_label else "normal"] += 1
    return out


# ==================================================================
# 리포트
# ==================================================================
def _print_report(res: DualLayerResult, rb: RecallBreakdown) -> None:
    line = "=" * 80
    print(line)
    print(" THOTH-ON 듀얼 레이어 (조직형 그래프 + 개인형 속성) — 한국형 합성 재측정")
    print(line)
    print(f"  결합 방식       : {res.combine}  (그래프+속성 청구 단위 결합)")
    print(f"  속성 모델       : {res.attr_model_kind}  (캐글 fraud_oracle 학습 — 도메인 전이)")
    print(f"  결합 임계       : {rb.threshold:.2f}")
    print(f"  청구 총수       : {len(res.claims):,}")
    print(f"  사기 청구       : {rb.total_fraud:,}  "
          f"(조직형 ring {rb.organized_fraud} + 개인형 opportunistic {rb.individual_fraud})")
    print("-" * 80)
    print(" 재현율 분해 — 그래프 단독 vs 속성 단독 vs 듀얼(결합)")
    print("-" * 80)
    print(f"  {'유형':<22}{'그래프단독':>14}{'속성단독':>14}{'듀얼결합':>14}")
    print(f"  {'조직형(ring)':<20}{rb.graph_only_recall_org:>14.3f}"
          f"{rb.attr_only_recall_org:>14.3f}{rb.dual_recall_org:>14.3f}")
    print(f"  {'개인형(opportunistic)':<16}{rb.graph_only_recall_ind:>14.3f}"
          f"{rb.attr_only_recall_ind:>14.3f}{rb.dual_recall_ind:>14.3f}")
    print(f"  {'통합(전체 사기)':<18}{rb.graph_recall_total:>14.3f}"
          f"{rb.attr_recall_total:>14.3f}{rb.dual_recall_total:>14.3f}")
    print("-" * 80)
    print(f"  듀얼 결합 정밀도 : {rb.dual_precision:.3f}  "
          f"(알림 {rb.dual_alerts:,}건 중 사기 {int(rb.dual_precision * rb.dual_alerts)})")
    print("-" * 80)
    print(" 핵심 — 그래프 한계 보완(개인형):")
    print(f"   · 그래프 단독 개인형 재현율 : {rb.graph_only_recall_ind:.3f} "
          f"(공모 네트워크 없어 구조적으로 못 잡음)")
    print(f"   · 속성 레이어 개인형 재현율 : {rb.attr_only_recall_ind:.3f} "
          f"(캐글 학습 개인 사기 신호로 회수)")
    print(f"   · 듀얼 통합 재현율          : {rb.dual_recall_total:.3f} "
          f"vs 그래프 단독 {rb.graph_recall_total:.3f} "
          f"(Δ{rb.dual_recall_total - rb.graph_recall_total:+.3f})")
    print("-" * 80)

    # 유형 라벨 분포.
    print(" 유형 라벨 분포 (청구 단위) — risk_type × 사기/정상")
    print("-" * 80)
    dist = _type_distribution(res)
    print(f"  {'유형':<14}{'사기':>10}{'정상':>10}{'합계':>10}{'사기율':>10}")
    for t in ("BOTH", "ORGANIZED", "INDIVIDUAL", "NONE"):
        d = dist.get(t, {"fraud": 0, "normal": 0})
        tot = d["fraud"] + d["normal"]
        rate = d["fraud"] / tot if tot else 0.0
        print(f"  {t:<14}{d['fraud']:>10}{d['normal']:>10}{tot:>10}{rate:>10.3f}")
    print(line)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="THOTH-ON 듀얼 레이어 (조직형 그래프 + 개인형 속성)")
    p.add_argument("--model", default="ens", choices=["lr", "rf", "gb", "hgb", "ens"],
                   help="속성 ML 모델(lr/rf/gb/hgb/ens) — 기본 ens(GB+HGB+RF 앙상블)")
    p.add_argument("--combine", default="max", choices=["max", "weighted"],
                   help="그래프+속성 결합 방식")
    p.add_argument("--threshold", type=float, default=0.5,
                   help="결합 위험 판정 임계(0~1)")
    args = p.parse_args(argv)

    if not db.healthcheck():
        print("Neo4j 미가용 — `make up && make wait-neo4j` 후 재실행")
        return 1
    try:
        from detection import attribute_model as attrmod
        if not attrmod._SKLEARN:
            print("scikit-learn 미설치 — `.venv/bin/pip install scikit-learn` 후 재실행")
            return 1
    except Exception:
        print("detection.attribute_model 임포트 실패")
        return 1

    res = score_dual_layer(model_kind=args.model, combine=args.combine)
    rb = measure_recall(res, threshold=args.threshold)
    _print_report(res, rb)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
