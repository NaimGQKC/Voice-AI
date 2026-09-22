# YEN Cuisine Japonaise — bilingual voice AI phone agent

A low-cost voice agent that answers the phone for restaurant (I'm not naming my client) and handles reservations — check
availability, book, look up, reschedule, cancel, answer FAQs, take messages and
takeout callbacks. It **greets in French** and follows the caller into English
if that's what they speak, switching mid-call if they do.

It books into the restaurant's **real Libro account**, which they already own.
The restaurant keeps the prompts, the logic, and the call records — that is the
point of the project, not a side effect.

**Status.** It books real tables on YEN's floor in text mode (verified, then
cancelled), and it holds a spoken conversation and calls the right tools with the
right arguments. Those two halves **have not yet been joined** — nobody has
spoken to it while it wrote to real Libro — and it is **not deployed**: no phone
number points at it. See [Phased plan](#phased-plan).

> **For the developer:** hand the owner
> [`docs/YEN_owner_questions.xlsx`](docs/YEN_owner_questions.xlsx) — it collects the
> remaining policy questions. Hours are confirmed from the venue's live Libro
> configuration; the rest still runs on realistic placeholders.

Built on [LiveKit Agents](https://docs.livekit.io/agents/). A **mock Libro API**
ships alongside, so the whole thing runs end-to-end with no Libro credentials and
no phone number — that is what the test suite and `console` mode use.

## The one idea that makes this cheap and safe

Every reservation tool depends only on the `ReservationService` abstraction
(`src/yen_agent/reservation/base.py`). Two interchangeable implementations:

| Implementation | Backend | When |
|---|---|---|
| `MockReservationService` | local FastAPI + SQLite (`mock_libro/`) | POC, demos, tests |
| `LibroPrivateReservationService` | **real YEN reservations** via the Libro dashboard API (token auth) | production ([guide](docs/LIBRO_PRIVATE_INTEGRATION.md)) |

(A third implementation against Libro's official **partner** OAuth API was
deleted — that route never responded to us, and a stub nobody can exercise is
just something for the next session to trip over.)

**Swapping mock → real is a config change** (`YEN_RESERVATION_BACKEND=libro-private`).
The agent and its tools never import HTTP or Libro specifics — each backend keeps
its own wire-format details in one file.

```
caller ─▶ LiveKit AgentSession (STT → LLM → TTS)
              │
              ▼
        ReservationAgent  (tools.py, @function_tool)
              │  delegates to
              ▼
          Concierge   (concierge.py — spoken responses, error handling)
              │  depends only on
              ▼
       ReservationService  (ABC)
          ├── MockReservationService        ─▶ mock_libro (FastAPI + SQLite)
          └── LibroPrivateReservationService ─▶ 
```

## Quick start

### 1. Run the test suite ($0, no keys, ~0.2s)

The reservation layer, the Libro-shaped mock, and the concierge logic are fully
covered without any cloud services or the heavy agent runtime:

```bash
pip install -e ".[dev]"
pytest -q          # 231 tests: mock JSON:API, service round-trips, dates, phones, concierge, resilience
```

### 2. Run the mock Libro server (optional — tests use it in-process)

```bash
python -m mock_libro --port 8000       # serves the Libro-shaped JSON:API
curl "http://localhost:8000/restricted/restaurant/seatings?date=2026-07-20&size=2"
```

### 3. See the reasoning without any keys (text demo)

```bash
python scripts/demo.py
```

This drives the agent's brain through a real script, printing exactly what it
would say.

> ⚠️ The demo runs against `mock_libro/floorplan.py`, an **invented** floor plan.
> Table combining and "the room is full" are properties of that mock, **not of
> YEN's real dining room** — real Libro does its own seating and the live adapter
> never returns a merged table. Don't cite the demo as evidence about the venue.

### 4. Run the actual voice agent

See **[Run it for real](#run-it-for-real-with-your-own-keys)** below for the
full key-by-key setup. The short version:

```bash
pip install -e ".[agent]"              # installs livekit-agents + plugins
cp .env.example .env                   # fill in LiveKit + Deepgram + LLM keys
python agent.py console                # talk to it in your terminal, no server/phone needed
```

The agent defaults to the in-process mock reservation backend, so it works the
moment your STT/LLM/TTS keys are set — no Libro access required.

## Run it for real (with your own keys)

To actually **hear and talk to it**, you need three free accounts. None require
a phone number for the first test; all have free tiers.

| # | Account | Free tier | What you copy into `.env` |
|---|---|---|---|
| 1 | [LiveKit Cloud](https://cloud.livekit.io) | Build tier, no card | `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` |
| 2 | [Deepgram](https://console.deepgram.com) | $200 credit, no card | `DEEPGRAM_API_KEY` (STT; also the fallback voice in `console` mode) |
| 3 | [Google AI Studio](https://aistudio.google.com/apikey) (Gemini) | free tier | `GOOGLE_API_KEY` |

(Prefer OpenAI for the LLM? set `YEN_LLM_PROVIDER=openai` and `OPENAI_API_KEY` instead.)

**Step by step:**

1. `pip install -e ".[agent]"` — installs `livekit-agents` and the Deepgram /
   Google plugins.
2. `cp .env.example .env`, then paste in the three keys above. The file has a
   clearly marked **REQUIRED** block at the top — 5 values, each annotated with
   the exact page to copy it from.
3. **Check your setup before starting:**
   ```bash
   python scripts/check_setup.py
   ```
   This validates the `.env`, the installed packages, and makes live test calls
   to Deepgram / Google / LiveKit to confirm each key actually works — with a
   fix hint for anything wrong. Green means go.
4. **Talk to it locally — no phone, no server:**
   ```bash
   python agent.py console
   ```
   Speak into your mic; you'll hear the agent answer. Try: *"Do you have a table
   for two on Saturday evening?"* → *"Make it a party of eight"* → *"Actually
   we're fourteen."*
5. **Test in the browser** (shows the agent as it would behave deployed):
   ```bash
   python agent.py dev
   ```
   Then open your project's **Agent Console** in the LiveKit Cloud dashboard and
   click connect. This uses free WebRTC minutes, not telephony.
6. **Add a real phone number (Phase 2, optional):** buy a Twilio Canadian local
   number, create an Elastic SIP trunk, and point an inbound LiveKit SIP trunk +
   dispatch rule at the agent. No agent code changes — a phone caller is just
   another participant. (Costs ~$1/mo for the number + per-minute usage.)

**The French voice needs LiveKit credentials.** It routes through LiveKit
Inference (`cartesia/sonic-3`, `language="fr"`), which bills on the LiveKit keys
above — there is deliberately **no separate Cartesia account or key**. Without
LiveKit credentials the agent falls back to Deepgram's English voice and logs a
loud warning, because French read by an English voice is something you want to
find in a log line rather than on a live call.

> **Model-name note:** the plugin model ids in `src/yen_agent/agent.py` are the
> recommended stack; if a plugin version rejects one, check the provider's
> current model list and adjust that one line. Model ids are checked against the
> plugin's own type literals — we shipped an invented one (`cartesia/sonic-3:fr`)
> once, and it would have crashed a live call.

## The stack

French-first, each layer swappable via `.env`. Reasoning in
[`docs/ARCHITECTURE_DECISIONS.md`](docs/ARCHITECTURE_DECISIONS.md).

| Layer | Choice | Notes |
|---|---|---|
| Framework | LiveKit Agents (Apache-2.0) | self-host worker; warm, so no cold start on an inbound call |
| STT | Deepgram Nova-3, `language="multi"` | FR/EN code-switching mid-sentence |
| LLM | Google Gemini 2.5 Flash | swappable — 10 providers wired in `agent.py` |
| TTS | Cartesia `sonic-3` (`fr`) via LiveKit Inference | no separate account or key |
| Telephony | Twilio Canadian local number → LiveKit SIP | LiveKit's own numbers are US-only |
| Hosting | Fly.io `yyz` (Toronto) | Law 25 residency + latency to Montreal |
| Call log | SQLite (WAL) local, Supabase `ca-central-1` in production | dashboard reads it live without blocking a call |

### Language

`YEN_LANGUAGE_MODE=multi` (the default) gives Nova-3 Multilingual STT and the
French locale. The greeting is **always French** — *"Bonjour. YEN Cuisine
Japonaise."* — because two-thirds of this venue's calls are in French. From the
caller's first words the agent follows whichever language they use, and can
switch mid-call.

**What auto-detection actually covers.** Deepgram Nova-3's real-time
code-switching supports exactly **10 languages**: English, Spanish, French,
German, Hindi, Russian, Portuguese, Japanese, Italian, Dutch. So:

| Language | Auto-detect + switch? |
|---|---|
| English | ✅ |
| French (incl. Québécois) | ✅ — Québec accent/idiom may cost some accuracy vs. Metropolitan French; worth testing with real callers |
| Spanish, German, Italian, Portuguese, … | ✅ (in the 10) |
| **Chinese (Mandarin/Cantonese)** | ❌ **not** in the code-switching set |

Chinese would need a different approach — either pinning STT to Chinese for a
dedicated line (losing auto-detect), or a different STT provider. Don't promise
trilingual EN/FR/ZH auto-switching on this stack without testing that path first.

## How the agent reasons about tables

The hard part of restaurant reservations isn't the calendar — it's the **room**.
All of that lives in `mock_libro/floorplan.py` as a deterministic engine (an LLM
should never do table math), and the agent reasons by *calling* it:

- **Floor plan:** YEN is modeled as an intimate room — a few 2-tops and 4-tops,
  one 6-top, and a sushi counter (placeholder inventory; confirm with the
  restaurant). Tables belong to **combinable groups** that can be pushed together.
- **Least-waste assignment:** for each request the engine picks the smallest
  single table that fits; if none fits, it **merges** the smallest set of tables
  in one group. A party of 8 becomes two combined 4-tops; the agent says so.
- **Turn time:** a booking holds its table(s) for the full sitting (lunch 75 min,
  dinner 105 min), so a 7 PM booking blocks *overlapping* times — not just the
  exact slot. A time can be open for two and full for eight.
- **Large-party escalation:** parties beyond what any arrangement can seat
  (currently 8) return a "needs staff" result; the agent stops trying to book and
  takes a message instead.
- **Hours & closed days:** lunch is offered Mon–Sat, dinner daily, each only up to
  a last-seating time — encoded as services, so the agent never offers a slot when
  the kitchen is closed.

`python scripts/demo.py` walks through all of these out loud. The same logic is
covered by `tests/test_floorplan.py` and `tests/test_reservation_service.py`.

## Robustness for real calls

The things that break voice agents in practice are handled deterministically
(not left to the model), following patterns from LiveKit's reference agents:

- **Dates** — callers say "this Friday", "tomorrow", "July 5". `datetime_resolve`
  turns those into real dates relative to today (in Montreal time), rolls
  past dates forward, and refuses dates in the past or beyond the booking
  horizon. Today's date is also injected into the prompt.
- **Phone numbers** — "(514) 555-1234", "514.555.1234", or spelled-out digits all
  normalize to one E.164 form, so a number given at booking matches at
  lookup/cancel. Invalid numbers are re-prompted, not silently accepted.
- **Remembered call state** — name, phone, and party size collected once persist
  for the rest of the call (LiveKit's `UserData` pattern), so the agent can
  cancel "the reservation under my number" without asking again.
- **Runtime** — bundled VAD, semantic turn detection + preemptive generation to
  cut latency, Krisp telephony noise cancellation (on LiveKit Cloud), and
  per-turn metrics + a usage summary per call.

## Phased plan

- **Phase 1 (this repo):** free, web-tested agent against the mock. ✅
- **Phase 2 — real reservations:** ✅ **in text mode.** Booking `11189765` was
  created on `api.libroreserve.com` via `scripts/test_booking_libro.py`, dated
  far into the future, then cancelled and verified. **Nobody has yet *spoken* to
  the agent while it wrote to real Libro** — that is `docs/QA_SCRIPT.md` §F and
  it has not been run.
- **Phase 3 — telephony and deployment:** buy a Twilio CA number, bridge via
  LiveKit SIP, deploy to Fly. **Not done.** Blocked on accounts, and on enabling
  billing for Gemini — the free tier rate-limited the first live test.

  To point at the real backend, set `YEN_RESERVATION_BACKEND=libro-private`
  with the YEN Libro token. First run the **read-only probe** to confirm the live
  API shapes, then a single controlled test booking. Full walkthrough:
  [`docs/LIBRO_PRIVATE_INTEGRATION.md`](docs/LIBRO_PRIVATE_INTEGRATION.md).

  ```bash
  python scripts/probe_libro_private.py --date 2026-08-15 --party 2   # safe, read-only
  ```

  Production goes through the dashboard API adapter. Libro's official **partner**
  API was never an option — that route didn't respond to us.

## Cost

**~$20 CAD/month**, against the incumbent's ~$275 CAD/month. Line items live in
`scripts/build_client_report.py`, which generates the PDF the owner was quoted
from — **that script is the source of truth**, not the prose here.

The figure rests on this venue's *measured* volume: **102 calls and 111
talk-minutes per month**, summed from an 84-call log. Most of the total is fixed
cost (hosting ~$9.80, phone number ~$1.15 + minutes), so it barely moves as calls
increase. Earlier versions of this file estimated 1,500–2,000 min/month and
$80–130 — that was an order of magnitude off the real venue.

Two things the number does not include: **maintenance**, currently unpriced
because it's being done for free, and a **$160 CAD one-time** tooling cost
already incurred. Deeper analysis in [`docs/COST.md`](docs/COST.md) (method and
volume) and [`docs/STACK_DECISION.md`](docs/STACK_DECISION.md) (rates verified
against primary sources).

## Project layout

```
mock_libro/            # the "external" Libro service: FastAPI + SQLite JSON:API mock
  app.py               #   endpoints + JSON:API serializers + error codes
  db.py                #   SQLite store, Yen seed data, table occupancy queries
  floorplan.py         #   tables, combinable groups, turn times, hours, assignment engine
src/yen_agent/
  reservation/         # the swap point
    base.py            #   ReservationService ABC  ← tools depend only on this
    models.py          #   provider-agnostic domain models
    errors.py          #   Libro error-code → typed exception mapping
    jsonapi.py         #   shared httpx JSON:API client
    mock.py            #   MockReservationService (in-process or http)
    libro_private.py   #   LibroPrivateReservationService — REAL YEN (token auth)
  concierge.py         # reservation orchestration + spoken responses (no LiveKit)
  datetime_resolve.py  # deterministic natural-language date parsing
  phone.py             # phone-number normalization to E.164
  tools.py             # LiveKit @function_tool wrappers
  agent.py             # AgentSession wiring + entrypoint (prewarm, metrics)
  prompts.py / faq.py  # system prompt + Yen FAQ knowledge base
  store.py             # durable call log: calls, messages, tool traces, outcomes
  dashboard.py         # read-only FastAPI HTML view over that log
  notify.py            # staff alerts (SMS / email) for messages and failures
  config.py            # env-driven settings
scripts/               # demo, setup check, call log CLI, Libro probes, client report
tests/                 # 231 tests, run with no cloud services and no API keys
docs/YEN_owner_questions.xlsx  # questions for the restaurant owner (hand this off)
```

## Disclosure

The agent tells callers it's an AI assistant and always offers a path to a human
or to leave a message — good practice, and required in some jurisdictions.

> The hours/address/menu in `src/yen_agent/faq.py` are drawn from public listings
> (the restaurant's site, OpenTable, Yelp, Tourisme Montréal). Third-party sources
> disagree slightly on exact hours, and the **table inventory in `floorplan.py` is
> a realistic placeholder** — confirm both with the restaurant before live use.
