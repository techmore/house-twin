"""
config.py — credential and settings persistence.

Settings live in a single JSON file outside version control (``config.json``).
Nothing here ever logs or returns a secret in plaintext to a template.
"""

from __future__ import annotations

import json
import os
import stat
import time
from pathlib import Path

#: Open Platform API domains, per the current v3.0 documentation.
#: https://opendoc.aqara.com/en/docs/developmanual/apiIntroduction/APIUsageGuide.html
API_DOMAINS = {
    "cn": "open-cn.aqara.com",
    "usa": "open-usa.aqara.com",
    "ger": "open-ger.aqara.com",
    "sg": "open-sg.aqara.com",
    "kr": "open-kr.aqara.com",
    "ru": "open-ru.aqara.com",
}

#: Free-tier ceiling is 80 devices for non-paying individual developers.
FREE_DEVICE_LIMIT = 80

#: Non-paid accounts are capped at 300 API calls per rolling 5 minutes.
FREE_CALLS_PER_5_MIN = 300

DEFAULT_CONFIG_PATH = Path(
    os.environ.get("HOUSE_TWIN_CONFIG", Path(__file__).resolve().parent.parent / "config.json")
)

#: Seconds between polls. One poll costs ~2 API calls, so this uses roughly
#: 0.7% of the free per-minute budget.
DEFAULT_POLL_INTERVAL = 60


def api_base(region: str = "usa") -> str:
    """Return the full v3.0 endpoint for *region*."""
    try:
        domain = API_DOMAINS[region]
    except KeyError:
        raise ValueError(
            f"unknown region {region!r}; expected one of {sorted(API_DOMAINS)}"
        ) from None
    return f"https://{domain}/v3.0/open/api"


def _read(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path} is not valid JSON: {exc}") from None


def _write(path: Path, data: dict) -> None:
    """Write *data* to *path*, owner-only, via a temp file + atomic replace."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    tmp.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0600 — holds live tokens
    tmp.replace(path)


class Config:
    """Reads and writes the on-disk settings file."""

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path else DEFAULT_CONFIG_PATH
        self._data = _read(self.path)

    # ── accessors ──────────────────────────────────────────────────────────

    def get(self, key: str, default=None):
        return self._data.get(key, default)

    def set(self, key: str, value) -> None:
        self._data[key] = value
        self.save()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        _write(self.path, self._data)

    # ── credentials ────────────────────────────────────────────────────────

    def set_credentials(self, app_id: str, app_key: str, key_id: str, region: str = "usa") -> None:
        if region not in API_DOMAINS:
            raise ValueError(f"unknown region {region!r}")
        self._data["app_id"] = app_id
        self._data["app_key"] = app_key
        self._data["key_id"] = key_id
        self._data["region"] = region
        self.save()

    def store_tokens(self, access_token: str, refresh_token: str, expires_in: int) -> None:
        self._data["access_token"] = access_token
        self._data["refresh_token"] = refresh_token
        self._data["token_expires_at"] = int(time.time()) + int(expires_in)
        self.save()

    def has_credentials(self) -> bool:
        return all(self._data.get(k) for k in ("app_id", "app_key", "key_id"))

    def has_token(self) -> bool:
        return bool(self._data.get("access_token"))

    def token_expires_at(self) -> int:
        return int(self._data.get("token_expires_at") or 0)

    def token_is_stale(self, margin: int = 300) -> bool:
        """True when the token is expired or within *margin* seconds of it."""
        return self.token_expires_at() - margin <= int(time.time())

    def credentials_for_signing(self) -> dict:
        """Assemble the kwargs :mod:`house_twin.signing` needs."""
        return {
            "app_id": self._data.get("app_id", ""),
            "app_key": self._data.get("app_key", ""),
            "key_id": self._data.get("key_id", ""),
            "access_token": self._data.get("access_token"),
        }

    def region(self) -> str:
        return self._data.get("region", "usa")
