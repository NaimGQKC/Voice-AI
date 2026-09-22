"""Reservation backend abstraction.

The agent's tools depend only on :class:`ReservationService` (see ``base.py``).

Two implementations exist:

* ``LibroPrivateReservationService`` — **the production backend.** Talks to the
  real Libro dashboard API and books real tables.
* ``MockReservationService`` — a local fake, used *only* so the test suite can
  run offline with no API keys. It is never used against real customers.
"""

from .base import ReservationService
from .errors import (
    BookingNotFoundError,
    LargePartyError,
    ModificationRestrictedError,
    NotCancelableError,
    PartySizeOutOfRangeError,
    ReservationError,
    SlotUnavailableError,
)
from .models import Availability, Booking, PaymentIntent, Person, TimeSlot

__all__ = [
    "ReservationService",
    "Availability",
    "Booking",
    "PaymentIntent",
    "Person",
    "TimeSlot",
    "ReservationError",
    "SlotUnavailableError",
    "PartySizeOutOfRangeError",
    "LargePartyError",
    "NotCancelableError",
    "ModificationRestrictedError",
    "BookingNotFoundError",
]


def build_service(settings=None):
    """Construct the configured reservation backend.

    This is the single place where the mock/real swap happens. ``settings`` is a
    :class:`resto_agent.config.Settings`; if omitted it is loaded from the
    environment.
    """

    from ..config import Settings

    settings = settings or Settings.from_env()
    backend = settings.reservation_backend

    if backend in ("mock", "mock-http"):
        from .mock import MockReservationService

        if backend == "mock-http":
            return MockReservationService.http(
                base_url=settings.mock_base_url,
                restaurant_id=settings.restaurant_id,
            )
        return MockReservationService.in_process(
            db_path=settings.mock_db_path,
            restaurant_id=settings.restaurant_id,
        )

    if backend == "libro-private":
        from .libro_private import LibroPrivateReservationService

        return LibroPrivateReservationService(
            token=settings.libro_private_token,
            email=settings.libro_private_email,
            restaurant_id=settings.libro_private_restaurant_id,
            base_url=settings.libro_private_base_url,
        )

    raise ValueError(
        f"Unknown reservation backend: {backend!r} "
        "(expected 'mock', 'mock-http', or 'libro-private')"
    )
