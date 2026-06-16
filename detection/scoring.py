"""리스크 스코어링 (WP2 · FR-3.5 / FR-5.1 준비).

Q1(공유 엔티티)·Q2(핫스팟)·Q3(crash-for-cash 순환) 신호를 고객 단위로 모아
0~100 리스크 스코어를 가중합으로 산출한다. 각 점수에는 **기여 신호 목록**을
부착하여(설명가능성, FR-5.1) "왜 의심인지"를 근거 경로로 제시한다.

[가중치 설계 근거 — data/synthetic_test 분포 측정]
    · 동일 계좌 공유(Q1-ACCOUNT)와 상호 교차 목격(Q3)은 정상 배경이 0건인
      매우 강한 링 신호 → 높은 가중치.
    · 동일 전화/차량 공유도 강한 신호(정상 배경 0건).
    · 주소 공유(Q1-ADDRESS)는 배경 노이즈가 커(2인 공유 200건+) 약한 신호 →
      낮은 가중치. 단독으로는 임계치를 넘지 못하도록 설계.
    · 핫스팟(Q2)은 정상 대형 병원도 다수 → 약한 corroborating 신호. 단,
      그 핫스팟 엔티티를 "공유 계좌/교차 목격 고객들이 함께" 이용하면 가중.

설계 목표(AC): 정상 고객 ≈ 0점, 링 멤버는 고점으로 분리.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from detection import detect

# ------------------------------------------------------------------
# 신호별 가중치 (가산점). 합산 후 100 으로 상한.
# ------------------------------------------------------------------
W_SHARED_ACCOUNT = 45.0   # 동일 계좌 공유 (강)
W_SHARED_PHONE = 35.0     # 동일 전화 공유 (강)
W_SHARED_VEHICLE = 35.0   # 동일 차량 공유 (강)
W_SHARED_ADDRESS = 6.0    # 동일 주소 공유 (약 — 배경 노이즈 큼)
W_CROSS_WITNESS = 45.0    # 상호 교차 목격 (강 — 링 핵심)
W_HOTSPOT = 8.0           # 핫스팟 엔티티 이용 (약 — corroborating)

# 공유 군집/순환의 규모가 클수록 가산(군집 크기 비례 보너스, 상한 있음).
CLUSTER_SIZE_BONUS = 3.0  # (군집 고객 수 - 2) 당 가산점
CLUSTER_SIZE_BONUS_CAP = 15.0

SCORE_CAP = 100.0
DEFAULT_ALERT_THRESHOLD = 50.0  # 이 점수 이상이면 알림 플래그


@dataclass
class CustomerRisk:
    """고객 1명의 리스크 스코어와 기여 신호(설명가능성 근거)."""

    customer_id: str
    score: float = 0.0
    is_fraud_ring: bool = False  # ground truth (평가용, 점수 계산에는 미사용)
    ring_id: str = ""
    signals: list[dict[str, Any]] = field(default_factory=list)

    @property
    def alerted(self) -> bool:
        return self.score >= DEFAULT_ALERT_THRESHOLD

    def add_signal(self, signal_type: str, weight: float, detail: dict[str, Any]) -> None:
        """기여 신호 1건을 추가하고 점수를 가산한다."""
        self.score = min(SCORE_CAP, self.score + weight)
        self.signals.append({"type": signal_type, "weight": weight, **detail})

    def to_dict(self) -> dict[str, Any]:
        return {
            "customer_id": self.customer_id,
            "score": round(self.score, 1),
            "alerted": self.alerted,
            "is_fraud_ring": self.is_fraud_ring,
            "ring_id": self.ring_id,
            "signals": self.signals,
        }


# 신호 종류별 가중치 매핑 (Q1 shared_type → weight)
_SHARED_WEIGHT = {
    "ACCOUNT": W_SHARED_ACCOUNT,
    "PHONE": W_SHARED_PHONE,
    "VEHICLE": W_SHARED_VEHICLE,
    "ADDRESS": W_SHARED_ADDRESS,
}


def _cluster_bonus(cluster_size: int) -> float:
    """군집 크기 비례 보너스(상한 적용)."""
    if cluster_size <= 2:
        return 0.0
    return min(CLUSTER_SIZE_BONUS_CAP, (cluster_size - 2) * CLUSTER_SIZE_BONUS)


def score_customers(
    *,
    min_customers: int = detect.DEFAULT_MIN_CUSTOMERS,
    include_address: bool = True,
    alert_threshold: float = DEFAULT_ALERT_THRESHOLD,
) -> dict[str, CustomerRisk]:
    """전 고객 리스크 스코어 산출 (FR-3.5).

    Q1~Q3 탐지 결과를 고객 단위로 집계해 가중합 점수와 기여 신호를 만든다.
    탐지 신호가 전혀 없는 고객은 결과 dict 에 등장하지 않으며 점수 0 으로 본다.

    Args:
        min_customers: Q1 공유 최소 고객 수.
        include_address: 주소 공유(약 신호) 포함 여부.
        alert_threshold: 알림 플래그 임계치(모듈 전역 기본을 덮어쓰지 않고
            CustomerRisk.alerted 판정에 사용하려면 점수만 보면 됨; 본 함수는
            반환 dict 의 점수로 판정 가능하도록 임계치를 신호에 기록).

    Returns:
        ``{customer_id: CustomerRisk}`` 매핑. 신호가 있는 고객만 포함.
    """
    risks: dict[str, CustomerRisk] = {}

    def _get(cid: str, ring_id: str = "") -> CustomerRisk:
        r = risks.get(cid)
        if r is None:
            r = CustomerRisk(customer_id=cid, ring_id=ring_id)
            risks[cid] = r
        return r

    # --- Q1 공유 엔티티 ---
    for grp in detect.run_shared_entities(
        min_customers=min_customers, include_address=include_address
    ):
        stype = grp["shared_type"]
        weight = _SHARED_WEIGHT.get(stype, 0.0)
        cids = grp["customer_ids"]
        bonus = _cluster_bonus(grp["num_customers"])
        peers_by_cid = {c: [x for x in cids if x != c] for c in cids}
        for cid in cids:
            r = _get(cid)
            r.add_signal(
                f"SHARED_{stype}",
                weight + bonus,
                {
                    "shared_key": grp["shared_key"],
                    "num_customers": grp["num_customers"],
                    "shared_with": peers_by_cid[cid],
                },
            )

    # --- Q3 crash-for-cash 순환 (상호 교차 목격) ---
    for cluster in detect.run_crash_rings():
        members = cluster["members"]
        seed = cluster["seed_customer"]
        bonus = _cluster_bonus(cluster["cluster_size"])
        r = _get(seed, ring_id=cluster.get("ring_id", ""))
        r.add_signal(
            "CROSS_WITNESS",
            W_CROSS_WITNESS + bonus,
            {
                "cluster_size": cluster["cluster_size"],
                "witnessed_with": [m for m in members if m != seed],
            },
        )

    # --- Q2 핫스팟 (corroborating) ---
    #   핫스팟 엔티티를 이용한 고객에게 약한 가산점. 단독으로는 임계 미달.
    for hs in detect.run_hotspots():
        for cid in hs["customer_ids"]:
            r = _get(cid)
            r.add_signal(
                f"HOTSPOT_{hs['entity_type']}",
                W_HOTSPOT,
                {
                    "entity_id": hs["entity_id"],
                    "entity_name": hs.get("entity_name"),
                    "num_customers": hs["num_customers"],
                },
            )

    # ground truth 라벨 부착 (평가/검증용 — 점수 계산에는 미사용)
    _attach_ground_truth(risks)

    # 알림 임계치를 각 risk 에 반영(전역 기본과 다를 수 있으므로)
    if alert_threshold != DEFAULT_ALERT_THRESHOLD:
        for r in risks.values():
            r.signals.append({"type": "_alert_threshold", "value": alert_threshold})

    return risks


def _attach_ground_truth(risks: dict[str, CustomerRisk]) -> None:
    """점수가 매겨진 고객의 ground truth 라벨(is_fraud_ring/ring_id)을 채운다."""
    if not risks:
        return
    from thoth import db

    ids = list(risks.keys())
    rows = db.run(
        """
        MATCH (c:Customer)
        WHERE c.customer_id IN $ids
        RETURN c.customer_id AS cid,
               coalesce(c.is_fraud_ring, false) AS is_fraud_ring,
               coalesce(c.ring_id, '') AS ring_id
        """,
        ids=ids,
    )
    for row in rows:
        r = risks.get(row["cid"])
        if r is not None:
            r.is_fraud_ring = bool(row["is_fraud_ring"])
            r.ring_id = row["ring_id"] or r.ring_id


def alerts(
    risks: dict[str, CustomerRisk],
    *,
    threshold: float = DEFAULT_ALERT_THRESHOLD,
) -> list[CustomerRisk]:
    """임계치 이상 고객을 점수 내림차순으로 반환(알림 큐)."""
    flagged = [r for r in risks.values() if r.score >= threshold]
    return sorted(flagged, key=lambda r: r.score, reverse=True)
