"""SQLite storage. One file: data/arbos.db."""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    asin TEXT PRIMARY KEY,
    data TEXT NOT NULL,
    source TEXT,
    fetched_at TEXT
);
CREATE TABLE IF NOT EXISTS upc_map (
    upc TEXT NOT NULL,
    asin TEXT NOT NULL,
    PRIMARY KEY (upc, asin)
);
CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    source TEXT,
    retailer TEXT,
    retailer_url TEXT,
    title TEXT,
    upc TEXT,
    asin TEXT,
    buy_cost REAL,
    cashback_pct REAL,
    notes TEXT,
    status TEXT NOT NULL DEFAULT 'new',      -- new | scored | bought | skipped
    verdict TEXT,
    profit REAL,
    roi REAL,
    max_buy_cost REAL,
    monthly_sales REAL,
    suggested_qty INTEGER,
    evaluation TEXT
);
CREATE INDEX IF NOT EXISTS leads_status ON leads(status);
CREATE TABLE IF NOT EXISTS purchases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bought_on TEXT NOT NULL,
    retailer TEXT,
    order_number TEXT,
    receipt_path TEXT,
    receipt_kind TEXT,                       -- online_order | store_receipt | invoice
    sales_tax REAL DEFAULT 0,
    cashback REAL DEFAULT 0,
    shipping REAL DEFAULT 0,
    payment TEXT,
    notes TEXT
);
CREATE TABLE IF NOT EXISTS lots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    purchase_id INTEGER REFERENCES purchases(id),
    lead_id INTEGER,
    asin TEXT NOT NULL,
    sku TEXT NOT NULL,
    fnsku TEXT,
    title TEXT,
    upc TEXT,
    qty INTEGER NOT NULL,
    qty_sold INTEGER NOT NULL DEFAULT 0,
    qty_lost INTEGER NOT NULL DEFAULT 0,
    unit_cost REAL NOT NULL,                 -- price paid per unit
    landed_unit_cost REAL NOT NULL,          -- + tax + prep + inbound - cashback
    target_price REAL,
    min_price REAL,
    max_price REAL,
    expiry TEXT,
    status TEXT NOT NULL DEFAULT 'bought',   -- bought | prepped | shipped | live | sold_out | removed
    shipment_id INTEGER,
    created_at TEXT NOT NULL,
    live_at TEXT,
    notes TEXT
);
CREATE INDEX IF NOT EXISTS lots_sku ON lots(sku);
CREATE INDEX IF NOT EXISTS lots_status ON lots(status);
CREATE TABLE IF NOT EXISTS shipments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft',    -- draft | shipped | receiving | closed
    amazon_shipment_id TEXT,
    carrier_cost REAL DEFAULT 0,
    placement_option TEXT,
    notes TEXT
);
CREATE TABLE IF NOT EXISTS sales (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT NOT NULL,
    order_item_id TEXT,
    sold_at TEXT NOT NULL,
    sku TEXT NOT NULL,
    asin TEXT,
    qty INTEGER NOT NULL,
    item_price REAL NOT NULL,
    referral_fee REAL DEFAULT 0,
    fba_fee REAL DEFAULT 0,
    other_fees REAL DEFAULT 0,
    refund REAL DEFAULT 0,
    cogs REAL DEFAULT 0,
    profit REAL DEFAULT 0,
    source TEXT,
    UNIQUE(order_id, sku, order_item_id)
);
CREATE INDEX IF NOT EXISTS sales_date ON sales(sold_at);
CREATE TABLE IF NOT EXISTS expenses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    spent_on TEXT NOT NULL,
    category TEXT,
    vendor TEXT,
    amount REAL NOT NULL,
    notes TEXT
);
CREATE TABLE IF NOT EXISTS cash_moves (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    moved_on TEXT NOT NULL,
    kind TEXT NOT NULL,                      -- deposit | withdrawal | payout | owner_draw
    amount REAL NOT NULL,
    notes TEXT
);
CREATE TABLE IF NOT EXISTS inventory_snapshot (
    sku TEXT PRIMARY KEY,
    asin TEXT,
    fnsku TEXT,
    fulfillable INTEGER DEFAULT 0,
    inbound INTEGER DEFAULT 0,
    reserved INTEGER DEFAULT 0,
    unfulfillable INTEGER DEFAULT 0,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS price_watch (
    asin TEXT PRIMARY KEY,
    title TEXT,
    target_buy_cost REAL,
    retailer TEXT,
    retailer_url TEXT,
    last_profit REAL,
    last_roi REAL,
    added_at TEXT
);
CREATE TABLE IF NOT EXISTS checklist (
    key TEXT PRIMARY KEY,
    done INTEGER NOT NULL DEFAULT 0,
    done_at TEXT
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    kind TEXT NOT NULL,
    message TEXT NOT NULL,
    data TEXT
);
CREATE TABLE IF NOT EXISTS scans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,                    -- running | done | cancelled | error
    sources TEXT,
    checked INTEGER DEFAULT 0,
    matched INTEGER DEFAULT 0,
    buys INTEGER DEFAULT 0,
    maybes INTEGER DEFAULT 0,
    message TEXT
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""

_lock = threading.RLock()


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


# columns added after the first release: (table, column, type)
MIGRATIONS = [
    ("leads", "scan_id", "INTEGER"),
    ("leads", "regular_price", "REAL"),
    ("leads", "margin", "REAL"),
    ("leads", "monthly_profit", "REAL"),
    ("leads", "flip", "INTEGER DEFAULT 0"),
]


def connect(path=None) -> sqlite3.Connection:
    config.ensure_dirs()
    conn = sqlite3.connect(str(path or config.DB_PATH), check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(SCHEMA)
    for table, col, typ in MIGRATIONS:
        have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if col not in have:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
    conn.commit()
    return conn


_conn = None


def get() -> sqlite3.Connection:
    global _conn
    with _lock:
        if _conn is None:
            _conn = connect()
        return _conn


def reset_connection() -> None:
    global _conn
    with _lock:
        if _conn is not None:
            _conn.close()
        _conn = None


@contextmanager
def tx():
    conn = get()
    with _lock:
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def rows(sql: str, params=()) -> list:
    with _lock:
        return [dict(r) for r in get().execute(sql, params).fetchall()]


def one(sql: str, params=()):
    with _lock:
        r = get().execute(sql, params).fetchone()
        return dict(r) if r else None


def scalar(sql: str, params=(), default=0):
    with _lock:
        r = get().execute(sql, params).fetchone()
        return r[0] if r and r[0] is not None else default


def log(kind: str, message: str, data=None) -> None:
    with tx() as c:
        c.execute("INSERT INTO events(at, kind, message, data) VALUES (?,?,?,?)",
                  (now(), kind, message, json.dumps(data) if data is not None else None))


def meta_get(key: str, default=None):
    r = one("SELECT value FROM meta WHERE key=?", (key,))
    return json.loads(r["value"]) if r else default


def meta_set(key: str, value) -> None:
    with tx() as c:
        c.execute("INSERT INTO meta(key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                  (key, json.dumps(value)))
