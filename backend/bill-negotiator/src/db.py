"""SQLite storage for the bill-negotiator connector. File-backed only (data/app.db)."""
import json
import sqlite3
import os
import uuid
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.environ.get("DATA_DIR", BASE_DIR / "data")) / "app.db"
SCHEMA = """
CREATE TABLE IF NOT EXISTS cases (
    id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    service_type TEXT NOT NULL,
    current_monthly_bill REAL NOT NULL,
    promo_end_date TEXT,
    account_tenure_months INTEGER,
    user_name TEXT,
    created_at TEXT NOT NULL,
    stripe_customer_id TEXT,
    billing_status TEXT NOT NULL DEFAULT 'not_set_up',
    new_monthly_bill REAL,
    outcome_reported_at TEXT,
    months_locked INTEGER,
    savings REAL,
    fee_rate REAL NOT NULL DEFAULT 0.35,
    fee_cents INTEGER,
    payment_intent_id TEXT,
    setup_intent_id TEXT
);
"""


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = _connect()
    try:
        conn.executescript(SCHEMA)
        _migrate(conn)
        conn.commit()
    finally:
        conn.close()


def _migrate(conn: sqlite3.Connection) -> None:
    """Tenant isolation: every case belongs to exactly one owner."""
    cols = [r[1] for r in conn.execute("PRAGMA table_info(cases)").fetchall()]
    if "checkout_session_id" not in cols:
        conn.execute("ALTER TABLE cases ADD COLUMN checkout_session_id TEXT")
    if "fee_terms_accepted_at" not in cols:
        conn.execute("ALTER TABLE cases ADD COLUMN fee_terms_accepted_at TEXT")
    if "fee_confirmed_at" not in cols:
        conn.execute("ALTER TABLE cases ADD COLUMN fee_confirmed_at TEXT")
    if "owner_id" not in cols:
        conn.execute("ALTER TABLE cases ADD COLUMN owner_id TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cases_owner ON cases(owner_id)")


def claim_case(cid: str, owner_id: str) -> dict | None:
    """Legacy rows without a verified owner are never claimable by record ID."""
    return get_case(cid, owner_id)


_UPDATE_ALLOWED = frozenset({
    "provider", "service_type", "current_monthly_bill", "promo_end_date", "account_tenure_months",
    "user_name", "stripe_customer_id", "setup_intent_id", "billing_status", "new_monthly_bill",
    "outcome_reported_at", "months_locked", "savings", "fee_cents", "payment_intent_id",
    "checkout_session_id", "fee_terms_accepted_at", "fee_confirmed_at",
})


def _row_to_case(row: sqlite3.Row) -> dict:
    return dict(row)


def create_case(provider: str, service_type: str, current_monthly_bill: float,
                promo_end_date: str | None, account_tenure_months: int | None,
                user_name: str | None, owner_id: str | None = None) -> dict:
    from datetime import datetime, timezone
    cid = uuid.uuid4().hex
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO cases (id, provider, service_type, current_monthly_bill, "
            "promo_end_date, account_tenure_months, user_name, created_at, fee_rate, owner_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0.35, ?)",
            (cid, provider, service_type, current_monthly_bill, promo_end_date,
             account_tenure_months, user_name,
             datetime.now(timezone.utc).isoformat(), owner_id),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM cases WHERE id = ?", (cid,)).fetchone()
        return _row_to_case(row)
    finally:
        conn.close()


def get_case(cid: str, owner_id: str | None = None) -> dict | None:
    conn = _connect()
    try:
        if owner_id is None:
            row = conn.execute("SELECT * FROM cases WHERE id = ?", (cid,)).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM cases WHERE id = ? AND owner_id = ?", (cid, owner_id)).fetchone()
        return _row_to_case(row) if row else None
    finally:
        conn.close()


def update_case(cid: str, owner_id: str | None = None, **fields) -> dict | None:
    if not fields:
        return get_case(cid, owner_id)
    unknown = set(fields) - _UPDATE_ALLOWED
    if unknown:
        raise ValueError(f"refusing to update unknown columns: {sorted(unknown)}")
    cols = ", ".join(f"{k} = ?" for k in fields)
    conn = _connect()
    try:
        sql = f"UPDATE cases SET {cols} WHERE id = ?"
        params: tuple = (*fields.values(), cid)
        if owner_id is not None:
            sql += " AND owner_id = ?"
            params = (*params, owner_id)
        if fields.get("billing_status") and fields["billing_status"] != "fee_charged":
            sql += " AND (billing_status IS NULL OR billing_status != 'fee_charged')"
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()
    return get_case(cid, owner_id)


def serialize(case: dict) -> dict:
    """Public shape of a case (drops nothing sensitive; no raw secrets stored)."""
    return {
        "case_id": case["id"],
        "provider": case["provider"],
        "service_type": case["service_type"],
        "current_monthly_bill": case["current_monthly_bill"],
        "promo_end_date": case["promo_end_date"],
        "account_tenure_months": case["account_tenure_months"],
        "user_name": case["user_name"],
        "created_at": case["created_at"],
        "billing_status": case["billing_status"],
        "stripe_customer_id": case["stripe_customer_id"],
        "outcome": {
            "new_monthly_bill": case["new_monthly_bill"],
            "months_locked": case["months_locked"],
            "reported_at": case["outcome_reported_at"],
        } if case["new_monthly_bill"] is not None else None,
        "savings": case["savings"],
        "fee_rate": case["fee_rate"],
        "fee_cents": case["fee_cents"],
        "payment_intent_id": case["payment_intent_id"],
    }


def summary_json(case: dict) -> str:
    return json.dumps(serialize(case), sort_keys=True)


def export_owner(owner_id: str) -> dict:
    if not owner_id:
        raise ValueError("An owner is required")
    with _connect() as conn:
        return {table: [dict(row) for row in conn.execute(f"SELECT * FROM {table} WHERE owner_id = ?", (owner_id,)).fetchall()]
                for table in ('cases',)}


def delete_owner(owner_id: str) -> dict:
    if not owner_id:
        raise ValueError("An owner is required")
    with _connect() as conn:
        rows = {table: [dict(row) for row in conn.execute(f"SELECT * FROM {table} WHERE owner_id = ?", (owner_id,)).fetchall()]
                for table in ('cases',)}
        for table in ('cases',):
            conn.execute(f"DELETE FROM {table} WHERE owner_id = ?", (owner_id,))
        conn.commit()
    return rows


def update_outcome(cid: str, owner_id: str, **fields) -> bool:
    if set(fields) - {"new_monthly_bill", "months_locked", "savings", "outcome_reported_at"}:
        raise ValueError("Invalid outcome fields")
    with _connect() as conn:
        result = conn.execute(f"UPDATE cases SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ? AND owner_id = ? AND fee_confirmed_at IS NULL AND billing_status != 'fee_charged'", [*fields.values(), cid, owner_id])
        conn.commit()
        return result.rowcount == 1


def reserve_fee(cid: str, owner_id: str, outcome_version: str, confirmed_at: str) -> bool:
    with _connect() as conn:
        result = conn.execute("UPDATE cases SET fee_confirmed_at = ? WHERE id = ? AND owner_id = ? AND outcome_reported_at = ?", (confirmed_at, cid, owner_id, outcome_version))
        conn.commit()
        return result.rowcount == 1
