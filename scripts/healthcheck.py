"""READ-ONLY health check for the live Libro integration. Built for cron.

╔══════════════════════════════════════════════════════════════════════════╗
║  READ-ONLY. This file issues HTTP GET and nothing else — no POST, no      ║
║  PATCH, no PUT, no DELETE, anywhere, ever. It cannot create, modify or    ║
║  cancel a reservation on the live floor. `_get()` below is the only       ║
║  request path in the module, and a test asserts the file contains no      ║
║  writing verb. (Owner's standing rule: see scripts/test_booking_libro.py) ║
╚══════════════════════════════════════════════════════════════════════════╝

Why this exists
---------------
The goal is *deploy to one restaurant and never touch it again*. The single
biggest threat to that goal is the thing this script watches:

We book real tables through Libro's **reverse-engineered private dashboard API**
using a **long-lived token**. Nobody at Libro owes us notice before changing it.
Two things can therefore break silently:

1. **The token dies** (expires, is rotated, the login is reset). Every booking
   fails from that moment on.
2. **The API drifts** — the availability payload changes shape, or the
   "a service is a 15-minute slot" model we derived stops holding. Bookings then
   fail in ways that look like "we're fully booked" to a caller.

Either way the agent degrades honestly (it captures callers for a callback
rather than lying to them — see `Concierge._degrade`), but *degraded is not
working*. Without this check the first person to notice is the restaurant owner,
weeks later, wondering why the phone agent stopped booking anyone.

This script's job is to make that discovery happen on a schedule instead.

What it verifies
----------------
  1. **Reachability** — api.libroreserve.com answers at all.
  2. **Auth** — the token is still accepted (not 401/403).
  3. **Availability shape** — `GET /availabilities/{date}` still returns
     `{ISO-datetime: {party-size "1".."6": {seating-area: count}}}`, and the
     party-size keys are still exactly 1-6. That ceiling is load-bearing: the
     adapter's MAX_ONLINE_PARTY and the "7+ is a human handoff" rule both depend
     on it.
  4. **Services shape** — `GET /services` still returns JSON:API records with a
     parseable `started-at`.
  5. **The service-as-slot model** — that at least one availability instant
     matches a service `started-at` exactly. This is the assumption the entire
     booking path rests on (the booking's time is derived from the referenced
     service, never sent). If it ever stops holding, every create returns
     422 "You must select a date & time" and the agent books nobody.
  6. **Latency** — a backend that answers in 9 seconds is broken for a phone
     call even though it is technically "up".

Usage
-----
    python scripts/healthcheck.py                # human-readable
    python scripts/healthcheck.py --quiet        # cron: silent unless unhealthy
    python scripts/healthcheck.py --json         # machine-readable

Cron (daily at 08:15, mail on failure only — `--quiet` prints nothing when OK,
and cron mails you whatever a job prints):

    15 8 * * * cd /srv/resto && /srv/resto/.venv/bin/python scripts/healthcheck.py --quiet

Exit codes (all non-zero mean "look at this"):
    0  healthy
    1  API DRIFT — the shapes changed. The adapter needs updating.
    2  AUTH FAILED — the Libro token is dead. Re-mint it from the dashboard.
    3  UNREACHABLE — network/DNS/timeout, or Libro is down.
    4  MISCONFIGURED — credentials missing from .env.

Credentials come from `.env` (LIBRO_PRIVATE_TOKEN / _EMAIL / _RESTAURANT_ID).
The token is never printed, logged, or included in any output of this script.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json as jsonlib
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

# Accept headers are per-endpoint; sending the wrong one returns 404, which
# would look like drift. See docs/LIBRO_PRIVATE_INTEGRATION.md.
ACCEPT_V1 = "application/vnd.libro-private-v1+json"   # JSON:API endpoints
ACCEPT_V2 = "application/vnd.libro-private-v2+json"   # /availabilities only

#: The party-size keys the availability endpoint has ALWAYS returned, verified by
#: direct probe over 28 days / 731 slots / 4,386 cells. Not a guess, and not a
#: policy choice — Libro cannot express a party of 7. If this set ever changes,
#: `libro_private.MAX_ONLINE_PARTY` and the large-party handoff are both wrong.
EXPECTED_PARTY_KEYS = frozenset({"1", "2", "3", "4", "5", "6"})

#: Days ahead to sample. Several, because a single date proves nothing: the
#: restaurant is legitimately closed some days, and an empty map on a Monday is
#: not drift. At least one sampled date must look normal.
DEFAULT_PROBE_OFFSETS = (2, 5, 9, 16, 23)

#: A read slower than this is a problem for a live call even if it succeeds —
#: the caller hears it as dead air. Warned, not failed: it may be one bad minute.
SLOW_READ_S = 2.5

#: Give the health check more rope than a phone call gets. A cron job can wait;
#: we would rather learn "slow" than "unreachable".
HTTP_TIMEOUT_S = 20.0

EXIT_OK, EXIT_DRIFT, EXIT_AUTH, EXIT_UNREACHABLE, EXIT_CONFIG = 0, 1, 2, 3, 4

GREEN, RED, YELLOW, DIM, END = "\033[92m", "\033[91m", "\033[93m", "\033[2m", "\033[0m"


# ---------------------------------------------------------------------------
# Shape validation — pure functions, no network, unit-tested offline.
# ---------------------------------------------------------------------------
def _is_iso_datetime(value: str) -> bool:
    try:
        dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return False
    return True


def _parse_instant(value: str):
    """ISO-8601 -> aware UTC datetime, or None. Mirrors the adapter's parser."""
    try:
        d = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return d.astimezone(dt.timezone.utc)


