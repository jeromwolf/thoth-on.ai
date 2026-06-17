"""리스크 스코어링 (WP2 · FR-3.5 / FR-5.1) — 정밀도 회복판.

Q1(공유 엔티티)·Q2(집중 핫스팟)·Q3(crash-for-cash 순환) 신호를 고객 단위로
모아 0~100 리스크 스코어를 산출한다. 각 점수에는 **기여 신호 목록**을 부착해
(설명가능성, FR-5.1) "왜 의심인지"를 근거 경로로 제시한다.

[정밀도 회복 설계 — 단순 "공유=의심"의 오탐을 제거]
    이전 버전은 공유 엔티티에 고정 가중치를 줘서, 같은 계좌/주소를 쓰는 정상
    가족(소수·같은 주소·장기 보유)까지 사기와 같은 점수를 받아 오탐이 폭증했다
    (정상 426명 오탐). data/synthetic_test 실측으로 다음 구분축을 도입한다:

    1) 정상 공유 구분 (가족 vs 사기):
       · 가족: 같은 계좌/전화/차량 + **같은 주소**(distinct_addr < n) + 청구가
         연중 분산(time_span ≈ 262일). → 약/감점 신호.
       · 사기: 같은 계좌 공유 + **서로 다른 주소**(distinct_addr = n, 무관한 다수)
         + 청구가 짧은 기간 집중(time_span ≈ 11일). → 강 신호.
       · 주소 공유 자체(SHARED_ADDRESS)는 가족 배경이 압도적 → 거의 0점.

    2) 핫스팟 baseline 정규화:
       · 인기 대형 병원/정비소의 정상 운영량(단순 건수)은 신호로 보지 않는다.
       · 비인기 (병원+정비소) 쌍을 소수 고객이 짧은 기간 함께 이용하는 "집중
         핫스팟"만 corroborating 약 신호로 가산(detect.run_focused_hotspots).

    3) 복수 신호 요구(핵심):
       · 단일 신호(계좌만/목격만)는 임계 부근 이하로 억제.
       · 서로 다른 신호 2종 이상 동시 충족 시 급가점(MULTI_SIGNAL_BONUS).
         사기 링은 여러 신호가 겹친다는 특성을 이용해 정밀도를 끌어올린다.

    4) 시간 군집 신호:
       · 공유 군집 청구가 짧은 기간에 집중되면(crash-for-cash 동시 청구)
         해당 공유 신호를 강화한다(시간 군집 보너스).

설계 목표(AC): 정상 가족 ≈ 저점(임계 미달), 링 멤버는 고점으로 분리.
기여 신호 목록·환각가드 입력 형식(case 상세·소명문)은 그대로 유지한다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from detection import detect

# ------------------------------------------------------------------
# 공유 엔티티 신호 — 가구(household) 인지 가중치.
#   강(强) : 서로 다른 주소의 무관한 다수가 같은 계좌/전화/차량을 공유.
#   약(弱) : 같은 주소의 소수(가족)가 공유 — 정상 배경이 압도적.
# 단일 강신호 1개로는 임계(50)를 넘지 못하게 설계하고(MULTI_SIGNAL 로만 급가점),
# 시간 군집/군집 규모 보너스로 사기 특성을 강화한다.
# ------------------------------------------------------------------
# [핵심 — 시간 군집이 1차 판별축]
#   같은 계좌/전화/차량을 "서로 다른 주소"의 다수가 공유하더라도, 청구가 연중
#   분산된 경우(가족이 이사·분가했으나 대표 계좌/전화 유지)는 정상일 수 있다
#   (실측: 정상 FP 가족 span 170~313일). 사기 링은 청구가 짧은 기간 집중된다
#   (실측 span <= 18일). 따라서 강 공유 신호의 **기본 가중치는 낮추고**, 시간
#   군집일 때만 결정적 가산을 주어 두 same-group 공유(계좌+전화)의 단순 합으로는
#   임계(50)를 넘지 못하게 한다.
W_SHARED_ACCOUNT_STRONG = 22.0   # 다른 주소 동일 계좌 공유 (강, 시간군집 아니면 중)
W_SHARED_ACCOUNT_FAMILY = 4.0    # 같은 주소(가족) 동일 계좌 공유 (약 — 정상)
W_SHARED_PHONE_STRONG = 18.0     # 다른 주소 동일 전화 공유 (강)
W_SHARED_PHONE_FAMILY = 3.0      # 같은 주소(가족) 동일 전화 (약)
W_SHARED_VEHICLE_STRONG = 18.0   # 다른 주소 동일 차량 공유 (강 — 명의도용/대포차)
W_SHARED_VEHICLE_FAMILY = 3.0    # 같은 주소(가족) 공동명의 차량 (약)
W_SHARED_ADDRESS = 2.0           # 주소 공유 자체 (거의 0 — 가족 노이즈 압도적)

W_CROSS_WITNESS = 50.0           # 상호 교차 목격 (강 — 정상 배경 0건이라 단독 알림 가능)
W_FOCUSED_HOTSPOT = 14.0         # 집중 핫스팟(비인기 병원+정비소·소수·시간군집) corroborating
W_FOCUSED_HOTSPOT_CAP = 14.0     # 집중 핫스팟 누적 상한(단독 임계 미달 보장)
# 담합 핫스팟 — 동일 병원+정비소·소수·매우 짧은 기간·단일 collision (정밀 ~81%).
# 약신호 링(hotspot_only/weak) 회수용 고정밀 신호로 단독 알림 가능 수준 가중.
W_COLLUSION_HOTSPOT = 50.0
W_COLLUSION_HOTSPOT_CAP = 50.0   # 군집 중복 가산 방지(고객당 1회 분량)

# 시간 군집 보너스 — 공유 군집 청구가 짧은 기간에 집중될수록 결정적 가산
# (crash-for-cash 동시 청구). 강 공유 신호이며 time_span<=임계일 때만 적용.
W_TIME_CLUSTER_BONUS = 26.0
TIME_CLUSTER_DAYS = detect.DEFAULT_TIME_CLUSTER_DAYS  # span <= 30일 이면 "시간 군집"

# 복수 신호 보너스(핵심) — 서로 다른 신호 "그룹" 2종 이상 동시 충족 시 급가점.
#   신호 그룹: SHARED(공유 엔티티 강신호) / WITNESS(교차목격) / HOTSPOT(집중 핫스팟).
#   사기 링은 여러 그룹이 겹치고, 정상 가족은 보통 한 그룹(공유)만 가진다.
#   단, HOTSPOT(집중 핫스팟)은 정상 배경이 큰 noisy 신호이므로, 이를 포함한
#   조합에는 약한 보너스만 준다(정상 가족이 우연히 같은 병원+정비소를 쓴 경우의
#   오탐 방지). 고신뢰 그룹(SHARED-강 / WITNESS) 2종이면 강한 보너스.
_HIGH_CONFIDENCE_GROUPS = {"SHARED", "WITNESS", "EMBED", "HUB", "STAR"}
MULTI_SIGNAL_BONUS_2 = 24.0          # 고신뢰 그룹 2종
MULTI_SIGNAL_BONUS_3 = 40.0          # 고신뢰 그룹 3종 이상(거의 확실)
MULTI_SIGNAL_BONUS_HOTSPOT = 8.0     # HOTSPOT 포함 조합(약 corroborating)

# ------------------------------------------------------------------
# WP-KR 한국 실제 사기 수법 신호 — 허위입원 star / 브로커·설계사 허브 / 정비비 과다.
#   star/허브 신호는 정상 배경(인기 병원·정상 설계사)을 detect 단계에서 배제하므로
#   고정밀이지만, 단독으로 임계를 넘기보다 corroborating 으로 두고 복수신호와
#   결합해 정밀도를 보호한다. 단, 브로커 허브·설계사 허브는 정상 배경이 detect 에서
#   걸러져 정밀도가 높아 단독 알림 가능 수준으로 둔다.
# ------------------------------------------------------------------
# 병원 단독 burst(star)는 정상 트래픽 균등으로 분리 한계가 있어 corroborating 약신호.
#   허위입원의 주 신호는 브로커 허브(브로커→한 병원 집중, 정밀 ~1.0)다.
W_FAKE_ADMISSION_STAR = 16.0     # 허위입원 star 병원 burst — corroborating 약신호
W_FAKE_ADMISSION_STAR_CAP = 16.0
W_BROKER_HUB = 52.0              # 브로커 알선 허브(한 병원 집중) — 강(단독 알림 가능)
W_AGENT_HUB = 52.0              # 설계사 가로채기 허브 — 강(단독 알림 가능)
W_REPAIR_OVERBILL = 50.0         # 정비비 과다청구(정비소 허브+금액 이상) — 강

# GDS 신호 (WP3 · FR-3.4) — 선택적 corroborating 가산점(룰 신호 대체 아님).
W_GDS_COMMUNITY = 18.0           # 다수 멤버 Louvain 커뮤니티 소속 (구조적)
W_GDS_PAGERANK = 4.0             # 상대적으로 높은 PageRank corroborating

# 그래프 임베딩/비지도 이상탐지 신호 (WP3 · FR-3.6) — detection.embedding.
#   CLIQUE: 고객-고객 유사 그래프(SIMILAR_TO)에서 차수>=2(3+ 클리크) — 라벨 미사용
#   비지도 신호. 실측 정밀 ~0.81 로 weak/hotspot_only 수법(룰 미탐)을 회수한다.
#   약신호 링 멤버(룰 점수 0~16)를 단독으로 임계 근처까지 끌어올리되, 정밀도
#   보호를 위해 단독 임계(50) 직하로 설계하고, 복수신호(다른 룰 신호와 겹치면)
#   보너스로 확실히 넘기게 한다. 정상 클리크 혼입(가족·우연 동행)은 보통 다른
#   강 신호가 없어 임계 미달로 남는다.
# 가중치 48 은 실측 튜닝 결과(임계 50 기준 F1 최대 운영점):
#   · 클리크 사기 멤버(특히 weak·hotspot_only)는 룰 점수가 매우 낮아(0~16) 이
#     가산으로 단독 임계(50)를 넘어 회수된다.
#   · 정상 클리크 혼입(가족·우연 동행)은 18~24명 수준으로 제한적이며, 임계 50
#     기준 FP 증가는 +15 내외(정밀 0.91→0.81)로 균형. 가중치를 더 높여도(>=50)
#     추가 사기는 안 잡히고 정상만 더 넘어 정밀도만 하락 → 48 이 운영 최적.
#   PAIR(차수==1)은 정밀 ~0.06 으로 noisy → 신호로 쓰지 않는다(정밀도 보호).
W_EMBED_CLIQUE = 48.0            # SIMILAR_TO 3+ 클리크 멤버 (강 — 약신호 링 회수)

# 공유 군집/순환 규모가 클수록 가산(상한 있음).
CLUSTER_SIZE_BONUS = 3.0
CLUSTER_SIZE_BONUS_CAP = 12.0

SCORE_CAP = 100.0
DEFAULT_ALERT_THRESHOLD = 50.0   # 이 점수 이상이면 알림 플래그

# 가구(가족) 판정 — 공유 군집이 "같은 주소의 소수"이면 가족으로 본다.
#   distinct_addresses < num_customers  : 적어도 일부가 같은 주소를 공유(가족적).
#   num_customers <= FAMILY_MAX_SIZE    : 소수 인원(대규모 무관 공유는 가족 아님).
FAMILY_MAX_SIZE = 6

# 복수 신호 보너스 산정에 쓰는 "강신호 그룹" 분류.
_GROUP_SHARED = "SHARED"
_GROUP_WITNESS = "WITNESS"
_GROUP_HOTSPOT = "HOTSPOT"
_GROUP_EMBED = "EMBED"   # 임베딩 비지도 클리크(FR-3.6) — 구조적 고신뢰 그룹
_GROUP_HUB = "HUB"       # WP-KR 브로커/설계사 허브(조직형 사기) — 고신뢰
_GROUP_STAR = "STAR"     # WP-KR 허위입원 star(병원 환자 집중) — 고신뢰


@dataclass
class CustomerRisk:
    """고객 1명의 리스크 스코어와 기여 신호(설명가능성 근거)."""

    customer_id: str
    score: float = 0.0
    is_fraud_ring: bool = False  # ground truth (평가용, 점수 계산에는 미사용)
    ring_id: str = ""
    signals: list[dict[str, Any]] = field(default_factory=list)
    _hotspot_total: float = 0.0          # 집중 핫스팟 누적(상한 적용용)
    _collusion_total: float = 0.0        # 담합 핫스팟 누적(상한 적용용)
    _signal_groups: set[str] = field(default_factory=set)  # 충족한 강신호 그룹
    _multi_applied: bool = False         # 복수 신호 보너스 1회 적용 여부

    @property
    def alerted(self) -> bool:
        return self.score >= DEFAULT_ALERT_THRESHOLD

    def _bump(self, weight: float) -> float:
        """점수 가산(상한 적용)하고 실제 반영된 양을 반환."""
        before = self.score
        self.score = min(SCORE_CAP, self.score + weight)
        return self.score - before

    def add_signal(
        self,
        signal_type: str,
        weight: float,
        detail: dict[str, Any],
        *,
        group: str | None = None,
    ) -> None:
        """기여 신호 1건을 추가하고 점수를 가산한다.

        집중 핫스팟(corroborating 약신호)은 고객당 누적 상한을 적용한다.
        ``group`` 이 주어지면 복수 신호 보너스 산정용 강신호 그룹으로 등록한다.
        """
        if signal_type.startswith("FOCUSED_HOTSPOT") or \
                signal_type.startswith("FAKE_ADMISSION_STAR"):
            remaining = max(0.0, W_FOCUSED_HOTSPOT_CAP - self._hotspot_total)
            weight = min(weight, remaining)
            self._hotspot_total += weight
            if weight <= 0.0:
                return  # 상한 도달 — 가산/기록 생략
        elif signal_type.startswith("COLLUSION_HOTSPOT"):
            remaining = max(0.0, W_COLLUSION_HOTSPOT_CAP - self._collusion_total)
            weight = min(weight, remaining)
            self._collusion_total += weight
            if weight <= 0.0:
                return

        self._bump(weight)
        self.signals.append({"type": signal_type, "weight": round(weight, 1), **detail})
        if group:
            self._signal_groups.add(group)

    def apply_multi_signal_bonus(self) -> None:
        """서로 다른 강신호 그룹 2종 이상 동시 충족 시 급가점(1회).

        사기 링은 공유+목격+핫스팟 등 여러 그룹이 겹친다. 정상 가족은 보통
        한 그룹(약한 공유)만 가지므로 보너스를 받지 못해 임계 미달로 남는다.
        """
        if self._multi_applied:
            return
        if len(self._signal_groups) < 2:
            return  # 단일 그룹 — 보너스 없음(정상 가족의 다중 공유 억제)

        # 고신뢰 그룹(공유 강신호 / 교차목격) 수를 우선 본다. 2종 이상이면 강한
        # 보너스. 고신뢰가 1종뿐이고 나머지가 noisy HOTSPOT 이면 약한 보너스만.
        high = self._signal_groups & _HIGH_CONFIDENCE_GROUPS
        if len(high) >= 3:
            bonus = MULTI_SIGNAL_BONUS_3
        elif len(high) >= 2:
            bonus = MULTI_SIGNAL_BONUS_2
        else:
            bonus = MULTI_SIGNAL_BONUS_HOTSPOT
        applied = self._bump(bonus)
        self.signals.append({
            "type": "MULTI_SIGNAL",
            "weight": round(applied, 1),
            "signal_groups": sorted(self._signal_groups),
            "num_groups": len(self._signal_groups),
            "high_confidence_groups": sorted(high),
        })
        self._multi_applied = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "customer_id": self.customer_id,
            "score": round(self.score, 1),
            "alerted": self.alerted,
            "is_fraud_ring": self.is_fraud_ring,
            "ring_id": self.ring_id,
            "signals": self.signals,
        }


def _cluster_bonus(cluster_size: int) -> float:
    """군집 크기 비례 보너스(상한 적용)."""
    if cluster_size <= 2:
        return 0.0
    return min(CLUSTER_SIZE_BONUS_CAP, (cluster_size - 2) * CLUSTER_SIZE_BONUS)


def _is_family_share(num_customers: int, distinct_addresses: int) -> bool:
    """공유 군집이 정상 가구(가족)로 보이는지 판정.

    같은 주소를 (일부라도) 공유하는 소수 인원이면 가족적 공유로 본다.
    사기 링은 서로 다른 주소의 무관한 다수가 같은 계좌를 쓰므로 여기 해당 안 됨.
    """
    if num_customers > FAMILY_MAX_SIZE:
        return False
    # distinct_addresses < num_customers → 적어도 두 명이 같은 주소(가족적).
    return distinct_addresses < num_customers


def _shared_weight(
    shared_type: str,
    *,
    is_family: bool,
) -> tuple[float, str | None]:
    """공유 신호의 가중치와 강신호 그룹을 반환.

    Returns:
        (weight, group). group 이 None 이면 약 신호(복수신호 보너스 미집계).
    """
    if shared_type == "ACCOUNT":
        return (W_SHARED_ACCOUNT_FAMILY, None) if is_family else \
               (W_SHARED_ACCOUNT_STRONG, _GROUP_SHARED)
    if shared_type == "PHONE":
        return (W_SHARED_PHONE_FAMILY, None) if is_family else \
               (W_SHARED_PHONE_STRONG, _GROUP_SHARED)
    if shared_type == "VEHICLE":
        return (W_SHARED_VEHICLE_FAMILY, None) if is_family else \
               (W_SHARED_VEHICLE_STRONG, _GROUP_SHARED)
    if shared_type == "ADDRESS":
        return (W_SHARED_ADDRESS, None)  # 항상 약 신호(가족 노이즈 압도)
    return (0.0, None)


def score_customers(
    *,
    min_customers: int = detect.DEFAULT_MIN_CUSTOMERS,
    include_address: bool = True,
    alert_threshold: float = DEFAULT_ALERT_THRESHOLD,
    use_gds: bool = False,
    use_embedding: bool = False,
) -> dict[str, CustomerRisk]:
    """전 고객 리스크 스코어 산출 (FR-3.5).

    Q1~Q3 탐지 결과를 고객 단위로 집계해 가중합 점수와 기여 신호를 만든다.
    탐지 신호가 전혀 없는 고객은 결과 dict 에 등장하지 않으며 점수 0 으로 본다.

    Args:
        min_customers: Q1 공유 최소 고객 수.
        include_address: 주소 공유(약 신호) 포함 여부.
        alert_threshold: 알림 판정 임계치(반환 dict 의 점수로 판정 가능하도록
            전역과 다르면 신호에 기록).
        use_gds: True 면 GDS 군집(Louvain)·중심성(PageRank) 신호를 corroborating
            가산점으로 반영(WP3 · FR-3.4). 속성이 없으면 자동 생략.
        use_embedding: True 면 그래프 임베딩/비지도 이상탐지(SIMILAR_TO 클리크) 신호를
            corroborating 가산점으로 반영(WP3 · FR-3.6). 라벨 미사용 비지도 신호로
            weak/hotspot_only 수법을 회수한다. SIMILAR_TO 엣지가 없으면 자동 생략.

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

    # --- Q1 공유 엔티티 (가구 인지 + 시간 군집) ---
    for grp in detect.run_shared_entities(
        min_customers=min_customers, include_address=include_address
    ):
        stype = grp["shared_type"]
        cids = grp["customer_ids"]
        num = grp["num_customers"]
        distinct_addr = grp.get("distinct_addresses", num)
        time_span = grp.get("time_span_days", -1)

        is_family = _is_family_share(num, distinct_addr)
        weight, group = _shared_weight(stype, is_family=is_family)
        if weight <= 0.0:
            continue

        bonus = _cluster_bonus(num) if group else 0.0

        # 시간 군집 보너스 — 강 공유 신호이며 청구가 짧은 기간 집중 시 가산.
        time_bonus = 0.0
        time_clustered = (
            group is not None and 0 <= time_span <= TIME_CLUSTER_DAYS
        )
        if time_clustered:
            time_bonus = W_TIME_CLUSTER_BONUS

        peers_by_cid = {c: [x for x in cids if x != c] for c in cids}
        for cid in cids:
            r = _get(cid)
            r.add_signal(
                f"SHARED_{stype}",
                weight + bonus + time_bonus,
                {
                    "shared_key": grp["shared_key"],
                    "num_customers": num,
                    "distinct_addresses": distinct_addr,
                    "time_span_days": time_span,
                    "household_like": is_family,
                    "time_clustered": time_clustered,
                    "shared_with": peers_by_cid[cid],
                },
                group=group,
            )

    # --- Q3 crash-for-cash 순환 (상호 교차 목격) — 강 신호, 정상 배경 0 ---
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
            group=_GROUP_WITNESS,
        )

    # --- Q2b 집중 핫스팟 (corroborating 약 신호) ---
    #   비인기 (병원+정비소)를 소수가 짧은 기간 함께 이용 — 단독 임계 미달.
    for hs in detect.run_focused_hotspots():
        for cid in hs["customer_ids"]:
            r = _get(cid)
            r.add_signal(
                "FOCUSED_HOTSPOT",
                W_FOCUSED_HOTSPOT,
                {
                    "entity_id": hs["entity_id"],
                    "entity_name": hs.get("entity_name"),
                    "shop_id": hs.get("shop_id"),
                    "num_customers": hs["num_customers"],
                    "span_days": hs.get("span_days"),
                },
                group=_GROUP_HOTSPOT,
            )

    # --- Q2c 담합 핫스팟 (고정밀 — 약신호 링 회수) ---
    #   동일 병원+정비소·소수·매우 짧은 기간·단일 collision → crash-for-cash 담합.
    #   정밀 ~81% 의 강 신호로 단독 알림 가능 수준. 고신뢰 그룹으로 등록.
    for hs in detect.run_collusion_hotspots():
        for cid in hs["customer_ids"]:
            r = _get(cid)
            r.add_signal(
                "COLLUSION_HOTSPOT",
                W_COLLUSION_HOTSPOT,
                {
                    "entity_id": hs["entity_id"],
                    "entity_name": hs.get("entity_name"),
                    "shop_id": hs.get("shop_id"),
                    "num_customers": hs["num_customers"],
                    "span_days": hs.get("span_days"),
                },
                group=_GROUP_WITNESS,  # 고신뢰 그룹(담합 = 동시 청구 공모)
            )

    # --- WP-KR 한국 수법 신호 — star / 브로커·설계사 허브 / 정비비 과다 ---
    _apply_korean_method_signals(risks, _get)

    # --- GDS 신호 (WP3 · FR-3.4, 선택적 corroborating) ---
    if use_gds:
        _apply_gds_signals(risks, _get)

    # --- 임베딩/비지도 이상신호 (WP3 · FR-3.6, 선택적 corroborating) ---
    if use_embedding:
        _apply_embedding_signals(risks, _get)

    # --- 복수 신호 보너스 (핵심) — 서로 다른 강신호 그룹 2종+ 동시 충족 시 급가점 ---
    for r in risks.values():
        r.apply_multi_signal_bonus()

    # ground truth 라벨 부착 (평가/검증용 — 점수 계산에는 미사용)
    _attach_ground_truth(risks)

    if alert_threshold != DEFAULT_ALERT_THRESHOLD:
        for r in risks.values():
            r.signals.append({"type": "_alert_threshold", "value": alert_threshold})

    return risks


