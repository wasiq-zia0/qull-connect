"""SQLite persistence for the unclaimed-property connector.

Single-file database at data/app.db (never in-memory). Tables:
  searches       - one row per intake (search id, claimant name, email, dob consent flag, payload JSON)
  search_states  - one row per (search, state abbr): claim status + timestamps
  recoveries     - one row per confirmed recovery: amount, fee, stripe payment id

PII minimization: we store name, email, states-of-residence/years, and prior names
(the payload needed to build claim packs). Date of birth is stored only as an
explicit consent flag plus the date when the user opts in (some states require it
at filing). No SSN is ever collected or stored.
"""
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "app.db"

STATUSES = ("not_started", "in_progress", "filed", "paid", "denied")

SCHEMA = """
CREATE TABLE IF NOT EXISTS searches (
    id TEXT PRIMARY KEY,
    full_legal_name TEXT NOT NULL,
    prior_names TEXT NOT NULL DEFAULT '[]',
    email TEXT NOT NULL DEFAULT '',
    dob TEXT DEFAULT NULL,
    dob_consent INTEGER NOT NULL DEFAULT 0,
    states_of_residence TEXT NOT NULL DEFAULT '[]',
    draft INTEGER NOT NULL DEFAULT 0,
    stripe_customer_id TEXT DEFAULT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS search_states (
    search_id TEXT NOT NULL,
    state_abbr TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'not_started',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (search_id, state_abbr),
    FOREIGN KEY (search_id) REFERENCES searches(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS recoveries (
    id TEXT PRIMARY KEY,
    search_id TEXT NOT NULL,
    state_abbr TEXT DEFAULT NULL,
    amount REAL NOT NULL,
    fee_cents INTEGER NOT NULL,
    payment_intent_id TEXT DEFAULT NULL,
    charged INTEGER NOT NULL DEFAULT 0,
    confirmed_at TEXT NOT NULL,
    FOREIGN KEY (search_id) REFERENCES searches(id) ON DELETE CASCADE
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init() -> None:
    with _conn() as conn:
        conn.executescript(SCHEMA)
        # lightweight migration for DBs created before the draft column existed
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(searches)")}
        if "draft" not in cols:
            conn.execute("ALTER TABLE searches ADD COLUMN draft INTEGER NOT NULL DEFAULT 0")
        # tenant-isolation migration: every user table gets owner_id + index
        for table in ("searches", "search_states", "recoveries"):
            _ensure_owner_column(conn, table)
        # audit trail for the legally-capped per-state fee rate
        rcols = {r["name"] for r in conn.execute("PRAGMA table_info(recoveries)")}
        if "fee_rate" not in rcols:
            conn.execute("ALTER TABLE recoveries ADD COLUMN fee_rate REAL")


def _ensure_owner_column(conn: sqlite3.Connection, table: str) -> None:
    cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
    if "owner_id" not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN owner_id TEXT")
    conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_owner ON {table}(owner_id)")


def create_search(full_legal_name: str, prior_names: list, email: str,
                  dob: str | None, dob_consent: bool, states_of_residence: list,
                  draft: bool = False, owner_id: str | None = None) -> str:
    search_id = uuid.uuid4().hex[:12]
    with _conn() as conn:
        conn.execute(
            "INSERT INTO searches (id, full_legal_name, prior_names, email, dob, dob_consent,"
            " states_of_residence, draft, owner_id, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (search_id, full_legal_name, json.dumps(prior_names), email, dob,
             int(dob_consent), json.dumps(states_of_residence), int(draft), owner_id, _now()))
        for entry in states_of_residence:
            abbr = entry.get("abbr") if isinstance(entry, dict) else entry
            conn.execute(
                "INSERT OR IGNORE INTO search_states (search_id, state_abbr, status, updated_at,"
                " owner_id) VALUES (?,?,?,?,?)",
                (search_id, abbr, "not_started", _now(), owner_id))
    return search_id


def set_draft(search_id: str, draft: bool, owner_id: str) -> None:
    with _conn() as conn:
        conn.execute("UPDATE searches SET draft=? WHERE id=? AND owner_id=?",
                     (int(draft), search_id, owner_id))


def adopt_search(search_id: str, owner_id: str) -> bool:
    """Claim an ownerless (life-event draft) search for the first user who touches it."""
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE searches SET owner_id=? WHERE id=? AND owner_id IS NULL",
            (owner_id, search_id))
        conn.execute(
            "UPDATE search_states SET owner_id=? WHERE search_id=? AND owner_id IS NULL",
            (owner_id, search_id))
        conn.execute(
            "UPDATE recoveries SET owner_id=? WHERE search_id=? AND owner_id IS NULL",
            (owner_id, search_id))
    return cur.rowcount > 0


def save_search_updates(search_id: str, rec: dict, owner_id: str) -> None:
    """Persist conversational updates from PATCH /api/searches/{id}."""
    with _conn() as conn:
        conn.execute(
            "UPDATE searches SET full_legal_name=?, prior_names=?, email=?, dob=?,"
            " dob_consent=? WHERE id=? AND owner_id=?",
            (rec["full_legal_name"], json.dumps(rec["prior_names"]), rec["email"],
             rec.get("dob"), int(rec["dob_consent"]), search_id, owner_id))
        current = {r["state_abbr"] for r in conn.execute(
            "SELECT state_abbr FROM search_states WHERE search_id=? AND owner_id=?",
            (search_id, owner_id))}
        wanted = {s["abbr"] for s in rec["states_of_residence"]}
        for abbr in wanted - current:
            conn.execute(
                "INSERT INTO search_states (search_id, state_abbr, status, updated_at, owner_id)"
                " VALUES (?,?,?,?,?)", (search_id, abbr, "not_started", _now(), owner_id))
        for abbr in current - wanted:
            conn.execute("DELETE FROM search_states WHERE search_id=? AND state_abbr=?"
                         " AND owner_id=?", (search_id, abbr, owner_id))


def get_search(search_id: str, owner_id: str) -> dict | None:
    """Owner-scoped read: returns the search only if it belongs to owner_id."""
    with _conn() as conn:
        row = conn.execute("SELECT * FROM searches WHERE id=? AND owner_id=?",
                           (search_id, owner_id)).fetchone()
        if row is None:
            return None
        states = conn.execute(
            "SELECT state_abbr, status, updated_at FROM search_states"
            " WHERE search_id=? AND owner_id=?",
            (search_id, owner_id)).fetchall()
        recoveries = conn.execute(
            "SELECT id, state_abbr, amount, fee_cents, fee_rate, payment_intent_id, charged,"
            " confirmed_at FROM recoveries WHERE search_id=? AND owner_id=?",
            (search_id, owner_id)).fetchall()
    s = dict(row)
    s["prior_names"] = json.loads(s["prior_names"])
    s["states_of_residence"] = json.loads(s["states_of_residence"])
    s["state_statuses"] = [dict(r) for r in states]
    s["recoveries"] = [dict(r) for r in recoveries]
    return s


def get_search_unscoped(search_id: str) -> dict | None:
    """Unscoped read for the draft-adoption path only. Not for user responses."""
    with _conn() as conn:
        row = conn.execute("SELECT id, owner_id FROM searches WHERE id=?",
                           (search_id,)).fetchone()
    return dict(row) if row else None


def set_state_status(search_id: str, abbr: str, status: str, owner_id: str) -> dict | None:
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    with _conn() as conn:
        row = conn.execute("SELECT 1 FROM search_states"
                           " WHERE search_id=? AND state_abbr=? AND owner_id=?",
                           (search_id, abbr, owner_id)).fetchone()
        if row is None:
            return None
        conn.execute("UPDATE search_states SET status=?, updated_at=?"
                     " WHERE search_id=? AND state_abbr=? AND owner_id=?",
                     (status, _now(), search_id, abbr, owner_id))
    return {"search_id": search_id, "state_abbr": abbr, "status": status, "updated_at": _now()}


def set_stripe_customer(search_id: str, customer_id: str, owner_id: str) -> None:
    with _conn() as conn:
        conn.execute("UPDATE searches SET stripe_customer_id=? WHERE id=? AND owner_id=?",
                     (customer_id, search_id, owner_id))


def get_stripe_customer(search_id: str, owner_id: str) -> str | None:
    with _conn() as conn:
        row = conn.execute("SELECT stripe_customer_id FROM searches WHERE id=? AND owner_id=?",
                           (search_id, owner_id)).fetchone()
    return row["stripe_customer_id"] if row else None


def has_charged_recovery(search_id: str, state_abbr: str | None, owner_id: str) -> bool:
    """Per-(search, state) double-charge guard: a genuine second recovery in
    another state stays chargeable; a retry in the same state is refused."""
    with _conn() as conn:
        if state_abbr is None:
            row = conn.execute(
                "SELECT 1 FROM recoveries WHERE search_id = ? AND state_abbr IS NULL"
                " AND charged = 1 AND owner_id = ? LIMIT 1",
                (search_id, owner_id)).fetchone()
        else:
            row = conn.execute(
                "SELECT 1 FROM recoveries WHERE search_id = ? AND state_abbr = ?"
                " AND charged = 1 AND owner_id = ? LIMIT 1",
                (search_id, state_abbr, owner_id)).fetchone()
    return row is not None


def record_recovery(search_id: str, amount: float, fee_cents: int, fee_rate: float,
                    state_abbr: str | None, payment_intent_id: str | None,
                    owner_id: str) -> dict:
    rid = uuid.uuid4().hex[:12]
    with _conn() as conn:
        conn.execute(
            "INSERT INTO recoveries (id, search_id, state_abbr, amount, fee_cents, fee_rate,"
            " payment_intent_id, charged, confirmed_at, owner_id)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (rid, search_id, state_abbr, amount, fee_cents, fee_rate, payment_intent_id,
             1 if payment_intent_id else 0, _now(), owner_id))
    return {"recovery_id": rid, "search_id": search_id, "state_abbr": state_abbr,
            "amount": amount, "fee_cents": fee_cents, "fee_rate": fee_rate,
            "payment_intent_id": payment_intent_id,
            "charged": bool(payment_intent_id)}
