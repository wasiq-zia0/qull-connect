"""SQLite persistence for the subscription-slayer connector.

The database lives at data/app.db (file-based, never in-memory).
Tables:
  subscriptions - detected recurring subscriptions and their lifecycle
  billing       - one Stripe customer record per owner
  charges       - the $10-per-cancellation fee charges

Tenant isolation: every row carries ``owner_id`` (the platform user id
resolved by ``identity``). Rows created by the service bus
(``POST /api/life-events``, service-key auth, no user) store
``owner_id=NULL``.
"""
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "data" / "app.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS subscriptions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id        TEXT,
    merchant        TEXT NOT NULL,
    merchant_key    TEXT NOT NULL,
    amount          REAL NOT NULL,
    currency        TEXT NOT NULL DEFAULT 'USD',
    frequency       TEXT NOT NULL DEFAULT 'monthly',
    confidence      REAL NOT NULL DEFAULT 0.0,
    first_seen      TEXT NOT NULL,
    last_seen       TEXT NOT NULL,
    occurrences     INTEGER NOT NULL DEFAULT 1,
    status          TEXT NOT NULL DEFAULT 'new',
    savings_monthly REAL,
    savings_confirmed INTEGER NOT NULL DEFAULT 0,
    notes           TEXT DEFAULT '',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS billing (
    owner_id        TEXT PRIMARY KEY,
    customer_id     TEXT NOT NULL,
    setup_intent_id TEXT,
    created_at      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS charges (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id         TEXT,
    customer_id      TEXT NOT NULL,
    amount_cents     INTEGER NOT NULL,
    currency         TEXT NOT NULL DEFAULT 'usd',
    description      TEXT NOT NULL,
    subscription_ids TEXT NOT NULL,
    payment_intent_id TEXT,
    status           TEXT NOT NULL DEFAULT 'pending',
    created_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_subscriptions_merchant_key ON subscriptions(merchant_key);
"""


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _migrate(conn: sqlite3.Connection) -> None:
    """Add owner_id tenant isolation to pre-existing databases.

    Also retires the old single-global-row ``billing`` shape
    (``id INTEGER PRIMARY KEY CHECK (id = 1)``) in favour of one row per
    owner keyed by ``owner_id``.
    """
    for table in ("subscriptions", "billing", "charges"):
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
        if "owner_id" not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN owner_id TEXT")
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_owner ON {table}(owner_id)")
    cols = [r[1] for r in conn.execute("PRAGMA table_info(billing)").fetchall()]
    if "id" in cols:
        # Old shape: one global row. Migrate it to the per-owner shape
        # (pre-launch; any rows here are dev/test data).
        conn.execute("ALTER TABLE billing RENAME TO billing_old")
        conn.execute("""CREATE TABLE billing (
            owner_id        TEXT PRIMARY KEY,
            customer_id     TEXT NOT NULL,
            setup_intent_id TEXT,
            created_at      TEXT NOT NULL
        )""")
        conn.execute(
            "INSERT INTO billing (owner_id, customer_id, setup_intent_id, created_at) "
            "SELECT NULL, customer_id, setup_intent_id, created_at FROM billing_old")
        conn.execute("DROP TABLE billing_old")
    conn.commit()


def get_db(path: Path = DB_PATH) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def upsert_subscription(conn: sqlite3.Connection, owner_id: str | None, *,
                        merchant: str, merchant_key: str,
                        amount: float, currency: str, frequency: str, confidence: float,
                        first_seen: str, last_seen: str, occurrences: int) -> int:
    """Insert a new subscription or refresh an existing one by (owner, merchant_key).

    Never overwrites a user's lifecycle status or confirmed savings data.
    Returns the subscription id.
    """
    now = _utcnow()
    row = conn.execute(
        "SELECT * FROM subscriptions WHERE merchant_key = ? AND "
        "(owner_id = ? OR (owner_id IS NULL AND ? IS NULL))",
        (merchant_key, owner_id, owner_id),
    ).fetchone()
    if row is None:
        cur = conn.execute(
            """INSERT INTO subscriptions
               (owner_id, merchant, merchant_key, amount, currency, frequency, confidence,
                first_seen, last_seen, occurrences, status, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (owner_id, merchant, merchant_key, amount, currency, frequency, confidence,
             first_seen, last_seen, occurrences, "new", now, now),
        )
        conn.commit()
        return cur.lastrowid
    conn.execute(
        """UPDATE subscriptions
           SET merchant = ?, amount = ?, currency = ?, frequency = ?,
               confidence = MAX(confidence, ?),
               first_seen = MIN(first_seen, ?), last_seen = MAX(last_seen, ?),
               occurrences = MAX(occurrences, ?), updated_at = ?
           WHERE id = ?""",
        (merchant, amount, currency, frequency, confidence,
         first_seen, last_seen, occurrences, now, row["id"]),
    )
    conn.commit()
    return row["id"]
