"""
store.py — SQLite persistence for sensor readings.

One table, one writer. The poller appends; the web layer reads. They share
state only through this database, mirroring the split already used by the
Emporia energy monitor.

Schema lives here and nowhere else.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

DEFAULT_DB_PATH = Path(
    __import__("os").environ.get(
        "HOUSE_TWIN_DB", Path(__file__).resolve().parent.parent / "house.db"
    )
)

#: Readings older than this are pruned once a day. A year of 9 sensors at one
#: row per minute is roughly 4.7M rows, so retention matters more than index
#: cleverness here.
DEFAULT_RETENTION_DAYS = 365

_SCHEMA = """
CREATE TABLE IF NOT EXISTS readings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    sensor_id   TEXT    NOT NULL,
    device_id   TEXT,
    ts          REAL    NOT NULL,
    temperature REAL,
    humidity    REAL,
    battery     INTEGER,
    online      INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_readings_sensor_ts ON readings (sensor_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_readings_ts ON readings (ts DESC);
"""


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    """Open a connection with WAL mode and dict-style rows."""
    db_path = Path(path) if path else DEFAULT_DB_PATH
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(_SCHEMA)
    return conn


def record(
    conn: sqlite3.Connection,
    sensor_id: str,
    *,
    device_id: str | None = None,
    ts: float | None = None,
    temperature: float | None = None,
    humidity: float | None = None,
    battery: int | None = None,
    online: bool = True,
) -> None:
    """Append one reading. ``None`` readings are still stored so that a sensor
    dropping offline leaves a visible gap rather than a stale last-known value."""
    conn.execute(
        """INSERT INTO readings
               (sensor_id, device_id, ts, temperature, humidity, battery, online)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            sensor_id,
            device_id,
            ts if ts is not None else time.time(),
            temperature,
            humidity,
            battery,
            1 if online else 0,
        ),
    )


def latest(conn: sqlite3.Connection) -> list[dict]:
    """Most recent reading per sensor, newest first."""
    rows = conn.execute(
        """
        SELECT r.* FROM readings r
        JOIN (SELECT sensor_id, MAX(ts) AS ts FROM readings GROUP BY sensor_id) m
          ON r.sensor_id = m.sensor_id AND r.ts = m.ts
        ORDER BY r.sensor_id
        """
    ).fetchall()
    return [dict(row) for row in rows]


def history(
    conn: sqlite3.Connection, sensor_id: str, hours: int = 24, limit: int = 2000
) -> list[dict]:
    """Readings for one sensor over the trailing *hours*, oldest first.

    Downsampled with SQL window functions when the range is dense enough that
    returning every row would bloat the response.
    """
    since = time.time() - hours * 3600
    total = conn.execute(
        "SELECT COUNT(*) AS n FROM readings WHERE sensor_id = ? AND ts >= ?",
        (sensor_id, since),
    ).fetchone()["n"]

    if total <= limit:
        rows = conn.execute(
            "SELECT * FROM readings WHERE sensor_id = ? AND ts >= ? ORDER BY ts",
            (sensor_id, since),
        ).fetchall()
    else:
        # bucket = floor so each bucket keeps its earliest sample
        bucket = max(1, (time.time() - since) // limit)
        rows = conn.execute(
            """
            SELECT * FROM (
                SELECT *, MIN(ts) OVER (PARTITION BY CAST(ts / ? AS INTEGER)) AS bucket_ts
                FROM readings WHERE sensor_id = ? AND ts >= ?
            ) WHERE ts = bucket_ts ORDER BY ts
            """,
            (bucket, sensor_id, since),
        ).fetchall()
    return [dict(row) for row in rows]


def prune(conn: sqlite3.Connection, retention_days: int = DEFAULT_RETENTION_DAYS) -> int:
    """Delete readings past the retention horizon. Returns rows removed."""
    cutoff = time.time() - retention_days * 86400
    cursor = conn.execute("DELETE FROM readings WHERE ts < ?", (cutoff,))
    return cursor.rowcount


def stats(conn: sqlite3.Connection, hours: int = 24) -> dict:
    """House-level rollup for the header strip."""
    since = time.time() - hours * 3600
    row = conn.execute(
        """
        SELECT COUNT(DISTINCT sensor_id) AS sensors,
               MIN(temperature)   AS temp_min,
               MAX(temperature)   AS temp_max,
               AVG(temperature)   AS temp_avg,
               MIN(humidity)      AS hum_min,
               MAX(humidity)      AS hum_max,
               AVG(humidity)      AS hum_avg,
               MAX(ts)            AS latest
        FROM readings WHERE ts >= ?
        """,
        (since,),
    ).fetchone()
    # dict(row), not a comprehension over row: iterating a sqlite3.Row yields
    # values, not column names.
    return dict(row)