def validate_availability_shape(body, *, date: str = "") -> list[str]:
    """Return a list of drift complaints about one /availabilities payload.

    Empty list == the shape is exactly what the adapter parses. The known-good
    shape (verified by direct probe) is::

        {ISO-datetime: {party-size "1".."6": {seating-area: count}}}

    where an empty inner map means "full for that party size at that time".

    An entirely empty top-level map is *not* drift on its own — the restaurant
    may be closed that day — so it is reported separately by the caller.
    """
    where = f"/availabilities/{date}" if date else "/availabilities"
    problems: list[str] = []

    if isinstance(body, list):
        return [f"{where}: returns a LIST, expected a bare object keyed by datetime"]
    if not isinstance(body, dict):
        return [f"{where}: returns {type(body).__name__}, expected an object"]
    if "data" in body and isinstance(body.get("data"), (list, dict)):
        # A JSON:API envelope here would mean the endpoint moved to the v1
        # dialect — the adapter reads the bare map and would see zero slots.
        return [f"{where}: now returns a JSON:API envelope ('data' key), "
                "expected a bare datetime->sizes map"]

    seen_party_keys: set[str] = set()
    for slot_time, size_map in body.items():
        if not _is_iso_datetime(slot_time):
            problems.append(f"{where}: key {slot_time!r} is not an ISO-8601 datetime")
            continue
        if not isinstance(size_map, dict):
            problems.append(f"{where}: {slot_time} maps to "
                            f"{type(size_map).__name__}, expected an object of "
                            "party-size -> counts")
            continue
        seen_party_keys |= set(map(str, size_map.keys()))
        for size, areas in size_map.items():
            if not isinstance(areas, dict):
                problems.append(f"{where}: {slot_time} size {size!r} maps to "
                                f"{type(areas).__name__}, expected "
                                "{seating-area: count} (empty = full)")
                continue
            for area, count in areas.items():
                if isinstance(count, bool) or not isinstance(count, (int, float)):
                    problems.append(
                        f"{where}: {slot_time} size {size!r} area {area!r} count is "
                        f"{type(count).__name__}, expected a number")

    if seen_party_keys:
        unexpected = sorted(seen_party_keys - EXPECTED_PARTY_KEYS)
        missing = sorted(EXPECTED_PARTY_KEYS - seen_party_keys)
        if unexpected:
            problems.append(
                f"{where}: NEW party-size keys {unexpected} — Libro has always "
                "returned exactly 1-6. MAX_ONLINE_PARTY and the large-party "
                "handoff in libro_private.py may now be wrong.")
        if missing:
            problems.append(
                f"{where}: party-size keys {missing} have DISAPPEARED — the agent "
                "will tell those party sizes we're fully booked.")
    return problems


