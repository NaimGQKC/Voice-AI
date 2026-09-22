# Setup — from nothing to talking, in tiers

Work down this file and stop wherever you like. **Tier 1 is ~10 minutes and two
free accounts**, and gets you a real conversation with the agent.

Run `python scripts/check_setup.py` at any point — it tells you exactly what is
missing and where to get it.

```bash
git clone <repo> && cd FuckEcosystemLockIn
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[agent,dev]"
cp .env.example .env
```

---

# TIER 1 — talk to it on your laptop

**Two keys. No credit card. ~10 minutes.**

## 1. Deepgram — the ears and the voice

1. Go to **<https://console.deepgram.com/signup>**
2. Sign up (Google/GitHub is fastest). **No card required.**
3. You land on the dashboard. Left sidebar → **API Keys**.
4. **Create a New API Key** → name it `resto-agent` → permissions **Member** →
   **Create Key**.
5. **Copy it now** — Deepgram shows the key exactly once.
6. Paste into `.env`:
   ```
   DEEPGRAM_API_KEY=<paste>
   ```

You get **$200 of free credit**, roughly 45,000 minutes. This venue uses ~111
minutes a month, so that is years of testing.

## 2. Google AI Studio — the brain

1. Go to **<https://aistudio.google.com/apikey>**
2. Sign in with any Google account.
3. **Create API key** → choose or create a project → **Create API key in new
   project**.
4. Copy it and paste into `.env`:
   ```
   GOOGLE_API_KEY=<paste>
   ```

## 3. Check and run

```bash
python scripts/check_setup.py     # should be all green for Tier 1
python -m resto_agent.agent console
```

Speak to it. **Use headphones** — without them the agent hears itself through
your speakers and interrupts itself, which looks like a bug and isn't.

Try: *"Do you have a table for two tomorrow at seven?"*

This runs against a **fake restaurant**. Nothing you do here touches the real venue.

> **Expect rough edges on the first run.** Nobody has spoken to this system yet;
> 231 tests cover the logic, not the voice. Finding two or three problems here is
> the point of doing it.

---

# TIER 2 — book real tables at the venue

**No new accounts.** You already have the Libro token.

1. In `.env`:
   ```
   LIBRO_PRIVATE_TOKEN=<the token>
   LIBRO_PRIVATE_EMAIL=<the login email>
   ```
2. Confirm the connection is healthy — **read-only, writes nothing**:
   ```bash
   python scripts/healthcheck.py
   ```
3. Flip the backend:
   ```
   AGENT_RESERVATION_BACKEND=libro-private
   ```

**Before you let it book anything by voice**, do one controlled write:

```bash
python scripts/test_booking_libro.py              # dry run, writes nothing
python scripts/test_booking_libro.py --yes-write  # books 2031, then cancels
```

The standing rule (enforced in that script's defaults): **far-future date →
cancel in the same run → report a link you can check with your own eyes.**

---

# TIER 3 — the dashboard

Nothing to install; it reads the same call log the agent writes.

```bash
python scripts/dashboard.py        # http://localhost:8080
```

Shows: calls, bookings made, **bookings that failed**, messages nobody has
actioned yet, and the share of callers who hung up without speaking.

Read-only by construction — there is no route that can change anything, and a
test enforces that. Libro stays the system of record.

**Phone numbers are masked** (`•••• 2020`). Add `?full=1` to reveal them.

> ⚠️ To expose it beyond your laptop you **must** set `AGENT_DASHBOARD_TOKEN`.
> The script refuses to bind to a public address without one, because the page
> shows guest names.

---

# TIER 4 — a real phone number

See **`docs/DEPLOY.md`**. Order matters: **LiveKit → Fly.io → Twilio →
Supabase**, because Twilio has to point at LiveKit's SIP address.

Roughly half a day, most of it waiting on account verification rather than work.

---

## When something breaks

| Symptom | Cause |
|---|---|
| Agent talks over itself / responds to nothing | No headphones — it's hearing its own voice |
| `Plugins must be registered on the main thread` | Stale install: `pip install -e ".[agent]"` |
| `429` / quota errors mid-conversation | Free LLM tier exhausted. Add billing, or switch `AGENT_LLM_PROVIDER` |
| It says a time isn't available when it is | Check `AGENT_RESERVATION_BACKEND` — `mock` has its own fake calendar |
| Long silence before it answers | The LLM's first-token time. Try another provider — it's one line in `.env` |

`python scripts/check_setup.py` diagnoses most of these.
