"""SQLite persistence for the moving-concierge connector. File-backed only (data/app.db)."""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "app.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS moves (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT NOT NULL,
    old_address TEXT NOT NULL,
    new_address TEXT NOT NULL,
    move_date TEXT NOT NULL,
    state TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft',
    stripe_customer_id TEXT,
    billing_status TEXT NOT NULL DEFAULT 'none',
    paid_at TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS checklist_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    move_id TEXT NOT NULL REFERENCES moves(id),
    category TEXT NOT NULL,
    title TEXT NOT NULL,
    detail TEXT NOT NULL,
    url TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    sort INTEGER NOT NULL DEFAULT 0
);
"""


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    # tenant-isolation migration: owner_id + index on every user table
    for table in ("moves", "checklist_items"):
        cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        if "owner_id" not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN owner_id TEXT")
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_owner ON {table}(owner_id)")
    return conn


def adopt_move(move_id: str, owner_id: str) -> None:
    """Claim an ownerless (life-event draft) move for the first user who touches it."""
    conn = connect()
    try:
        conn.execute("UPDATE moves SET owner_id = ? WHERE id = ? AND owner_id IS NULL",
                     (owner_id, move_id))
        conn.execute("UPDATE checklist_items SET owner_id = ? WHERE move_id = ? AND owner_id IS NULL",
                     (owner_id, move_id))
        conn.commit()
    finally:
        conn.close()
