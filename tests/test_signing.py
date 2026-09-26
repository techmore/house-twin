"""
Tests for the Aqara OpenAPI v3.0 request signer.

The important one is ``test_doc_example_vector``: it reproduces the worked
example published on Aqara's signature-rules page. That example is what proves
we match the server, and it is the only ground truth available without live
credentials.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from house_twin.signing import (
    DOC_TEST_VECTOR,
    build_headers,
    normalize_key_id,
    sign,
)


def test_doc_example_vector() -> None:
    """Reproduce the signature published in Aqara's own documentation."""
    v = DOC_TEST_VECTOR
    got = sign(
        app_id=v["app_id"],
        key_id=v["key_id"],
        app_key=v["app_key"],
        nonce=v["nonce"],
        timestamp=v["timestamp"],
        access_token=v["access_token"],
    )
    assert got == v["expected_sign"], f"expected {v['expected_sign']}, got {got}"


def test_key_id_prefix_is_stripped() -> None:
    """The console's 'k.' display prefix must not reach the hash."""
    v = DOC_TEST_VECTOR
    assert normalize_key_id("k.abc123") == "abc123"
    assert normalize_key_id("abc123") == "abc123"
    # Prefixed and bare forms of the same key must sign identically.
    assert sign(v["app_id"], "k.abc", v["app_key"], "n", "1") == sign(
        v["app_id"], "abc", v["app_key"], "n", "1"
    )


def test_empty_token_is_omitted_entirely() -> None:
    """An absent Accesstoken must not leave a dangling 'Accesstoken=' pair."""
    headers = build_headers("app", "k.key", "secret")
    assert "Accesstoken" not in headers
    # A token-less request signs differently from an empty-token request, and
    # must equal the no-token form.
    assert headers["Sign"] == sign("app", "k.key", "secret", headers["Nonce"], headers["Time"])


def test_signature_is_deterministic() -> None:
    """Same inputs, same signature — nonce/time are injected by the caller."""
    a = sign("app", "key", "secret", "nonce1", "1700000000000", "tok")
    b = sign("app", "key", "secret", "nonce1", "1700000000000", "tok")
    assert a == b
    assert len(a) == 32 and a == a.lower()


def test_headers_carry_required_fields() -> None:
    headers = build_headers("app", "k.key", "secret", "tok")
    for field in ("Appid", "Keyid", "Nonce", "Time", "Sign", "Accesstoken"):
        assert field in headers, f"missing {field}"
    assert headers["Keyid"] == "key"
    assert headers["Accesstoken"] == "tok"
    assert headers["Lang"] == "en"


def test_nonce_and_time_are_unique_per_call() -> None:
    a = build_headers("app", "key", "secret", "tok")
    b = build_headers("app", "key", "secret", "tok")
    assert a["Nonce"] != b["Nonce"]
    assert a["Sign"] != b["Sign"]


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS  {name}")
            except AssertionError as exc:
                failures += 1
                print(f"FAIL  {name}: {exc}")
    print(f"\n{'all tests passed' if not failures else f'{failures} failure(s)'}")
    sys.exit(1 if failures else 0)
