"""A local mock of the Libro reservation JSON:API.

This package represents an *external* service (Libro). It deliberately lives at
the repo root, not under ``resto_agent``, to reinforce that the agent must only
talk to it through the ``ReservationService`` abstraction — never by importing
mock internals.

The mock mirrors the subset of Libro's JSON:API the agent needs:

  GET    /restricted/restaurants
  GET    /restricted/restaurant/seatings           (availability)
  GET    /restricted/restaurant/bookings           (list, filter[phone])
  POST   /restricted/restaurant/bookings           (create)
  GET    /restricted/restaurant/bookings/{id}
  PATCH  /restricted/restaurant/bookings/{id}       (update size/note)
  PUT    /restricted/restaurant/bookings/{id}/reschedule
  DELETE /restricted/restaurant/bookings/{id}       (cancel)
  GET    /restricted/people/{id}
  PATCH  /restricted/people/{id}
  POST   /restricted/payment-intents/initialize

Responses follow JSON:API (``data``/``attributes``/``relationships``) and use
Libro's structured error codes (2001, 2005, 4001, ...).
"""

from .app import create_app

__all__ = ["create_app"]
