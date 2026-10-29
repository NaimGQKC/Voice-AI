# Recording the one-minute demo

A real spoken call, against the **mock** reservation system, with the booking
appearing on screen as the caller speaks. No real restaurant is touched.

## Before you record (one-time, ~10 minutes)

You need three keys in `.env`. All have free tiers; see `docs/SETUP.md`.

| Key | Why it matters for the video |
|---|---|
| `DEEPGRAM_API_KEY` | hears the caller |
| `GOOGLE_API_KEY` | the brain. **Enable billing** — the free tier rate-limited a live test mid-call |
| `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` | **the French voice.** Without them the agent falls back to an English-only voice and the opening *"Bonjour"* comes out mangled |

Then:

```bash
pip install -e ".[agent]"
python scripts/check_setup.py        # everything green before you hit record
```

## Record

```bash
python scripts/demo_call.py
```

This starts the mock system, opens the live reservation book in your browser,
and starts the call. **Use headphones** — without them the agent hears itself and
interrupts itself, which looks like a bug and isn't.

Lay the screen out side by side: **terminal on the left, browser on the right.**
The terminal shows the agent's tool calls as they fire; the browser shows the
booking landing.

The launcher forces the mock backend and blanks the Libro credentials, so it is
safe even if your `.env` contains the real token.

## The 60 seconds

| Time | On screen | You say / happens |
|---|---|---|
| 0:00–0:05 | Empty reservation book, "MOCK DATA" badge visible | *(title card or voiceover: "A French/English voice agent booking into a reservation system.")* |
| 0:05–0:12 | Agent greets | *"Bonjour. Demo Bistro."* then *"Assistant virtuel, je vous écoute."* |
| 0:12–0:25 | Terminal: `check_availability` fires | *"Une table pour deux demain à sept heures."* Agent offers 7 PM — not 7 AM |
| 0:25–0:40 | Language switch | Answer in **English**: *"Yes, 7 works — name's Alex, number is 514-555-0142."* Agent follows you and reads the number back |
| 0:40–0:48 | **Booking row appears and flashes green** | Agent confirms. This is the shot the whole video is for |
| 0:48–0:56 | Row turns red: `cancelled` | *"Actually, can you cancel that?"* — the incumbent system could not do this |
| 0:56–1:00 | End card | *"234 tests · FR/EN · swappable reservation backend"* |

**Reality check on timing.** A natural booking is closer to 70–90 seconds live,
because each turn carries about 1.3 seconds of model latency. Record the full
call, then trim dead air in the edit. Add "edited for length" on screen — it is
honest, and an interviewer who later sees the real latency will not feel misled.

## Lines that exercise the interesting behaviour

- *"Sept heures"* with no AM/PM → it chooses **7 PM**
- *"Demain soir"* → it resolves the date itself; it must never ask what tomorrow is
- Ask for a **party of 8** → it declines to book and offers a person (the booking API cannot express 7+)
- Ask *"C'est combien, le plateau?"* → it **refuses to quote prices**

## If something goes wrong

- **Nothing happens when you speak** → wrong microphone; check the OS input device.
- **Agent talks over itself** → headphones.
- **Long silence, then an error** → the language model hit its rate limit. Enable billing.
- **The greeting sounds like a French word read by an English voice** → LiveKit credentials are missing.
- **The book stays empty after the agent says "booked"** → stop and tell someone; that is a real bug, not a demo problem.

Always keep a clean recording of one good call as a backup before any live demo.

## What to claim, and what not to

Say: *"It holds a real spoken conversation, books and cancels through a reservation
API, and I tested it against 234 cases including the failure modes."*

Do not say it is deployed or answering a phone number — it is not. And do not say
it has been joined end-to-end to a real restaurant's live system by voice: the
booking path was proven in text mode and the voice path against this mock, but
nobody has done both at once.
