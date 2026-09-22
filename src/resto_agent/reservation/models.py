"""Provider-agnostic domain models.

These are what the agent's tools see. They are deliberately decoupled from
Libro's JSON:API wire format so the tool layer never imports HTTP/Libro
specifics. The JSON:API client (``jsonapi.py``) is responsible for translating
between these models and the wire format.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class TimeSlot:
    """A single bookable seating returned by the availability lookup.

    ``arrangement`` and ``tables`` reflect how the room would seat this party at
    this time — a single table or a combined ("merged") one — so the agent can
    explain it to the caller.
    """

    time: str  # ISO-8601 datetime, e.g. "2026-06-28T18:00:00-04:00"
    label: str  # human label, e.g. "6:00 PM"
    experience_id: str
    experience_name: str
    payment_required: bool = False
    arrangement: str = "single"  # "single" | "merged"
    tables: tuple[str, ...] = ()
    seats: int = 0

    @property
    def is_merged(self) -> bool:
        return self.arrangement == "merged"


@dataclass(frozen=True)
class Availability:
    date: str  # "YYYY-MM-DD"
    party_size: int
    slots: list[TimeSlot] = field(default_factory=list)

    @property
    def is_available(self) -> bool:
        return bool(self.slots)


@dataclass(frozen=True)
class Person:
    id: str
    first_name: str = ""
    last_name: str = ""
    phone: str = ""
    email: str = ""

    @property
    def full_name(self) -> str:
        return " ".join(p for p in (self.first_name, self.last_name) if p).strip()


@dataclass(frozen=True)
class Booking:
    id: str
    size: int
    status: str  # e.g. "confirmed", "cancelled", "seated", "no_show"
    time: str  # ISO-8601 datetime
    restaurant_id: str
    person_id: str
    experience_id: str = ""
    experience_name: str = ""
    note: str = ""
    locale: str = "en"
    modification_restricted: bool = False
    tables: tuple[str, ...] = ()
    arrangement: str = "single"  # "single" | "merged"
    duration_min: int = 0

    @property
    def is_cancelled(self) -> bool:
        return self.status.lower() in ("cancelled", "canceled")

    @property
    def is_merged(self) -> bool:
        return self.arrangement == "merged" or len(self.tables) > 1


@dataclass(frozen=True)
class PaymentIntent:
    id: str
    status: str
    amount: int = 0  # minor units (cents)
    currency: str = "CAD"
    client_secret: str = ""
    payment_url: str = ""
