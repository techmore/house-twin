"""
seed.py — fill the database with synthetic readings.

Lets the visualisation, the API and the charts be exercised before the Aqara
authorisation is done, and gives a sane baseline to compare real data against.
Clearly labelled so it can never be mistaken for a real reading.

    python -m house_twin.seed --hours 48
    python -m house_twin.seed --clear
"""

from __future__ import annotations

import argparse
import math
import random
import sys
import time

from . import store
from .house import House


def clear(conn) -> int:
    cursor = conn.execute("DELETE FROM readings")
    conn.commit()
    return cursor.rowcount


def seed(conn, house: House, hours: int = 48, step: int = 60) -> int:
    """Write one reading per sensor per *step* seconds over the trailing window.

    Models a diurnal cycle: the attic runs hottest and peaks in the late
    afternoon, the slab-level rooms lag and stay flatter, and the baths swing
    hardest because they are small and unconditioned.
    """
    random.seed(7)
    now = time.time()
    since = now - hours * 3600
    written = 0

    for sensor in house.sensors:
        room = house.room(sensor.room_id)
        # Per-room character: baseline °C, diurnal swing, lag in hours.
        profile = {
            "living": (21.0, 2.2, 3.0),
            "kitchen": (22.0, 2.8, 2.2),
            "dining": (21.6, 2.4, 2.8),
            "service": (23.5, 3.4, 1.4),
            "circulation": (20.4, 2.0, 3.4),
            "utility": (19.8, 2.6, 3.0),
            "storage": (18.5, 3.0, 4.0),
        }[room.kind]
        base, swing, lag = profile

        for ts in range(int(since), int(now), step):
            hour = ((ts / 3600) - lag) % 24
            # Peak mid-afternoon, trough before dawn.
            diurnal = math.sin((hour - 9) / 24 * 2 * math.pi)
            # The attic sits above the conditioned envelope, so it runs hotter.
            level_bonus = sensor.level * 1.5
            temp = base + swing * diurnal + level_bonus + random.gauss(0, 0.22)
            humidity = max(
                18.0,
                min(72.0, 52.0 - swing * 3.4 * diurnal - level_bonus * 1.6 + random.gauss(0, 1.1)),
            )
            store.record(
                conn,
                sensor.id,
                device_id=f"seed-{sensor.id}",
                ts=float(ts),
                temperature=round(temp, 1),
                humidity=round(humidity, 1),
                battery=random.randint(62, 100),
                online=True,
            )
            written += 1

    conn.commit()
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Synthetic data for the house twin.")
    parser.add_argument("--hours", type=int, default=48, help="trailing window to fill")
    parser.add_argument("--step", type=int, default=60, help="seconds between samples")
    parser.add_argument("--clear", action="store_true", help="wipe all readings and exit")
    parser.add_argument("--db", help="path to the SQLite file")
    args = parser.parse_args(argv)

    conn = store.connect(args.db)
    house = House.load()

    if args.clear:
        removed = clear(conn)
        print(f"cleared {removed} readings")
        return 0

    written = seed(conn, house, hours=args.hours, step=args.step)
    print(f"wrote {written} synthetic readings across {len(house.sensors)} sensors")
    print("note: these are simulated, not Aqara data")
    return 0


if __name__ == "__main__":
    sys.exit(main())
