"""
poller.py — the write side. Polls Aqara on an interval and appends readings.

Run standalone::

    python -m house_twin.poller

Deliberately never raises out of the loop: a transient API failure must not
kill a long-running agent. Error counts are tracked and logged so a persistently
broken setup is visible rather than silent.
"""

from __future__ import annotations

import logging
import signal
import sys
import time

from . import store
from .client import AqaraClient, AqaraError
from .config import DEFAULT_POLL_INTERVAL, Config
from .house import House

log = logging.getLogger("house_twin.poller")


def match_device(sensor, devices: list[dict]) -> dict | None:
    """Find the Aqara device feeding *sensor* by case-insensitive name match.

    Falls back to positional assignment when nothing matches, so a fresh install
    still shows data — the mapping is a convenience, not a requirement.
    """
    if sensor.match:
        needle = sensor.match.lower()
        for device in devices:
            if needle in str(device.get("name", "")).lower():
                return device
    return None


def assign_devices(house: House, devices: list[dict]) -> dict[str, dict]:
    """Return ``sensor_id -> device`` for every placement.

    Named matches win first; whatever is left is filled in declaration order so
    that a house full of generically-named sensors ("Sensor 1", "Sensor 2")
    still lights up.
    """
    mapping: dict[str, dict] = {}
    leftover: list[dict] = []

    for sensor in house.sensors:
        device = match_device(sensor, devices)
        if device:
            mapping[sensor.id] = device
        else:
            leftover.append(sensor)

    unclaimed = [d for d in devices if d.get("did") not in
                 {m["did"] for m in mapping.values()}]
    for sensor, device in zip(leftover, unclaimed):
        mapping[sensor.id] = device
        log.info("assigned %s -> %s (positional fallback)", sensor.id, device.get("name"))

    return mapping


def poll_once(client: AqaraClient, house: House, conn) -> int:
    """One full cycle. Returns the number of readings written."""
    sensors = client.read_sensors()
    if not sensors:
        log.warning("no sensors returned — not authorised yet, or no TH devices")
        return 0

    # The client returns raw devices; re-derive the name list for matching.
    by_did = {s["did"]: s for s in sensors}
    mapping = assign_devices(house, list(by_did.values()))

    written = 0
    for sensor in house.sensors:
        device = mapping.get(sensor.id)
        if not device:
            log.debug("no device for %s", sensor.id)
            continue
        reading = by_did.get(device["did"], device)
        store.record(
            conn,
            sensor.id,
            device_id=device.get("did"),
            temperature=reading.get("temperature"),
            humidity=reading.get("humidity"),
            battery=reading.get("battery"),
            online=reading.get("online", True),
        )
        written += 1

    conn.commit()
    log.info("wrote %d/%d sensor readings", written, len(house.sensors))
    return written


def run(
    interval: int = DEFAULT_POLL_INTERVAL,
    config: Config | None = None,
    house: House | None = None,
) -> None:
    """Poll forever until SIGINT/SIGTERM."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    config = config or Config()
    house = house or House()
    client = AqaraClient(config)
    conn = store.connect()
    running = True

    def stop(signum, _frame):
        nonlocal running
        log.info("signal %s received, finishing current cycle", signum)
        running = False

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    if not config.has_credentials():
        log.error("no credentials in %s — run `python -m house_twin.auth` first",
                  config.path)
    elif not config.has_token():
        log.error("credentials present but not authorised — run `python -m house_twin.auth`")

    last_prune = 0.0
    consecutive_errors = 0

    while running:
        started = time.time()
        try:
            poll_once(client, house, conn)
            consecutive_errors = 0
        except AqaraError as exc:
            consecutive_errors += 1
            log.warning("poll failed (%d in a row): %s", consecutive_errors, exc)
            # A rejected signature or expired token is worth retrying sooner.
            if exc.code in (401, 403):
                log.info("auth rejected, forcing token refresh")
                try:
                    client.refresh()
                except AqaraError as refresh_exc:
                    log.error("token refresh failed: %s", refresh_exc)
        except Exception:
            consecutive_errors += 1
            log.exception("unexpected error during poll")

        if time.time() - last_prune > 86400:
            removed = store.prune(conn)
            conn.commit()
            if removed:
                log.info("pruned %d readings past retention", removed)
            last_prune = time.time()

        elapsed = time.time() - started
        # Back off when failing so a bad token does not burn API quota.
        wait = interval if not consecutive_errors else min(interval * 2, 300)
        if consecutive_errors:
            wait = min(wait, max(60, 30 * consecutive_errors))
        remaining = max(0.0, wait - elapsed)
        if running and remaining:
            time.sleep(remaining)

    conn.close()
    log.info("poller stopped")


if __name__ == "__main__":
    try:
        run()
    except KeyboardInterrupt:
        sys.exit(0)
