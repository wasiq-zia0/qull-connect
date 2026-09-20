"""SQLite storage for the final-paycheck connector. Never in-memory."""
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("FINAL_PAYCHECK_DB", Path(os.environ.get("DATA_DIR", BASE_DIR / "data")) / "app.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS cases (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    employee_name TEXT NOT NULL,
    employee_email TEXT NOT NULL DEFAULT '',
    employer_name TEXT NOT NULL,
    employer_address TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL,
    last_day_worked TEXT NOT NULL,
    termination_type TEXT NOT NULL,
    wages_owed_cents INTEGER NOT NULL,
    pay_period TEXT NOT NULL DEFAULT '',
    next_payday TEXT DEFAULT NULL,
    forwarding_address TEXT NOT NULL DEFAULT '',
    deadline TEXT DEFAULT NULL,
    status TEXT NOT NULL DEFAULT 'waiting',
    stripe_customer_id TEXT DEFAULT NULL,
    stripe_setup_intent_id TEXT DEFAULT NULL,
    recovered_amount_cents INTEGER DEFAULT NULL,
    fee_cents INTEGER DEFAULT NULL,
    fee_payment_intent_id TEXT DEFAULT NULL,
    fee_status TEXT NOT NULL DEFAULT 'none',
    demand_letter_path TEXT DEFAULT NULL,
    demand_letter_at TEXT DEFAULT NULL,
    is_draft INTEGER NOT NULL DEFAULT 0,
    raw_intake TEXT NOT NULL DEFAULT '{}'
);
"""


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(cases)").fetchall()]
        if "checkout_session_id" not in cols:
            conn.execute("ALTER TABLE cases ADD COLUMN checkout_session_id TEXT")
        if "fee_terms_accepted_at" not in cols:
            conn.execute("ALTER TABLE cases ADD COLUMN fee_terms_accepted_at TEXT")
        if "fee_confirmed_at" not in cols:
            conn.execute("ALTER TABLE cases ADD COLUMN fee_confirmed_at TEXT")
        if "is_draft" not in cols:  # migrate DBs created before drafts existed
            conn.execute("ALTER TABLE cases ADD COLUMN is_draft INTEGER NOT NULL DEFAULT 0")
        if "owner_id" not in cols:  # tenant isolation: every case has exactly one owner
            conn.execute("ALTER TABLE cases ADD COLUMN owner_id TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_cases_owner ON cases(owner_id)")


def claim_case(case_id: str, owner_id: str) -> dict | None:
    """Legacy rows without a verified owner are never claimable by record ID."""
    return get_case(case_id, owner_id)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_case(data: dict, owner_id: str | None = None) -> dict:
    """Create a case. Draft cases (from life events) may omit fields;
    missing values default to empty/zero and are filled on confirm."""
    case_id = uuid.uuid4().hex
    row = {
        "id": case_id,
        "created_at": _now(),
        "owner_id": owner_id,
        "employee_name": data.get("employee_name", ""),
        "employee_email": data.get("employee_email", ""),
        "employer_name": data.get("employer_name", ""),
        "employer_address": data.get("employer_address", ""),
        "state": data["state"].upper(),
        "last_day_worked": data.get("last_day_worked", ""),
        "termination_type": data.get("termination_type", "fired"),
        "wages_owed_cents": data.get("wages_owed_cents", 0),
        "pay_period": data.get("pay_period", ""),
        "next_payday": data.get("next_payday"),
        "forwarding_address": data.get("forwarding_address", ""),
        "deadline": data.get("deadline"),
        "status": data.get("status", "waiting"),
        "is_draft": data.get("is_draft", 0),
        "raw_intake": json.dumps(data.get("raw_intake", data)),
    }
    with connect() as conn:
        conn.execute(
            """INSERT INTO cases (id, created_at, owner_id, employee_name, employee_email,
               employer_name, employer_address, state, last_day_worked,
               termination_type, wages_owed_cents, pay_period, next_payday,
               forwarding_address, deadline, status, is_draft, raw_intake)
               VALUES (:id, :created_at, :owner_id, :employee_name, :employee_email,
               :employer_name, :employer_address, :state, :last_day_worked,
               :termination_type, :wages_owed_cents, :pay_period, :next_payday,
               :forwarding_address, :deadline, :status, :is_draft, :raw_intake)""",
            row,
        )
    return get_case(case_id)


def get_case(case_id: str, owner_id: str | None = None) -> dict | None:
    with connect() as conn:
        if owner_id is None:
            row = conn.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM cases WHERE id = ? AND owner_id = ?",
                (case_id, owner_id)).fetchone()
    return dict(row) if row else None


def update_case(case_id: str, fields: dict, owner_id: str | None = None) -> dict:
    allowed = {r[1] for r in connect().execute("PRAGMA table_info(cases)")} - {"id", "owner_id", "created_at"}
    if not fields or set(fields) - allowed:
        raise ValueError("Invalid case update columns")
    sets = ", ".join(f"{k} = :{k}" for k in fields)
    fields = dict(fields)
    fields["id"] = case_id
    sql = f"UPDATE cases SET {sets} WHERE id = :id"
    if owner_id is not None:
        sql += " AND owner_id = :owner_id"
        fields["owner_id"] = owner_id
    if fields.get("fee_status") and fields["fee_status"] != "charged":
        sql += " AND fee_status != 'charged'"
    with connect() as conn:
        conn.execute(sql, fields)
    return get_case(case_id, owner_id)


def list_cases(owner_id: str | None = None) -> list[dict]:
    with connect() as conn:
        if owner_id is None:
            rows = conn.execute("SELECT * FROM cases ORDER BY created_at DESC").fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM cases WHERE owner_id = ? ORDER BY created_at DESC",
                (owner_id,)).fetchall()
    return [dict(r) for r in rows]


def export_owner(owner_id: str) -> dict:
    if not owner_id:
        raise ValueError("An owner is required")
    with connect() as conn:
        return {table: [dict(row) for row in conn.execute(f"SELECT * FROM {table} WHERE owner_id = ?", (owner_id,)).fetchall()]
                for table in ('cases',)}


def delete_owner(owner_id: str) -> dict:
    if not owner_id:
        raise ValueError("An owner is required")
    with connect() as conn:
        rows = {table: [dict(row) for row in conn.execute(f"SELECT * FROM {table} WHERE owner_id = ?", (owner_id,)).fetchall()]
                for table in ('cases',)}
        for table in ('cases',):
            conn.execute(f"DELETE FROM {table} WHERE owner_id = ?", (owner_id,))
        conn.commit()
    return rows


def activate_draft(case_id: str, owner_id: str, fields: dict) -> bool:
    allowed = {"employee_name", "employer_name", "state", "last_day_worked", "termination_type", "wages_owed_cents", "next_payday", "forwarding_address", "is_draft"}
    if set(fields) - allowed:
        raise ValueError("Invalid draft completion fields")
    with connect() as conn:
        result = conn.execute(f"UPDATE cases SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ? AND owner_id = ? AND is_draft = 1 AND fee_confirmed_at IS NULL", [*fields.values(), case_id, owner_id])
        return result.rowcount == 1
