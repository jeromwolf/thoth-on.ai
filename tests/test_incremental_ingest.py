"""증분 적재(incremental ingest) 단위 검증.

Neo4j 불필요한 순수 단위(@pytest.mark.smoke):
  - 워터마크 헬퍼(_max_created_at, _filter_since)
  - 상태 입출력(read/write_ingest_state) 라운드트립

선택 통합(@pytest.mark.integration): 실제 Neo4j 필요.
"""
import csv
import json
from pathlib import Path

import pytest

from ingest.loader import (
    _filter_since,
    _max_created_at,
    read_ingest_state,
    write_ingest_state,
)

pytestmark = pytest.mark.smoke


# ==================================================================
# _max_created_at
# ==================================================================
def test_max_created_at_normal():
    rows = [
        {"created_at": "2026-01-01T00:00:00"},
        {"created_at": "2026-03-15T12:00:00"},
        {"created_at": "2026-02-10T00:00:00"},
    ]
    assert _max_created_at(rows) == "2026-03-15T12:00:00"


def test_max_created_at_empty_rows():
    assert _max_created_at([]) is None


def test_max_created_at_missing_and_blank_ignored():
    rows = [
        {"created_at": ""},
        {"foo": "bar"},  # created_at 없음
        {"created_at": "2026-05-01T00:00:00"},
        {"created_at": None},
    ]
    assert _max_created_at(rows) == "2026-05-01T00:00:00"


def test_max_created_at_all_blank():
    rows = [{"created_at": ""}, {"created_at": None}, {"foo": "x"}]
    assert _max_created_at(rows) is None


# ==================================================================
# _filter_since
# ==================================================================
def test_filter_since_none_returns_all_no_skip():
    rows = [
        {"id": 1, "created_at": "2026-01-01T00:00:00"},
        {"id": 2, "created_at": ""},
        {"id": 3},
    ]
    filtered, skipped = _filter_since(rows, None)
    assert filtered == rows
    assert skipped == 0


def test_filter_since_strictly_greater():
    rows = [
        {"id": 1, "created_at": "2026-01-01T00:00:00"},  # <  watermark
        {"id": 2, "created_at": "2026-02-01T00:00:00"},  # == watermark
        {"id": 3, "created_at": "2026-03-01T00:00:00"},  # >  watermark
    ]
    watermark = "2026-02-01T00:00:00"
    filtered, skipped = _filter_since(rows, watermark)
    assert [r["id"] for r in filtered] == [3]
    assert skipped == 2  # id1(과거) + id2(동일) 모두 skip


def test_filter_since_blank_created_at_skipped_when_watermark():
    rows = [
        {"id": 1, "created_at": ""},        # 비어있음 → skip
        {"id": 2},                           # created_at 없음 → skip
        {"id": 3, "created_at": None},       # None → skip
        {"id": 4, "created_at": "2026-09-09T00:00:00"},  # 통과
    ]
    filtered, skipped = _filter_since(rows, "2026-01-01T00:00:00")
    assert [r["id"] for r in filtered] == [4]
    assert skipped == 3


# ==================================================================
# read/write_ingest_state 라운드트립
# ==================================================================
def test_read_ingest_state_missing_returns_default(tmp_path: Path):
    state = read_ingest_state(tmp_path / "nope.json")
    assert state == {"last_watermark": None, "runs": []}


def test_write_read_roundtrip(tmp_path: Path):
    state_path = tmp_path / "sub" / "ingest_state.json"  # 부모 mkdir 검증
    state = {
        "last_watermark": "2026-06-18T00:00:00",
        "runs": [
            {
                "watermark_before": None,
                "watermark_after": "2026-06-18T00:00:00",
                "counts": {"node:Customer": 3, "edge:FILED": 2},
                "skipped": 0,
            }
        ],
    }
    write_ingest_state(state, state_path)
    assert state_path.exists()

    loaded = read_ingest_state(state_path)
    assert loaded == state


def test_write_state_is_valid_json_utf8(tmp_path: Path):
    state_path = tmp_path / "state.json"
    write_ingest_state({"last_watermark": "한글-워터마크", "runs": []}, state_path)
    raw = state_path.read_text(encoding="utf-8")
    parsed = json.loads(raw)
    assert parsed["last_watermark"] == "한글-워터마크"
    # ensure_ascii=False 검증: 한글이 이스케이프되지 않고 그대로 저장됨
    assert "한글-워터마크" in raw


# ==================================================================
# 통합(선택) — 실제 Neo4j 필요, 2회차 델타=0 검증
# ==================================================================
def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


@pytest.mark.integration
def test_load_incremental_second_run_no_delta(tmp_path: Path):
    """동일 데이터로 2회 호출 시 2회차 노드 델타가 0 인지 검증(Neo4j 필요)."""
    from ingest.loader import load_incremental

    data_dir = tmp_path / "data"
    # 최소 소스 — created_at 보유.
    _write_csv(data_dir / "customers.csv", [
        {"customer_id": "C1", "name": "a", "id_number": "1", "email": "a@x",
         "birth_date": "1990-01-01", "gender": "M", "address": "서울시 강남구 1",
         "phone_number": "010-1111-2222", "created_at": "2026-01-01T00:00:00",
         "is_fraud_ring": "false", "ring_id": "", "ring_pattern": ""},
    ])
    _write_csv(data_dir / "claims.csv", [
        {"claim_id": "CL1", "customer_id": "C1", "incident_date": "2026-01-02",
         "report_date": "2026-01-03", "incident_type": "collision",
         "incident_location": "서울", "claimed_amount": "100", "paid_amount": "90",
         "claim_status": "paid", "fraud_label": "false", "created_at": "2026-01-03T00:00:00"},
    ])
    for name in ("policies", "vehicles", "accounts", "hospitals", "repair_shops"):
        _write_csv(data_dir / f"{name}.csv", [{"created_at": "2026-01-01T00:00:00"}])

    state_path = tmp_path / "ingest_state.json"

    first = load_incremental(data_dir, state_path=state_path)
    assert first["node:Customer"] == 1
    assert first["node:Claim"] == 1

    second = load_incremental(data_dir, state_path=state_path)
    assert second["node:Customer"] == 0
    assert second["node:Claim"] == 0
    assert second["_skipped"] >= 2
