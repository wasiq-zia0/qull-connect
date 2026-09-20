"""SQLite persistence for eu261-flight-comp. File-backed at data/app.db — NEVER in-memory."""
from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "app.db"

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
    if "owner_id" not in cols:
        conn.execute("ALTER TABLE claims ADD COLUMN owner_id TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_claims_owner ON claims(owner_id)")


def create_claim(payload: dict, owner_id: str | None = None) -> str:
    cid = uuid.uuid4().hex[:12]
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
               "fee_amount_eur", "payment_intent_id", "payload"}
    cols = [k for k in fields if k in allowed]
    if not cols:
        return
    vals = [json.dumps(fields[k]) if k == "payload" else fields[k] for k in cols]
    sql = f"UPDATE claims SET {', '.join(f'{c} = ?' for c in cols)} WHERE id = ?"
    params: list = [*vals, cid]
    if owner_id is not None:
        sql += " AND owner_id = ?"
        params.append(owner_id)
    with connect() as conn:
        conn.execute(sql, params)
