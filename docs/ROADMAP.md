# What we're building, what we're not, and in what order

Written because the scope had drifted out of anyone's head — including the
owner's. If you read one section, read **"The one thing that matters"**.

---

## What this is, in one paragraph

A phone agent for **one restaurant** (the restaurant, Montreal) that
answers calls in French and English, books/cancels/changes reservations in the
restaurant's real Libro account, answers questions from their real knowledge
base, and — for everything it *can't* do — captures the caller's details and puts
them in front of staff instead of hanging up on them. It must run unattended for
months on roughly $10–15/month, and the restaurant must own the logic and the
data.

## The one thing that matters

**Nobody has ever spoken to this agent while it was connected to real Libro.**

Text-mode logic against real Libro: proven. A real booking created and cancelled
on the real floor: proven. Someone *talking* to it and getting a real table:
**never done.**

Everything in this repo is downstream of that. A latency benchmark, a health
check, a greeting rewrite — all of it is guesswork until the core loop has run
once end to end. **That is the next milestone, and most other work should queue
behind it.**

---

## Are we over-engineering? Partly, yes.

Honest assessment, so we stop repeating the pattern.

### Where we genuinely over-built

**The table-merging engine** (`mock_libro/floorplan.py`) is the clearest case. We
built a full floor-plan model — combinable table groups, turn times, capacity
search — to reason about seating. Then the API probe showed **Libro does its own
seating and never exposes tables**, and the availability endpoint doesn't even
have a seating-area dimension. Roughly a day of work with **zero production
value**. It survives only as a test fixture that can realistically refuse a
booking, and it's now labelled as invented at the top of the file.

*Root cause:* we designed against an imagined backend instead of probing the real
one first. The probe that settled it took a couple of hours.

**Six LLM providers** (`groq | cerebras | xai | openai | livekit | google`). We
need one, with one fallback. The rest is configuration surface that has to keep
working and never gets exercised.

**Documentation sprawl.** Nine docs and counting. Several were written for
decisions that have since been reversed. Docs that don't change what someone does
are cost, not value.

### Where the "extra" work has already paid for itself

- **`ReservationService`** — when Libro's real API turned out completely
  different from expected, one file changed. Highest-return decision in the repo.
- **Deterministic dates/times/phones** — the AM/PM rule we derived independently
  turned out to match the incumbent's own production rule exactly.
- **Durable message storage** — fixed the agent lying to callers.
- **The availability cascade** — targets a *measured* failure: 43% of real calls
  ended in a transfer.

### The pattern to keep

Build against **probed reality, not imagined reality.** Every time we probed
first, we were right. Every time we designed first, we built something we threw
away.

---

## Are we using the research well? Yes — but it needs an end.

Four research drops, four direct code changes. That's a high hit rate:

| Research | What it changed |
|---|---|
| Sadie teardown | Replaced invented FAQ answers with the venue's real ones (we'd claimed vegan; truth is vegetarian yes, vegan no) |
| 84-call log | Built takeout handling (~1 call in 6), found cancellation is a free differentiator |
| Availability API probe | Corrected the party ceiling to 6; confirmed `{}` = unavailable |
| Large-party UI probe | Confirmed the 6/7 wall is structural, not policy |

**The risk now is an infinite research loop.** Research is comfortable; shipping
is not. So:

**Remaining questions worth asking — and then we stop:**
1. Where does the 7+ handoff actually get written down today? (The floor manager
   is recording it *somewhere*, and that's where our captures should land.)
2. What happens on the venue's phone line today when nobody picks up?
3. Nothing else. Everything further is optimisation of a system that has not run.

Both are **owner questions, already on the spreadsheet** — not Cowork tasks.

---

## Scope fence

### In scope for v1

1. Answer the phone; short French-first greeting; detect and switch language
2. Check availability, book, cancel, and modify reservations for **1–6 guests**
3. Answer questions from the real knowledge base; never improvise facts
4. Capture, durably, everything it can't complete: **takeout**, **7+ parties**,
   messages, waitlist — and notify the restaurant
5. Survive Libro being unreachable without lying or dropping the caller
6. Let the owner and developer see what happened on every call

### Explicitly NOT in v1

- **An operator web dashboard.** Libro is already the system of record for
  reservations. A CLI answers "did it work?" without anything to host or secure.
- **Live warm transfer to a human.** Blocked on the owner, and genuinely complex.
  Capture-and-callback covers the same need at a fraction of the cost.
- **Taking payment**, for takeout or deposits.
- **Multi-restaurant / multi-tenant.** One venue. Every abstraction for "other
  restaurants" is speculative.
- **Storing call transcripts.** Far more sensitive than a name and number, and
  not needed to operate.
- **Seating-area preferences** (bar, counter seating) — Libro does not expose them.
- **Table-merging logic** — Libro owns seating. We do not model the floor.
- **Any automated path for 7+.** It does not exist in Libro. Human handoff by
  design, not a limitation to engineer around.

---

## Phases

Each phase has an **exit criterion**. Don't start the next one until it's met.

### Phase 0 — finish what's in flight *(now)*
Three parallel workstreams: greeting + telemetry, Libro resilience, latency
benchmark.
**Exit:** merged, tests green.

### Phase 1 — THE MILESTONE: one real voice booking
Run the agent in console or browser mode pointed at **real Libro**. Speak to it.
Book a table for a far-future date. Verify in the dashboard. Cancel it.
**Exit:** one booking created by voice, verified by eye, and cancelled.
**Everything else waits for this.** It will surface a dozen problems no amount of
unit testing will.

### Phase 2 — harden what Phase 1 breaks
Fix what the real call exposes. Measure real latency and real greeting timing
against the instrumentation. Test a Libro outage deliberately.
**Exit:** five consecutive voice bookings with no manual intervention; measured
TTFT within budget.

### Phase 3 — telephony and deployment
Twilio number (Montreal 514/438) → LiveKit SIP. Deploy to a small always-on host
with a **persistent volume** for the call log. Health check on a schedule.
**Exit:** you can phone a real number from your own mobile and book a table.

### Phase 4 — supervised parallel run
The AI answers a **test number**, not the restaurant's line. You and the owner
make real-ish calls. Review every call in the log.
**Exit:** the owner has heard it handle their own scenarios and agrees it's ready.

### Phase 5 — go live, narrowly
**Overflow only** — the AI picks up when nobody answers after N rings. Lowest-risk
entry. The restaurant's normal flow is untouched; the AI only catches calls that
would otherwise be lost.
**Exit:** a month with no incident and messages actually reaching staff.

---

## How to tell if we're over-engineering again

Three questions before building anything:

1. **Is this downstream of a measured problem, or an imagined one?** The cascade
   came from a measured 43% transfer rate. The floorplan engine came from
   imagination.
2. **Have we probed the real system, or are we designing against a guess?** If we
   haven't probed, probe first. It has been faster every single time.
3. **Does this need to exist before the first real voice call?** Usually no.
