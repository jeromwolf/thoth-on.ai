"""API 서비스 계층 (WP5).

라우터가 도메인 로직(스코어링·케이스 생성·경로·소명문·그래프 탐색·KPI)을
직접 호출하지 않도록, 변환·집계 로직을 모은 얇은 서비스 모듈.

[Neo4j 의존]
    · ``refresh_cases`` / ``customer_subgraph`` 는 Neo4j 가 필요(integration).
    · ``case_detail`` / KPI 집계는 케이스 저장소(SQLite) + 케이스에 첨부된 신호로
      동작하므로 Neo4j 없이도 가능(smoke). 단, 신호 경로 빌드는 케이스 신호에서
      합성하므로 추가 Neo4j 왕복이 필요 없다.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.cases import Case, CaseStore, CaseStatus
from detection import detect
from detection import paths as path_builder
from detection import scoring
from explain import explainer
from explain.provider import LLMProvider, get_provider

# vis-network 노드 그룹(타입) — 색상 구분용으로 그대로 노출.
_NODE_GROUPS = {
    "Customer", "Account", "Phone", "Vehicle", "Address",
    "Hospital", "RepairShop", "Claim",
}


# ==================================================================
# 케이스 큐 생성 (Neo4j 필요)
# ==================================================================
def refresh_cases(
    store: CaseStore,
    *,
    threshold: float = scoring.DEFAULT_ALERT_THRESHOLD,
    top: Optional[int] = None,
    use_gds: bool = False,
    actor: str = "system",
) -> int:
    """리스크 스코어 상위 고객으로 케이스 큐를 (재)생성한다.

    탐지/스코어링(Neo4j)을 실행해 임계 이상 고객을 케이스로 멱등 생성하고,
    각 케이스에 기여 신호를 첨부 저장한다. 생성된 케이스 수를 반환.

    신호는 SQLite 케이스 테이블에 별도로 보관되지 않으므로(PoC), 상세 조회 시
    재계산이 필요하다. 본 함수는 점수/링/신호를 메모리 캐시(앱 상태)에 적재하는
    역할도 겸한다 — 호출자가 반환된 risk 맵을 캐시하도록 설계.
    """
    risks = scoring.score_customers(alert_threshold=threshold, use_gds=use_gds)
    flagged = scoring.alerts(risks, threshold=threshold)
    if top is not None:
        flagged = flagged[:top]
    created = 0
    for r in flagged:
        case_id = f"CASE-{r.customer_id}"
        store.create_case(
            case_id=case_id, customer_id=r.customer_id,
            score=r.score, ring_id=r.ring_id, actor=actor,
        )
        created += 1
    return created


def score_signal_cache(
    *,
    threshold: float = scoring.DEFAULT_ALERT_THRESHOLD,
    use_gds: bool = False,
) -> Dict[str, scoring.CustomerRisk]:
    """전 고객 리스크 스코어를 산출해 ``{customer_id: CustomerRisk}`` 로 반환.

    케이스 상세(신호·경로·소명문)는 이 캐시의 신호를 사용한다(Neo4j 필요).
    """
    return scoring.score_customers(alert_threshold=threshold, use_gds=use_gds)


# ==================================================================
# 케이스 상세 조립 (신호 캐시 기반 — Neo4j 불필요)
# ==================================================================
def build_case_detail(
    case: Case,
    signals: List[Dict[str, Any]],
    *,
    provider: Optional[LLMProvider] = None,
) -> Dict[str, Any]:
    """케이스 + 기여 신호로부터 상세(경로·소명문·환각가드)를 조립한다.

    Neo4j 없이 케이스 신호만으로 동작한다(경로는 신호에서 합성, 소명문은
    Mock/실 provider 가 생성). 라우터가 그대로 응답 모델에 매핑할 dict 반환.

    Args:
        case: 대상 케이스(메타).
        signals: 기여 신호 목록(scoring 출력 형태). 내부 메타(_*)는 무시됨.
        provider: 설명 provider. None 이면 env 기반 기본(mock).

    Returns:
        ``signals``·``paths``·``explanation`` 을 포함한 상세 dict.
    """
    prov = provider or get_provider()
    case_paths = path_builder.build_paths(case.customer_id, signals)
    exp = explainer.generate_explanation(case.customer_id, case_paths, provider=prov)

    signal_models = [
        {
            "type": s.get("type", ""),
            "weight": s.get("weight"),
            "detail": {k: v for k, v in s.items()
                       if k not in {"type", "weight"}},
        }
        for s in signals
        if not str(s.get("type", "")).startswith("_")
    ]

    return {
        "signals": signal_models,
        "paths": case_paths,
        "explanation": exp.to_dict(),
    }


# ==================================================================
# 고객 서브네트워크 (vis-network) — Neo4j 필요
# ==================================================================
_SUBGRAPH_CYPHER = """
MATCH (c:Customer {customer_id: $cid})
OPTIONAL MATCH (c)-[r]-(n)
WHERE n:Account OR n:Phone OR n:Vehicle OR n:Address
   OR n:Hospital OR n:RepairShop OR n:Claim