def _apply_korean_method_signals(
    risks: dict[str, CustomerRisk],
    get_risk: Any,
) -> None:
    """WP-KR 한국 실제 사기 수법 신호를 가산한다(라벨 미사용 구조 신호).

    · 허위입원 star : 비인기 병원에 환자 집중(나이롱) → 환자 전원에 강 신호(STAR).
    · 브로커 허브   : 다수 고객 알선 → 알선 고객 전원에 강 신호(HUB).
    · 설계사 허브   : 다수 계약 모집 + 청구금 소수 공통계좌 집중 → 고객에 강 신호(HUB).
    · 정비비 과다청구: 비인기 정비소 다수 고객 + 금액 이상 → 중강 신호(HOTSPOT 그룹).

    신규 노드(Broker/Agent)가 없는 구버전 데이터에서는 detect 가 빈 결과를 주어
    조용히 생략된다(기존 동작 유지).
    """
    # Q4 허위입원 star
    for st in detect.run_admission_stars():
        for cid in st["customer_ids"]:
            r = get_risk(cid)
            r.add_signal(
                "FAKE_ADMISSION_STAR",
                W_FAKE_ADMISSION_STAR,
                {
                    "hospital_id": st["entity_id"],
                    "hospital_name": st.get("entity_name"),
                    "num_patients": st["num_customers"],
                    "span_days": st.get("span_days"),
                },
                group=_GROUP_HOTSPOT,  # 병원 단독 burst — corroborating 약신호
            )

    # Q5 브로커 허브
    for hub in detect.run_broker_hubs():
        for cid in hub["customer_ids"]:
            r = get_risk(cid)
            r.add_signal(
                "BROKER_HUB",
                W_BROKER_HUB,
                {
                    "broker_id": hub["entity_id"],
                    "broker_name": hub.get("entity_name"),
                    "num_brokered": hub["num_customers"],
                },
                group=_GROUP_HUB,
            )

    # Q6 설계사 허브(가로채기)
    for hub in detect.run_agent_hubs():
        for cid in hub["customer_ids"]:
            r = get_risk(cid)
            r.add_signal(
                "AGENT_HUB",
                W_AGENT_HUB,
                {
                    "agent_id": hub["entity_id"],
                    "agent_name": hub.get("entity_name"),
                    "shared_account_no": hub.get("account_no"),
                    "num_customers": hub["num_customers"],
                },
                group=_GROUP_HUB,
            )

    # Q7 정비비 과다청구
    for ob in detect.run_repair_overbills():
        for cid in ob["customer_ids"]:
            r = get_risk(cid)
            r.add_signal(
                "REPAIR_OVERBILL",
                W_REPAIR_OVERBILL,
                {
                    "shop_id": ob["entity_id"],
                    "shop_name": ob.get("entity_name"),
                    "num_customers": ob["num_customers"],
                    "avg_amount": ob.get("avg_amount"),
                },
                group=_GROUP_HOTSPOT,  # 금액 이상은 corroborating(정상 고가 수리 혼입 가능)
            )


