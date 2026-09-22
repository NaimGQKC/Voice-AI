"""Structured reservation errors mapped from Libro's error codes.

Libro returns JSON:API ``errors`` objects with stable numeric ``code`` values.
We translate them into typed exceptions so the agent can respond gracefully and
deterministically rather than parsing prose. Each error carries a
``spoken_message`` suitable for the voice agent to say to a caller.

Code references (from the Libro partner schema mirrored by the mock):
  2001  slot unavailable
  2005  party size out of range
  4001  booking not cancelable
The ``modification-restricted`` booking flag is surfaced as
``ModificationRestrictedError`` (no numeric code; it is an attribute, not an
API error) so changes that require restaurant staff degrade to "take a message".
"""

from __future__ import annotations


class ReservationError(Exception):
    """Base class for all reservation backend failures."""

    #: Libro numeric code, when one applies.
    code: str | None = None
    #: A caller-friendly line the voice agent can speak.
    spoken_message: str = (
        "I'm sorry, something went wrong on our reservation system. "
        "Let me take a message and have the team follow up."
    )

    def __init__(self, message: str | None = None, *, code: str | None = None,
                 detail: str | None = None, spoken_message: str | None = None):
        super().__init__(message or self.spoken_message)
        if code is not None:
            self.code = code
        self.detail = detail
        if spoken_message is not None:
            self.spoken_message = spoken_message


class SlotUnavailableError(ReservationError):
    code = "2001"
    spoken_message = (
        "That time isn't available anymore. I can check the closest open times "
        "if you'd like."
    )


class PartySizeOutOfRangeError(ReservationError):
    code = "2005"
    spoken_message = (
        "I can book tables for parties up to our online limit. For a larger "
        "group I'll take a message so the team can arrange it for you."
    )


class LargePartyError(ReservationError):
    """Party exceeds what the room can seat in any arrangement -> needs staff."""

    code = "2006"
    spoken_message = (
        "For a party that size we arrange the seating with our team directly. "
        "Let me take your name and number and they'll call you right back."
    )


class NotCancelableError(ReservationError):
    code = "4001"
    spoken_message = (
        "I'm not able to cancel that reservation online. Let me take a message "
        "so the restaurant can take care of it."
    )


class ModificationRestrictedError(ReservationError):
    code = None
    spoken_message = (
        "That reservation can only be changed by our team directly. I'll take a "
        "message and they'll follow up with you."
    )


class BookingNotFoundError(ReservationError):
    code = "404"
    spoken_message = (
        "I couldn't find a reservation under that information. Could you confirm "
        "the phone number or name it's under?"
    )


class BackendUnavailableError(ReservationError):
    """The reservation backend could not be reached, or did not answer in time.

    **This class is different in kind from every other error here**, and the
    difference is the whole reason it exists.

    Every other error is a *negative answer* from a backend that is working:
    "that slot is gone", "no such booking", "too many people". The agent can
    safely tell the caller what happened, because it knows what happened.

    This one is **no answer at all** — a DNS failure, a dead socket, a read
    timeout, an HTTP 5xx, or a rejected token. The state of the world is
    genuinely *unknown*. So the only honest responses are:

    * never assert that a booking succeeded,
    * never assert that a booking failed *for a reason the caller can act on*
      ("we're full") — that is a lie that loses a paying guest,
    * capture the caller's details durably and promise a human callback.

    ``Concierge`` catches this specifically and routes to the ``_persist``
    capture path. A captured caller is recoverable; a dropped one is not.
    """

    code = "503"
    spoken_message = (
        "I'm having trouble reaching our reservation system right now. Let me "
        "take your name and number so the team can call you right back."
    )


class BackendAuthError(BackendUnavailableError):
    """The backend rejected our credentials (401/403).

    Subclasses :class:`BackendUnavailableError` on purpose: mid-call the
    degradation is identical (we cannot book, so capture the caller). It is a
    separate class only so ``scripts/healthcheck.py`` can shout "the Libro token
    is dead" rather than "the network is flaky" — those need different fixes,
    and the token is the single most likely thing to expire unattended.
    """

    code = "401"


class BookingOutcomeUnknownError(BackendUnavailableError):
    """A booking *create* was sent and never answered.

    The request may have reached Libro and seated the guest, or it may not have.
    We cannot tell, and there is no idempotency key on this API to ask with.

    Therefore this is **never retried**: re-sending is how you seat one guest at
    two tables on a Saturday night. The caller is captured for a human callback,
    which is recoverable in both directions (confirm it, or make it).
    """

    code = "503-write"
    spoken_message = (
        "Our reservation system stopped responding while I was booking that, so "
        "I can't confirm it went through. Let me take your details and the team "
        "will call you right back to confirm."
    )


# Map Libro numeric codes -> exception classes.
_CODE_REGISTRY: dict[str, type[ReservationError]] = {
    cls.code: cls
    for cls in (
        SlotUnavailableError,
        PartySizeOutOfRangeError,
        LargePartyError,
        NotCancelableError,
        BookingNotFoundError,
    )
    if cls.code is not None
}


def error_from_jsonapi(status_code: int, payload: dict | None) -> ReservationError:
    """Build a typed :class:`ReservationError` from a JSON:API error response."""

    errors = (payload or {}).get("errors") or []
    first = errors[0] if errors else {}
    code = str(first.get("code")) if first.get("code") is not None else None
    detail = first.get("detail") or first.get("title")

    if code and code in _CODE_REGISTRY:
        return _CODE_REGISTRY[code](detail, detail=detail)
    if status_code == 404:
        return BookingNotFoundError(detail, detail=detail)
    return ReservationError(detail or f"Reservation API error (HTTP {status_code})",
                            code=code, detail=detail)
