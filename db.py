"""
db.py — SQLite storage for glucose readings.
"""

import sqlite3
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), "dexcom.db")


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS readings (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp   TEXT NOT NULL,          -- ISO8601 UTC
                value_mgdl  INTEGER NOT NULL,
                trend_code  INTEGER,                -- 1-9
                trend_arrow TEXT,
                trend_desc  TEXT,
                inserted_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_readings_ts
            ON readings(timestamp)
        """)
        conn.commit()


def insert_reading(timestamp: datetime, value: int, trend_code: int,
                   trend_arrow: str, trend_desc: str) -> bool:
    """Insert a reading. Returns True if inserted, False if duplicate."""
    try:
        with get_conn() as conn:
            conn.execute("""
                INSERT INTO readings (timestamp, value_mgdl, trend_code, trend_arrow, trend_desc)
                VALUES (?, ?, ?, ?, ?)
            """, (timestamp.isoformat(), value, trend_code, trend_arrow, trend_desc))
            conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False  # duplicate


def get_latest(n: int = 1) -> list[dict]:
    """Return the last n readings, newest first."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT timestamp, value_mgdl, trend_arrow, trend_desc
            FROM readings
            ORDER BY timestamp DESC
            LIMIT ?
        """, (n,)).fetchall()
    return [dict(r) for r in rows]


def get_range(from_iso: str, to_iso: str) -> list[dict]:
    """Return readings in [from_iso, to_iso] range."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT timestamp, value_mgdl, trend_arrow, trend_desc
            FROM readings
            WHERE timestamp BETWEEN ? AND ?
            ORDER BY timestamp ASC
        """, (from_iso, to_iso)).fetchall()
    return [dict(r) for r in rows]
