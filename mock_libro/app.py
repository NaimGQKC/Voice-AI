"""FastAPI application exposing Libro-shaped JSON:API endpoints (table-aware)."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from . import db as dbmod
from . import floorplan
from .db import Database

CONTENT_TYPE = "application/vnd.libro-restricted-v2+json"


# -- JSON:API serializers ---------------------------------------------------

def _table_names(table_ids_csv: str) -> list[str]:
    names = []
    for tid in filter(None, (table_ids_csv or "").split(",")):
        t = floorplan.TABLES_BY_ID.get(tid)
        if t:
            names.append(t.name)
    return names


def _person_resource(row) -> dict:
    return {
        "type": "person",
        "id": row["id"],
        "attributes": {
            "first-name": row["first_name"],
            "last-name": row["last_name"],
            "phone": row["phone"],
            "email": row["email"],
        },
    }


def _booking_resource(row, *, experience=None) -> dict:
    rel = {
        "restaurant": {"data": {"type": "restaurant", "id": row["restaurant_id"]}},
        "person": {"data": {"type": "person", "id": row["person_id"]}},
    }
    if row["experience_id"]:
        rel["experience"] = {"data": {"type": "experience", "id": row["experience_id"]}}
    names = _table_names(row["table_ids"])
    attrs = {
        "size": row["size"],
        "status": row["status"],
        "time": row["time"],
        "duration-min": row["duration_min"],
        "tables": names,
        "arrangement": "merged" if len(names) > 1 else "single",
        "note": row["note"],
        "locale": row["locale"],
        "modification-restricted": bool(row["modification_restricted"]),
    }
    if experience is not None:
        attrs["experience-name"] = experience["name"]
    return {"type": "booking", "id": row["id"], "attributes": attrs, "relationships": rel}


def _restaurant_resource(row) -> dict:
    return {
        "type": "restaurant",
        "id": row["id"],
        "attributes": {
            "name": row["name"],
            "locality": row["locality"],
            "timezone": row["timezone"],
        },
    }


def _ok(data, status: int = 200) -> JSONResponse:
    return JSONResponse({"data": data}, status_code=status, media_type=CONTENT_TYPE)


def _error(code: str, status: int, title: str, detail: str = "") -> JSONResponse:
    return JSONResponse(
        {"errors": [{"code": code, "status": str(status), "title": title,
                     "detail": detail or title}]},
        status_code=status,
        media_type=CONTENT_TYPE,
    )


def _attrs(body: dict) -> dict:
    if isinstance(body, dict) and "data" in body and isinstance(body["data"], dict):
        return body["data"].get("attributes", {}) or {}
    return body or {}


def _party_size_error(size: int) -> JSONResponse | None:
    """Return the right JSON:API error for an invalid party size, else None."""
    if size < dbmod.MIN_PARTY_SIZE:
        return _error("2005", 422, "Party size out of range",
                      f"Party size must be at least {dbmod.MIN_PARTY_SIZE}.")
    if floorplan.requires_staff(size):
        return _error(
            "2006", 422, "Large party requires staff",
            f"Parties over {floorplan.MAX_ONLINE_PARTY} need to be arranged with our team.",
        )
    return None


def create_app(db_path: str = ":memory:", *, seed: bool = True) -> FastAPI:
    db = Database(db_path, seed=seed)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        db.close()

    app = FastAPI(title="Mock Libro Reserve API", version="v2", lifespan=lifespan)
    app.state.db = db

    # -- restaurants -------------------------------------------------------
    @app.get("/restricted/restaurants")
    def list_restaurants():
        return _ok([_restaurant_resource(r) for r in db.list_restaurants()])

    # -- availability (seatings, table-aware) ------------------------------
    @app.get("/restricted/restaurant/seatings")
    def seatings(date: str, size: int = 2, restaurant: str = dbmod.RESTAURANT_ID):
        err = _party_size_error(size)
        if err is not None:
            return err

        slots: list[dict] = []
        for seating in floorplan.candidate_starts(date):
            if floorplan.is_past(seating.time):
                continue
            free = db.free_table_ids(restaurant, seating.time, seating.turn_minutes)
            assignment = floorplan.assign(size, free)
            if assignment is None:
                continue
            slots.append(
                {
                    "time": seating.time,
                    "experience": {"id": seating.experience_id,
                                   "name": seating.experience_name},
                    "payment-required": False,
                    "arrangement": "merged" if assignment.merged else "single",
                    "tables": assignment.table_names,
                    "seats": assignment.seats,
                }
            )
        return _ok({
            "type": "seatings",
            "id": f"{restaurant}:{date}",
            "attributes": {"size": size, "slots": {date: slots}},
        })

    # -- bookings: list / create ------------------------------------------
    @app.get("/restricted/restaurant/bookings")
    def list_bookings(request: Request):
        phone = request.query_params.get("filter[phone]") or \
            request.query_params.get("phone") or ""
        rows = db.list_bookings_for_phone(phone) if phone else []
        return _ok([_booking_resource(r) for r in rows])

    @app.post("/restricted/restaurant/bookings")
    async def create_booking(request: Request):
        attrs = _attrs(await request.json())
        size = int(attrs.get("size", 0) or 0)
        err = _party_size_error(size)
        if err is not None:
            return err

        time = attrs.get("time", "")
        restaurant_id = attrs.get("restaurant-id") or dbmod.RESTAURANT_ID

        seating = floorplan.slot_for_time(time)
        if seating is None or floorplan.is_past(time):
            return _error("2001", 422, "Slot unavailable",
                          "The requested time is not an open seating.")

        free = db.free_table_ids(restaurant_id, time, seating.turn_minutes)
        assignment = floorplan.assign(size, free)
        if assignment is None:
            return _error("2001", 422, "Slot unavailable",
                          "We're fully committed at that time for that party size.")

        person = db.upsert_person(
            first_name=attrs.get("first-name", ""),
            last_name=attrs.get("last-name", ""),
            phone=attrs.get("phone", ""),
            email=attrs.get("email", ""),
        )
        booking = db.insert_booking(
            restaurant_id=restaurant_id,
            person_id=person["id"],
            experience_id=seating.experience_id,
            size=size,
            time=time,
            duration_min=seating.turn_minutes,
            table_ids=assignment.table_ids,
            note=attrs.get("note", ""),
            locale=attrs.get("locale", "en"),
        )
        return _ok(_booking_resource(booking, experience=db.get_experience(seating.experience_id)),
                   status=201)

    @app.get("/restricted/restaurant/bookings/{booking_id}")
    def get_booking(booking_id: str):
        row = db.get_booking(booking_id)
        if not row:
            return _error("404", 404, "Booking not found")
        return _ok(_booking_resource(row, experience=db.get_experience(row["experience_id"])))

    @app.patch("/restricted/restaurant/bookings/{booking_id}")
    async def update_booking(booking_id: str, request: Request):
        row = db.get_booking(booking_id)
        if not row:
            return _error("404", 404, "Booking not found")
        if row["modification_restricted"]:
            return _error("4002", 422, "Modification restricted",
                          "This booking can only be changed by restaurant staff.")
        attrs = _attrs(await request.json())
        fields: dict = {}
        if "size" in attrs:
            size = int(attrs["size"])
            err = _party_size_error(size)
            if err is not None:
                return err
            # Re-seat the (possibly larger/smaller) party at the same time.
            free = db.free_table_ids(
                row["restaurant_id"], row["time"], row["duration_min"], exclude_id=booking_id
            )
            assignment = floorplan.assign(size, free)
            if assignment is None:
                return _error("2001", 422, "Slot unavailable",
                              "We can't fit that party size at the current time.")
            fields["size"] = size
            fields["table_ids"] = assignment.table_ids
        if "note" in attrs:
            fields["note"] = attrs["note"]
        updated = db.update_booking(booking_id, **fields)
        return _ok(_booking_resource(updated))

    @app.put("/restricted/restaurant/bookings/{booking_id}/reschedule")
    async def reschedule_booking(booking_id: str, request: Request):
        row = db.get_booking(booking_id)
        if not row:
            return _error("404", 404, "Booking not found")
        if row["modification_restricted"]:
            return _error("4002", 422, "Modification restricted",
                          "This booking can only be changed by restaurant staff.")
        attrs = _attrs(await request.json())
        new_time = attrs.get("time", "")
        seating = floorplan.slot_for_time(new_time)
        if seating is None or floorplan.is_past(new_time):
            return _error("2001", 422, "Slot unavailable",
                          "The requested time is not an open seating.")
        free = db.free_table_ids(
            row["restaurant_id"], new_time, seating.turn_minutes, exclude_id=booking_id
        )
        assignment = floorplan.assign(row["size"], free)
        if assignment is None:
            return _error("2001", 422, "Slot unavailable",
                          "We're fully committed at that time for that party size.")
        updated = db.update_booking(
            booking_id,
            time=new_time,
            experience_id=seating.experience_id,
            duration_min=seating.turn_minutes,
            table_ids=assignment.table_ids,
        )
        return _ok(_booking_resource(updated))

    @app.delete("/restricted/restaurant/bookings/{booking_id}")
    def cancel_booking(booking_id: str):
        row = db.get_booking(booking_id)
        if not row:
            return _error("404", 404, "Booking not found")
        if row["modification_restricted"]:
            return _error("4001", 422, "Not cancelable",
                          "This booking can only be changed by restaurant staff.")
        if row["status"] != "confirmed" or floorplan.is_past(row["time"]):
            return _error("4001", 422, "Not cancelable",
                          "This booking can no longer be cancelled online.")
        updated = db.update_booking(booking_id, status="cancelled")
        return _ok(_booking_resource(updated))

    # -- people ------------------------------------------------------------
    @app.get("/restricted/people/{person_id}")
    def get_person(person_id: str):
        row = db.get_person(person_id)
        if not row:
            return _error("404", 404, "Person not found")
        return _ok(_person_resource(row))

    @app.patch("/restricted/people/{person_id}")
    async def update_person(person_id: str, request: Request):
        attrs = _attrs(await request.json())
        row = db.update_person(
            person_id,
            first_name=attrs.get("first-name"),
            last_name=attrs.get("last-name"),
            phone=attrs.get("phone"),
            email=attrs.get("email"),
        )
        if not row:
            return _error("404", 404, "Person not found")
        return _ok(_person_resource(row))

    # -- payment intents ---------------------------------------------------
    @app.post("/restricted/payment-intents/initialize")
    async def init_payment_intent(request: Request):
        attrs = _attrs(await request.json())
        booking_id = attrs.get("booking-id", "")
        amount = int(attrs.get("amount", 0) or 0)
        currency = attrs.get("currency", "CAD")
        intent_id = f"pi_{booking_id[-8:] or 'mock'}"
        return _ok(
            {
                "type": "payment-intent",
                "id": intent_id,
                "attributes": {
                    "status": "requires_payment",
                    "amount": amount,
                    "currency": currency,
                    "client-secret": f"{intent_id}_secret_mock",
                    "payment-url": f"https://pay.libro.mock/{intent_id}",
                },
            },
            status=201,
        )

    return app


app = create_app(":memory:", seed=True)
