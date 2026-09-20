"""SQLite persistence for the class-action-cash connector.

File-backed only (data/app.db) — never in-memory, so matches and billing
survive restarts.
"""
import sqlite3
import os
from datetime import datetime, timezone
from pathlib import Path

DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("DATA_DIR", DIR / "data")) / "app.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS matches (
  id TEXT PRIMARY KEY,
  settlement_name TEXT NOT NULL,
  claim_deadline TEXT,
  status TEXT NOT NULL DEFAULT 'candidate',
  matched_keyword TEXT,
  evidence_json TEXT,
  typical_payout_range TEXT,
  official_claim_url TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS billing (
  match_id TEXT PRIMARY KEY,
  customer_name TEXT,
  customer_email TEXT,
  stripe_customer_id TEXT,
  setup_intent_id TEXT,
  payment_intent_id TEXT,
  payout_amount_cents INTEGER,
  fee_cents INTEGER,
  fee_rate REAL DEFAULT 0.20,
  status TEXT DEFAULT 'setup',
  updated_at TEXT NOT NULL
);
"""


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Tenant isolation: every match and billing row belongs to exactly one owner."""
    for table in ("matches", "billing"):
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
        if table == "billing":
            if "checkout_session_id" not in cols:
                conn.execute("ALTER TABLE billing ADD COLUMN checkout_session_id TEXT")
            if "fee_terms_accepted_at" not in cols:
                conn.execute("ALTER TABLE billing ADD COLUMN fee_terms_accepted_at TEXT")
            if "fee_confirmed_at" not in cols:
                conn.execute("ALTER TABLE billing ADD COLUMN fee_confirmed_at TEXT")
        if "owner_id" not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN owner_id TEXT")
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_owner ON {table}(owner_id)")


def claim_match(match_id: str, owner_id: str) -> dict | None:
    """Legacy rows without a verified owner are never claimable by record ID."""
    return get_match(match_id, owner_id)


def save_match(m: dict, owner_id: str | None = None) -> None:
    now = _utcnow()
    with _conn() as conn:
        conn.execute(
            """INSERT INTO matches (id, settlement_name, claim_deadline, status,
                                    matched_keyword, evidence_json, typical_payout_range,
                                    official_claim_url, created_at, updated_at, owner_id)
               VALUES (:id, :settlement_name, :claim_deadline, :status, :matched_keyword,
                       :evidence_json, :typical_payout_range, :official_claim_url, :now, :now,
                       :owner_id)
               ON CONFLICT(id) DO UPDATE SET
                 status=excluded.status, evidence_json=excluded.evidence_json,
                 updated_at=excluded.updated_at""",
            {**m, "now": now, "owner_id": owner_id})


def get_match(match_id: str, owner_id: str | None = None):
    with _conn() as conn:
        if owner_id is None:
            row = conn.execute("SELECT * FROM matches WHERE id = ?", (match_id,)).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM matches WHERE id = ? AND owner_id = ?",
                (match_id, owner_id)).fetchone()
    return dict(row) if row else None


def list_matches(owner_id: str | None = None) -> list:
    with _conn() as conn:
        if owner_id is None:
            rows = conn.execute("SELECT * FROM matches ORDER BY created_at DESC").fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM matches WHERE owner_id = ? ORDER BY created_at DESC",
                (owner_id,)).fetchall()
    return [dict(r) for r in rows]


def update_match_status(match_id: str, status: str, owner_id: str | None = None) -> None:
    with _conn() as conn:
        if owner_id is None:
            conn.execute("UPDATE matches SET status = ?, updated_at = ? WHERE id = ?",
                         (status, _utcnow(), match_id))
        else:
            conn.execute("UPDATE matches SET status = ?, updated_at = ? "
                         "WHERE id = ? AND owner_id = ?",
                         (status, _utcnow(), match_id, owner_id))


def save_billing(b: dict, owner_id: str | None = None) -> None:
    now = _utcnow()
    with _conn() as conn:
        existing = conn.execute("SELECT owner_id, status FROM billing WHERE match_id = ?", (b["match_id"],)).fetchone()
        if existing and existing["owner_id"] != owner_id:
            raise ValueError("Billing owner does not match")
        if existing and existing["status"] == "billed" and b.get("status") != "billed":
            return
        conn.execute(
            """INSERT INTO billing (match_id, customer_name, customer_email,
                                    stripe_customer_id, setup_intent_id, payment_intent_id,
                                    payout_amount_cents, fee_cents, fee_rate, status, updated_at,
                                    owner_id)
               VALUES (:match_id, :customer_name, :customer_email, :stripe_customer_id,
                       :setup_intent_id, :payment_intent_id, :payout_amount_cents,
                       :fee_cents, :fee_rate, :status, :now, :owner_id)
               ON CONFLICT(match_id) DO UPDATE SET
                 customer_name=excluded.customer_name, customer_email=excluded.customer_email,
                 stripe_customer_id=excluded.stripe_customer_id,
                 setup_intent_id=excluded.setup_intent_id,
                 payment_intent_id=excluded.payment_intent_id,
                 payout_amount_cents=excluded.payout_amount_cents,
                 fee_cents=excluded.fee_cents, status=excluded.status,
                 -- adopt the owner on legacy ownerless rows; never steal one
                 owner_id=COALESCE(billing.owner_id, excluded.owner_id),
                 updated_at=excluded.updated_at""",
            {**{k: b.get(k) for k in
                 ("match_id", "customer_name", "customer_email", "stripe_customer_id",
                  "setup_intent_id", "payment_intent_id", "payout_amount_cents",
                  "fee_cents")},
             "fee_rate": b.get("fee_rate", 0.20), "status": b.get("status", "setup"),
             "now": now, "owner_id": owner_id})
        for column in ("checkout_session_id", "fee_terms_accepted_at", "fee_confirmed_at"):
            if column in b:
                conn.execute(f"UPDATE billing SET {column} = ? WHERE match_id = ? AND owner_id = ?", (b[column], b["match_id"], owner_id))


def get_billing(match_id: str, owner_id: str | None = None):
    with _conn() as conn:
        if owner_id is None:
            row = conn.execute("SELECT * FROM billing WHERE match_id = ?",
                               (match_id,)).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM billing WHERE match_id = ? AND owner_id = ?",
                (match_id, owner_id)).fetchone()
    return dict(row) if row else None


def export_owner(owner_id: str) -> dict:
    if not owner_id:
        raise ValueError("An owner is required")
    with _conn() as conn:
        return {table: [dict(row) for row in conn.execute(f"SELECT * FROM {table} WHERE owner_id = ?", (owner_id,)).fetchall()]
                for table in ('matches', 'billing')}


def delete_owner(owner_id: str) -> dict:
    if not owner_id:
        raise ValueError("An owner is required")
    with _conn() as conn:
        rows = {table: [dict(row) for row in conn.execute(f"SELECT * FROM {table} WHERE owner_id = ?", (owner_id,)).fetchall()]
                for table in ('matches', 'billing')}
        for table in ('billing', 'matches'):
            conn.execute(f"DELETE FROM {table} WHERE owner_id = ?", (owner_id,))
        conn.commit()
    return rows
