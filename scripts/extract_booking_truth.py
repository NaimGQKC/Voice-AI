"""Extract the GROUND TRUTH for creating a Libro booking, from a HAR capture.

Instead of guessing which fields the create needs, this reads the dashboard's own
successful `POST /bookings` (HTTP 201) out of a browser HAR and prints it in
full, then correlates it with the `/services` responses in the same capture so we
can see exactly:

  1. every attribute the dashboard sends (untruncated, PII redacted)
  2. which `service` id it referenced, and that service's own attributes
  3. how the created booking's `time` relates to `expected-leave-at`
     (i.e. the real turn length the server applies)
  4. the cancel payload (PATCH)

Output is safe to share: names/phones/emails/notes are masked, and request
headers (token) are never read. The raw HAR stays local.

Usage:
    python scripts/extract_booking_truth.py libro.har
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PII_HINTS = ("first", "last", "name", "phone", "email", "formatted",
             "searchdisplay", "search-display", "note", "address", "display",
             "consent", "avatar", "edit-url")


def redact(o, key: str = ""):
    """Mask PII but keep every key and all structural/enum values."""
    if isinstance(o, dict):
        return {k: redact(v, k) for k, v in o.items()}
    if isinstance(o, list):
        return [redact(v, key) for v in o]
    kl = key.lower()
    if any(h in kl for h in PII_HINTS) and o not in (None, "", 0, False, []):
        return "<redacted>"
    return o


def _parse_dt(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        d = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)


def _json_or_none(text, encoding=None):
    if not text or encoding == "base64":
        return None
    try:
        return json.loads(text)
    except ValueError:
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description="Extract booking ground truth from a HAR.")
    ap.add_argument("har", help="path to the .har file")
    ap.add_argument("--out", default=str(ROOT / "libro_booking_truth.json"),
                    help="write the redacted create payload here (git-ignored)")
    args = ap.parse_args()

    path = Path(args.har)
    if not path.exists():
        print(f"File not found: {path}")
        return 1
    try:
        entries = json.loads(path.read_text(encoding="utf-8-sig"))["log"]["entries"]
    except Exception as exc:  # noqa: BLE001
        print(f"Could not parse HAR: {type(exc).__name__}: {exc}")
        return 1

    creates, patches, service_lists = [], [], []
    for e in entries:
        req, res = e.get("request", {}), e.get("response", {})
        url = req.get("url", "")
        if "libroreserve.com" not in url:
            continue
        p = urlsplit(url).path
        method = req.get("method", "")
        content = res.get("content", {}) or {}
        body = _json_or_none(content.get("text"), content.get("encoding"))
        rbody = _json_or_none((req.get("postData", {}) or {}).get("text"))
        status = res.get("status")

        if method == "POST" and p.rstrip("/").endswith("/bookings"):
            creates.append({"path": p, "status": status, "req": rbody, "res": body})
        elif method == "PATCH" and "/bookings/" in p:
            patches.append({"path": p, "status": status, "req": rbody, "res": body})
        elif p.rstrip("/").endswith("/services") and isinstance(body, dict):
            service_lists.append({"query": urlsplit(url).query, "body": body})

    ok = [c for c in creates if c["status"] in (200, 201)]
    if not ok:
        print("No successful POST /bookings found in this HAR.")
        print(f"(found {len(creates)} create attempt(s), {len(patches)} patch(es))")
        print("Re-capture while actually completing a booking in the dashboard.")
        return 1

    create = ok[0]
    print("=" * 72)
    print(f"GROUND TRUTH: POST {create['path']} -> {create['status']}")
    print("=" * 72)
    print("\n--- FULL REQUEST BODY (redacted, untruncated) ---")
    print(json.dumps(redact(create["req"]), indent=2, ensure_ascii=False))

    # Which service did it reference?
    rels = ((create["req"] or {}).get("data", {}) or {}).get("relationships", {}) or {}
    svc_id = str((((rels.get("service") or {}).get("data")) or {}).get("id", ""))
    attrs_req = ((create["req"] or {}).get("data", {}) or {}).get("attributes", {}) or {}
    attrs_res = ((create["res"] or {}).get("data", {}) or {}).get("attributes", {}) or {}

    print("\n--- KEY FIELDS ---")
    print(f"  request  expected-leave-at : {attrs_req.get('expected-leave-at')}")
    print(f"  request  'time' present?   : {'time' in attrs_req}")
    print(f"  request  service id        : {svc_id or '(none)'}")
    print(f"  response time              : {attrs_res.get('time')}")
    print(f"  response expected-leave-at : {attrs_res.get('expected-leave-at')}")
    start, leave = _parse_dt(attrs_res.get("time")), _parse_dt(attrs_res.get("expected-leave-at"))
    if start and leave:
        print(f"  => server turn length      : {int((leave - start).total_seconds() // 60)} min")

    # Correlate the referenced service with the /services listings.
    print("\n--- SERVICES SEEN IN THIS CAPTURE ---")
    found = False
    for listing in service_lists:
        data = listing["body"].get("data", [])
        if not isinstance(data, list):
            continue
        print(f"  [query: {listing['query'] or '(none)'}] -> {len(data)} service(s)")
        for s in data:
            sid = str(s.get("id", ""))
            a = s.get("attributes", {}) or {}
            mark = "  <-- USED BY THE BOOKING" if sid == svc_id else ""
            if sid == svc_id:
                found = True
            print(f"     id={sid} status={a.get('status')} "
                  f"started-at={a.get('started-at')} slots={a.get('slots')} "
                  f"min={a.get('min-slots')} max={a.get('max-slots')}{mark}")
    if svc_id and not found:
        print(f"\n  !! Service {svc_id} used by the booking is NOT in any /services")
        print("     response above — so the create references a service the")
        print("     dashboard got from a DIFFERENT call (check the query params).")

    if patches:
        print("\n--- CANCEL / UPDATE (PATCH) REQUEST BODY ---")
        print(json.dumps(redact(patches[0]["req"]), indent=2, ensure_ascii=False))

    Path(args.out).write_text(json.dumps(
        {"create_request": redact(create["req"]),
         "create_response": redact(create["res"]),
         "patch_request": redact(patches[0]["req"]) if patches else None},
        indent=2, ensure_ascii=False))
    print(f"\nSaved redacted payloads to: {args.out}")
    print("Everything above is safe to share.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
