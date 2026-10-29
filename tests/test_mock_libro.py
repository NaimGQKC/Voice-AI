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


# ---------------------------------------------------------------------------
# The demo reservation book (/demo/book)
# ---------------------------------------------------------------------------
# A recording shows this page next to the terminal, so what it shows has to be
# right, not merely present. The first version converted each booking time into
# the VIEWER'S timezone and showed a 7:00 PM booking as 11:00 PM on any machine
# that was not in Montreal; nothing on the data side could have caught that.


def _book(c: TestClient, *, time: str, size: int = 2, phone: str = "+15145550142"):
    return c.post("/restricted/restaurant/bookings", json={"data": {"attributes": {
        "size": size, "time": time, "first-name": "Alex", "phone": phone}}})


def test_demo_feed_lists_a_booking_made_through_the_api_then_its_cancellation():
    c = client()
    assert c.get("/demo/bookings.json").json() == []

    r = _book(c, time=slot_time(future_date(3), "19:00"))
    assert r.status_code == 201, r.text
    rows = c.get("/demo/bookings.json").json()
    assert [(b["first_name"], b["size"], b["status"]) for b in rows] == [("Alex", 2, "confirmed")]

    bid = r.json()["data"]["id"]
    assert c.delete(f"/restricted/restaurant/bookings/{bid}").status_code in (200, 204)
    rows = c.get("/demo/bookings.json").json()
    # Cancelled, not deleted: the row has to stay so the page can show it turning red.
    assert [b["status"] for b in rows] == ["cancelled"]


def test_demo_feed_keeps_the_restaurants_own_wall_clock_time():
    """The stored offset-qualified time is passed through untouched for the page to
    read as wall-clock. Re-expressing it in UTC here would reintroduce the bug."""
    c = client()
    t = slot_time(future_date(3), "19:00")
    assert _book(c, time=t).status_code == 201
    assert c.get("/demo/bookings.json").json()[0]["time"] == t


def test_demo_page_is_clearly_labelled_mock_and_never_uses_the_viewers_timezone():
    page = client().get("/demo/book").text
    assert "MOCK DATA" in page
    # Times must be read from the string, not via new Date(isoString). Comments are
    # excluded: the page explains this very pitfall in one.
    code = "\n".join(ln for ln in page.splitlines() if not ln.lstrip().startswith("//"))
    assert "new Date(t)" not in code
