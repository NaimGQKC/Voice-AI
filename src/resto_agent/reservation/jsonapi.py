"""A JSON:API ReservationService implemented over httpx.

This holds *all* of the Libro wire-format knowledge: building JSON:API request
envelopes, parsing ``data``/``attributes``/``relationships`` responses, and
turning structured error bodies into typed exceptions. This is what
``MockReservationService`` is built on, so the agent's tools behave identically
against the mock and against real Libro.

Note that ``LibroPrivateReservationService`` does **not** use this client: the
private dashboard API turned out to need per-endpoint ``Accept`` versioning and
its own auth header, so it keeps its wire details in its own file.
"""

from __future__ import annotations

import httpx

from .base import ReservationService
from .errors import error_from_jsonapi
from .models import Availability, Booking, PaymentIntent, Person, TimeSlot

ACCEPT = "application/vnd.libro-restricted-v2+json"


def _slot_label(iso_time: str) -> str:
    try:
        hour = int(iso_time[11:13])
        minute = iso_time[14:16]
    except (ValueError, IndexError):
        return iso_time
    suffix = "AM" if hour < 12 else "PM"
    return f"{hour % 12 or 12}:{minute} {suffix}"


class JsonApiReservationService(ReservationService):
    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        restaurant_id: str,
        accept: str = ACCEPT,
    ):
        self._client = client
        self._restaurant_id = restaurant_id
        self._accept = accept

    # -- low-level request -------------------------------------------------
    async def _auth_headers(self) -> dict[str, str]:
        """Override in subclasses that need bearer tokens."""
        return {}

    async def _request(
        self, method: str, path: str, *, json: dict | None = None,
        params: dict | None = None,
    ) -> dict:
        headers = {"Accept": self._accept}
        if json is not None:
            headers["Content-Type"] = self._accept
        headers.update(await self._auth_headers())
        resp = await self._client.request(
            method, path, json=json, params=params, headers=headers
        )
        body: dict | None
        try:
            body = resp.json()
        except ValueError:
            body = None
        if resp.status_code >= 400:
            raise error_from_jsonapi(resp.status_code, body)
        return body or {}

    @staticmethod
    def _envelope(resource_type: str, attributes: dict) -> dict:
        return {"data": {"type": resource_type, "attributes": attributes}}

    # -- parsers -----------------------------------------------------------
    def _parse_booking(self, resource: dict) -> Booking:
        attrs = resource.get("attributes", {})
        rel = resource.get("relationships", {})

        def _rel_id(name: str) -> str:
            data = (rel.get(name) or {}).get("data") or {}
            return data.get("id", "")

        return Booking(
            id=resource.get("id", ""),
            size=int(attrs.get("size", 0) or 0),
            status=attrs.get("status", ""),
            time=attrs.get("time", ""),
            restaurant_id=_rel_id("restaurant") or self._restaurant_id,
            person_id=_rel_id("person"),
            experience_id=_rel_id("experience"),
            experience_name=attrs.get("experience-name", ""),
            note=attrs.get("note", "") or "",
            locale=attrs.get("locale", "en") or "en",
            modification_restricted=bool(attrs.get("modification-restricted", False)),
            tables=tuple(attrs.get("tables", []) or []),
            arrangement=attrs.get("arrangement", "single"),
            duration_min=int(attrs.get("duration-min", 0) or 0),
        )

    @staticmethod
    def _parse_person(resource: dict) -> Person:
        attrs = resource.get("attributes", {})
        return Person(
            id=resource.get("id", ""),
            first_name=attrs.get("first-name", "") or "",
            last_name=attrs.get("last-name", "") or "",
            phone=attrs.get("phone", "") or "",
            email=attrs.get("email", "") or "",
        )

    # -- ReservationService API -------------------------------------------
    async def check_availability(self, date: str, party_size: int) -> Availability:
        body = await self._request(
            "GET",
            "/restricted/restaurant/seatings",
            params={"date": date, "size": party_size,
                    "restaurant": self._restaurant_id},
        )
        attrs = body.get("data", {}).get("attributes", {})
        raw_slots = (attrs.get("slots") or {}).get(date, [])
        slots = [
            TimeSlot(
                time=s["time"],
                label=_slot_label(s["time"]),
                experience_id=(s.get("experience") or {}).get("id", ""),
                experience_name=(s.get("experience") or {}).get("name", ""),
                payment_required=bool(s.get("payment-required", False)),
                arrangement=s.get("arrangement", "single"),
                tables=tuple(s.get("tables", []) or []),
                seats=int(s.get("seats", 0) or 0),
            )
            for s in raw_slots
        ]
        return Availability(date=date, party_size=party_size, slots=slots)

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
        attributes = {
            "size": party_size,
            "time": time,
            "first-name": first_name,
            "last-name": last_name,
            "phone": phone,
            "email": email,
            "note": note,
            "locale": locale,
            "restaurant-id": self._restaurant_id,
        }
        if experience_id:
            attributes["experience-id"] = experience_id
        body = await self._request(
            "POST", "/restricted/restaurant/bookings",
            json=self._envelope("booking", attributes),
        )
        return self._parse_booking(body["data"])

    async def get_booking(self, booking_id: str) -> Booking:
        body = await self._request(
            "GET", f"/restricted/restaurant/bookings/{booking_id}"
        )
        return self._parse_booking(body["data"])

    async def list_bookings(
        self, *, phone: str = "", person_id: str = ""
    ) -> list[Booking]:
        params = {}
        if phone:
            params["filter[phone]"] = phone
        body = await self._request(
            "GET", "/restricted/restaurant/bookings", params=params
        )
        return [self._parse_booking(r) for r in body.get("data", [])]

    async def update_booking(
        self, booking_id: str, *, party_size: int | None = None,
        note: str | None = None,
    ) -> Booking:
        attributes: dict = {}
        if party_size is not None:
            attributes["size"] = party_size
        if note is not None:
            attributes["note"] = note
        body = await self._request(
            "PATCH", f"/restricted/restaurant/bookings/{booking_id}",
            json=self._envelope("booking", attributes),
        )
        return self._parse_booking(body["data"])

    async def cancel_booking(self, booking_id: str) -> Booking:
        body = await self._request(
            "DELETE", f"/restricted/restaurant/bookings/{booking_id}"
        )
        return self._parse_booking(body["data"])

    async def reschedule_booking(self, booking_id: str, *, new_time: str) -> Booking:
        body = await self._request(
            "PUT", f"/restricted/restaurant/bookings/{booking_id}/reschedule",
            json=self._envelope("booking", {"time": new_time}),
        )
        return self._parse_booking(body["data"])

    async def get_person(self, person_id: str) -> Person:
        body = await self._request("GET", f"/restricted/people/{person_id}")
        return self._parse_person(body["data"])

    async def update_person(
        self, person_id: str, *, first_name: str | None = None,
        last_name: str | None = None, phone: str | None = None,
        email: str | None = None,
    ) -> Person:
        attributes: dict = {}
        if first_name is not None:
            attributes["first-name"] = first_name
        if last_name is not None:
            attributes["last-name"] = last_name
        if phone is not None:
            attributes["phone"] = phone
        if email is not None:
            attributes["email"] = email
        body = await self._request(
            "PATCH", f"/restricted/people/{person_id}",
            json=self._envelope("person", attributes),
        )
        return self._parse_person(body["data"])

    async def init_payment_intent(
        self, *, booking_id: str, amount: int, currency: str = "CAD"
    ) -> PaymentIntent:
        body = await self._request(
            "POST", "/restricted/payment-intents/initialize",
            json=self._envelope(
                "payment-intent",
                {"booking-id": booking_id, "amount": amount, "currency": currency},
            ),
        )
        resource = body["data"]
        attrs = resource.get("attributes", {})
        return PaymentIntent(
            id=resource.get("id", ""),
            status=attrs.get("status", ""),
            amount=int(attrs.get("amount", 0) or 0),
            currency=attrs.get("currency", currency),
            client_secret=attrs.get("client-secret", ""),
            payment_url=attrs.get("payment-url", ""),
        )

    async def aclose(self) -> None:
        await self._client.aclose()
