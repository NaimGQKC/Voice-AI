"""Text-mode demo of the agent's reservation reasoning — no voice, no cloud.

Drives the Concierge against the in-process mock so you can *see* what the agent
would say, including table-merging and large-party escalation. Run:

    python scripts/demo.py
"""

from __future__ import annotations

import asyncio
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from resto_agent.concierge import Concierge  # noqa: E402
from resto_agent.reservation.mock import MockReservationService  # noqa: E402

TZ = "-04:00"


def line(label: str, text: str) -> None:
    print(f"  {label:<8} {text}")


async def scenario(title: str, coro) -> None:
    print(f"\n=== {title} ===")
    print(await coro)


async def main() -> None:
    # A fixed future weekday so output is stable and slots aren't in the past.
    date = (dt.date.today() + dt.timedelta(days=14)).isoformat()
    svc = MockReservationService.in_process(db_path=":memory:")
    c = Concierge(svc)

    print(f"(demo date: {date})")

    line("CALLER", "Do you have a table for two this evening?")
    await scenario("Availability — party of 2 (dinner)",
                   c.check_availability(date=date, party_size=2, part_of_day="dinner"))

    line("CALLER", "Let's do 7. The name's Alex, number 514-555-1234.")
    await scenario("Book — party of 2 (single table)",
                   c.book_reservation(time=f"{date}T19:00:00{TZ}", party_size=2,
                                      first_name="Alex", phone="+15145551234"))

    line("CALLER", "Actually, can you fit a group of eight for dinner?")
    await scenario("Availability — party of 8 (needs a combined table)",
                   c.check_availability(date=date, party_size=8, part_of_day="dinner"))

    await scenario("Book — party of 8 (engine merges two 4-tops)",
                   c.book_reservation(time=f"{date}T19:00:00{TZ}", party_size=8,
                                      first_name="Priya", phone="+15145558000"))

    line("CALLER", "And one more party of eight at the same time?")
    await scenario("Book — second party of 8 at 7 PM (room can't seat it)",
                   c.book_reservation(time=f"{date}T19:00:00{TZ}", party_size=8,
                                      first_name="Sam", phone="+15145558001"))

    # --- the cascade: what happens when we can't give them what they asked for
    line("CALLER", "Do you have 7 o'clock?")
    await scenario("Exact time requested (note: '7' is read as 7 PM, not 7 AM)",
                   c.check_availability(date=date, party_size=2,
                                        part_of_day="dinner", preferred_time="7"))

    from resto_agent.reservation.models import Availability as _A

    async def _full(d, size):
        return _A(date=d, party_size=size, slots=[])

    real_check = c.service.check_availability
    c.service.check_availability = _full
    line("CALLER", "What about Friday at 8?")
    await scenario("Fully booked everywhere -> capture, never a dead-end transfer",
                   c.check_availability(date=date, party_size=2,
                                        part_of_day="dinner", preferred_time="8"))
    print(c.join_waitlist(name="Sam", phone="514-555-0143", party_size=2,
                          preferred_time="8"))
    print(f"[waitlist captures: {len(c.waitlist)}]")
    c.service.check_availability = real_check

    line("CALLER", "We're actually a group of 14 for a birthday.")
    await scenario("Availability — party of 14 (beyond the room -> staff)",
                   c.check_availability(date=date, party_size=14))
    print(c.take_message(name="Jordan", phone="+15145559000",
                         message="Party of 14 for a birthday, wants this Friday evening"))
    print(f"\n[messages captured for staff: {len(c.messages)}]")

    # --- input understanding (the things that break live) -----------------
    import datetime as _dt

    from resto_agent import datetime_resolve as dr
    from resto_agent.phone import normalize_phone

    print("\n=== Input understanding (deterministic) ===")
    ref = _dt.date(2026, 7, 1)  # a Wednesday
    for phrase in ["this Friday", "tomorrow", "next Monday", "July 5", "in 3 days"]:
        print(f"  date  {phrase!r:>14} (today Wed 2026-07-01) -> {dr.resolve_date(phrase, today=ref)}")
    for raw in ["514-555-1234", "(514) 555 7788", "five one four five five five one two one two"]:
        print(f"  phone {raw!r:>48} -> {normalize_phone(raw)}")

    await svc.aclose()


if __name__ == "__main__":
    asyncio.run(main())
