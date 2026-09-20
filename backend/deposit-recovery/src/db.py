"""SQLite persistence for deposit-recovery cases and life-event drafts.

All values go through parameterized queries — user input never touches SQL
structure (column names come from fixed whitelists only).

Tenant isolation: every case and draft carries ``owner_id`` (the platform
user id resolved by ``src.identity``). All new REST and MCP rows, including life-event drafts, require a verified
owner. Legacy NULL-owner rows remain inaccessible until an operator can
establish ownership outside the public API.
"""
import json
import sqlite3
import os
from pathlib import Path

DB_PATH = Path(os.environ.get("DATA_DIR", Path(__file__).resolve().parent.parent / "data")) / "app.db"

_CASES_SCHEMA = """
CREATE TABLE IF NOT EXISTS cases (
    id TEXT PRIMARY KEY,
    tenant_name TEXT NOT NULL,
    tenant_forwarding_address TEXT NOT NULL,
    state TEXT NOT NULL,
    move_out TEXT NOT NULL,
    forwarding_date TEXT,
    deposit REAL NOT NULL,
    landlord_name TEXT NOT NULL,
    landlord_address TEXT NOT NULL,
    rental_address TEXT NOT NULL,
    stripe_customer_id TEXT,
    billing_status TEXT,
    amount_recovered REAL,
    fee_charged_cents INTEGER,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
"""

_DRAFTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS drafts (
    id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    payload TEXT NOT NULL,
    state TEXT,
    tenant_name TEXT,
    tenant_forwarding_address TEXT,
    move_out TEXT,
    forwarding_date TEXT,
    deposit REAL,
    landlord_name TEXT,
    landlord_address TEXT,
    rental_address TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
"""

_CASE_COLUMNS = (
    "tenant_name", "tenant_forwarding_address", "state", "move_out", "tenancy_end",
    "forwarding_date", "deposit", "landlord_name", "landlord_address",
    "rental_address", "stripe_customer_id", "billing_status",
    "amount_recovered", "fee_charged_cents", "letter_status",
    'checkout_session_id', 'fee_terms_accepted_at', 'fee_confirmed_at', 'setup_intent_id', 'payment_intent_id',
)

# owner_id is set at insert and on claim/promote only — never via update_case.
_CASE_INSERT_COLUMNS = _CASE_COLUMNS + ("owner_id",)

_DRAFT_COLUMNS = (
    "event_type", "payload", "state", "tenant_name", "tenant_forwarding_address",
    "move_out", "tenancy_end", "forwarding_date", "deposit", "landlord_name",
    "landlord_address", "rental_address",
)

_DRAFT_INSERT_COLUMNS = _DRAFT_COLUMNS + ("owner_id",)


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
    conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{table}_{column} ON {table}({column})")


def init_db() -> None:
    with _connect() as conn:
        conn.execute(_CASES_SCHEMA)
        conn.execute(_DRAFTS_SCHEMA)
        _ensure_column(conn, "cases", "tenancy_end", "TEXT")
        _ensure_column(conn, "drafts", "tenancy_end", "TEXT")
        _ensure_column(conn, "cases", "checkout_session_id", "TEXT")
        _ensure_column(conn, "cases", "fee_terms_accepted_at", "TEXT")
        _ensure_column(conn, "cases", "fee_confirmed_at", "TEXT")
        _ensure_column(conn, "cases", "setup_intent_id", "TEXT")
        _ensure_column(conn, "cases", "payment_intent_id", "TEXT")

        _ensure_column(conn, "cases", "owner_id", "TEXT")
        _ensure_column(conn, "cases", "letter_status", "TEXT")
        _ensure_column(conn, "drafts", "owner_id", "TEXT")
        conn.commit()


def _insert(table: str, row_id: str, fields: dict, columns: tuple) -> None:
    vals = {c: fields.get(c) for c in columns}
    cols = ["id", *columns]
    with _connect() as conn:
        conn.execute(
            f"INSERT INTO {table} ({', '.join(cols)}) "  # table/cols are fixed whitelists
            f"VALUES ({', '.join('?' for _ in cols)})",
            [row_id, *[vals[c] for c in columns]],
        )
        conn.commit()


def _get(table: str, row_id: str, owner_id: str | None = None) -> dict | None:
    sql = f"SELECT * FROM {table} WHERE id = ?"  # table is a fixed literal
    params: list = [row_id]
    if owner_id is not None:
        sql += " AND owner_id = ?"
        params.append(owner_id)
    with _connect() as conn:
        row = conn.execute(sql, params).fetchone()
    return dict(row) if row else None


def insert_case(case_id: str, fields: dict, owner_id: str | None = None) -> None:
    _insert("cases", case_id, {**fields, "owner_id": owner_id}, _CASE_INSERT_COLUMNS)


def get_case(case_id: str, owner_id: str | None = None) -> dict | None:
    return _get("cases", case_id, owner_id)


def update_case(case_id: str, fields: dict, owner_id: str | None = None) -> None:
    fields = {k: v for k, v in fields.items() if k in _CASE_COLUMNS}
    if not fields:
        return
    sets = ", ".join(f"{k} = ?" for k in fields)  # keys are whitelisted
    sql = f"UPDATE cases SET {sets} WHERE id = ?"
    params: list = [*fields.values(), case_id]
    if owner_id is not None:
        sql += " AND owner_id = ?"
        params.append(owner_id)
    if fields.get("billing_status") and fields["billing_status"] != "fee_charged":
        sql += " AND (billing_status IS NULL OR billing_status != 'fee_charged')"
    with _connect() as conn:
        conn.execute(sql, params)
        conn.commit()


def insert_draft(draft_id: str, fields: dict, owner_id: str | None = None) -> None:
    payload = fields.get("payload")
    row = dict(fields)
    row["payload"] = json.dumps(payload) if not isinstance(payload, str) else payload
    row["owner_id"] = owner_id
    _insert("drafts", draft_id, row, _DRAFT_INSERT_COLUMNS)


def get_draft(draft_id: str) -> dict | None:
    # Unscoped by design: the caller checks the draft's owner_id itself
    # (service-created drafts have owner_id NULL until claimed via promote).
    return _get("drafts", draft_id)


def delete_draft(draft_id: str, owner_id: str) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM drafts WHERE id = ? AND owner_id = ?", (draft_id, owner_id))
        conn.commit()


def count_cases() -> int:
    with _connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM cases").fetchone()[0]


def export_owner(owner_id: str) -> dict:
    if not owner_id:
        raise ValueError("An owner is required")
    with _connect() as conn:
        return {table: [dict(row) for row in conn.execute(f"SELECT * FROM {table} WHERE owner_id = ?", (owner_id,)).fetchall()]
                for table in ('cases', 'drafts')}


def delete_owner(owner_id: str) -> dict:
    if not owner_id:
        raise ValueError("An owner is required")
    with _connect() as conn:
        rows = {table: [dict(row) for row in conn.execute(f"SELECT * FROM {table} WHERE owner_id = ?", (owner_id,)).fetchall()]
                for table in ('cases', 'drafts')}
        for table in ('drafts', 'cases'):
            conn.execute(f"DELETE FROM {table} WHERE owner_id = ?", (owner_id,))
        conn.commit()
    return rows


def promote_draft(draft_id: str, owner_id: str, case_id: str, fields: dict) -> bool:
    """Consume an owned draft and create its case in one transaction."""
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if not conn.execute("SELECT id FROM drafts WHERE id = ? AND owner_id = ?", (draft_id, owner_id)).fetchone():
            return False
        columns = ("id", *_CASE_INSERT_COLUMNS)
        row = {**fields, "id": case_id, "owner_id": owner_id}
        conn.execute(f"INSERT INTO cases ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})", [row.get(k) for k in columns])
        conn.execute("DELETE FROM drafts WHERE id = ? AND owner_id = ?", (draft_id, owner_id))
        conn.commit()
    return True
