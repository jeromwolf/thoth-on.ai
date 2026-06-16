"""API 인증/인가 + 세션 의존성 (WP5 · FR-8.1 AC).

PoC 인증은 헤더 기반 역할 주입이다. 실제 OAuth/OIDC 는 V1 이므로, 여기서는
``X-Role`` 헤더(또는 ``X-API-Key`` / ``Authorization: Bearer <token>``)로 역할을
주입하고, ``core.security.rbac.check_access`` 로 데이터 등급 인가를 수행한다.

[설계]
    · ``get_principal``       — 요청에서 actor(사용자 식별)와 Role 을 추출.
    · ``require_data_class``  — 특정 DataClass 접근 권한을 요구하는 의존성 팩토리.
        권한 부족이면 403 + 감사(result="denied"), 통과하면 감사(result="ok").
    · ``get_case_store``      — 케이스 저장소 의존성(앱 상태에서 주입, 테스트 오버라이드 용이).

모든 민감 접근은 ``core.security.audit.audit_event`` 로 불변 기록된다(NFR).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from fastapi import Depends, Header, HTTPException, Request, status

from core.cases import CaseStore
from core.security.audit import audit_event
from core.security.rbac import DataClass, Role, check_access


# ==================================================================
# Principal (인증 주체)
# ==================================================================
@dataclass(frozen=True)
class Principal:
    """인증된 요청 주체. PoC 헤더 기반."""

    actor: str          # 사용자/시스템 식별자(감사 actor)
    role: Role          # 역할(권한 수준)


# PoC 토큰 → 역할 매핑(데모용). 실제 환경에선 IdP 토큰 검증으로 대체.
_TOKEN_ROLE_MAP = {
    "adjuster-token": Role.CLAIMS_ADJUSTER,
    "analyst-token": Role.FRAUD_ANALYST,
    "risk-token": Role.RISK_MANAGER,
    "admin-token": Role.ADMIN,
}


def _resolve_role(
    x_role: Optional[str],
    x_api_key: Optional[str],
    authorization: Optional[str],
) -> Role:
    """헤더에서 역할을 해석. 우선순위: X-Role > Bearer 토큰 > X-API-Key.

    인식 불가/미제공이면 ``Role.PUBLIC`` (최소 권한).
    """
    # 1) X-Role: 역할명 직접 지정(예: FRAUD_ANALYST).
    if x_role:
        try:
            return Role[x_role.strip().upper()]
        except KeyError:
            pass
    # 2) Authorization: Bearer <token>
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
        if token in _TOKEN_ROLE_MAP:
            return _TOKEN_ROLE_MAP[token]
    # 3) X-API-Key
    if x_api_key and x_api_key in _TOKEN_ROLE_MAP:
        return _TOKEN_ROLE_MAP[x_api_key]
    return Role.PUBLIC


def get_principal(
    x_role: Optional[str] = Header(None, alias="X-Role"),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    x_actor: Optional[str] = Header(None, alias="X-Actor"),
    authorization: Optional[str] = Header(None, alias="Authorization"),
) -> Principal:
    """요청 헤더에서 인증 주체(actor + Role)를 추출하는 의존성.

    actor 는 ``X-Actor`` 헤더(없으면 역할명 기반 기본값)로 정한다.
    """
    role = _resolve_role(x_role, x_api_key, authorization)
    actor = x_actor or f"{role.name.lower()}@poc"
    return Principal(actor=actor, role=role)


def require_data_class(
    data_class: DataClass,
    action: str,
) -> Callable[..., Principal]:
    """``data_class`` 접근 권한을 요구하는 의존성 팩토리.

    역할 권한이 데이터 등급 요구치 미만이면 403 을 발생시키고 감사에
    ``result="denied"`` 로 기록한다. 통과하면 ``result="ok"`` 로 접근을 기록한다.

    Args:
        data_class: 보호 대상 데이터 등급(예: ``DataClass.FRAUD_CASE``).
        action: 감사 액션명(예: ``"api.case.detail"``).

    Returns:
        FastAPI 의존성 함수(통과 시 ``Principal`` 반환).
    """

    def _dependency(
        request: Request,
        principal: Principal = Depends(get_principal),
    ) -> Principal:
        decision = check_access(principal.role, data_class)
        target = request.path_params.get("case_id") or request.path_params.get(
            "customer_id", ""
        )
        if not decision.allowed:
            audit_event(
                action,
                principal.actor,
                target=str(target),
                result="denied",
                meta={
                    "role": principal.role.name,
                    "data_class": data_class.name,
                    "reason": decision.reason,
                    "path": request.url.path,
                },
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"권한 부족: {principal.role.name} 역할은 "
                    f"{data_class.name} 데이터에 접근할 수 없습니다 ({decision.reason})"
                ),
            )
        audit_event(
            action,
            principal.actor,
            target=str(target),
            result="ok",
            meta={"role": principal.role.name, "data_class": data_class.name,
                  "path": request.url.path},
        )
        return principal

    return _dependency


# ==================================================================
# 케이스 저장소 의존성
# ==================================================================
def get_case_store(request: Request) -> CaseStore:
    """앱 상태(``app.state.case_store``)에서 케이스 저장소를 주입.

    테스트는 ``app.dependency_overrides[get_case_store]`` 로 임시 DB 를 주입한다.
    """
    store = getattr(request.app.state, "case_store", None)
    if store is None:
        store = CaseStore()
        request.app.state.case_store = store
    return store


def get_signal_cache(request: Request) -> Dict[str, List[Dict[str, Any]]]:
    """케이스별 기여 신호 캐시(``{customer_id: signals}``)를 앱 상태에서 주입.

    케이스 저장소(SQLite)는 신호를 영속화하지 않으므로(PoC), 스코어링으로
    산출한 신호를 앱 상태 캐시에 보관하여 상세 조회 시 경로·소명문을 만든다.
    캐시에 없으면 빈 신호로 동작(소명문은 "근거 없음" → 환각가드 trivially 통과).
    테스트는 이 캐시에 직접 신호를 주입해 Neo4j 없이 상세를 검증할 수 있다.
    """
    cache = getattr(request.app.state, "signal_cache", None)
    if cache is None:
        cache = {}
        request.app.state.signal_cache = cache
    return cache
