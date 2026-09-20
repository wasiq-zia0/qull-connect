"""SQLite storage for the bill-negotiator connector. File-backed only (data/app.db)."""
import json
import sqlite3
import uuid
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "app.db"
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
    if "owner_id" not in cols:
        conn.execute("ALTER TABLE cases ADD COLUMN owner_id TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cases_owner ON cases(owner_id)")


def claim_case(cid: str, owner_id: str) -> dict | None:
    """Bind an ownerless (life-event draft) case to the first owner who
    presents its unguessable ID. Returns the case, or None if it does not
    exist or is already owned by someone else."""
    conn = _connect()
    try:
        conn.execute("UPDATE cases SET owner_id = ? WHERE id = ? AND owner_id IS NULL",
                     (owner_id, cid))
        conn.commit()
    finally:
        conn.close()
    return get_case(cid, owner_id)


# Columns update_case may write (defense in depth: no caller can ever set an
# arbitrary column; see the **fields SQL pattern noted in the security audit).
_UPDATE_ALLOWED = frozenset({
    "provider", "service_type", "current_monthly_bill", "promo_end_date",
    "account_tenure_months", "user_name", "stripe_customer_id",
    "setup_intent_id", "billing_status", "new_monthly_bill",
    "outcome_reported_at", "months_locked", "savings", "fee_cents",
    "payment_intent_id",
})


def _row_to_case(row: sqlite3.Row) -> dict:
    return dict(row)


def create_case(provider: str, service_type: str, current_monthly_bill: float,
                promo_end_date: str | None, account_tenure_months: int | None,
                user_name: str | None, owner_id: str | None = None) -> dict:
    from datetime import datetime, timezone
    cid = uuid.uuid4().hex[:12]
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
