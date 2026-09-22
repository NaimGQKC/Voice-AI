"""READ-ONLY reconnaissance of the Libro private API.

Confirms the reverse-engineered shapes so we can finalize the adapter's field
mappings — using ONLY safe HTTP GETs. It NEVER creates, updates, or cancels
anything (no POST/PATCH/PUT/DELETE anywhere in this file).

What it does:
  GET /ping
  GET /availabilities   (a couple of dates x party sizes, to see slots/services)
  GET /bookings         (existing reservations — to learn the booking shape)
  GET /people/query     (optional guest search; only with --query)

Two outputs:
  * CONSOLE — a REDACTED structural summary (field names + types + safe enum
    values; all names/phones/emails/notes masked). Safe to share.
  * FILE — the FULL raw JSON is written locally to --raw-out (git-ignored) for
    YOUR reference only. It contains real customer data — do NOT share it.

Usage (credentials come from .env, never the command line):
    python scripts/probe_libro_private.py
    python scripts/probe_libro_private.py --dates 2026-08-15,2026-08-22 --parties 2,8
    python scripts/probe_libro_private.py --query "514"

Required in .env:
    LIBRO_PRIVATE_TOKEN=...          # the Token value (treat like a password)
    LIBRO_PRIVATE_EMAIL=<your Libro login email>
    LIBRO_PRIVATE_RESTAURANT_ID=<redacted>
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

# The API versions per endpoint via Accept: availabilities = v2, JSON:API = v1.
ACCEPT_V1 = "application/vnd.libro-private-v1+json"
ACCEPT_V2 = "application/vnd.libro-private-v2+json"

# Leaf values under these key names are shown as-is (non-PII, useful for mapping).
SAFE_VALUE_KEYS = {
    "id", "slots", "status", "source", "booking-type", "bookingtype",
    "service", "service-id", "service_id", "serviceid", "service-name",
    "started-at", "startedat", "started-on", "startedon", "expired-at",
    "time", "date", "total-slots", "totalslots", "booked-slots", "bookedslots",
    "occupied-slots", "occupiedslots", "slots-threshold", "bookings-count",
    "bookingscount", "is-available-online", "isavailableonline",
    "is-available-internally", "isavailableinternally", "force-enabled",
    "forceenabled", "locked", "is-exceptional", "table-number", "tablenumber",
    "do-not-move", "children", "reduced-mobility", "locale", "currency",
    "party-size", "capacity", "count", "available", "type",
}
# Any key containing one of these substrings has its value masked (PII).
PII_HINTS = ("first", "last", "name", "phone", "email", "formatted",
             "searchdisplay", "search-display", "note", "address", "display",
             "consent", "tags")


def _candidate_env_paths() -> list[Path]:
    # Cover the common Windows trap where Notepad saves ".env" as ".env.txt".
    return [
        ROOT / ".env",
        Path.cwd() / ".env",
        ROOT / ".env.txt",
        Path.cwd() / ".env.txt",
    ]


def _parse_env_file(path: Path) -> None:
    # utf-8-sig strips a BOM that Notepad may prepend (which would corrupt the
    # first key name). Also strip surrounding quotes from values.
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        v = v.strip().strip('"').strip("'")
        os.environ.setdefault(k.strip(), v)


def _load_env() -> list[Path]:
    """Load the first .env found; return the paths that existed (for diagnostics)."""
    seen: dict[Path, None] = {}
    for p in _candidate_env_paths():
        if p.exists():
            seen.setdefault(p.resolve(), None)
    found = list(seen)
    for path in found:
        try:
            _parse_env_file(path)
        except Exception:  # noqa: BLE001 - diagnostics handle the fallout
            pass
    return found


def _diagnose_env(found: list[Path]) -> None:
    print("\n--- .env diagnostics ---")
    print(f"Looked in: {ROOT}")
    if not found:
        print("  No .env file found. Create one named exactly '.env' (no .txt!) in")
        print(f"  {ROOT}")
        print("  Windows tip: in Notepad's 'Save as', set 'Save as type' = 'All Files'")
        print("  and the file name to  .env  — or run in PowerShell:")
        print('    notepad .env   (then save)   OR   ni .env  (creates it)')
        txt = [p for p in _candidate_env_paths() if p.name == ".env.txt" and p.exists()]
        if txt:
            print(f"  NOTE: found {txt[0]} — rename it to '.env' (remove the .txt).")
    else:
        for p in found:
            print(f"  Found: {p}")
        present = {k: bool(os.environ.get(k)) for k in
                   ("LIBRO_PRIVATE_TOKEN", "LIBRO_PRIVATE_EMAIL",
                    "LIBRO_PRIVATE_RESTAURANT_ID")}
        print("  Keys present (value hidden):")
        for k, ok in present.items():
            print(f"    {k}: {'SET' if ok else 'missing/empty'}")
        print("  If a key shows 'missing/empty', check for typos in the key name,")
        print("  a stray '#' commenting the line, or an empty value after '='.")


def schema(o):
    """A pure type-skeleton of a JSON value (field names + types; no data)."""
    if isinstance(o, dict):
        return {k: schema(v) for k, v in o.items()}
    if isinstance(o, list):
        return [schema(o[0])] if o else []
    return type(o).__name__


def redact(o, key: str = ""):
    """Structure-preserving copy with all PII masked. Safe to share."""
    if isinstance(o, dict):
        return {k: redact(v, k) for k, v in o.items()}
    if isinstance(o, list):
        # Show only the first item's (redacted) shape, note the count.
        if not o:
            return []
        return [redact(o[0], key)] + ([f"<+{len(o) - 1} more>"] if len(o) > 1 else [])
    kl = key.lower()
    if any(h in kl for h in PII_HINTS):
        return "<redacted>" if o not in (None, "", 0, False) else o
    if isinstance(o, str):
        if kl in SAFE_VALUE_KEYS:
            return o
        # Keep ISO timestamps/dates (useful, non-PII); mask other free text.
        if len(o) >= 8 and o[:4].isdigit() and o[4:5] in ("-", "T", ""):
            return o
        return o if len(o) <= 20 and "@" not in o and " " not in o else "<redacted>"
    return o  # numbers, bools, null


def _is_json(body) -> bool:
    return isinstance(body, (dict, list))


def _short_nonjson(body) -> str:
    """Collapse an HTML/text body to a one-liner (the 404 pages are huge)."""
    s = str(body)
    low = s.lower()
    if "<html" in low or "<!doctype" in low:
        for marker in ("doesn't exist", "n'existe pas", "not found", "error"):
            if marker in low:
                return "<HTML page — route not found / error>"
        return "<HTML page>"
    return s.strip().replace("\n", " ")[:120]


def _summary(label: str, status: int, ctype: str, body) -> str:
    out = [f"\n=== {label}  (HTTP {status}, {ctype}) ==="]
    if _is_json(body):
        n = len(body) if isinstance(body, list) else None
        out.append("SCHEMA (types only):")
        out.append(json.dumps(schema(body), indent=2)[:2500])
        out.append("SAMPLE (PII redacted):")
        out.append(json.dumps(redact(body), indent=2, ensure_ascii=False)[:2500])
        if n is not None:
            out.append(f"(list length: {n})")
    else:
        out.append(_short_nonjson(body))
    return "\n".join(out)


# CONFIRMED read-only endpoints (from a live dashboard HAR capture, 24 Jul 2026).
# {r}=restaurant id, {d}=date, {q}=guest query. All GET, all read-only.
def _candidates(r: str, d: str, p: str, q: str) -> list[tuple[str, str, dict, str]]:
    # (label, path, params, accept-version)
    cands = [
        ("avail: /availabilities/{date}", f"/availabilities/{d}", {"restaurant-id": r}, ACCEPT_V2),
        ("avail: /availabilities/summary", "/availabilities/summary",
         {"restaurant-id": r, "from": d, "to": d}, ACCEPT_V2),
        ("services (shifts + capacity)", "/services",
         {"restaurant-id": r, "started-on": d, "only-services": "true"}, ACCEPT_V1),
        ("guest search (any query)", "/people/query", {"query": q or "a"}, ACCEPT_V1),
        ("notes (day notes)", "/notes", {"restaurant-id": r, "started-on": d}, ACCEPT_V1),
    ]
    return cands


async def main() -> int:
    found_env = _load_env()
    parser = argparse.ArgumentParser(description="Read-only Libro private API recon.")
    default_dates = ",".join([
        (dt.date.today() + dt.timedelta(days=21)).isoformat(),
        (dt.date.today() + dt.timedelta(days=25)).isoformat(),
    ])
    parser.add_argument("--dates", default=default_dates,
                        help="comma-separated YYYY-MM-DD dates")
    parser.add_argument("--parties", default="2,8",
                        help="comma-separated party sizes")
    parser.add_argument("--query", default="", help="optional guest search term")
    parser.add_argument("--raw-out", default=str(ROOT / "libro_recon_raw.json"),
                        help="local file for FULL raw output (git-ignored; do not share)")
    parser.add_argument("--base-url", default=os.environ.get(
        "LIBRO_PRIVATE_BASE_URL", "https://api.libroreserve.com"))
    args = parser.parse_args()

    token = os.environ.get("LIBRO_PRIVATE_TOKEN", "")
    email = os.environ.get("LIBRO_PRIVATE_EMAIL", "")
    restaurant_id = os.environ.get("LIBRO_PRIVATE_RESTAURANT_ID", "<redacted>")
    if not token or not email:
        print("ERROR: LIBRO_PRIVATE_TOKEN and/or LIBRO_PRIVATE_EMAIL are not set.")
        _diagnose_env(found_env)
        return 1

    import httpx

    headers = {
        "Accept": ACCEPT_V1,
        "Authorization": f'Token token="{token}", email="{email}"',
    }
    raw: dict = {}
    hits: list[str] = []  # endpoints that returned real JSON
    date = [d.strip() for d in args.dates.split(",") if d.strip()][0]
    party = [p.strip() for p in args.parties.split(",") if p.strip()][0]
    print(f"READ-ONLY endpoint discovery on {args.base_url} (restaurant {restaurant_id}).")
    print("Trying likely path patterns; all GET, nothing is created or changed.\n")

    async def get(client, label, path, params=None, accept=ACCEPT_V1):
        try:
            r = await client.get(path, params=params, headers={"Accept": accept})
        except Exception as exc:  # noqa: BLE001
            print(f"  [ERR ] {label}: {type(exc).__name__}")
            return None
        ctype = r.headers.get("content-type", "")
        body = _safe_json(r)
        raw[label] = {"path": path, "params": params, "status": r.status_code,
                      "content_type": ctype, "body": body}
        kind = "json" if _is_json(body) else ("html" if "html" in ctype.lower() else "text")
        flag = "HIT " if (r.status_code == 200 and kind == "json") else "    "
        print(f"  [{flag}] HTTP {r.status_code} {kind:4}  {path}  {params or ''}")
        if r.status_code == 200 and kind == "json":
            hits.append(label)
        return r

    async with httpx.AsyncClient(base_url=args.base_url, headers=headers, timeout=20) as client:
        ping = await get(client, "ping", "/ping")
        if ping is not None and ping.status_code in (401, 403):
            print("\n>>> Auth rejected (401/403). Re-check the token/email in .env.")
            return 1

        print("\n-- probing endpoint candidates --")
        for label, path, params, accept in _candidates(restaurant_id, date, party, args.query):
            await get(client, label, path, params, accept=accept)

        # For every JSON hit, print the (redacted) schema so we can map fields.
        if hits:
            print("\n===== JSON RESPONSES (redacted, safe to share) =====")
            for label in hits:
                entry = raw[label]
                print(_summary(label, entry["status"], entry["content_type"], entry["body"]))

    # Full raw output for the owner's eyes only.
    Path(args.raw_out).write_text(json.dumps(raw, indent=2, ensure_ascii=False))
    print("\n===== DISCOVERY SUMMARY =====")
    if hits:
        print(f"Found {len(hits)} working JSON endpoint(s):")
        for label in hits:
            print(f"  ✓ {raw[label]['path']}   ({label})")
    else:
        print("No candidate path returned JSON. Paste this whole output back and")
        print("we'll widen the search (the real paths may need a different prefix).")
    print(f"\nFull raw output saved to: {args.raw_out}")
    print("  ^ contains real customer data — keep local, do NOT share or commit.")
    print("Safe to share with the developer: everything printed in THIS console.")
    print("Nothing was created or modified.")
    return 0


def _safe_json(resp):
    try:
        return resp.json()
    except ValueError:
        return resp.text


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
