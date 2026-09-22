"""The ReservationService abstraction the agent depends on.

Tools call only these methods; they never touch HTTP or Libro specifics. This
is what makes the mock -> real-Libro swap a one-line change.
"""

from __future__ import annotations

import abc

from .models import Availability, Booking, PaymentIntent, Person


class ReservationService(abc.ABC):
    """Async interface to a restaurant reservation backend."""

    @abc.abstractmethod
    async def check_availability(self, date: str, party_size: int) -> Availability:
        """Return open seatings for ``date`` (YYYY-MM-DD) and ``party_size``."""

    @abc.abstractmethod
    async def create_booking(
        self,
        *,
        time: str,
        party_size: int,
        first_name: str,
        last_name: str = "",
        phone: str = "",
        email: str = "",
        note: str = "",
        locale: str = "en",
        experience_id: str = "",
    ) -> Booking:
        """Create a reservation, creating/linking the guest as needed."""

    @abc.abstractmethod
    async def get_booking(self, booking_id: str) -> Booking:
        ...

    @abc.abstractmethod
    async def list_bookings(
        self, *, phone: str = "", person_id: str = ""
    ) -> list[Booking]:
        """Look up a guest's bookings by phone number or person id."""

    @abc.abstractmethod
    async def update_booking(
        self,
        booking_id: str,
        *,
        party_size: int | None = None,
        note: str | None = None,
    ) -> Booking:
        ...

    @abc.abstractmethod
    async def cancel_booking(self, booking_id: str) -> Booking:
        ...

    @abc.abstractmethod
    async def reschedule_booking(self, booking_id: str, *, new_time: str) -> Booking:
        ...

    @abc.abstractmethod
    async def get_person(self, person_id: str) -> Person:
        ...

    @abc.abstractmethod
    async def update_person(
        self,
        person_id: str,
        *,
        first_name: str | None = None,
        last_name: str | None = None,
        phone: str | None = None,
        email: str | None = None,
    ) -> Person:
        ...

    @abc.abstractmethod
    async def init_payment_intent(
        self, *, booking_id: str, amount: int, currency: str = "CAD"
    ) -> PaymentIntent:
        ...

    async def aclose(self) -> None:
        """Release resources (HTTP clients, DB connections). Override as needed."""
        return None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()
