# Going live: the four accounts and what each one is for

This is the "run it for real" checklist. Everything here is **one-time setup** —
after this, the only thing that changes is Libro.

Order matters: LiveKit before Twilio (Twilio needs LiveKit's SIP address).

---

## 0. What each thing is actually for

| | Why it exists | Ongoing work |
|---|---|---|
| **LiveKit** (free Build tier) | Carries the audio and bridges the phone call | None |
| **Fly.io** (~$9.80 CAD/mo) | Runs our agent process, always on | None once deployed |
| **Twilio** (~$2/mo) | Owns the Montreal phone number | None after setup |
| **Supabase** (free) | Stores the call log so you can check what happened | Nothing, but see the pause trap |
| **Google AI** | The LLM | None |
| **Deepgram** | Speech in / speech out | None |
| **Libro** | The booking system | ← **the only one you actually manage** |

We do **not** pay LiveKit's $50 Ship tier. That tier exists to stop agents
cold-starting, and self-hosting the worker on Fly solves the same problem for
under $10. See `docs/HOSTING_DECISION.md`.

> ⚠️ **This file is a checklist, not a walkthrough.** It tells you which accounts
> you need and in what order, but it does not tell you what each screen says or
> where to click. `docs/SETUP.md` is the model to follow. Rewriting this
> click-by-click is an open task.

---

## 1. LiveKit (free Build tier)

1. Create a project at **cloud.livekit.io**.
2. Copy `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`.
3. Create an **inbound SIP trunk** and note the SIP host it gives you
   (`<something>.sip.livekit.cloud`).
4. Add a **dispatch rule** so an inbound call creates a room and dispatches our
   agent to it.

Build tier includes **1,000 third-party SIP minutes/month**; we need ~111.

> ⚠️ **Do NOT use LiveKit's hosted agent deployment.** On Build, agents shut down
> when idle and take **10–20 seconds** to rejoin. At ~3 calls/day essentially
> every call would pay it, which is exactly the dead-air problem we spent a
> workstream fixing. We run the worker on Fly instead, where it never sleeps.

---

## 2. Fly.io — the agent worker

```bash
fly launch --no-deploy          # creates the app; fly.toml is already written
fly secrets set \
  LIVEKIT_URL=... LIVEKIT_API_KEY=... LIVEKIT_API_SECRET=... \
  DEEPGRAM_API_KEY=... \
  GOOGLE_API_KEY=... \
  LIBRO_PRIVATE_TOKEN=... LIBRO_PRIVATE_EMAIL=... \
  AGENT_DB_URL=...
fly deploy
fly logs                        # watch the worker register with LiveKit
```

Two things in `fly.toml` are load-bearing and must not be "tidied up":

- **`policy = "always"`** — Fly's default is `on-failure` *with a retry budget*.
  A crash loop exhausts it, Fly stops restarting, and the phone line is dead
  permanently with no alert.
- **`min_machines_running = 1` / `auto_stop_machines = false`** — this *is* the
  always-warm guarantee. A stopped worker is a dead phone line.

---

## 3. Twilio — the phone number

1. Buy a **local Montreal number** (514 or 438). ~$1.15/mo.
2. Create an **Elastic SIP Trunk**.
3. Point its **Origination URI** at the LiveKit SIP host from step 1.
4. Assign the number to the trunk.

The owner asked for a **brand-new number for the AI**, keeping **514-555-0100**
as the in-store line. So the AI never touches the existing number — the lowest
risk way to start, and it makes the live-transfer target unambiguous.

---

## 4. Supabase — the call log

Create a project in **`ca-central-1` (Montréal)**. Not the default US region:
the table holds guest names and phone numbers, and Quebec's Law 25 governs
personal information leaving the province.

> ⚠️ **The pause trap.** Supabase Free pauses a project after ~7 days of low
> activity. At ~3 calls/day our write rate sits near that threshold, and a paused
> database means the agent cannot record the messages it promises to pass on.
> Mitigation: the agent pings the DB daily. Verify that is running before you
> stop watching.

---

## 5. Before the first real call

- [ ] `python scripts/check_setup.py` passes
- [ ] `python scripts/healthcheck.py` passes against live Libro (read-only)
- [ ] Call the Twilio number from your own mobile — does it answer, in French?
- [ ] Make ONE booking for a far-future date, verify it in the Libro dashboard,
      cancel it, and confirm the cancellation. See the standing rule at the top
      of `scripts/test_booking_libro.py`.
- [ ] `python scripts/calls.py` shows that call
- [ ] `python scripts/calls.py --greeting` shows the caller spoke (not a
      zero-user-turn call)

## 6. Not built yet — decide before going live

**Live transfer.** The owner wants calls the AI can't handle transferred to a
person, and explicitly does *not* want messages taken. We have not built that,
because his own answers contain a conflict worth resolving first: he also says
that when staff can't answer, the line "simply goes unanswered… during peak
rushes we will even deliberately hang up or mute."

A rush is exactly when the AI will be transferring. Transfer-only, in that
window, hands the caller a phone ringing in an empty room — worse than what
exists today. The likely answer is transfer first, capture as the fallback when
nobody picks up, but that is his call to make.
