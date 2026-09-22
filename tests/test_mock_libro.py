"""Verify the mock returns Libro-shaped JSON:API payloads and error codes."""

from __future__ import annotations

from fastapi.testclient import TestClient

from mock_libro.app import CONTENT_TYPE, create_app
from tests.conftest import future_date, slot_time


def client() -> TestClient:
    return TestClient(create_app(":memory:", seed=True))


def test_restaurants_jsonapi_shape():
    r = client().get("/restricted/restaurants")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith(CONTENT_TYPE)
    data = r.json()["data"]
    assert data[0]["type"] == "restaurant"
    assert data[0]["attributes"]["name"] == "Demo Bistro"


def test_seatings_returns_date_keyed_slots():
    date = future_date()
    r = client().get("/restricted/restaurant/seatings", params={"date": date, "size": 2})
    assert r.status_code == 200
    attrs = r.json()["data"]["attributes"]
    slots = attrs["slots"][date]
    assert slots, "expected available slots for a future date"
    slot = slots[0]
    assert {"time", "experience", "payment-required"} <= set(slot)
    assert {"id", "name"} <= set(slot["experience"])


def test_seatings_rejects_zero_party_size_with_2005():
    r = client().get("/restricted/restaurant/seatings", params={"date": future_date(), "size": 0})
    assert r.status_code == 422
    assert r.json()["errors"][0]["code"] == "2005"


def test_seatings_large_party_returns_2006():
    r = client().get("/restricted/restaurant/seatings", params={"date": future_date(), "size": 30})
    assert r.status_code == 422
    assert r.json()["errors"][0]["code"] == "2006"


def test_seatings_slot_exposes_arrangement():
    date = future_date()
    r = client().get("/restricted/restaurant/seatings", params={"date": date, "size": 8})
    slot = r.json()["data"]["attributes"]["slots"][date][0]
    assert slot["arrangement"] in ("single", "merged")
    assert slot["seats"] >= 8


def test_create_booking_returns_201_and_relationships():
    date = future_date()
    body = {
        "data": {
            "type": "booking",
            "attributes": {
                "size": 2,
                "time": slot_time(date, "18:30"),
                "first-name": "Alex",
                "phone": "+15145551234",
            },
        }
    }
    r = client().post("/restricted/restaurant/bookings", json=body)
    assert r.status_code == 201
    data = r.json()["data"]
    assert data["type"] == "booking"
    assert data["attributes"]["status"] == "confirmed"
    assert data["attributes"]["modification-restricted"] is False
    assert data["relationships"]["person"]["data"]["type"] == "person"
    assert data["relationships"]["experience"]["data"]["id"]


def test_create_booking_unavailable_slot_2001():
    body = {
        "data": {
            "type": "booking",
            "attributes": {
                "size": 2,
                "time": slot_time(future_date(), "03:00"),  # not a configured seating
                "first-name": "Alex",
                "phone": "+15145550000",
            },
        }
    }
    r = client().post("/restricted/restaurant/bookings", json=body)
    assert r.status_code == 422
    assert r.json()["errors"][0]["code"] == "2001"


def test_cancel_then_recancel_is_4001():
    c = client()
    date = future_date()
    created = c.post(
        "/restricted/restaurant/bookings",
        json={"data": {"type": "booking", "attributes": {
            "size": 2, "time": slot_time(date, "19:00"),
            "first-name": "Sam", "phone": "+15145559999"}}},
    ).json()["data"]
    bid = created["id"]

    first = c.delete(f"/restricted/restaurant/bookings/{bid}")
    assert first.status_code == 200
    assert first.json()["data"]["attributes"]["status"] == "cancelled"

    second = c.delete(f"/restricted/restaurant/bookings/{bid}")
    assert second.status_code == 422
    assert second.json()["errors"][0]["code"] == "4001"


def test_unknown_booking_404():
    r = client().get("/restricted/restaurant/bookings/booking_does_not_exist")
    assert r.status_code == 404
    assert r.json()["errors"][0]["code"] == "404"


def test_payment_intent_initialize():
    r = client().post(
        "/restricted/payment-intents/initialize",
        json={"data": {"type": "payment-intent", "attributes": {
            "booking-id": "booking_abc12345", "amount": 5000, "currency": "CAD"}}},
    )
    assert r.status_code == 201
    attrs = r.json()["data"]["attributes"]
    assert attrs["amount"] == 5000
    assert attrs["payment-url"].startswith("https://")