WITH c, collect(DISTINCT {rel: type(r), node: n}) AS direct
RETURN c AS center, direct
"""

# 중심 고객과 엔티티를 공유하는 동료 고객(2-hop) 탐색.
_PEERS_CYPHER = """
MATCH (c:Customer {customer_id: $cid})-[r1]->(shared)
WHERE shared:Account OR shared:Phone OR shared:Vehicle OR shared:Address
MATCH (peer:Customer)-[r2]->(shared)
WHERE peer.customer_id <> $cid
RETURN DISTINCT peer.customer_id AS peer_id,
       coalesce(peer.ring_id, '') AS peer_ring,
       coalesce(peer.is_fraud_ring, false) AS peer_fraud,
       labels(shared)[0] AS shared_type,
       coalesce(shared.account_no, shared.number_hash, shared.vin,
                shared.address_id, '') AS shared_key,
       type(r2) AS rel
"""

_CENTER_CYPHER = """
MATCH (c:Customer {customer_id: $cid})
RETURN c.customer_id AS cid,
       coalesce(c.ring_id, '') AS ring_id,
       coalesce(c.is_fraud_ring, false) AS is_fraud
"""


def _entity_key(node_type: str, props: Dict[str, Any]) -> str:
    """엔티티 노드의 안정적 식별 키를 속성에서 추출."""
    for k in ("customer_id", "account_no", "number_hash", "vin",
              "address_id", "hospital_id", "shop_id", "claim_id"):
        if props.get(k):
            return str(props[k])
    # fallback: elementId 대신 타입+해시 회피 — 첫 값 사용.
    return f"{node_type}:{next(iter(props.values()), 'unknown')}"


def _entity_label(node_type: str, props: Dict[str, Any]) -> str:
    """표시 라벨(민감 식별자는 부분 마스킹)."""
    if node_type == "Account" and props.get("account_no"):
        key = str(props["account_no"])
        return key[:3] + "***" + key[-2:] if len(key) > 4 else key
    if node_type == "Phone" and props.get("number_hash"):
        key = str(props["number_hash"])
        return key[:3] + "***" + key[-2:] if len(key) > 4 else key
    if props.get("name"):
        return str(props["name"])
    return _entity_key(node_type, props)


def customer_subgraph(customer_id: str) -> Dict[str, Any]:
    """고객 주변 서브네트워크를 vis-network JSON 형태로 구성(Neo4j 필요).

    중심 고객 → 직접 연결 엔티티(계좌·전화·차량·주소·병원·정비소·청구) +
    공유 엔티티를 통해 연결된 동료 고객(2-hop)을 노드/엣지로 반환한다.
    동료 고객·중심 고객이 같은 ring_id 이거나 fraud 플래그면 ``suspicious=True``.

    Returns:
        ``{customer_id, center, ring_id, nodes, edges, node_count, edge_count}``.
        고객이 존재하지 않으면 빈 그래프(node_count=0).
    """
    from thoth import db

    center_rows = db.run(_CENTER_CYPHER, cid=customer_id)
    if not center_rows:
        return {
            "customer_id": customer_id, "center": customer_id, "ring_id": "",
            "nodes": [], "edges": [], "node_count": 0, "edge_count": 0,
        }
    center = center_rows[0]
    center_ring = center["ring_id"] or ""
    center_fraud = bool(center["is_fraud"])

    nodes: Dict[str, Dict[str, Any]] = {}
    edges: List[Dict[str, Any]] = []
    edge_seen: set = set()

    def _add_node(nid: str, label: str, group: str, suspicious: bool,
                  title: Optional[str] = None) -> None:
        if nid not in nodes:
            nodes[nid] = {"id": nid, "label": label, "group": group,
                          "suspicious": suspicious, "title": title or label}
        elif suspicious:
            nodes[nid]["suspicious"] = True

    def _add_edge(src: str, dst: str, label: str, suspicious: bool) -> None:
        key = (src, dst, label)
        if key in edge_seen:
            return
        edge_seen.add(key)
        edges.append({"from": src, "to": dst, "label": label,
                      "suspicious": suspicious})

    # 중심 고객 노드.
    _add_node(customer_id, customer_id, "Customer", center_fraud,
              title=f"중심 고객 {customer_id}"
              + (f" (링 {center_ring})" if center_ring else ""))

    # 직접 연결 엔티티.
    direct_rows = db.run(_SUBGRAPH_CYPHER, cid=customer_id)
    if direct_rows:
        for item in direct_rows[0].get("direct", []) or []:
            node = item.get("node")
            rel = item.get("rel")
            if not node:
                continue
            # node.data() 는 속성 dict. 라벨은 별도 쿼리 없이 키로 타입 추정.
            props = dict(node)
            node_type = _infer_type(props)
            nid = _entity_key(node_type, props)
            _add_node(nid, _entity_label(node_type, props), node_type, False)
            _add_edge(customer_id, nid, rel or "REL", False)

    # 공유 엔티티로 연결된 동료 고객(의심 플래그).
    peer_rows = db.run(_PEERS_CYPHER, cid=customer_id)
    for row in peer_rows:
        peer_id = row["peer_id"]
        peer_ring = row["peer_ring"] or ""
        peer_fraud = bool(row["peer_fraud"])
        shared_key = str(row["shared_key"] or "")
        shared_type = row["shared_type"]
        rel = row["rel"]
        # 동료가 중심과 같은 링이면 의심.
        suspicious = bool(
            peer_fraud or (center_ring and peer_ring == center_ring)
        )
        _add_node(peer_id, peer_id, "Customer", suspicious,
                  title=f"동료 고객 {peer_id}"
                  + (f" (링 {peer_ring})" if peer_ring else ""))
        if shared_key:
            shared_label = _entity_label(shared_type, {_share_prop(shared_type): shared_key})
            _add_node(shared_key, shared_label, shared_type, suspicious)
            _add_edge(peer_id, shared_key, rel, suspicious)
            _add_edge(customer_id, shared_key, rel, suspicious)

    node_list = list(nodes.values())
    return {
        "customer_id": customer_id,
        "center": customer_id,
        "ring_id": center_ring,
        "nodes": node_list,
        "edges": edges,
        "node_count": len(node_list),
        "edge_count": len(edges),
    }


def _share_prop(shared_type: str) -> str:
    return {
        "Account": "account_no", "Phone": "number_hash",
        "Vehicle": "vin", "Address": "address_id",
    }.get(shared_type, "id")


def _infer_type(props: Dict[str, Any]) -> str:
    """노드 속성으로 타입을 추정(라벨 미반환 환경 대비)."""
    if "account_no" in props:
        return "Account"
    if "number_hash" in props:
        return "Phone"
    if "vin" in props:
        return "Vehicle"
    if "address_id" in props:
        return "Address"
    if "hospital_id" in props:
        return "Hospital"
    if "shop_id" in props:
        return "RepairShop"
    if "claim_id" in props:
        return "Claim"
    if "customer_id" in props:
        return "Customer"
    return "Entity"


# ==================================================================
# KPI 집계 (FR-9.2) — 케이스 저장소 + 신호 캐시 기반
# ==================================================================
def compute_kpi(
    store: CaseStore,
    *,
    threshold: float = scoring.DEFAULT_ALERT_THRESHOLD,
    suspected_rings: Optional[int] = None,
) -> Dict[str, Any]:
    """경영 대시보드 KPI 를 집계한다(FR-9.2).

    케이스 저장소(SQLite)만으로 동작하므로 Neo4j 없이 가능(smoke). ``suspected_rings``
    가 주어지지 않으면 케이스의 distinct ring_id 수로 추정한다.

    추정 지표 가정 (PoC 합성 데이터 기준, 실운영 시 재보정 필요):
        - ``daily_throughput_estimate``: 케이스당 검토 20분, 순수 업무시간 4h/일
          → 1인당 12건/일 처리 가능.
        - ``estimated_savings_krw``: 사기 판정 케이스당 평균 청구액 500만 원 가정.
          실제 청구 데이터가 없는 PoC 환경이므로 추정치. 실운영 시 실 지급액 적용.
        - ``detection_rate_pct``: 고위험 케이스 / 전체 케이스(케이스 저장소 기준).
          전체 청구 모집단 대비 탐지율 아님.

    Returns:
        총 케이스 수·상태 분포·의심 링 수·고위험 건수·사기 판정 누계·평균 점수
        분리도·처리량 추정·탐지율·절감액 추정 등을 담은 dict.
    """
    cases = store.queue()
    total = len(cases)

    status_dist: Dict[str, int] = {s.value: 0 for s in CaseStatus}
    high_scores: List[float] = []
    low_scores: List[float] = []
    all_scores: List[float] = []
    rings: set = set()
    fraud_verdicts = 0

    for c in cases:
        status_dist[c.status.value] = status_dist.get(c.status.value, 0) + 1
        all_scores.append(c.score)
        if c.score >= threshold:
            high_scores.append(c.score)
        else:
            low_scores.append(c.score)
        if c.ring_id:
            rings.add(c.ring_id)
        if c.status == CaseStatus.FRAUD:
            fraud_verdicts += 1

    def _avg(xs: List[float]) -> float:
        return round(sum(xs) / len(xs), 2) if xs else 0.0

    avg_high = _avg(high_scores)
    avg_low = _avg(low_scores)
    rings_count = suspected_rings if suspected_rings is not None else len(rings)

    # ── 추정 지표 계산 ────────────────────────────────────────────
    # 처리량: 케이스당 평균 검토 20분, 1일 순수 업무시간 4시간 기준.
    _REVIEW_MINUTES_PER_CASE = 20
    _WORK_MINUTES_PER_DAY = 4 * 60
    daily_throughput_estimate: int = _WORK_MINUTES_PER_DAY // _REVIEW_MINUTES_PER_CASE

    # 탐지율(케이스 저장소 기준): 고위험 건수 / 전체 케이스 × 100.
    high_risk_count = len(high_scores)
    detection_rate_pct = round(
        (high_risk_count / total * 100) if total > 0 else 0.0, 1
    )

    # 절감액 추정: 사기 판정 케이스 × 500만 원(PoC 합성 데이터 기준 추정).
    # 실제 청구 데이터가 없어 평균 청구액을 500만 원으로 가정(PoC 합성 시드 참조).
    _AVG_CLAIM_KRW = 5_000_000
    estimated_savings_krw: int = fraud_verdicts * _AVG_CLAIM_KRW

    savings_assumption = (
        f"사기판정 {fraud_verdicts}건 × 평균 청구액 500만 원 가정(PoC 합성 데이터 추정). "
        "실운영 시 실 지급액 기준 재보정 필요."
    )

    return {
        "total_cases": total,
        "status_distribution": status_dist,
        "suspected_rings": rings_count,
        "high_risk_cases": high_risk_count,
        "fraud_verdicts": fraud_verdicts,
        "avg_score": _avg(all_scores),
        "avg_high_risk_score": avg_high,
        "avg_low_risk_score": avg_low,
        "score_separation": round(avg_high - avg_low, 2),
        "threshold": threshold,
        # 추정 지표
        "daily_throughput_estimate": daily_throughput_estimate,
        "detection_rate_pct": detection_rate_pct,
        "estimated_savings_krw": estimated_savings_krw,
        "savings_assumption": savings_assumption,
    }


def count_suspected_rings() -> int:
    """탐지 기준 의심 링(crash-for-cash 군집) 수(Neo4j 필요)."""
    clusters = detect.run_crash_rings()
    ring_ids = {c.get("ring_id") for c in clusters if c.get("ring_id")}
    # ring_id 가 비어도 군집 자체는 의심 — seed 기준 distinct 군집 수로 보강.
    return len(ring_ids) if ring_ids else len(clusters)
