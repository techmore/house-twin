# House Twin

A Three.js model of the house wired to live Aqara temperature/humidity
readings, with history so you can see how rooms drift over days and seasons.

The building is **30 × 33 ft, slab on grade, two occupied floors plus a standing
attic** — 990 sq ft per level, 2,580 sq ft conditioned.

---

## Status

| Piece | State |
|---|---|
| Request signing | **Verified** against Aqara's published test vector |
| API endpoints | Corrected to the current v3.0 spec |
| Authorisation flow | Written; **not yet exercised** — needs an approved developer app |
| Sensor polling | Written; blocked on the above |
| House geometry | Done, validated at import |
| IDW temperature field | Done, verified rendering |
| History + charts | Done |

The one thing standing between this and live data is Aqara developer approval.
See [Getting authorised](#getting-authorised).

---

## Quick start

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# Populate with simulated data so the model has something to show
.venv/bin/python -m house_twin.seed --hours 48

.venv/bin/python -m house_twin.web     # → http://127.0.0.1:5002
```

Drag to orbit, scroll to zoom, click any sensor for its history. The
**Explode floors** slider separates the three levels so you can see inside.

---

## Getting authorised

### 1. Register a developer app

Go to <https://developer.aqara.com/register> and register. The wizard is three
steps: account → information → **awaiting moderation**. Approval is manual, so
budget some waiting time.

> Registration reopened after this project was first written; an older note in
> the sibling energy-monitor repo still says signup is unavailable. It is not.

Once approved, the console gives you an **Appid**, an **AppKey** and a
**Keyid**. The Keyid is displayed with a `k.` prefix — that is fine, the signer
strips it (see below).

### 2. Store the credentials

```bash
.venv/bin/python -m house_twin.auth setup \
    --app-id  <Appid> \
    --app-key <AppKey> \
    --key-id  <Keyid> \
    --region  usa
```

`--region` accepts `usa`, `ger`, `sg`, `cn`, `kr`, `ru`. Use whichever matches
your Aqara account; `usa` is the default.

### 3. Authorise your Aqara account

```bash
.venv/bin/python -m house_twin.auth login you@example.com
```

Aqara emails or texts a one-time code (valid 10 minutes). Paste it back and the
token pair is written to `config.json` with `0600` permissions.

Check state any time — this never prints a secret:

```bash
.venv/bin/python -m house_twin.auth status
```

### 4. Start polling

```bash
PYTHONUNBUFFERED=1 .venv/bin/python -u -m house_twin.poller
```

---

## How the auth flow works

There is **no browser redirect and no `redirect_uri`** to configure. Aqara
discontinued custom redirect URIs in June 2026, but the documented
[Aqara Account Authorization Mode](https://opendoc.aqara.com/en/docs/developmanual/authManagement/aqaraauthMode.html)
never needed one — it is three server-to-server calls:

| Step | Intent | Result |
|---|---|---|
| 1 | `config.auth.getAuthCode` | code emailed/SMSed to you, 10 min life |
| 2 | `config.auth.getToken` | `accessToken` + `refreshToken` + `openId` |
| 3 | `config.auth.refreshToken` | renewed pair |

The poller calls step 3 on its own when the token is within 5 minutes of
expiry, so this is a one-time setup step in practice.

---

## Request signing

Signing is the part most likely to waste your time, because Aqara's published
example is subtly wrong. Verified against the spec:

```
sign = MD5(("Accesstoken=" + token
            + "&Appid="   + app_id
            + "&Keyid="   + key_id        # WITHOUT the console's "k." prefix
            + "&Nonce="   + nonce
            + "&Time="    + timestamp_ms
            + app_key                     # raw suffix, no separator
           ).lower())
```

Three things that differ from a naive reading of the docs:

1. **MD5, not HMAC.** AppKey is concatenated as a raw suffix, not used as a
   key. The predecessor of this code in the energy-monitor repo used
   HMAC-SHA256 and could never have authenticated.
2. **Strip the `k.` prefix from Keyid.** The docs show `k.7878…` in their
   step-1 illustration but sign the bare `7878…` in their own Java reference
   implementation. Only the bare form reproduces their published signature.
3. **An absent Accesstoken is omitted entirely** — not sent as an empty value —
   otherwise it leaves a dangling `Accesstoken=` pair and corrupts the hash.

Run the tests to confirm:

```bash
.venv/bin/python tests/test_signing.py
```

`test_doc_example_vector` reproduces the signature published on Aqara's own
signature-rules page, which is the only ground truth available without live
credentials.

---

## Endpoints

The Open Platform is region-scoped and versioned:

```
https://open-usa.aqara.com/v3.0/open/api      # also -ger -sg -cn -kr -ru
```

Older code floating around uses `aiot-open-3rd.aqara.com/open/api`, which is
no longer the documented host or path.

---

## Free-tier limits

Non-paying individual developers are capped at:

| Limit | Value | How this design stays under it |
|---|---|---|
| API calls | 300 / 5 min | ~2 calls per poll at a 60 s interval |
| Monthly calls | 100,000 | ~86k/month at 60 s polling |
| Devices | 80 | 9 sensor placements |
| Token life | 30 days max | auto-refresh in the poller |
| Accounts | own account only | fine for a single house |

The 60-second poll interval is a deliberate choice. Polling faster buys
nothing for temperature/humidity, which drift on a scale of minutes, and it
sits comfortably inside the burst limit.

---

## Layout

```
house_twin/
  signing.py    MD5 request signing (the fiddly part)
  config.py     credential + token persistence, region → endpoint
  client.py     OpenAPI v3.0 client: auth, devices, batched resource reads
  auth.py       CLI for the three-step authorisation flow
  house.py      the building: levels, rooms, sensor placements
  store.py      SQLite schema, reads, history, retention
  poller.py     the write side — polls and appends
  web.py        the read side — JSON API + static host (127.0.0.1 only)
  seed.py       synthetic data for development
  static/       Three.js front end (three.js vendored locally)
tests/
  test_signing.py
```

Two processes, one database — the poller writes, the web layer reads, and they
never import each other.

---

## The model

`house.py` is the single source of truth for geometry. Rooms are axis-aligned
rectangles in feet, and **every level is asserted at import to tile the full
30 × 33 footprint exactly**. Change a dimension and a bad edit fails loudly
rather than leaving a hole in the floor.

```
Main Floor (y=0)          Second Floor (y=9)        Attic (y=18)
  Living      18×16         Bedroom 1   15×16         Standing   24×25
  Kitchen     12×10         Bedroom 2   15×12         Store N    30×4
  Dining      12×6          Bedroom 3   15×12         Store S    30×4
  Entry        8×9          Landing     15×8          Store W     3×25
  Bath        10×9          Bath        15×9          Store E     3×25
  Utility     18×8          Stair Hall  15×9
  Stair Hall  12×17
```

The attic is inset with knee-wall storage around it, which is what a standing
attic actually looks like in section — 600 sq ft of headroom, 390 sq ft of
knee wall.

### Temperature field

Floor plates are textured with an inverse-distance-weighted field computed from
the sensors on that level, so colour between two sensors is **interpolated from
readings, not estimated**. A level with no sensors renders neutral grey rather
than a plausible-looking fake, and a level with exactly one sensor renders flat
— which is correct, since a single sample cannot imply a gradient.

To change the layout or move a sensor, edit `house.py`. Sensor-to-device
matching is by case-insensitive substring on the device name, with a positional
fallback so a house full of generically named sensors still lights up.

---

## API

| Route | Returns |
|---|---|
| `GET /api/house` | levels, rooms, sensor placements |
| `GET /api/readings?hours=24` | latest per sensor + 24 h rollup |
| `GET /api/history/<id>?hours=24` | time series, downsampled to 2000 points |
| `GET /api/status` | readiness; never includes credentials or tokens |

---

## Notes

- `config.json` holds live tokens. It is written `0600` and gitignored.
- Readings are retained 365 days and pruned daily. A year of 9 sensors at one
  row per minute is roughly 4.7M rows.
- An offline sensor still records a row, so the chart shows a real gap rather
  than silently holding the last good value.
- `house_twin.seed` writes clearly-labelled synthetic data. Clear it with
  `--clear` before trusting anything on screen.