def _apply_gds_signals(
    risks: dict[str, CustomerRisk],
    get_risk: Any,
) -> None:
    """GDS 군집(Louvain)·중심성(PageRank) 신호를 corroborating 가산점으로 반영.

    ground truth(ring_id)를 쓰지 않고 순수 구조 신호만 사용한다:
        · 다수 멤버 Louvain 커뮤니티(같은 커뮤니티에 Customer 2명 이상) 소속.
        · 커뮤니티 상대 PageRank 가 높은 핫스팟 인접 고객 → 약한 가산.

    GDS write 속성(``louvain_community``)이 없으면 조용히 생략.
    """
    from thoth import db

    try:
        rows = db.run(
            """
            MATCH (c:Customer)
            WHERE c.louvain_community IS NOT NULL
            WITH c.louvain_community AS comm, collect(c) AS members
            WHERE size(members) >= 2
            UNWIND members AS c
            RETURN c.customer_id AS cid,
                   comm AS community,
                   size(members) AS community_size,
                   coalesce(c.pagerank_score, 0.0) AS pagerank
            """
        )
    except Exception:
        return

    if not rows:
        return

    for row in rows:
        cid = row["cid"]
        r = risks.get(cid)
        if r is None:
            r = get_risk(cid)
        r.add_signal(
            "GDS_COMMUNITY",
            W_GDS_COMMUNITY,
            {
                "community": row["community"],
                "community_size": row["community_size"],
            },
            group=_GROUP_WITNESS,  # 구조적 군집 — 별도 그룹으로 복수신호에 기여
        )
        if float(row["pagerank"]) > 0.0:
            r.add_signal(
                "GDS_PAGERANK",
                W_GDS_PAGERANK,
                {"pagerank_score": round(float(row["pagerank"]), 4)},
            )


