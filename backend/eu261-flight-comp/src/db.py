"""SQLite persistence for eu261-flight-comp. File-backed at data/app.db — NEVER in-memory."""
from __future__ import annotations

import json
import sqlite3
import os
import uuid
from pathlib import Path

DB_PATH = Path(os.environ.get("DATA_DIR", Path(__file__).resolve().parent.parent / "data")) / "app.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS claims (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    payload TEXT NOT NULL,
    stripe_customer_id TEXT,
    billing_status TEXT NOT NULL DEFAULT 'none',   -- none | card_pending | ready | fee_charged
    payout_amount_eur REAL,
    fee_amount_eur REAL,
    payment_intent_id TEXT
);
"""


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute(SCHEMA)
    _migrate(conn)
    conn.commit()
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Tenant isolation: every claim carries ``owner_id``.

    Claims created via the service bus (``POST /api/life-events``, service-key
    auth, no user) store ``owner_id=NULL``.
    """
    cols = [r[1] for r in conn.execute("PRAGMA table_info(claims)").fetchall()]
    if "checkout_session_id" not in cols:
        conn.execute("ALTER TABLE claims ADD COLUMN checkout_session_id TEXT")
    if "fee_terms_accepted_at" not in cols:
        conn.execute("ALTER TABLE claims ADD COLUMN fee_terms_accepted_at TEXT")
    if "fee_confirmed_at" not in cols:
        conn.execute("ALTER TABLE claims ADD COLUMN fee_confirmed_at TEXT")
    if "setup_intent_id" not in cols:
        conn.execute("ALTER TABLE claims ADD COLUMN setup_intent_id TEXT")
    if "owner_id" not in cols:
        conn.execute("ALTER TABLE claims ADD COLUMN owner_id TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_claims_owner ON claims(owner_id)")


def create_claim(payload: dict, owner_id: str | None = None) -> str:
    cid = uuid.uuid4().hex
    with connect() as conn:
        conn.execute("INSERT INTO claims (id, payload, owner_id) VALUES (?, ?, ?)",
                     (cid, json.dumps(payload), owner_id))
    return cid


def get_claim(cid: str, owner_id: str | None = None) -> dict | None:
    sql = "SELECT * FROM claims WHERE id = ?"
    params: list = [cid]
    if owner_id is not None:
        sql += " AND owner_id = ?"
        params.append(owner_id)
    with connect() as conn:
        row = conn.execute(sql, params).fetchone()
    if not row:
        return None
    out = dict(row)
    out["payload"] = json.loads(out["payload"])
    return out


def update_claim(cid: str, owner_id: str | None = None, **fields) -> None:
    allowed = {"stripe_customer_id", "billing_status", "payout_amount_eur",
               "fee_amount_eur", "payment_intent_id", "payload", 'checkout_session_id', 'fee_terms_accepted_at', 'fee_confirmed_at', 'setup_intent_id'}
    cols = [k for k in fields if k in allowed]
    if not cols:
        return
    vals = [json.dumps(fields[k]) if k == "payload" else fields[k] for k in cols]
    sql = f"UPDATE claims SET {', '.join(f'{c} = ?' for c in cols)} WHERE id = ?"
    params: list = [*vals, cid]
    if owner_id is not None:
        sql += " AND owner_id = ?"
        params.append(owner_id)
    if fields.get("billing_status") and fields["billing_status"] != "fee_charged":
        sql += " AND (billing_status IS NULL OR billing_status != 'fee_charged')"
    with connect() as conn:
        conn.execute(sql, params)


def export_owner(owner_id: str) -> dict:
    if not owner_id:
        raise ValueError("An owner is required")
    with connect() as conn:
        return {table: [dict(row) for row in conn.execute(f"SELECT * FROM {table} WHERE owner_id = ?", (owner_id,)).fetchall()]
                for table in ('claims',)}


def delete_owner(owner_id: str) -> dict:
    if not owner_id:
        raise ValueError("An owner is required")
    with connect() as conn:
        rows = {table: [dict(row) for row in conn.execute(f"SELECT * FROM {table} WHERE owner_id = ?", (owner_id,)).fetchall()]
                for table in ('claims',)}
        for table in ('claims',):
            conn.execute(f"DELETE FROM {table} WHERE owner_id = ?", (owner_id,))
        conn.commit()
    return rows


def complete_claim(cid: str, owner_id: str, payload: dict) -> bool:
    with connect() as conn:
        result = conn.execute("UPDATE claims SET payload = ? WHERE id = ? AND owner_id = ? AND fee_confirmed_at IS NULL", (json.dumps(payload), cid, owner_id))
        return result.rowcount == 1


def reserve_fee(cid: str, owner_id: str, payload: dict, confirmed_at: str) -> bool:
    with connect() as conn:
        result = conn.execute("UPDATE claims SET fee_confirmed_at = ? WHERE id = ? AND owner_id = ? AND payload = ?", (confirmed_at, cid, owner_id, json.dumps(payload)))
        return result.rowcount == 1