def validate_services_shape(body, *, date: str = "") -> list[str]:
    """Return drift complaints about one /services payload.

    Expected: JSON:API ``{"data": [{"id", "type": "services",
    "attributes": {"started-at": ISO-8601}}]}``. The booking create references a
    service by id and the server derives the reservation's date/time from it, so
    a missing id or an unparseable ``started-at`` means nobody can be booked.
    """
    where = f"/services (started-on={date})" if date else "/services"
    if not isinstance(body, dict):
        return [f"{where}: returns {type(body).__name__}, expected a JSON:API object"]
    records = body.get("data")
    if not isinstance(records, list):
        return [f"{where}: 'data' is {type(records).__name__}, expected a list"]
    if not records:
        return [f"{where}: returned no service records — the day has bookable "
                "slots but no services to attach a booking to"]

    problems: list[str] = []
    for rec in records[:200]:
        if not isinstance(rec, dict):
            problems.append(f"{where}: a record is {type(rec).__name__}, expected an object")
            break
        if not rec.get("id"):
            problems.append(f"{where}: a service record has no 'id' — nothing to "
                            "reference in a booking create")
            break
        if rec.get("type") not in (None, "services"):
            problems.append(f"{where}: record type is {rec.get('type')!r}, expected 'services'")
            break
        started = (rec.get("attributes") or {}).get("started-at")
        if not _is_iso_datetime(started or ""):
            problems.append(f"{where}: 'started-at' is {started!r}, not an ISO-8601 "
                            "datetime — slot matching cannot work")
            break
    return problems


def service_matches_a_slot(availability, services) -> bool:
    """Does any service start EXACTLY on an availability instant?

    This is the "a service IS a 15-minute seating slot" model, which the whole
    booking path depends on: `create_booking` finds the service whose
    ``started-at`` equals the requested time and references it, because the
    reservation's datetime is server-derived from that relationship.

    If this ever returns False on a day with real availability, creates will
    start failing with 422 code 1006 and the agent will book nobody.
    """
    if not isinstance(availability, dict) or not isinstance(services, dict):
        return False
    slot_instants = {
        i for i in (_parse_instant(k) for k in availability) if i is not None
    }
    if not slot_instants:
        return False
    for rec in services.get("data") or []:
        if not isinstance(rec, dict):
            continue
        started = _parse_instant((rec.get("attributes") or {}).get("started-at", ""))
        if started is not None and started in slot_instants:
            return True
    return False


def has_bookable_cell(availability) -> bool:
    """True if any slot shows a positive count for any party size."""
    if not isinstance(availability, dict):
        return False
    for size_map in availability.values():
        if not isinstance(size_map, dict):
            continue
        for areas in size_map.values():
            if isinstance(areas, dict) and any(
                isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0
                for v in areas.values()
            ):
                return True
    return False


# ---------------------------------------------------------------------------
# The check itself
# ---------------------------------------------------------------------------
class Report:
    def __init__(self) -> None:
        self.checks: list[dict] = []
        self.drift: list[str] = []
        self.auth_failed = False
        self.unreachable: list[str] = []
        self.warnings: list[str] = []

    def add(self, name: str, ok: bool, note: str = "") -> None:
        self.checks.append({"check": name, "ok": ok, "note": note})

    @property
    def exit_code(self) -> int:
        if self.auth_failed:
            return EXIT_AUTH
        if self.unreachable:
            return EXIT_UNREACHABLE
        if self.drift:
            return EXIT_DRIFT
        return EXIT_OK


def _load_env() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    try:
        from dotenv import load_dotenv

        load_dotenv(env_path)
    except ImportError:
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


async def _get(client, path: str, *, params: dict, accept: str):
    """The ONLY request path in this module. GET, by construction.

    Returns ``(status_code, body_or_None, elapsed_seconds)``. Raises whatever
    httpx raises on a transport failure; the caller classifies it.
    """
    started = time.monotonic()
    resp = await client.get(path, params=params, headers={"Accept": accept})
    elapsed = time.monotonic() - started
    try:
        body = resp.json()
    except ValueError:
        body = None
    return resp.status_code, body, elapsed


