from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .config import DATA_DIR, ensure_data_dir

DB_PATH = DATA_DIR / "history.db"


def get_db_connection() -> sqlite3.Connection:
    ensure_data_dir()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with get_db_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS health_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                health_score INTEGER NOT NULL,
                cpu_percent REAL NOT NULL,
                memory_percent REAL NOT NULL,
                storage_used_bytes INTEGER NOT NULL,
                storage_total_bytes INTEGER NOT NULL,
                battery_percent REAL
            )
            """
        )
        conn.commit()


def save_history(
    health_score: int,
    cpu_percent: float,
    memory_percent: float,
    storage_used_bytes: int,
    storage_total_bytes: int,
    battery_percent: float | None,
) -> None:
    init_db()
    with get_db_connection() as conn:
        conn.execute(
            """
            INSERT INTO health_history (
                timestamp, health_score, cpu_percent, memory_percent,
                storage_used_bytes, storage_total_bytes, battery_percent
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.now(timezone.utc).isoformat(),
                health_score,
                cpu_percent,
                memory_percent,
                storage_used_bytes,
                storage_total_bytes,
                battery_percent,
            ),
        )
        conn.commit()


def get_history(limit: int = 100) -> list[dict[str, object]]:
    init_db()
    with get_db_connection() as conn:
        cursor = conn.execute(
            "SELECT * FROM health_history ORDER BY timestamp DESC LIMIT ?", (limit,)
        )
        rows = cursor.fetchall()
        return [dict(row) for row in reversed(rows)]

# Initialize DB on import
init_db()
