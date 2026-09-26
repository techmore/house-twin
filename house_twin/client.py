"""
client.py — Aqara Cloud OpenAPI v3.0 client.

Every call is a POST to ``https://{domain}/v3.0/open/api`` with a JSON body of
``{"intent": ..., "data": {...}}`` and the signed headers from
:mod:`house_twin.signing`.

The client is deliberately thin and synchronous. It raises :class:`AqaraError`
with the server's own ``message`` so the poller can log something actionable.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

from .config import Config, api_base
from .signing import build_headers

log = logging.getLogger(__name__)

# Resource IDs for the temperature/humidity sensor family. Values arrive in
# hundredths, so temperature and humidity both need dividing by 100.
RESOURCE_TEMPERATURE = "0.1.85"
RESOURCE_HUMIDITY = "0.2.85"
RESOURCE_BATTERY = "8.0.2005"

#: Model fragments identifying temp/humidity sensors. Matched case-insensitively
#: against ``model`` from ``query.device.info``.
TH_MODEL_MARKERS = ("sensor_ht", "sensor_air", "temphumid", "th")

DEFAULT_TIMEOUT = 15


class AqaraError(RuntimeError):
    """An error returned by the Aqara API, or a transport failure."""

    def __init__(self, message: str, *, code: int | None = None, intent: str | None = None):
        super().__init__(message)
        self.code = code
        self.intent = intent


class AqaraClient:
    """Signed JSON-RPC style access to the Aqara Open Platform."""

    def __init__(self, config: Config, *, timeout: int = DEFAULT_TIMEOUT):
        self.config = config
        self.timeout = timeout

    # ── transport ──────────────────────────────────────────────────────────

    def call(self, intent: str, data: dict | None = None) -> dict:
        """POST one *intent* and return the ``result`` payload.

        Raises :class:`AqaraError` on any non-zero ``code`` or transport error.
        """
        url = api_base(self.config.region())
        body = json.dumps({"intent": intent, "data": data or {}}).encode("utf-8")
        headers = build_headers(**self.config.credentials_for_signing())

        request = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:400]
            raise AqaraError(
                f"HTTP {exc.code} from {url}: {detail}", intent=intent
            ) from exc
        except urllib.error.URLError as exc:
            raise AqaraError(f"cannot reach {url}: {exc.reason}", intent=intent) from exc
        except json.JSONDecodeError as exc:
            raise AqaraError(f"non-JSON response from {url}: {exc}", intent=intent) from exc

        code = payload.get("code")
        if code != 0:
            raise AqaraError(
                payload.get("message") or f"intent {intent} failed with code {code}",
                code=code,
                intent=intent,
            )
        return payload.get("result") or {}

    # ── auth (Aqara Account Authorization Mode) ────────────────────────────
    #
    # Server-to-server; no browser redirect and no redirect_uri. See
    # https://opendoc.aqara.com/en/docs/developmanual/authManagement/aqaraauthMode.html

    def request_auth_code(self, account: str, validity: str = "30d") -> str:
        """Step 1 — have Aqara email/SMS *account* a one-time code.

        The code is valid for 10 minutes. *validity* sets the access-token
        lifetime: ``1h``–``24h`` or ``1d``–``30d`` (default ``7d``).
        """
        result = self.call(
            "config.auth.getAuthCode",
            {"account": account, "accountType": 0, "accessTokenValidity": validity},
        )
        code = result.get("authCode")
        if not code:
            raise AqaraError("getAuthCode returned no authCode", intent="config.auth.getAuthCode")
        return code

    def exchange_auth_code(self, account: str, auth_code: str) -> dict:
        """Step 2 — trade the emailed code for an access/refresh token pair."""
        result = self.call(
            "config.auth.getToken",
            {"authCode": auth_code, "account": account, "accountType": 0},
        )
        if not result.get("accessToken"):
            raise AqaraError("getToken returned no accessToken", intent="config.auth.getToken")
        self.config.store_tokens(
            result["accessToken"], result.get("refreshToken", ""), result.get("expiresIn", 0)
        )
        return result

    def refresh(self) -> dict:
        """Step 3 — renew the token pair. Refresh token outlives it by 30 days."""
        refresh_token = self.config.get("refresh_token")
        if not refresh_token:
            raise AqaraError("no refresh_token stored")
        result = self.call("config.auth.refreshToken", {"refreshToken": refresh_token})
        self.config.store_tokens(
            result.get("accessToken", ""), result.get("refreshToken", ""), result.get("expiresIn", 0)
        )
        return result

    def ensure_fresh_token(self) -> None:
        """Refresh proactively when the token is expired or nearly so."""
        if self.config.has_token() and self.config.token_is_stale():
            log.info("access token stale, refreshing")
            self.refresh()

    # ── devices ────────────────────────────────────────────────────────────

    def list_devices(self, page: int = 1, size: int = 50) -> list[dict]:
        """Return every device on the authorised account."""
        result = self.call("query.device.info", {"pageNum": page, "pageSize": size})
        return result.get("data") or []

    def list_th_sensors(self) -> list[dict]:
        """Filter :meth:`list_devices` down to temperature/humidity sensors."""
        devices = self.list_devices()
        return [
            d
            for d in devices
            if any(m in str(d.get("model", "")).lower() for m in TH_MODEL_MARKERS)
        ]

    def read_resources(self, resources: list[tuple[str, str]]) -> dict[tuple[str, str], object]:
        """Batch-read ``(subjectId, resourceId)`` pairs in a single API call.

        Batching is what keeps us inside the free tier: six sensors polled once a
        minute is two calls per minute against a ceiling of 60.
        """
        if not resources:
            return {}
        payload = [
            {"subjectId": subject, "resourceId": resource} for subject, resource in resources
        ]
        result = self.call("query.resource.value", {"resources": payload})
        values: dict[tuple[str, str], object] = {}
        for entry in result or []:
            subject, resource = entry.get("subjectId"), entry.get("resourceId")
            if subject and resource:
                values[(subject, resource)] = entry.get("value")
        return values

    def read_sensors(self) -> list[dict]:
        """Fetch current temperature, humidity and battery for all TH sensors.

        One ``query.device.info`` plus one batched ``query.resource.value``.
        Returns ``[]`` when nothing is configured rather than raising, so a
        not-yet-authorised setup degrades quietly.
        """
        if not (self.config.has_credentials() and self.config.has_token()):
            return []
        self.ensure_fresh_token()

        sensors = self.list_th_sensors()
        if not sensors:
            return []

        wanted = (RESOURCE_TEMPERATURE, RESOURCE_HUMIDITY, RESOURCE_BATTERY)
        pairs = [(d["did"], r) for d in sensors for r in wanted]
        values = self.read_resources(pairs)

        def scaled(did: str, resource: str, divisor: int) -> float | None:
            raw = values.get((did, resource))
            if raw is None:
                return None
            try:
                return round(int(raw) / divisor, 1)
            except (TypeError, ValueError):
                return None

        def battery(did: str) -> int | None:
            raw = values.get((did, RESOURCE_BATTERY))
            try:
                return int(raw) if raw is not None else None
            except (TypeError, ValueError):
                return None

        return [
            {
                "did": d["did"],
                "name": d.get("name") or d["did"],
                "model": d.get("model", ""),
                "temperature": scaled(d["did"], RESOURCE_TEMPERATURE, 100),
                "humidity": scaled(d["did"], RESOURCE_HUMIDITY, 100),
                "battery": battery(d["did"]),
                "online": d.get("state", 0) == 1,
            }
            for d in sensors
        ]
