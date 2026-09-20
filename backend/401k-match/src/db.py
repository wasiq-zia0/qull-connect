"""SQLite persistence for the 401k-match connector. File-backed only (data/app.db)."""

import json
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "app.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS plans (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    inputs TEXT NOT NULL,
    result TEXT NOT NULL,
    paid INTEGER NOT NULL DEFAULT 0,
    stripe_customer_id TEXT,
    stripe_setup_intent_id TEXT,
    stripe_payment_intent_id TEXT,
    paid_at TEXT,
    owner_id TEXT
)
"""


DRAFT_SCHEMA = """
CREATE TABLE IF NOT EXISTS drafts (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    state TEXT NOT NULL,
    owner_id TEXT
)
"""


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute(SCHEMA)
    conn.execute(DRAFT_SCHEMA)
    # tenant-isolation migration: owner_id + index on every user table
    for table in ("plans", "drafts"):
        cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        if "owner_id" not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN owner_id TEXT")
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_owner ON {table}(owner_id)")
    return conn


def save_plan(plan: dict) -> None:
    with _conn() as c:
        c.execute(
            """INSERT OR REPLACE INTO plans
               (id, created_at, inputs, result, paid, stripe_customer_id,
                stripe_setup_intent_id, stripe_payment_intent_id, paid_at, owner_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                plan["id"],
                plan["created_at"],
                json.dumps(plan["inputs"]),
                json.dumps(plan["result"]),
                1 if plan.get("paid") else 0,
                plan.get("stripe_customer_id"),
                plan.get("stripe_setup_intent_id"),
                plan.get("stripe_payment_intent_id"),
                plan.get("paid_at"),
                plan.get("owner_id"),
            ),
        )


def _row_to_plan(row) -> dict:
    return {
        "id": row["id"],
        "created_at": row["created_at"],
        "inputs": json.loads(row["inputs"]),
        "result": json.loads(row["result"]),
        "paid": bool(row["paid"]),
        "stripe_customer_id": row["stripe_customer_id"],
        "stripe_setup_intent_id": row["stripe_setup_intent_id"],
        "stripe_payment_intent_id": row["stripe_payment_intent_id"],
        "paid_at": row["paid_at"],
        "owner_id": row["owner_id"],
    }


def get_plan(plan_id: str, owner_id: str) -> dict | None:
    """Owner-scoped read: 404 unless the plan belongs to the caller."""
    with _conn() as c:
        row = c.execute("SELECT * FROM plans WHERE id = ? AND owner_id = ?",
                        (plan_id, owner_id)).fetchone()
    if not row:
        return None
    return _row_to_plan(row)


def set_paid(plan_id: str, customer_id: str, payment_intent_id: str, paid_at: str,
             owner_id: str) -> bool:
    """Atomic compare-and-set: only transitions unpaid→paid. Returns True if
    this call performed the transition (False = already paid / not owned)."""
    with _conn() as c:
        cur = c.execute(
            """UPDATE plans SET paid = 1, stripe_customer_id = ?,
               stripe_payment_intent_id = ?, paid_at = ?
               WHERE id = ? AND owner_id = ? AND paid = 0""",
            (customer_id, payment_intent_id, paid_at, plan_id, owner_id),
        )
    return cur.rowcount > 0


def set_billing(plan_id: str, customer_id: str, setup_intent_id: str, owner_id: str) -> None:
    with _conn() as c:
        c.execute(
            """UPDATE plans SET stripe_customer_id = ?, stripe_setup_intent_id = ?
               WHERE id = ? AND owner_id = ?""",
            (customer_id, setup_intent_id, plan_id, owner_id),
        )


# ---- conversational drafts (golden path: trigger -> one tap -> done) ----

def save_draft(draft: dict) -> None:
    with _conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO drafts (id, created_at, state, owner_id)"
            " VALUES (?, ?, ?, ?)",
            (draft["id"], draft["created_at"], json.dumps(draft["state"]),
             draft.get("owner_id")),
        )


def get_draft(draft_id: str, owner_id: str) -> dict | None:
    """Owner-scoped read. Claims ownerless life-event drafts on first touch."""
    with _conn() as c:
        row = c.execute("SELECT * FROM drafts WHERE id = ? AND owner_id = ?",
                        (draft_id, owner_id)).fetchone()
        if row is None:
            unowned = c.execute("SELECT * FROM drafts WHERE id = ? AND owner_id IS NULL",
                                (draft_id,)).fetchone()
            if unowned is None:
                return None
            c.execute("UPDATE drafts SET owner_id = ? WHERE id = ? AND owner_id IS NULL",
                      (owner_id, draft_id))
            row = c.execute("SELECT * FROM drafts WHERE id = ? AND owner_id = ?",
                            (draft_id, owner_id)).fetchone()
    if not row:
        return None
    return {"id": row["id"], "created_at": row["created_at"],
            "state": json.loads(row["state"]), "owner_id": row["owner_id"]}


def delete_draft(draft_id: str, owner_id: str) -> None:
    with _conn() as c:
        c.execute("DELETE FROM drafts WHERE id = ? AND owner_id = ?", (draft_id, owner_id))
