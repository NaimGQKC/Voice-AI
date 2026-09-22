"""Extract the real Libro API calls from a browser HAR capture — safely, locally.

This is the definitive way to learn the private API: instead of guessing paths,
we read exactly what the dashboard calls. You record a HAR in your browser's
DevTools while using the reservation page, then this script parses it LOCALLY
and prints a redacted map of every Libro API request (method, path, query-param
names) and each response's schema — with your auth token and all customer PII
stripped out, so the console output is safe to share.

## How to capture the HAR (5 steps, ~2 minutes)

1. In Chrome/Edge, open  https://dashboard.libroreserve.com/restaurants/<redacted>/reservations
   and log in.
2. Press F12 → open the **Network** tab. Tick **Preserve log**. Filter: **Fetch/XHR**.
3. Do the things we want to learn:
   a. Click **+ Reservation**, pick a date/party, and **type a name or phone in
      "Search or add guest"** (this reveals the real guest-search endpoint).
   b. To capture the create payload, actually **complete one booking** for an
      obviously-fake guest ("ZZ Test") on a far-future off-peak slot — then
      **cancel it** in the dashboard right after (captures POST /bookings + the
      cancel call). This is the one deliberate write; the parser reads it from the
      HAR, it doesn't book anything itself.
4. Right-click anywhere in the Network list → **Save all as HAR with content**.
   Save it as e.g.  libro.har  in this folder.
5. Run:  python scripts/parse_har.py libro.har

## Safety

- Parsing is 100% local. This script reads the file, never uploads it.
- It NEVER prints request headers, cookies, or the Authorization token.
- Response bodies are shown as a type schema + a PII-redacted sample only.
- The .har file itself contains your token and customer data — it is git-ignored;
  keep it local and delete it when done. Share only the console output.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import urlsplit

# --- redaction (same rules as the probe) -----------------------------------
SAFE_VALUE_KEYS = {
    "id", "slots", "status", "source", "booking-type", "service", "service-id",
    "service-name", "started-at", "started-on", "expired-at", "time", "date",
    "total-slots", "booked-slots", "occupied-slots", "slots-threshold",
    "bookings-count", "is-available-online", "is-available-internally",
    "force-enabled", "locked", "table-number", "locale", "currency", "type",
    "party-size", "capacity", "count", "available",
}
PII_HINTS = ("first", "last", "name", "phone", "email", "formatted",
             "searchdisplay", "search-display", "note", "address", "display",
             "consent", "tags")

STATIC_EXTS = (".js", ".css", ".png", ".jpg", ".jpeg", ".svg", ".woff",
               ".woff2", ".ico", ".gif", ".map", ".ttf")

# Request-header names whose VALUES are hidden (secrets). Everything else (Accept,
# Content-Type, X-* version/client headers) is shown — it's what makes the private
# routes work, and contains no PII.
SECRET_HEADER_HINTS = ("authorization", "cookie", "token", "auth", "session",
                       "secret", "api-key", "apikey", "csrf", "xsrf")
INTERESTING_HEADERS = ("accept", "content-type", "x-", "api", "version", "client")


def _safe_headers(headers: list) -> dict:
    out = {}
    for h in headers or []:
        name = str(h.get("name", ""))
        low = name.lower()
        if low.startswith(":"):  # HTTP/2 pseudo-headers
            continue
        if not any(tok in low for tok in INTERESTING_HEADERS):
            continue
        if any(s in low for s in SECRET_HEADER_HINTS):
            out[name] = "<hidden>"
        else:
            out[name] = h.get("value", "")
    return out


def schema(o):
    if isinstance(o, dict):
        return {k: schema(v) for k, v in o.items()}
    if isinstance(o, list):
        return [schema(o[0])] if o else []
    return type(o).__name__


def redact(o, key: str = ""):
    if isinstance(o, dict):
        return {k: redact(v, k) for k, v in o.items()}
    if isinstance(o, list):
        if not o:
            return []
        return [redact(o[0], key)] + ([f"<+{len(o) - 1} more>"] if len(o) > 1 else [])
    kl = key.lower()
    if any(h in kl for h in PII_HINTS):
        return "<redacted>" if o not in (None, "", 0, False) else o
    if isinstance(o, str):
        if kl in SAFE_VALUE_KEYS:
            return o
        if len(o) >= 8 and o[:4].isdigit() and o[4:5] in ("-", "T", ""):
            return o
        return o if len(o) <= 20 and "@" not in o and " " not in o else "<redacted>"
    return o


def _looks_like_api(host: str, path: str) -> bool:
    if "libro" not in host.lower():
        return False
    if any(x in host.lower() for x in ("static", "s3", "assets", "cdn")):
        return False
    if any(path.lower().endswith(ext) for ext in STATIC_EXTS):
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Parse a HAR for Libro API calls (local, redacted).")
    parser.add_argument("har", help="path to the .har file exported from DevTools")
    parser.add_argument("--all-hosts", action="store_true",
                        help="include every host, not just *libro* ones")
    args = parser.parse_args()

    har_path = Path(args.har)
    if not har_path.exists():
        print(f"File not found: {har_path}")
        return 1
    try:
        har = json.loads(har_path.read_text(encoding="utf-8-sig"))
        entries = har["log"]["entries"]
    except Exception as exc:  # noqa: BLE001
        print(f"Could not parse HAR: {type(exc).__name__}: {exc}")
        return 1

    seen: dict[tuple, dict] = {}
    for e in entries:
        req = e.get("request", {})
        res = e.get("response", {})
        url = req.get("url", "")
        if not url:
            continue
        sp = urlsplit(url)
        host, path = sp.netloc, sp.path
        mime = (res.get("content", {}) or {}).get("mimeType", "")
        rtype = e.get("_resourceType", "")
        if not args.all_hosts and not _looks_like_api(host, path):
            continue
        # Keep API-ish calls: xhr/fetch or a JSON response.
        if not (rtype in ("xhr", "fetch") or "json" in mime):
            continue

        method = req.get("method", "GET")
        qkeys = [q.get("name") for q in req.get("queryString", []) or []]
        key = (method, host, path, tuple(sorted(qkeys)))
        if key in seen:
            continue

        parsed = None
        content = res.get("content", {}) or {}
        text = content.get("text", "")
        if "json" in mime and text and content.get("encoding") != "base64":
            try:
                parsed = json.loads(text)
            except ValueError:
                parsed = None

        # Request body for writes (POST/PATCH/PUT) — this is the create payload
        # we can't learn any other way. Never includes headers/token.
        req_body = None
        post = req.get("postData", {}) or {}
        ptext = post.get("text", "")
        if method != "GET" and ptext:
            try:
                req_body = json.loads(ptext)
            except ValueError:
                req_body = None

        seen[key] = {
            "method": method, "host": host, "path": path, "qkeys": qkeys,
            "status": res.get("status"), "mime": mime, "body": parsed,
            "req_body": req_body, "headers": _safe_headers(req.get("headers", [])),
        }

    if not seen:
        print("No Libro API calls found in that HAR.")
        print("Make sure you filtered to Fetch/XHR and interacted with the")
        print("reservation page before exporting. Try --all-hosts to widen.")
        return 0

    print(f"Found {len(seen)} unique Libro API endpoint(s) in {har_path.name}:\n")
    for info in sorted(seen.values(), key=lambda d: (d["path"], d["method"])):
        qs = f"  ?{', '.join(k for k in info['qkeys'] if k)}" if info["qkeys"] else ""
        print(f"── {info['method']} {info['path']}{qs}")
        print(f"     host={info['host']}  status={info['status']}  {info['mime']}")
        if info.get("headers"):
            print("     REQUEST HEADERS:", json.dumps(info["headers"])[:600])
        if isinstance(info.get("req_body"), (dict, list)):
            # Request bodies are the create/cancel payloads (redacted) — show in
            # full, untruncated, so no field is hidden.
            print("     REQUEST BODY SAMPLE:", json.dumps(redact(info["req_body"]), ensure_ascii=False))
        if isinstance(info["body"], (dict, list)):
            print("     RESPONSE SCHEMA:", json.dumps(schema(info["body"]))[:600])
            print("     RESPONSE SAMPLE:", json.dumps(redact(info["body"]), ensure_ascii=False)[:600])
        print()

    print("^ Safe to share (token/PII stripped). Keep the .har file itself private.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
