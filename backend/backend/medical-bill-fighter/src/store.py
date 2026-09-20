"""SQLite persistence for medical-bill-fighter.

Database: data/app.db (file-based, never in-memory). All queries are
parameterized — user input never alters query structure.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

_DB_PATH = Path(__file__).resolve().parents[1] / "data" / "app.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cases (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    intake_json TEXT NOT NULL,
    findings_json TEXT NOT NULL DEFAULT '[]',
    outcome_json TEXT,
    stripe_customer_id TEXT,
    fee_cents INTEGER,
    fee_status TEXT,
    owner_id TEXT
);
"""


def _db() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    # tenant-isolation migration: owner_id + index
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(cases)")}
    if "owner_id" not in cols:
        conn.execute("ALTER TABLE cases ADD COLUMN owner_id TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cases_owner ON cases(owner_id)")
    return conn


def create_case(intake: dict[str, Any], findings: list[dict[str, Any]],
                owner_id: Optional[str] = None) -> str:
    case_id = uuid.uuid4().hex[:12]
    now = datetime.now(timezone.utc).isoformat()
    with _db() as conn:
        conn.execute(
            "INSERT INTO cases (id, created_at, intake_json, findings_json, owner_id)"
            " VALUES (?, ?, ?, ?, ?)",
            (case_id, now, json.dumps(intake), json.dumps(findings), owner_id),
        )
    return case_id


def _row_to_case(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "created_at": row["created_at"],
        "intake": json.loads(row["intake_json"]),
        "findings": json.loads(row["findings_json"]),
        "outcome": json.loads(row["outcome_json"]) if row["outcome_json"] else None,
        "stripe_customer_id": row["stripe_customer_id"],
        "fee_cents": row["fee_cents"],
        "fee_status": row["fee_status"],
        "owner_id": row["owner_id"],
    }


def get_case(case_id: str, owner_id: str) -> Optional[dict[str, Any]]:
    """Owner-scoped read: returns the case only if it belongs to owner_id."""
    with _db() as conn:
        row = conn.execute("SELECT * FROM cases WHERE id = ? AND owner_id = ?",
                           (case_id, owner_id)).fetchone()
    if row is None:
        return None
    return _row_to_case(row)


def get_case_unscoped(case_id: str) -> Optional[dict[str, Any]]:
    """Unscoped read for the draft-adoption path only. Not for user responses."""
    with _db() as conn:
        row = conn.execute("SELECT id, owner_id FROM cases WHERE id = ?",
                           (case_id,)).fetchone()
    return dict(row) if row else None


def adopt_case(case_id: str, owner_id: str) -> bool:
    """Claim an ownerless (life-event draft) case for the first user who touches it."""
    with _db() as conn:
        cur = conn.execute("UPDATE cases SET owner_id = ? WHERE id = ? AND owner_id IS NULL",
                           (owner_id, case_id))
    return cur.rowcount > 0


def set_findings(case_id: str, findings: list[dict[str, Any]], owner_id: str) -> bool:
    with _db() as conn:
        cur = conn.execute(
            "UPDATE cases SET findings_json = ? WHERE id = ? AND owner_id = ?",
            (json.dumps(findings), case_id, owner_id),
        )
    return cur.rowcount > 0


def set_outcome(case_id: str, outcome: dict[str, Any], owner_id: str) -> bool:
    with _db() as conn:
        cur = conn.execute(
            "UPDATE cases SET outcome_json = ? WHERE id = ? AND owner_id = ?",
            (json.dumps(outcome), case_id, owner_id),
        )
    return cur.rowcount > 0


def set_billing(case_id: str, owner_id: str,
                stripe_customer_id: Optional[str] = None,
                fee_cents: Optional[int] = None,
                fee_status: Optional[str] = None) -> bool:
    sets, vals = [], []
    if stripe_customer_id is not None:
        sets.append("stripe_customer_id = ?")
        vals.append(stripe_customer_id)
    if fee_cents is not None:
        sets.append("fee_cents = ?")
        vals.append(fee_cents)
    if fee_status is not None:
        sets.append("fee_status = ?")
        vals.append(fee_status)
    if not sets:
        return False
    vals.extend([case_id, owner_id])
    with _db() as conn:
        cur = conn.execute(
            f"UPDATE cases SET {', '.join(sets)} WHERE id = ? AND owner_id = ?", vals)
    return cur.rowcount > 0
