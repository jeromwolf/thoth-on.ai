"""감사로그 골격 (NFR: 불변 기록). flux-platform AuditMiddleware 패턴.

WP0: append-only JSONL 로거. WP6에서 불변 스토리지/서명으로 확장.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_AUDIT_PATH = Path(os.getenv("THOTH_AUDIT_LOG", "logs/audit.jsonl"))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class AuditLog:
    action: str                       # 예: "case.update", "graph.query"
    actor: str                        # 사용자 ID (마스킹된 값 권장)
    target: str = ""                  # 대상 리소스 ID
    result: str = "ok"               # ok | denied | error
    ts: str = field(default_factory=_now_iso)
    meta: dict = field(default_factory=dict)


def audit_event(
    action: str,
    actor: str,
    target: str = "",
    result: str = "ok",
    meta: Optional[dict] = None,
    path: Optional[Path] = None,
) -> AuditLog:
    """감사 이벤트를 append-only 로 기록하고 객체를 반환."""
    entry = AuditLog(action=action, actor=actor, target=target, result=result, meta=meta or {})
    dest = path or _AUDIT_PATH
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(entry), ensure_ascii=False) + "\n")
    return entry
