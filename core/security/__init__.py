"""보안 레이어 (NFR): RBAC + 감사로그. flux-platform 패턴 벤더링.

WP0에서 골격만 제공하고 WP6에서 완성한다.
"""
from core.security.audit import AuditLog, audit_event
from core.security.rbac import AccessDecision, Role, check_access

__all__ = ["AuditLog", "audit_event", "AccessDecision", "Role", "check_access"]
