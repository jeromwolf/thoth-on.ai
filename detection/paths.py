"""근거 관계 경로 빌더 (WP4-2 · FR-5.1).

리스크 스코어의 **기여 신호**(``scoring.CustomerRisk.signals``)를 구조화된
**관계 경로 데이터**(노드/엣지 시퀀스)로 변환한다. 어떤 공유 엔티티·교차목격
순환이 점수에 기여했는지를 LLM 설명·시각화의 입력으로 명시한다.

[설계]
    · 경로는 ``{nodes, edges, signal_type, weight, label}`` 구조.
      - nodes: ``{id, type, label}`` 시퀀스 (Customer/Account/Phone/Vehicle/
        Address/Hospital/RepairShop/Claim).
      - edges: ``{source, target, type}`` 시퀀스 (PAID_TO/HAS_PHONE/OWNS/
        LIVES_AT/SHARED_WITH/WITNESSED_BY/TREATED_AT/REPAIRED_AT).
    · 신호 dict 만으로 경로를 합성한다(추가 Neo4j 왕복 불필요 — 신호에 이미
      shared_key·shared_with·entity_id 가 들어있다). 환각 가드가 대조할 수 있도록
      경로에 등장하는 모든 엔티티 id 를 명시한다.

이 경로 데이터는 환각 가드(``explain.explainer.verify_grounding``)가
"소명문이 인용하는 엔티티가 실재 경로에 있는지" 대조하는 유일한 근거가 된다.
"""
from __future__ import annotations

from typing import Any

# 공유 신호 타입 → (엔티티 노드 타입, 엔티티-고객 연결 엣지 타입)
_SHARED_ENTITY_META: dict[str, tuple[str, str, str]] = {
    # signal_type: (entity_node_type, edge_type, korean_label)
    "SHARED_ACCOUNT": ("Account", "PAID_TO", "동일 계좌"),
    "SHARED_PHONE": ("Phone", "HAS_PHONE", "동일 전화"),
    "SHARED_VEHICLE": ("Vehicle", "OWNS", "동일 차량"),
    "SHARED_ADDRESS": ("Address", "LIVES_AT", "동일 주소"),
}

_HOTSPOT_META: dict[str, tuple[str, str, str]] = {
    "HOTSPOT_HOSPITAL": ("Hospital", "TREATED_AT", "병원 핫스팟"),
    "HOTSPOT_REPAIR_SHOP": ("RepairShop", "REPAIRED_AT", "정비소 핫스팟"),
    "HOTSPOT_ACCOUNT": ("Account", "PAID_TO", "계좌 핫스팟"),
}


def _node(node_id: str, node_type: str, label: str = "") -> dict[str, str]:
    return {"id": str(node_id), "type": node_type, "label": label or str(node_id)}


def _edge(source: str, target: str, edge_type: str) -> dict[str, str]:
    return {"source": str(source), "target": str(target), "type": edge_type}