async def run_health_check(*, offsets=DEFAULT_PROBE_OFFSETS,
                           today: dt.date | None = None) -> Report:
    import httpx

    report = Report()
    token = os.environ.get("LIBRO_PRIVATE_TOKEN", "").strip()
    email = os.environ.get("LIBRO_PRIVATE_EMAIL", "").strip()
    restaurant_id = os.environ.get("LIBRO_PRIVATE_RESTAURANT_ID", "<redacted>").strip()
    base_url = os.environ.get("LIBRO_PRIVATE_BASE_URL",
                              "https://api.libroreserve.com").strip()
    today = today or dt.date.today()

    # The token itself is never echoed — only whether it is present.
    if not token or not email:
        report.add("credentials present", False,
                   "LIBRO_PRIVATE_TOKEN / LIBRO_PRIVATE_EMAIL missing from .env")
        report.unreachable.append("__config__")
        return report
    report.add("credentials present", True, f"restaurant-id={restaurant_id}")

    headers = {"Authorization": f'Token token="{token}", email="{email}"'}
    async with httpx.AsyncClient(base_url=base_url, timeout=HTTP_TIMEOUT_S,
                                 headers=headers) as client:
        good_date = ""          # a date that returned a usable availability map
        good_availability = None
        any_answered = False

        for offset in offsets:
            date = (today + dt.timedelta(days=offset)).isoformat()
            try:
                status, body, elapsed = await _get(
                    client, f"/availabilities/{date}",
                    params={"restaurant-id": restaurant_id}, accept=ACCEPT_V2)
            except Exception as exc:  # noqa: BLE001 - classify, don't crash cron
                report.unreachable.append(f"/availabilities/{date}: "
                                          f"{type(exc).__name__}: {exc}")
                continue

            any_answered = True
            if status in (401, 403):
                report.auth_failed = True
                report.add("auth accepted", False,
                           f"HTTP {status} — the Libro token is no longer valid. "
                           "Log in to dashboard.libroreserve.com and re-mint it "
                           "into .env (LIBRO_PRIVATE_TOKEN).")
                return report
            if status >= 500:
                report.unreachable.append(f"/availabilities/{date}: HTTP {status}")
                continue
            if status != 200:
                report.drift.append(
                    f"/availabilities/{date}: HTTP {status} — the endpoint or its "
                    "Accept header version has changed")
                continue
            if elapsed > SLOW_READ_S:
                report.warnings.append(
                    f"/availabilities/{date} took {elapsed:.1f}s — a caller hears "
                    f"anything over ~{SLOW_READ_S:.0f}s as dead air")

            problems = validate_availability_shape(body, date=date)
            if problems:
                report.drift.extend(problems)
            elif isinstance(body, dict) and body and good_availability is None:
                good_date, good_availability = date, body

        if not any_answered:
            report.add("libro reachable", False,
                       "no sampled date got a response — network, DNS, or Libro down")
            return report
        report.add("libro reachable", True)
        report.add("auth accepted", True, "token still valid")

        if report.drift:
            report.add("availability shape", False, f"{len(report.drift)} problem(s)")
        else:
            report.add("availability shape", True,
                       "datetime -> party-size 1-6 -> {area: count}")

        if good_availability is None:
            # Every sampled date came back empty. Not proof of drift, but the
            # restaurant being closed for three weeks is not plausible either.
            report.drift.append(
                f"every sampled date ({', '.join(str(o) for o in offsets)} days out) "
                "returned an EMPTY availability map — either the restaurant is "
                "closed that far ahead, or the endpoint stopped returning slots")
            report.add("bookable slots visible", False, "no slots on any sampled date")
            return report

        report.add("bookable slots visible", True,
                   f"{len(good_availability)} slots on {good_date}"
                   + ("" if has_bookable_cell(good_availability)
                      else " (all full — shape checked, capacity not)"))

        # ---- services: the other half of a booking --------------------------
        try:
            status, services, elapsed = await _get(
                client, "/services",
                params={"restaurant-id": restaurant_id, "started-on": good_date,
                        "only-services": "true"},
                accept=ACCEPT_V1)
        except Exception as exc:  # noqa: BLE001
            report.unreachable.append(f"/services: {type(exc).__name__}: {exc}")
            return report

        if status in (401, 403):
            report.auth_failed = True
            report.add("auth accepted", False, f"/services returned HTTP {status}")
            return report
        if status != 200:
            report.drift.append(
                f"/services: HTTP {status} — endpoint or Accept version changed "
                "(this endpoint needs the v1 media type)")
            report.add("services shape", False, f"HTTP {status}")
            return report

        problems = validate_services_shape(services, date=good_date)
        report.drift.extend(problems)
        report.add("services shape", not problems,
                   "JSON:API records with started-at" if not problems
                   else f"{len(problems)} problem(s)")

        # ---- the load-bearing assumption ------------------------------------
        if not problems:
            matched = service_matches_a_slot(good_availability, services)
            report.add("service-as-slot model holds", matched,
                       "a service starts exactly on an availability instant"
                       if matched else "NO service start matches any availability "
                       "instant — create_booking cannot resolve a slot and every "
                       "booking will fail with 422 'You must select a date & time'")
            if not matched:
                report.drift.append(
                    f"service-as-slot model broken on {good_date}: no service "
                    "'started-at' equals any availability instant")

    return report


