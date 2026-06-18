"""케이스 관리 (WP4-1 · FR-4.1 / FR-4.3).

의심 케이스 큐와 상태 머신, 담당자 배정, 상태변경 이력, 조사관 판정 피드백을
경량 SQLite 에 저장한다(PoC). 모든 케이스 행위(생성·배정·상태변경·판정)는
``core.security.audit.audit_event`` 로 불변 감사 기록된다(NFR).

[설계]
    · 케이스 메타(점수·상태·담당자) + 상태변경 이력 + 판정 피드백을 3개 테이블로 분리.
    · 상태 전이는 명시적 전이표(``_VALID_TRANSITIONS``)로 제한 — 유효 전이만 허용.
    · 케이스 큐는 리스크 스코어 내림차순 정렬(조사관 우선순위).
    · ground truth(ring_id)와 무관한 **운영 피드백** 라벨을 별도 기록(FR-4.3).

상태(enum):
    UNASSIGNED(미배정) → ASSIGNED(조사중) → {FRAUD(사기) | NORMAL(정상) | HOLD(보류)}
    HOLD 는 다시 조사중/판정으로 전이 가능.
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Iterator, Optional

from core.security.audit import audit_event


# ==================================================================
# 상태 머신
# ==================================================================
class CaseStatus(str, Enum):
    """케이스 상태. 값은 SQLite 에 저장되는 문자열."""

    UNASSIGNED = "UNASSIGNED"   # 미배정
    INVESTIGATING = "INVESTIGATING"  # 조사중
    FRAUD = "FRAUD"             # 사기 판정
    NORMAL = "NORMAL"           # 정상 판정
    HOLD = "HOLD"               # 보류


# 유효 상태 전이표: from -> 허용되는 to 집합.
_VALID_TRANSITIONS: dict[CaseStatus, set[CaseStatus]] = {
    CaseStatus.UNASSIGNED: {CaseStatus.INVESTIGATING, CaseStatus.HOLD},
    CaseStatus.INVESTIGATING: {
        CaseStatus.FRAUD,
        CaseStatus.NORMAL,
        CaseStatus.HOLD,
        CaseStatus.UNASSIGNED,
    },
    CaseStatus.HOLD: {CaseStatus.INVESTIGATING, CaseStatus.FRAUD, CaseStatus.NORMAL},
    # 판정 완료 상태는 재오픈(조사중)만 허용 — 오판 정정 경로.
    CaseStatus.FRAUD: {CaseStatus.INVESTIGATING},
    CaseStatus.NORMAL: {CaseStatus.INVESTIGATING},
}


class InvalidTransition(Exception):
    """허용되지 않은 상태 전이 시도."""


class CaseNotFound(Exception):
    """존재하지 않는 케이스 참조."""


def is_valid_transition(src: CaseStatus, dst: CaseStatus) -> bool:
    """``src`` 에서 ``dst`` 로의 상태 전이가 유효한지 여부."""
    return dst in _VALID_TRANSITIONS.get(src, set())


# ==================================================================
# 데이터 모델
# ==================================================================
@dataclass
class Case:
    """케이스 1건의 메타 + 첨부된 근거(경로/신호)."""

    case_id: str
    customer_id: str
    score: float
    status: CaseStatus
    ring_id: str = ""
    assignee: str = ""
    created_at: str = ""
    updated_at: str = ""
    # 근거(WP4-2): 기여 신호 + 의심 관계 경로. 메모리 첨부(영속화 옵션).
    signals: list[dict[str, Any]] = field(default_factory=list)
    paths: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "customer_id": self.customer_id,
            "score": round(self.score, 1),
            "status": self.status.value,
            "ring_id": self.ring_id,
            "assignee": self.assignee,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "signals": self.signals,
            "paths": self.paths,
        }


@dataclass(frozen=True)
class HistoryEntry:
    """상태변경 이력 1건."""

    case_id: str
    from_status: str
    to_status: str
    actor: str
    note: str
    ts: str


@dataclass(frozen=True)
class Verdict:
    """조사관 판정 피드백 1건(FR-4.3)."""

    case_id: str
    label: str        # "FRAUD" | "NORMAL"
    actor: str
    comment: str
    ts: str


# ==================================================================
# 저장소 (SQLite)
# ==================================================================
def _default_db_path() -> Path:
    return Path(os.getenv("THOTH_CASE_DB", "data/cases.db"))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class CaseStore:
    """케이스 메타·이력·판정을 SQLite 에 저장하는 경량 저장소.

    스키마는 ``migrate()`` 로 코드에서 생성한다(마이그레이션 함수). 동일 경로로
    여러 번 호출해도 멱등.
    """

    def __init__(self, db_path: Optional[str | Path] = None) -> None:
        self.db_path = Path(db_path) if db_path else _default_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.migrate()

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # --- 스키마 마이그레이션 -------------------------------------
    def migrate(self) -> None:
        """테이블이 없으면 생성(멱등). PoC 스키마."""
        with self._conn() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS cases (
                    case_id     TEXT PRIMARY KEY,
                    customer_id TEXT NOT NULL,
                    score       REAL NOT NULL,
                    status      TEXT NOT NULL,
                    ring_id     TEXT NOT NULL DEFAULT '',
                    assignee    TEXT NOT NULL DEFAULT '',
                    created_at  TEXT NOT NULL,
                    updated_at  TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_cases_score ON cases(score DESC);
                CREATE INDEX IF NOT EXISTS idx_cases_status ON cases(status);

                CREATE TABLE IF NOT EXISTS case_history (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    case_id     TEXT NOT NULL,
                    from_status TEXT NOT NULL,
                    to_status   TEXT NOT NULL,
                    actor       TEXT NOT NULL,
                    note        TEXT NOT NULL DEFAULT '',
                    ts          TEXT NOT NULL,
                    FOREIGN KEY(case_id) REFERENCES cases(case_id)
                );
                CREATE INDEX IF NOT EXISTS idx_history_case ON case_history(case_id);

                CREATE TABLE IF NOT EXISTS case_verdicts (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    case_id     TEXT NOT NULL,
                    label       TEXT NOT NULL,
                    actor       TEXT NOT NULL,
                    comment     TEXT NOT NULL DEFAULT '',
                    ts          TEXT NOT NULL,
                    FOREIGN KEY(case_id) REFERENCES cases(case_id)
                );
                CREATE INDEX IF NOT EXISTS idx_verdict_case ON case_verdicts(case_id);
                """
            )

    # --- 생성 ----------------------------------------------------
    def create_case(
        self,
        *,
        case_id: str,
        customer_id: str,
        score: float,
        ring_id: str = "",
        actor: str = "system",
    ) -> Case:
        """케이스 1건 생성(미배정 상태). 멱등 — 동일 case_id 재생성 시 기존 반환.

        모든 생성은 ``case.create`` 로 감사 기록된다.
        """
        existing = self.get_case(case_id)
        if existing is not None:
            return existing
        now = _now_iso()
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO cases(case_id, customer_id, score, status, ring_id, "
                "created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
                (case_id, customer_id, float(score), CaseStatus.UNASSIGNED.value,
                 ring_id, now, now),
            )
        audit_event(
            "case.create", actor, target=case_id,
            meta={"customer_id": customer_id, "score": round(float(score), 1),
                  "ring_id": ring_id},
        )
        return Case(
            case_id=case_id, customer_id=customer_id, score=float(score),
            status=CaseStatus.UNASSIGNED, ring_id=ring_id,
            created_at=now, updated_at=now,
        )

    # --- 점수 갱신 -----------------------------------------------
    def update_score(
        self, case_id: str, score: float, *, actor: str = "system"
    ) -> Case:
        """케이스 리스크 점수만 갱신한다(상태·이력·담당자 불변).

        재학습 모델 활성화 등으로 스코어링 결과가 달라졌을 때, 큐 우선순위를
        최신 점수로 반영하기 위한 경로다. 상태 머신은 건드리지 않으며 점수와
        ``updated_at`` 만 변경한다. 모든 갱신은 ``case.rescore`` 로 감사 기록된다.
        """
        case = self._require(case_id)
        now = _now_iso()
        with self._conn() as conn:
            conn.execute(
                "UPDATE cases SET score = ?, updated_at = ? WHERE case_id = ?",
                (float(score), now, case_id),
            )
        audit_event(
            "case.rescore", actor, target=case_id,
            meta={"old_score": round(case.score, 1),
                  "new_score": round(float(score), 1)},
        )
        case.score = float(score)
        case.updated_at = now
        return case

    # --- 조회 ----------------------------------------------------
    def get_case(self, case_id: str) -> Optional[Case]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM cases WHERE case_id = ?", (case_id,)
            ).fetchone()
        return _row_to_case(row) if row else None

    def queue(
        self,
        *,
        status: Optional[CaseStatus] = None,
        limit: Optional[int] = None,
    ) -> list[Case]:
        """케이스 큐를 리스크 스코어 내림차순으로 반환(조사관 우선순위).

        Args:
            status: 지정 시 해당 상태만 필터.
            limit: 최대 건수.
        """
        sql = "SELECT * FROM cases"
        params: list[Any] = []
        if status is not None:
            sql += " WHERE status = ?"
            params.append(status.value)
        sql += " ORDER BY score DESC, case_id ASC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(int(limit))
        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_row_to_case(r) for r in rows]

    # --- 배정 ----------------------------------------------------
    def assign(self, case_id: str, assignee: str, *, actor: str = "system") -> Case:
        """담당자 배정. 미배정 케이스를 조사중으로 자동 전이한다.

        모든 배정은 ``case.assign`` 으로 감사 기록된다.
        """
        case = self._require(case_id)
        now = _now_iso()
        # 미배정 → 조사중 자동 전이(배정 = 조사 착수).
        new_status = case.status
        if case.status == CaseStatus.UNASSIGNED:
            new_status = CaseStatus.INVESTIGATING
        with self._conn() as conn:
            conn.execute(
                "UPDATE cases SET assignee = ?, status = ?, updated_at = ? "
                "WHERE case_id = ?",
                (assignee, new_status.value, now, case_id),
            )
        if new_status != case.status:
            self._record_history(case_id, case.status, new_status, actor,
                                  note=f"배정에 의한 전이(담당자={assignee})")
        audit_event(
            "case.assign", actor, target=case_id,
            meta={"assignee": assignee, "status": new_status.value},
        )
        case.assignee = assignee
        case.status = new_status
        case.updated_at = now
        return case

    # --- 상태 전이 -----------------------------------------------
    def transition(
        self,
        case_id: str,
        to_status: CaseStatus,
        *,
        actor: str = "system",
        note: str = "",
    ) -> Case:
        """케이스 상태를 전이한다. 유효 전이만 허용(아니면 ``InvalidTransition``).

        모든 전이는 이력 테이블과 ``case.transition`` 감사에 기록된다.
        """
        case = self._require(case_id)
        if not is_valid_transition(case.status, to_status):
            audit_event(
                "case.transition", actor, target=case_id, result="denied",
                meta={"from": case.status.value, "to": to_status.value},
            )
            raise InvalidTransition(
                f"{case.status.value} -> {to_status.value} 는 허용되지 않는 전이입니다"
            )
        now = _now_iso()
        with self._conn() as conn:
            conn.execute(
                "UPDATE cases SET status = ?, updated_at = ? WHERE case_id = ?",
                (to_status.value, now, case_id),
            )
        self._record_history(case_id, case.status, to_status, actor, note=note)
        audit_event(
            "case.transition", actor, target=case_id,
            meta={"from": case.status.value, "to": to_status.value, "note": note},
        )
        case.status = to_status
        case.updated_at = now
        return case

    # --- 판정 피드백 (FR-4.3) ------------------------------------
    def record_verdict(
        self,
        case_id: str,
        label: str,
        *,
        actor: str = "system",
        comment: str = "",
    ) -> Verdict:
        """조사관 판정 라벨(사기/정상)과 코멘트를 기록(FR-4.3).

        판정은 케이스 상태도 FRAUD/NORMAL 로 전이시킨다(유효 전이일 때).
        ground truth(ring_id)와 별개의 **운영 피드백**이다.
        모든 판정은 ``case.verdict`` 로 감사 기록된다.
        """
        label = label.upper()
        if label not in {"FRAUD", "NORMAL"}:
            raise ValueError("label 은 'FRAUD' 또는 'NORMAL' 이어야 합니다")
        case = self._require(case_id)
        now = _now_iso()
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO case_verdicts(case_id, label, actor, comment, ts) "
                "VALUES (?,?,?,?,?)",
                (case_id, label, actor, comment, now),
            )
        # 케이스 상태 전이(유효할 때만). HOLD/INVESTIGATING/UNASSIGNED 에서 가능.
        target_status = CaseStatus.FRAUD if label == "FRAUD" else CaseStatus.NORMAL
        if is_valid_transition(case.status, target_status):
            self.transition(case_id, target_status, actor=actor,
                             note=f"판정({label}): {comment}")
        audit_event(
            "case.verdict", actor, target=case_id,
            meta={"label": label, "comment": comment},
        )
        return Verdict(case_id=case_id, label=label, actor=actor,
                       comment=comment, ts=now)

    def verdicts(self, case_id: str) -> list[Verdict]:
        """케이스의 판정 피드백 이력(시간순)."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM case_verdicts WHERE case_id = ? ORDER BY id ASC",
                (case_id,),
            ).fetchall()
        return [Verdict(case_id=r["case_id"], label=r["label"], actor=r["actor"],
                        comment=r["comment"], ts=r["ts"]) for r in rows]

    # --- 이력 ----------------------------------------------------
    def history(self, case_id: str) -> list[HistoryEntry]:
        """케이스 상태변경 이력(시간순)."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM case_history WHERE case_id = ? ORDER BY id ASC",
                (case_id,),
            ).fetchall()
        return [HistoryEntry(case_id=r["case_id"], from_status=r["from_status"],
                             to_status=r["to_status"], actor=r["actor"],
                             note=r["note"], ts=r["ts"]) for r in rows]

    # --- 내부 ----------------------------------------------------
    def _require(self, case_id: str) -> Case:
        case = self.get_case(case_id)
        if case is None:
            raise CaseNotFound(f"케이스 없음: {case_id}")
        return case

    def _record_history(
        self, case_id: str, src: CaseStatus, dst: CaseStatus, actor: str, note: str
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO case_history(case_id, from_status, to_status, actor, "
                "note, ts) VALUES (?,?,?,?,?,?)",
                (case_id, src.value, dst.value, actor, note, _now_iso()),
            )


def _row_to_case(row: sqlite3.Row) -> Case:
    return Case(
        case_id=row["case_id"],
        customer_id=row["customer_id"],
        score=float(row["score"]),
        status=CaseStatus(row["status"]),
        ring_id=row["ring_id"],
        assignee=row["assignee"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
