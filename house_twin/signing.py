"""
signing.py — Aqara Cloud OpenAPI v3.0 request signing.

Reference: https://opendoc.aqara.com/en/docs/developmanual/apiIntroduction/signGenerationRules.html

Algorithm
---------
1. ASCII-sort the request headers that participate in the signature and splice
   them as ``Name=Value`` pairs joined by ``&``:

       Accesstoken=…&Appid=…&Keyid=…&Nonce=…&Time=…

2. Concatenate the AppKey directly onto the end of that string. There is **no**
   separator — the AppKey is a raw suffix, not an HMAC key.

3. Lowercase the whole resulting string.

4. MD5 it and take the 32 hex digits.

Two traps that the published documentation gets wrong or omits
--------------------------------------------------------------
* **Strip the ``k.`` prefix from Keyid.** The docs' step-1 illustration shows
  ``Keyid=k.78784564654feda454557``, but their own Java reference implementation
  signs the bare ``78784564654feda454557``. Only the bare form reproduces the
  signature published alongside that example. We normalise it here so a value
  pasted straight out of the Aqara console still signs correctly.

* **Accesstoken is optional but conditional.** The spec notes that some
  interfaces do not require it, and in that case it must not participate in the
  signature at all. Passing an empty string would otherwise emit a stray
  ``Accesstoken=`` pair and corrupt the hash.
"""

from __future__ import annotations

import hashlib
import time
import uuid

#: Header names that participate in the signature, in ASCII sort order.
#: Sorting is what the spec asks for, so we derive it rather than trusting the
#: literal order to stay correct if a header is ever added.
_SIGNED_HEADERS = ("Accesstoken", "Appid", "Keyid", "Nonce", "Time")

#: Published worked example from the signature-rules page, used by the tests to
#: prove this implementation reproduces Aqara's own reference output.
DOC_TEST_VECTOR = {
    "app_id": "4e693d54d75db580a56d1263",
    "key_id": "k.78784564654feda454557",
    "access_token": "532cad73c5493193d63d367016b98b27",
    "nonce": "C6wuzd0Qguxzelhb",
    "timestamp": "1618914078668",
    "app_key": "gU7Qtxi4dWnYAdmudyxni52bWZ58b8uN",
    "expected_sign": "bfd8dd0e7c108353e6740d81e05982d8",
}


def normalize_key_id(key_id: str) -> str:
    """Return *key_id* without the console's optional ``k.`` display prefix.

    The Aqara developer console presents Keyid as ``k.<value>`` but the signature
    is computed over ``<value>``. Signing the prefixed form yields a hash that
    the API rejects, so we always strip it.
    """
    key_id = key_id.strip()
    if key_id.startswith("k."):
        return key_id[2:]
    return key_id


def _preimage(
    app_id: str,
    key_id: str,
    nonce: str,
    timestamp: str,
    app_key: str,
    access_token: str | None = None,
) -> str:
    """Build the exact string that gets lowercased and MD5'd."""
    values = {
        "Accesstoken": access_token or "",
        "Appid": app_id,
        "Keyid": normalize_key_id(key_id),
        "Nonce": nonce,
        "Time": timestamp,
    }
    # Drop empty values entirely rather than emitting "Accesstoken=" — per the
    # spec an absent token must not participate in the signature.
    pairs = [f"{name}={values[name]}" for name in _SIGNED_HEADERS if values[name]]
    return "&".join(pairs) + app_key


def sign(
    app_id: str,
    key_id: str,
    app_key: str,
    nonce: str,
    timestamp: str,
    access_token: str | None = None,
) -> str:
    """Return the 32-character lowercase MD5 signature for one request."""
    preimage = _preimage(app_id, key_id, nonce, timestamp, app_key, access_token)
    return hashlib.md5(preimage.lower().encode("utf-8")).hexdigest()


def build_headers(
    app_id: str,
    key_id: str,
    app_key: str,
    access_token: str | None = None,
    *,
    lang: str = "en",
    nonce: str | None = None,
    timestamp: str | None = None,
) -> dict[str, str]:
    """Build the full auth header set for an OpenAPI v3.0 request.

    ``Time`` is milliseconds since the epoch, as a string. ``Nonce`` must differ
    on every request.
    """
    nonce = nonce or uuid.uuid4().hex
    timestamp = timestamp or str(int(time.time() * 1000))
    return {
        "Content-Type": "application/json",
        "Appid": app_id,
        "Keyid": normalize_key_id(key_id),
        "Nonce": nonce,
        "Time": timestamp,
        "Sign": sign(app_id, key_id, app_key, nonce, timestamp, access_token),
        "Lang": lang,
        # Absent rather than empty when we have no token yet — the auth-code
        # and token-exchange calls are made before one exists.
        **({"Accesstoken": access_token} if access_token else {}),
    }