def build_paths(customer_id: str, signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """기여 신호 목록을 구조화된 관계 경로 리스트로 변환(FR-5.1).

    Args:
        customer_id: 케이스 대상 고객 ID.
        signals: ``scoring.CustomerRisk.signals`` (type/weight + 상세).

    Returns:
        경로 dict 리스트. 각 경로는 ``signal_type``·``weight``·``label``·
        ``nodes``·``edges``·``entities``(인용 가능한 모든 엔티티 id 집합) 포함.
    """
    paths: list[dict[str, Any]] = []
    for sig in signals:
        stype = sig.get("type", "")
        if stype.startswith("_"):  # 내부 메타(_alert_threshold 등) 제외
            continue
        if stype in _SHARED_ENTITY_META:
            paths.append(_build_shared_path(customer_id, sig, stype))
        elif stype == "CROSS_WITNESS":
            paths.append(_build_cross_witness_path(customer_id, sig))
        elif stype in _HOTSPOT_META:
            paths.append(_build_hotspot_path(customer_id, sig, stype))
        elif stype.startswith("GDS_"):
            paths.append(_build_gds_path(customer_id, sig, stype))
    return paths


def _build_shared_path(
    customer_id: str, sig: dict[str, Any], stype: str
) -> dict[str, Any]:
    """공유 엔티티 경로: 고객들 -[edge]-> 공유 엔티티."""
    entity_type, edge_type, label = _SHARED_ENTITY_META[stype]
    shared_key = str(sig.get("shared_key", ""))
    peers = [str(p) for p in sig.get("shared_with", [])]
    members = [customer_id] + peers

    nodes: list[dict[str, str]] = [
        _node(shared_key, entity_type, _mask(shared_key, entity_type))
    ]
    edges: list[dict[str, str]] = []
    for cid in members:
        nodes.append(_node(cid, "Customer"))
        edges.append(_edge(cid, shared_key, edge_type))

    entities = set(members) | {shared_key}
    return {
        "signal_type": stype,
        "weight": sig.get("weight"),
        "label": f"{label} 공유 ({len(members)}인)",
        "shared_key": shared_key,
        "entity_type": entity_type,
        "members": members,
        "nodes": nodes,
        "edges": edges,
        "entities": sorted(entities),
    }


def _build_cross_witness_path(
    customer_id: str, sig: dict[str, Any]
) -> dict[str, Any]:
    """교차 목격 순환 경로: 고객 <-WITNESSED_BY-> 동료(상호)."""
    peers = [str(p) for p in sig.get("witnessed_with", [])]
    members = [customer_id] + peers

    nodes = [_node(c, "Customer") for c in members]
    edges: list[dict[str, str]] = []
    # 양방향 교차 목격을 seed↔peer 로 표현.
    for peer in peers:
        edges.append(_edge(customer_id, peer, "WITNESSED_BY"))
        edges.append(_edge(peer, customer_id, "WITNESSED_BY"))

    entities = set(members)
    return {
        "signal_type": "CROSS_WITNESS",
        "weight": sig.get("weight"),
        "label": f"상호 교차 목격 순환 ({len(members)}인)",
        "members": members,
        "nodes": nodes,
        "edges": edges,
        "entities": sorted(entities),
    }


def _build_hotspot_path(
    customer_id: str, sig: dict[str, Any], stype: str
) -> dict[str, Any]:
    """핫스팟 경로: 고객 -[edge]-> 집중 엔티티."""
    entity_type, edge_type, label = _HOTSPOT_META[stype]
    entity_id = str(sig.get("entity_id", ""))
    entity_name = sig.get("entity_name") or entity_id

    nodes = [
        _node(customer_id, "Customer"),
        _node(entity_id, entity_type, str(entity_name)),
    ]
    edges = [_edge(customer_id, entity_id, edge_type)]
    entities = {customer_id, entity_id}
    return {
        "signal_type": stype,
        "weight": sig.get("weight"),
        "label": f"{label}: {entity_name} ({sig.get('num_customers')}인 이용)",
        "entity_id": entity_id,
        "entity_name": str(entity_name),
        "entity_type": entity_type,
        "nodes": nodes,
        "edges": edges,
        "entities": sorted(entities),
    }


def _build_gds_path(
    customer_id: str, sig: dict[str, Any], stype: str
) -> dict[str, Any]:
    """GDS 구조 신호 경로: 고객의 커뮤니티/중심성 corroborating."""
    nodes = [_node(customer_id, "Customer")]
    detail = {k: v for k, v in sig.items() if k not in {"type", "weight"}}
    return {
        "signal_type": stype,
        "weight": sig.get("weight"),
        "label": f"GDS 구조 신호({stype})",
        "detail": detail,
        "nodes": nodes,
        "edges": [],
        "entities": [customer_id],
    }


def _mask(key: str, entity_type: str) -> str:
    """계좌/전화 등 민감 식별자를 부분 마스킹한 표시 라벨."""
    if entity_type in {"Account", "Phone"} and len(key) > 4:
        return key[:3] + "***" + key[-2:]
    return key


def collect_entities(paths: list[dict[str, Any]]) -> set[str]:
    """경로 목록 전체에서 인용 가능한 모든 엔티티 id 집합을 모은다.

    환각 가드가 "소명문 인용 엔티티 ⊆ 실재 경로 엔티티" 를 대조할 기준 집합.
    """
    entities: set[str] = set()
    for p in paths:
        entities.update(str(e) for e in p.get("entities", []))
        # 노드 id 도 포함(마스킹 라벨이 아닌 원본 id).
        for n in p.get("nodes", []):
            entities.add(str(n["id"]))
    return entities