def _apply_embedding_signals(
    risks: dict[str, CustomerRisk],
    get_risk: Any,
) -> None:
    """그래프 임베딩/비지도 이상신호(SIMILAR_TO 클리크)를 corroborating 가산.

    ``detection.embedding`` 의 고객-고객 유사 그래프에서 차수>=2(3+ 클리크) 멤버를
    강 신호로 가산한다. 이 신호는 **라벨을 전혀 쓰지 않는 완전 비지도** 구조
    신호이므로 평가 누수(라벨 치팅)가 원천적으로 없다(FR-3.6).

    SIMILAR_TO 엣지가 없으면(임베딩 파이프라인 미실행) 조용히 생략한다.
    PAIR(차수==1)은 정밀 ~0.06 으로 noisy 하여 신호로 쓰지 않는다.
    """
    from detection import embedding

    try:
        signals = embedding.compute_anomaly_signals(attach_labels=False)
    except Exception:
        return
    if not signals:
        return

    for cid, sig in signals.items():
        if not sig.is_clique:
            continue  # PAIR(약·noisy)은 가산하지 않음
        r = risks.get(cid)
        if r is None:
            r = get_risk(cid)
        r.add_signal(
            "EMBED_CLIQUE",
            W_EMBED_CLIQUE,
            {
                "similar_degree": sig.similar_degree,
                "similar_peers": sig.similar_peers,
            },
            group=_GROUP_EMBED,
        )


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
