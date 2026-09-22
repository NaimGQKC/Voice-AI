# Methodology: safely integrating with Libro's private API

How we connected the voice agent to the venue's real reservation system (LibroReserve,
now OpenTable) without official partner access, and did it safely enough to run
against a production floor.

## The situation

- The venue takes reservations through Libro. The **official partner API** route did
  not respond, so there was no supported integration path.
- Libro's dashboard (`dashboard.libroreserve.com`) is an Ember.js single-page app
  talking to a private JSON:API at `api.libroreserve.com`, authenticated with a
  long-lived **`Authorization: Token`** credential.
- Goal: let the agent check availability and create reservations programmatically,
  matching exactly what a staff member does in the dashboard.

## Principles we held to

1. **Read before write.** Everything was confirmed with read-only `GET`s first.
   No `POST`/`PATCH` touched the live floor until the shapes were understood and a
   single, deliberate test was agreed.
2. **The token is a password.** It's a master credential (read + write on every
   reservation, plus the realtime feed). It lives only in a local, git-ignored
   `.env`; it is never logged, committed, printed, or pasted into chat. Tooling
   that could expose it (the HAR parser) explicitly refuses to read request
   headers.
3. **Customer PII stays local.** Any output meant to be shared is passed through
   a redactor that masks names, phones, emails, and notes — verified against
   synthetic data to prove nothing leaks. Raw captures (`libro_recon_raw.json`,
   `*.har`) are git-ignored and kept on the operator's machine only.
4. **Isolate the wire format.** All Libro-specific detail lives in one adapter
   (`libro_private.py`) behind the `ReservationService` interface, so the agent,
   tools, and conversation logic never change — and a future API change touches
   one file.

## How the spec was established (in order)

1. **Auth probe.** A read-only script confirmed `GET /ping → 200` with the token,
   proving the credential and host were valid.
2. **Path discovery.** The endpoint paths from an initial report turned out to be
   guesses — every one returned an HTML `404`. Rather than keep guessing, we
   switched to observing the app directly.
3. **Live capture.** The real endpoints were read from the dashboard's own
   network traffic (Ember Data adapter calls / a HAR export), giving exact paths,
   query params, and response shapes — not inference.
4. **Modeling.** The confirmed shapes were encoded into the adapter, and the
   non-obvious semantics (the nested availability map, the service/shift
   relationship) were covered by unit tests using captured sample shapes.

## What we confirmed (from live traffic)

| Capability | Call |
|---|---|
| Availability for a day | `GET /availabilities/{YYYY-MM-DD}?restaurant-id=<redacted>` |
| Services (shift capacity) | `GET /services?restaurant-id=<redacted>&started-on={date}` |
| Day notes | `GET /notes?restaurant-id=<redacted>&started-on={date}` |
| Guest search | `GET /people/query?query={text}` |
| Guest create | `POST /people` (JSON:API, type `people`) |
| Create reservation | `POST /bookings` (JSON:API, type `bookings`) |
| Read / update / cancel | `GET/PATCH /bookings/{id}` |

- **Format:** JSON:API (`data / type / id / attributes / relationships`), keys
  dash-cased. Auth `Token` header; `Accept: application/vnd.libro-private-v2+json`.
- **Availability** is a map `time → party-size → { seatingArea: count }`; empty
  `{}` means full. Only party sizes **1–6** appear, so 7+ escalates to staff.
- **A booking** carries its datetime in `time` (matching the availability keys),
  party size in `slots`, and references a `person` and a `service` (shift).

## The one derived piece

The exact **required fields and status enum for `POST /bookings`** on a fresh
create were not fully observable from read traffic. The adapter sends a minimal,
well-formed payload and this is confirmed by **one controlled test booking**:
a fake guest ("ZZ Test"), a far-future off-peak slot, capture the result, then
cancel. Everything else is confirmed.

## Concurrency & correctness

- Availability is checked immediately before creating; a slot can still fill in
  between (walk-in, another caller). The adapter maps the resulting `409/422` to
  a graceful spoken "that just filled up — here are nearby times" rather than
  asserting a seat it doesn't hold.
- Optionally, Libro's realtime **Pusher** channel (`POST /session/pusher/auth`)
  can feed live capacity so the agent reacts mid-call; not required for the MVP.

## Risk register

- **Undocumented, private API.** May change without notice, especially during the
  OpenTable migration. Mitigation: single-file adapter + a test suite that pins
  the shapes.
- **Token custody.** Long-lived and powerful. Store in a secrets manager for
  production; rotate on any suspicion of exposure; prefer a dedicated integration
  login over a personal one if Libro/OpenTable will provide one.
- **Terms of service.** Automating a private API may bump against Libro/OpenTable
  terms; the restaurant owning the account strengthens the position, but it's
  worth a review before going live.

## Reproducibility

Two safe, local tools capture everything above:

- `scripts/probe_libro_private.py` — read-only endpoint checks; redacted,
  shareable console output; full raw kept local.
- `scripts/parse_har.py` — parses a browser HAR export locally; strips the token
  and masks PII; prints the endpoint + schema map.