def _print_human(report: Report) -> None:
    print("Libro integration health check")
    print(f"  {dt.datetime.now().astimezone().isoformat(timespec='seconds')}\n")
    for c in report.checks:
        mark = f"{GREEN}[ok]{END}  " if c["ok"] else f"{RED}[FAIL]{END}"
        print(f"  {mark} {c['check']}")
        if c["note"]:
            print(f"         {DIM}{c['note']}{END}")
    for w in report.warnings:
        print(f"  {YELLOW}[warn]{END} {w}")
    if report.unreachable:
        print(f"\n  {RED}Unreachable:{END}")
        for u in report.unreachable:
            print(f"    - {u}")
    if report.drift:
        print(f"\n  {RED}API DRIFT — the adapter's assumptions no longer hold:{END}")
        for d in report.drift:
            print(f"    - {d}")
        print(f"\n  {DIM}Fix in src/resto_agent/reservation/libro_private.py; "
              f"see docs/LIBRO_PRIVATE_INTEGRATION.md.{END}")
    print()
    code = report.exit_code
    if code == EXIT_OK:
        print(f"{GREEN}✓ Healthy.{END} Libro auth works and the API shapes are unchanged.")
    elif code == EXIT_AUTH:
        print(f"{RED}✗ AUTH FAILED — the Libro token is dead. The agent is booking "
              f"nobody right now.{END}")
    elif code == EXIT_UNREACHABLE:
        print(f"{RED}✗ UNREACHABLE — could not talk to Libro.{END}")
    else:
        print(f"{RED}✗ API DRIFT — Libro changed. The adapter needs updating.{END}")


def _summary(report: Report) -> str:
    code = report.exit_code
    return {
        EXIT_OK: "healthy",
        EXIT_DRIFT: "api-drift",
        EXIT_AUTH: "auth-failed",
        EXIT_UNREACHABLE: "unreachable",
        EXIT_CONFIG: "misconfigured",
    }[code]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="READ-ONLY health check for the live Libro integration.")
    ap.add_argument("--quiet", action="store_true",
                    help="print nothing when healthy (cron-friendly)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--days", default=",".join(str(d) for d in DEFAULT_PROBE_OFFSETS),
                    help="comma-separated days-ahead to sample "
                         f"(default: {','.join(str(d) for d in DEFAULT_PROBE_OFFSETS)})")
    args = ap.parse_args(argv)

    try:
        offsets = tuple(int(d) for d in args.days.split(",") if d.strip())
    except ValueError:
        print("--days must be comma-separated integers", file=sys.stderr)
        return EXIT_CONFIG
    if not offsets:
        print("--days must list at least one day", file=sys.stderr)
        return EXIT_CONFIG

    _load_env()
    try:
        report = asyncio.run(run_health_check(offsets=offsets))
    except ImportError:
        print("httpx is not installed — run: pip install -e .", file=sys.stderr)
        return EXIT_CONFIG

    # Missing credentials is a config problem, not an outage.
    if report.unreachable == ["__config__"]:
        report.unreachable = []
        if args.json:
            print(jsonlib.dumps({"status": "misconfigured",
                                 "exit_code": EXIT_CONFIG,
                                 "checks": report.checks}, indent=2))
        else:
            print("LIBRO_PRIVATE_TOKEN / LIBRO_PRIVATE_EMAIL are not set in .env",
                  file=sys.stderr)
        return EXIT_CONFIG

    code = report.exit_code
    if args.json:
        if not (args.quiet and code == EXIT_OK):
            print(jsonlib.dumps({
                "status": _summary(report), "exit_code": code,
                "checks": report.checks, "drift": report.drift,
                "unreachable": report.unreachable, "warnings": report.warnings,
            }, indent=2))
    elif args.quiet:
        if code != EXIT_OK:
            # Cron mails whatever the job prints, so print the actionable part.
            print(f"Libro health check FAILED ({_summary(report)})")
            for item in report.unreachable + report.drift:
                print(f"  - {item}")
            for c in report.checks:
                if not c["ok"] and c["note"]:
                    print(f"  - {c['check']}: {c['note']}")
    else:
        _print_human(report)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
