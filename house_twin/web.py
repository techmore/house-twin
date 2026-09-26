"""
web.py — Flask read-only API plus static host for the Three.js front end.

Binds 127.0.0.1 only. Reads the same SQLite file the poller writes; never
imports the poller.
"""

from __future__ import annotations

import logging
import os

from flask import Flask, jsonify, request, send_from_directory

from . import store
from .client import AqaraClient, AqaraError
from .config import Config
from .house import House

log = logging.getLogger(__name__)

STATIC_DIR = "static"

app = Flask(__name__, static_folder=STATIC_DIR, static_url_path="/static")

# Reloaded on demand so editing house_layout.json does not need a restart.
def current_house() -> House:
    return House.load()


house = current_house()


def _db():
    return store.connect()


# ── pages ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


# ── API ──────────────────────────────────────────────────────────────────────

@app.route("/api/house")
def api_house():
    """Static geometry: levels, rooms, sensor placements."""
    return jsonify(current_house().to_dict())


@app.route("/api/layout", methods=["POST"])
def api_save_layout():
    """Persist a layout override — used by the in-scene editor.

    Validates before writing: a rejected layout returns 400 and leaves the file
    on disk untouched, so a bad drag can never corrupt a working model.
    """
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "expected a JSON object"}), 400
    # An empty body is almost certainly a broken client. Without this guard it
    # would validate as "all defaults" and quietly overwrite a saved layout.
    if not payload.keys() & {"levels", "sensors"}:
        return jsonify({"error": "layout must contain 'levels' and/or 'sensors'"}), 400
    try:
        candidate = House.from_dict(payload)
    except (ValueError, KeyError, TypeError) as exc:
        return jsonify({"error": str(exc)}), 400

    try:
        path = candidate.save_layout()
    except OSError as exc:
        return jsonify({"error": f"cannot write layout: {exc}"}), 500

    global house
    house = candidate
    return jsonify({"saved": str(path), "meta": candidate.summary()})


@app.route("/api/readings")
def api_readings():
    """Latest reading for every sensor, keyed by placement id."""
    with _db() as conn:
        latest = {row["sensor_id"]: row for row in store.latest(conn)}
        summary = store.stats(conn, hours=int(request.args.get("hours", 24)))

    sensors = []
    for placement in house.sensors:
        row = latest.get(placement.id)
        sensors.append(
            {
                **placement.to_dict(),
                "temperature": row["temperature"] if row else None,
                "humidity": row["humidity"] if row else None,
                "battery": row["battery"] if row else None,
                "online": bool(row["online"]) if row else None,
                "ts": row["ts"] if row else None,
            }
        )
    return jsonify({"sensors": sensors, "summary": summary})


@app.route("/api/history/<sensor_id>")
def api_history(sensor_id: str):
    """Time series for one sensor. ``?hours=24&limit=2000``"""
    if house.room(sensor_id) is None and not any(
        s.id == sensor_id for s in house.sensors
    ):
        return jsonify({"error": f"unknown sensor {sensor_id}"}), 404

    hours = int(request.args.get("hours", 24))
    limit = int(request.args.get("limit", 2000))
    with _db() as conn:
        rows = store.history(conn, sensor_id, hours=hours, limit=limit)
    return jsonify(
        {
            "sensor_id": sensor_id,
            "hours": hours,
            "points": [
                {
                    "ts": r["ts"],
                    "temperature": r["temperature"],
                    "humidity": r["humidity"],
                    "battery": r["battery"],
                }
                for r in rows
            ],
        }
    )


@app.route("/api/status")
def api_status():
    """Readiness of the upstream integration, plus live device count.

    Never returns credentials or tokens.
    """
    config = Config()
    payload = {
        "credentials": config.has_credentials(),
        "authorised": config.has_token(),
        "region": config.region(),
        "endpoint": f"https://{config.region()}",
    }
    if config.has_credentials() and config.has_token():
        try:
            client = AqaraClient(config)
            devices = client.list_devices()
            payload["devices"] = len(devices)
            payload["th_sensors"] = len(client.list_th_sensors())
            payload["reachable"] = True
        except AqaraError as exc:
            payload["reachable"] = False
            payload["error"] = str(exc)
    return jsonify(payload)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    # Bound to loopback only. Never expose this beyond the machine.
    host = os.environ.get("HOUSE_TWIN_HOST", "127.0.0.1")
    port = int(os.environ.get("HOUSE_TWIN_PORT", "5002"))
    app.run(host=host, port=port, debug=False)
