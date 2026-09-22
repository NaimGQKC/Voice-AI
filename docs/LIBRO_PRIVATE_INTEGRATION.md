# Connecting the agent to the real venue reservations (Libro private API)

the venue's reservations run on **LibroReserve** (now part of OpenTable). The official
partner API route did not respond, so the live path is the **private dashboard
API** — the same REST/JSON API that `dashboard.libroreserve.com` uses, based on a
network analysis of the live session (July 2026).

Because every backend sits behind the `ReservationService` interface, supporting
this is **one adapter file** — `src/resto_agent/reservation/libro_private.py` — and
a config switch. The agent, tools, and concierge are unchanged.

## The dialect (vs. the mock)

| | Mock / partner dialect | Private dashboard dialect |
|---|---|---|
| Base URL | `api.staging.libro.app` | `https://api.libroreserve.com` |
| Auth | OAuth bearer | `Authorization: Token token="…", email="…"` (static) |
| Accept | `…libro-restricted-v2+json` | **per-endpoint**: `…libro-private-v1+json` (JSON:API) / `…libro-private-v2+json` (/availabilities); writes send `Content-Type: application/vnd.api+json` |
| Party size | `size` | `slots` |
| Availability | `GET /restricted/…/seatings` | `GET /availabilities/{YYYY-MM-DD}?restaurant-id=<redacted>` |
| Reservation | `booking` | `POST /bookings` (JSON:API; `time` + `slots`; person+service rels) |
| Guest | `person` | `person` (`GET /people/query` to search; `POST /people` type `people`) |
| Restaurant | rest id in path | `restaurant-id=<redacted>` query param |

Endpoints (confirmed from a live dashboard HAR, 24 Jul 2026):
`GET /availabilities/{date}?restaurant-id=<redacted>`,
`GET /services?restaurant-id=<redacted>&started-on={date}&only-services=true`,
`GET /people/query?query=`, `GET/POST /people`,
`POST /bookings`, `GET/PATCH /bookings/{id}`.

**The key gotcha is the per-endpoint API version in the `Accept` header**
(confirmed from the dashboard's own request headers): `/availabilities/*` needs
`application/vnd.libro-private-v2+json`, while every JSON:API endpoint
(`/people`, `/services`, `/notes`, `/bookings`, ...) needs the **v1** media type.
Sending v2 to a v1 endpoint (or vice-versa) returns a 404. Writes additionally
send `Content-Type: application/vnd.api+json`.

### The key insight: a **service IS a 15-minute seating slot**

Not a shift. `GET /services` returns ~39 records for a day, one per quarter hour.
**The reservation's date/time is derived from the referenced service** — the
create never sends a `time` attribute (it's server-derived and read-only).
Referencing the wrong service returns `422 code 1006 "You must select a date &
time"`.

So: match the service whose `started-at` **exactly equals** the desired slot
instant (compare in UTC). Do **not** filter by `status` — a captured live booking
used a service marked `"closed"` (staff can book outside online hours).

**Confirmed `POST /bookings` body** (mirrors the dashboard field-for-field):

```json
{ "data": { "type": "bookings",
  "attributes": { "slots": 2, "status": "approved",
                  "booking-type": "reservation",
                  "expected-leave-at": "2031-07-31T21:15:00.000Z",
                  "quoted-wait-time": 900, "children": false,
                  "reduced-mobility": false, "do-not-move": false,
                  "tags": [], "answers": [], "note": "", "private-note": "" },
  "relationships": {
    "service":    { "data": { "type": "services",    "id": "<slot service id>" } },
    "person":     { "data": { "type": "people",      "id": "<personId>" } },
    "restaurant": { "data": { "type": "restaurants", "id": "<redacted>" } } } } }
```

`expected-leave-at` = slot + **90 min** (the server's turn length, confirmed by
comparing a created booking's `time` to its `expected-leave-at`).

Cancel/update is `PATCH /bookings/{id}`: the dashboard resends the record's full
writable attribute set, so the adapter reads the booking first and echoes it back
with only the changed fields merged. Rescheduling means **swapping the service**
(since the service is the slot), not editing a time.

**Availability shape** — a bare map `time → party-size → { seatingArea: count }`:

```json
{ "2026-07-24T19:30:00-04:00": { "2": {"": 14}, "4": {"": 5}, "6": {} } }
```

Empty `{}` = full for that party size at that time; a positive count = seatable.
The endpoint only exposes party sizes **1–6**, so 7+ is treated as a large party
(escalate to staff). A booking references the covering **service** (shift), which
the adapter resolves from `GET /services` by the slot's time window.

## Security (read this)

The token is **long-lived and powerful** — it can read and write every
reservation on the account (it also authenticates the Pusher live feed). Treat it
like a password:

- It lives **only** in your local `.env` (git-ignored). Never commit it, never
  log it, never paste it into chat or a PR.
- If it may have leaked, rotate it (re-log in to the dashboard to mint a new one).
- The adapter loads it from `LIBRO_PRIVATE_TOKEN` and never prints it.

This is an **undocumented internal API**: it can change without notice (the
OpenTable migration makes that more likely). The adapter isolates every
wire-format detail so a change touches one file.

## Status: confirmed end-to-end ✅

On 24 Jul 2026 the adapter completed a full **create → cancel** cycle against the
live floor (`scripts/test_booking_libro.py --yes-write`):

```
Found 28 open slots; using 11:30 AM (2026-09-08T11:30:00-04:00)
CREATED   booking id=<redacted> status=approved time=2026-09-08T15:30:00Z
CANCELLED status=cancelled
```

The created `time` (15:30Z) is exactly the requested slot (11:30 EDT), proving
the service-as-slot model below. Availability, guest lookup/create, slot
resolution, booking creation and cancellation are all verified against
production.

**Step 1 — read-only probe (safe, confirms the reads live):**

```bash
# credentials go in .env, never on the command line
python scripts/probe_libro_private.py
```

Hits only the confirmed read endpoints (`/availabilities/{date}`, `/services`,
`/notes`, and `/people/query` with `--query`). Creates nothing. The output is
redacted (PII masked) and safe to share.

**Step 2 — one controlled test booking (writes to the floor, so deliberate):**

Create a single booking for an obviously-fake guest ("ZZ Test") on a far-future,
off-peak slot while watching the network tab, to capture the exact `POST
/bookings` body — then cancel it. That confirms the create/cancel mapping. (Can
also be done end-to-end through the agent once availability is confirmed.)

## Turning it on

In `.env`:

```dotenv
AGENT_RESERVATION_BACKEND=libro-private
LIBRO_PRIVATE_TOKEN=<the token — treat like a password>
LIBRO_PRIVATE_EMAIL=<your Libro login email>
LIBRO_PRIVATE_RESTAURANT_ID=<redacted>
```

Then run the agent exactly as before (`python agent.py console`). Every booking
now lands on the venue's real Libro floor.

## Notes

- **Concurrency / double-booking:** between the availability check and the create
  call, a walk-in or another caller can take the last table. The adapter maps a
  422 to a graceful "that just filled up" so the agent offers another time rather
  than asserting a seat it doesn't have.
- **Live availability (later):** the dashboard gets real-time updates via a
  Pusher websocket (`POST /session/pusher/auth`). For a phone flow the
  check-then-book pull is sufficient; a Pusher subscription would let the agent
  react to a slot filling mid-call. Not needed for the MVP.
- **Deposits:** handled by Moneris in this dialect; the adapter currently declines
  deposit setup gracefully (not needed for the phone MVP).
