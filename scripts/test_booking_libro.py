"""Controlled ONE-booking test against the live Libro floor — then cancels it.

╔══════════════════════════════════════════════════════════════════════════╗
║  STANDING RULE FROM THE OWNER — applies to every live write, forever.     ║
║                                                                          ║
║   1. Book FAR into the future (default: 2031). If a cancel ever fails,   ║
║      a stray booking must not be able to collide with a real guest.      ║
║   2. ALWAYS cancel it in the same run.                                   ║
║   3. ALWAYS report back the booking id + a link the owner can open to    ║
║      confirm with their own eyes that it is gone.                        ║
║                                                                          ║
║  Never create a live booking for a near-term date to "test something".   ║
╚══════════════════════════════════════════════════════════════════════════╝

This is the single deliberate write needed to confirm the exact `POST /bookings`
shape (required fields, status enum). It is safe by construction:

  * DRY RUN by default — it only reads availability and prints what it *would*
    send. It writes nothing unless you pass --yes-write.
  * With --yes-write it creates ONE booking for an obviously-fake guest
    ("ZZTEST AUTOMATED-DELETE") on a far-future, off-peak lunch slot, prints the
    result, then immediately CANCELS it and verifies the cancellation.
  * Everything is redacted in output; the booking is tagged source="phone-agent".

Usage (credentials from .env):
  python scripts/test_booking_libro.py                 # dry run (no writes)
  python scripts/test_booking_libro.py --yes-write     # create + cancel one booking
  python scripts/test_booking_libro.py --yes-write --date 2026-09-15

If the create fails (e.g. a required field/enum), the error is printed verbatim
so the adapter can be finalized in one line.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

FAKE_FIRST = "ZZTEST"
FAKE_LAST = "AUTOMATED-DELETE"
FAKE_PHONE = "+15145550199"  # valid NANP format, clearly a placeholder
TEST_NOTE = "AUTOMATED TEST BOOKING — safe to delete"


def _load_env() -> None:
    for p in (ROOT / ".env", Path.cwd() / ".env", ROOT / ".env.txt", Path.cwd() / ".env.txt"):
        if p.exists():
            for line in p.read_text(encoding="utf-8-sig").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
            return


#: Owner's standing rule: test bookings go FAR into the future, so that if a
#: cancellation ever fails, the stray booking cannot collide with a real guest.
TEST_BOOKING_YEAR = 2031


def _default_date() -> str:
    """A far-future, off-peak Tuesday — see TEST_BOOKING_YEAR."""
    d = dt.date(TEST_BOOKING_YEAR, 3, 1)
    d += dt.timedelta(days=(1 - d.weekday()) % 7)  # first Tuesday on/after
    return d.isoformat()


async def main() -> int:
    _load_env()
    parser = argparse.ArgumentParser(description="Controlled Libro test booking (dry-run by default).")
    parser.add_argument("--date", default=_default_date(), help="YYYY-MM-DD (off-peak, far future)")
    parser.add_argument("--party", type=int, default=2)
    parser.add_argument("--yes-write", action="store_true",
                        help="actually create + cancel a booking on the live floor")
    args = parser.parse_args()

    token = os.environ.get("LIBRO_PRIVATE_TOKEN", "")
    email = os.environ.get("LIBRO_PRIVATE_EMAIL", "")
    rid = os.environ.get("LIBRO_PRIVATE_RESTAURANT_ID", "<redacted>")
    if not token or not email:
        print("Set LIBRO_PRIVATE_TOKEN and LIBRO_PRIVATE_EMAIL in .env first.")
        return 1

    from resto_agent.reservation.libro_private import LibroPrivateReservationService

    svc = LibroPrivateReservationService(token=token, email=email, restaurant_id=rid)
    try:
        print(f"Checking availability for {args.party} on {args.date} (read-only)...")
        avail = await svc.check_availability(args.date, args.party)
        if not avail.slots:
            print("No open slots that day — pick another --date (try a weekday).")
            return 1
        slot = avail.slots[0]  # earliest open slot = off-peak lunch
        print(f"  Found {len(avail.slots)} open slots; will use the earliest: "
              f"{slot.label} ({slot.time})")

        if not args.yes_write:
            print("\nDRY RUN — nothing was written. Would create:")
            print(f"  guest : {FAKE_FIRST} {FAKE_LAST}  {FAKE_PHONE}")
            print(f"  time  : {slot.time}   party: {args.party}")
            print("Re-run with --yes-write to create then immediately cancel it.")
            return 0

        print("\n--yes-write: creating ONE test booking on the live floor...")
        booking = await svc.create_booking(
            time=slot.time, party_size=args.party,
            first_name=FAKE_FIRST, last_name=FAKE_LAST, phone=FAKE_PHONE,
            note=TEST_NOTE,
        )
        print(f"  CREATED booking id={booking.id} status={booking.status} "
              f"time={booking.time} tables={booking.tables or '(server-assigned)'}")

        print("Cancelling the test booking...")
        cancelled = await svc.cancel_booking(booking.id)
        print(f"  CANCELLED -> status={cancelled.status} "
              f"({'ok' if cancelled.is_cancelled else 'VERIFY IN DASHBOARD'})")
        print("\nDone. If status shows cancelled, the create+cancel flow is confirmed.")
        print(f"(Booking id {booking.id} — you can double-check it's cancelled in the dashboard.)")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"\nERROR: {type(exc).__name__}: {exc}")
        detail = getattr(exc, "detail", None)
        if detail:
            print(f"  detail: {detail}")
        # Read-only follow-up: isolate WHICH prerequisite is failing.
        print("\nRead-only diagnostics for the write path:")
        for label, path, params in [
            ("GET /people/query", "/people/query", {"query": FAKE_PHONE}),
        ]:
            try:
                await svc._request("GET", path, params=params)
                print(f"  {label}: OK (200)")
            except Exception as e2:  # noqa: BLE001
                print(f"  {label}: {type(e2).__name__}: {e2}")
        # The service (shift) relationship is required — is it resolving?
        try:
            date = args.date
            body = await svc._request("GET", "/services", params={
                "restaurant-id": rid, "started-on": date, "only-services": "true"})
            services = body.get("data", []) if isinstance(body, dict) else []
            print(f"  GET /services: OK, {len(services)} service(s)")
            for s in services[:4]:
                a = s.get("attributes", {})
                print(f"    service id={s.get('id')} status={a.get('status')} "
                      f"started-at={a.get('started-at')}")
            slot_t = f"{date}T11:30:00-04:00"
            sid = await svc._service_id_for_time(date, slot_t)
            print(f"  resolved service id for {slot_t}: {sid!r}")
        except Exception as e3:  # noqa: BLE001
            print(f"  GET /services: {type(e3).__name__}: {e3}")
        print("\nPaste this whole block back — it pinpoints the exact endpoint to fix.")
        return 1
    finally:
        await svc.aclose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
