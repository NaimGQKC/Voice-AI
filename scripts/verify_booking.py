"""Verify a Libro booking is cancelled — and cancel it if it isn't.

Belt-and-braces check so you never have to open the dashboard:

    python scripts/verify_booking.py            # checks the test booking
    python scripts/verify_booking.py <redacted>  # or any booking id

It reads the booking (read-only). If it is already cancelled it says so and
stops. If it is somehow still active, it cancels it and re-reads to confirm.
Only ever touches the ONE booking id you pass.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

#: The controlled test booking created by scripts/test_booking_libro.py.
DEFAULT_BOOKING_ID = "<redacted>"

BOLD, GREEN, RED, YELLOW, END = "\033[1m", "\033[92m", "\033[91m", "\033[93m", "\033[0m"


def _load_env() -> None:
    for p in (ROOT / ".env", Path.cwd() / ".env", ROOT / ".env.txt", Path.cwd() / ".env.txt"):
        if p.exists():
            for line in p.read_text(encoding="utf-8-sig").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
            return


def _banner(booking) -> None:
    print("\n" + "=" * 56)
    print(f"  BOOKING {booking.id}")
    print(f"    status : {booking.status}")
    print(f"    time   : {booking.time or '(none)'}")
    print(f"    party  : {booking.size}")
    print("=" * 56)


async def main() -> int:
    _load_env()
    ap = argparse.ArgumentParser(description="Verify (and if needed cancel) a booking.")
    ap.add_argument("booking_id", nargs="?", default=DEFAULT_BOOKING_ID)
    args = ap.parse_args()

    token = os.environ.get("LIBRO_PRIVATE_TOKEN", "")
    email = os.environ.get("LIBRO_PRIVATE_EMAIL", "")
    rid = os.environ.get("LIBRO_PRIVATE_RESTAURANT_ID", "<redacted>")
    if not token or not email:
        print("Set LIBRO_PRIVATE_TOKEN and LIBRO_PRIVATE_EMAIL in .env first.")
        return 1

    from resto_agent.reservation.errors import BookingNotFoundError
    from resto_agent.reservation.libro_private import LibroPrivateReservationService

    svc = LibroPrivateReservationService(token=token, email=email, restaurant_id=rid)
    try:
        print(f"Reading booking {args.booking_id} (read-only)...")
        try:
            booking = await svc.get_booking(args.booking_id)
        except BookingNotFoundError:
            print(f"\n{GREEN}{BOLD}✅ BOOKING NOT FOUND — it does not exist on the "
                  f"floor at all. Nothing to worry about.{END}\n")
            return 0

        _banner(booking)
        if booking.is_cancelled:
            print(f"\n{GREEN}{BOLD}✅ CONFIRMED CANCELLED — nothing is booked on "
                  f"the venue's floor.{END}")
            print(f"{GREEN}   No action needed. You can ignore the dashboard.{END}\n")
            return 0

        print(f"\n{YELLOW}{BOLD}⚠️  STILL ACTIVE — cancelling it now...{END}")
        await svc.cancel_booking(args.booking_id)
        print("   re-reading to confirm...")
        booking = await svc.get_booking(args.booking_id)
        _banner(booking)
        if booking.is_cancelled:
            print(f"\n{GREEN}{BOLD}✅ CONFIRMED CANCELLED — it is now off the floor.{END}\n")
            return 0
        print(f"\n{RED}{BOLD}❌ STILL SHOWING '{booking.status}' — please cancel "
              f"booking {booking.id} in the dashboard manually.{END}\n")
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"\n{RED}ERROR: {type(exc).__name__}: {exc}{END}")
        print(f"{RED}Could not verify — check booking {args.booking_id} in the "
              f"dashboard to be safe.{END}\n")
        return 1
    finally:
        await svc.aclose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
