# What 84 real calls to this venue actually look like

Source: the incumbent system's own call log for the venue, **30 Jun – 25 Jul 2026**,
84 calls, exported read-only and redacted (guest names pseudonymised, phone
numbers masked). This is measured behaviour at *this* venue, not industry
averages — which makes it the most valuable input we have.

## The headline number

**Only 13 of 84 calls (15%) produced a booking or a modification.
36 of 84 (43%) ended with a transfer to a human.**

The incumbent is not deflecting call volume. It is triaging it.

| Outcome | Calls |
|---|---|
| **Transferred to a human** | **36** |
| Caller hung up | 16 |
| No interaction at all (0 user turns) | 16 |
| Booked | 12 |
| Ended, no action | 3 |
| SMS sent | 2 |
| Modified | 1 |

Transfers by reason: general-inquiry 8 · human-requested 8 · **takeout 8** ·
no-availability 7 · api-error 1 · large-booking 1 · lost-item 1.

## What we changed because of this

### 1. Takeout is the biggest single leak — and we had nothing (now fixed)

8 calls transferred as `takeout`, plus ~6 more who hung up after being told to
use the website. **Roughly one call in six is someone trying to order food.**
The incumbent has no takeout path at all, so its only moves were "go to the
website" or "let me transfer you" — and the transcripts show callers hanging up
on both.

We can't take payment or fire an order to the kitchen. But we can capture the
order and the caller's number durably and put it in front of staff immediately.
That beats both incumbent options, because the caller gets a callback instead of
a dial tone. → `Concierge.handle_takeout`, tool `handle_takeout`.

### 2. Cancellations were a *capability* gap, not a model failure

Four attempted cancellations (#4542081, #4569020, #4780168, #4780191) — **every
one transferred.** The incumbent has a `FindReservations` tool but **no
`CancelReservation` tool**, so the model physically could not complete the task.
One tool result even says the request "could not be completed through the
automated system due to cancellation restrictions."

**We already have cancellation.** This is a real, free differentiator; there is
now a regression test proving it completes without a human.

### 3. Party size: I changed it to 7, then had to change it back

The venue's own rule is *"accept the reservation if party size is ≤ 7; if
strictly greater than 7, transfer."* Our Libro adapter had `MAX_ONLINE_PARTY = 6`,
so I raised it to 7 to match.

**That was wrong, and a later probe of the Libro API proved it.** The
availability endpoint only ever exposes party sizes **1–6** — Libro cannot
express a party of seven at all, so a booking for 7 is not something the
incumbent's prompt could deliver either, whatever it claimed. **Reverted to 6**
(`MAX_ONLINE_PARTY` in `libro_private.py` and `faq.py`), and 7+ escalates.

The lesson is worth more than the constant: **the incumbent's prompt is
aspirational, the API is truth.** Don't take a competitor's system prompt as
evidence of a capability.

### 4. Hours: confirmed, no longer a guess

Straight from the venue's live configuration:

```
Mon–Sat  11:30–14:30  and  17:00–21:30
Sunday                     17:00–21:30   (dinner only)
```

This **matches what we already had**. It moves hours from "placeholder to
confirm with the owner" to "confirmed", and closes that questionnaire item.

### 5. Our AM/PM rule is independently validated

The incumbent's prompt contains the same rule we derived: *no AM/PM context and
hour between 1 and 8 → always PM*, explicitly including 24-hour-style input
("07h15" → 19h15). Our implementation already does this. Now tested against the
venue's stated rule rather than our own reasoning.

### 6. Availability failure is the norm, not the exception

`GetBookingAvailability` ran 33 times; **16 of those calls** were flagged
"requested time unavailable" or "lookup returned nothing", and 7 ended in a
`no-availability` transfer. One call (#4771197) called it **five times** before
booking.

The recurring shape: caller wants a table *now* or in 15 minutes → nothing found
→ alternatives offered → declined → transfer. Our cascade targets exactly this,
and ends in a callback capture instead of a transfer.

## Findings we have NOT yet acted on

**19% of calls (16 of 84) had zero user turns** — the caller heard the greeting
and hung up. Six were under 15 seconds. This is a large, cheap-to-fix loss and
it is probably about greeting length and how quickly the caller hears something
useful. We had our own 13-second greeting bug earlier; this suggests measuring
time-to-first-useful-word rather than eyeballing it. **Open.**

**A tool returned a bare error string, and the agent could not tell.** In
#4825246 `SendSms` returned the plain text *"I'm having trouble processing that
request right now"* instead of a JSON envelope. Because success detection looked
for the envelope, the model read it as prose and improvised; the caller hung up.
**Lesson for us: a tool result that isn't the expected shape must be treated as a
hard failure, not narrated.** Our tools are wrapped in `@_safe` and return
strings by design, so we are not exposed the same way — but we have no test
asserting it. **Open.**

**Platform summaries can be wrong.** #4728077's summary claims a modification was
confirmed; the tool trace shows `FindReservations → GetBookingAvailability` and
no `ModifyReservation` call. A reminder not to trust a generated summary as
evidence of what happened — check the tool trace.

**The corpus is roughly two-thirds French.** We have been treating French as a
secondary mode behind an env flag. At this venue it is the majority language.
That likely deserves reconsidering before launch.

## Regression set

`tests/test_real_call_patterns.py` encodes the cases above. The call log also
names the calls that best discriminate a good agent from a bad one:

- **Must not regress:** #4680999, #4726808, #4795351, #4806665 (clean single-lookup
  bookings) · #4627579 (the only successful modification) · #4771197 (booking after
  five lookups, with an allergy note) · #4745793 (asked 17:35, accepted 18:00).
- **Must do better:** #4825246 (SendSms failure) · #4727324 (party of 7 error →
  transfer) · #4569020 / #4780191 (cancellations) · #4544152 / #4730445 / #4825891
  (takeout) · #4629185 (three lookups, nothing, hung up).
- **Hardest bilingual:** #4474562 (FR, 15 turns, allergies + SMS + booking) ·
  #4792243 (FR, seating preference) · #4760819 (FR, address read aloud).

When the full transcript export (`messages[]` with tool calls and arguments)
lands, these become replayable rather than merely documented.
