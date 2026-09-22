# Why we chose each piece of the stack

Written to be defensible out loud. Every entry: what it is in plain terms, what we
compared it against, why we picked it, **what we gave up**, and when we'd choose
differently. If you can't state the downside of a choice, you don't understand it
well enough to defend it.

## The goal these choices serve

**Deploy to one restaurant and never have to touch it again**, at low cost.

That is a stricter requirement than "own your stack", and it changes the ranking.
Every choice below is now judged on three things, in order:

1. **Unattended reliability** — will this still work in eight months with nobody
   watching it? Anything that can silently expire is a defect, not a saving.
2. **Cost** — but note the costs here are dominated by *fixed* monthly items
   (phone number, hosting), not usage. At ~90 talk-minutes/month the AI itself is
   a couple of dollars.
3. **Speed** — every 100ms is audible to a caller.

Two consequences worth stating up front, because they reverse earlier decisions:

* **Free tiers are a liability, not a saving.** A free tier that lapses takes the
  restaurant's phone down. Production runs on paid plans with a card on file.
* **Fewer *accounts* beat fewer vendors.** Each extra provider is another key that
  can expire and another bill that can fail — but that concern is about
  credentials, not logos. The French voice is Cartesia, and it is fine precisely
  because it bills through LiveKit Inference on credentials we already have. No
  new account, no new key.

---

## The big one: LiveKit Agents (voice orchestration)

**What it does.** Runs the real-time loop: capture caller audio → speech-to-text →
LLM → text-to-speech → play back, while handling the hard parts — interruptions
(barge-in), knowing when the caller finished speaking (turn detection), echo
cancellation, and bridging phone calls in over SIP.

**Alternatives considered**

| Option | What it is | Why not |
|---|---|---|
| **Vapi** | Managed voice-agent platform. What Sadie uses. | ~$0.05/min platform fee on top of model costs; your prompt and call data live in their system. Fastest to ship, least ownership — the opposite of this project's point. |
| **Retell / Bland** | Same category as Vapi | Same trade: speed now, lock-in later. |
| **Pipecat** (open source) | Python framework, similar shape to LiveKit | Genuinely comparable. Smaller ecosystem, and no first-party WebRTC/SIP infrastructure — you assemble telephony yourself. |
| **Build on raw WebSockets** | Wire up Deepgram + LLM + TTS yourself | We'd spend weeks reimplementing barge-in and turn detection badly. Not a real option. |

**Why LiveKit**
- **Apache-2.0 open source.** The framework is ours; we can self-host the whole media stack if the economics ever demand it. That is literally the repo's thesis.
- **Every layer is swappable by config** — STT, LLM, TTS are plugins. We've already switched LLM providers four times without touching agent code.
- Solves the genuinely hard real-time problems (semantic turn detection, barge-in, echo cancellation) that we would otherwise get wrong.
- Phone support comes free: a SIP caller is just another participant in a room, so **the same agent works on the browser and the phone with zero code changes.**
- Ships a first-party **eval/simulation framework** (`livekit.agents.evals`) for testing conversations.

**What we gave up**
- **More moving parts than a managed platform.** We debug plugin versions, deprecations, and Windows quirks ourselves — we've hit all three.
- We currently lean on **LiveKit Cloud** for hosted turn detection and noise cancellation. That's a soft dependency: it degrades gracefully (falls back to VAD) but the best behaviour is on their infrastructure.
- **API churn.** 1.6.4 → 1.6.6 deprecated things under us mid-project.

**Re-examined against "deploy once and never touch it."** That goal is Vapi's strongest argument, so it deserves a straight answer rather than a slogan. It still doesn't win, for one concrete reason: **Vapi replaces the voice loop, not the ops.** The Libro integration is custom either way, so with Vapi you still host a webhook server for the booking tools — same deployment, same thing to keep alive — plus a per-minute platform fee, plus the loss of being able to fix the pipeline yourself when Libro's private API shifts under us (which it will). You'd add a dependency and remove almost no work.

**When we'd genuinely choose differently:** if the restaurant needed *no* custom backend integration — just an FAQ bot and a calendar — Vapi would be the right call and this repo would be over-engineering.

---

## Speech-to-text: Deepgram Nova-3

**Alternatives:** OpenAI Whisper (self-host), AssemblyAI, Google, Azure, AWS.

**Why Deepgram**
- Built for **streaming** — partial results as the caller speaks, which is what makes sub-second response possible. Whisper is batch-first; it transcribes after you stop talking.
- **Trained on telephony audio.** Phone calls are 8 kHz narrowband — much worse than a podcast mic — and most models are trained on clean audio.
- **Real-time code-switching across 10 languages** including French — one setting turns on EN/FR auto-detection, which Montreal needs.
- $200 free credit and per-second billing. (It can also do TTS, and Aura-2 does support French — we route the voice through LiveKit Inference instead, see below.)

**What we gave up / what worries me**
- **No published French-Canadian accuracy figure for Nova-3.** An independent Québécois benchmark (CRIM, 24 models) puts `whisper-large-v3-turbo` at 8.2% word error rate and shows that models topping the standard benchmarks can do *badly* on real Québécois. We're choosing partly on faith.
- Add restaurant noise and plan for **15–25% real-world error rate**, not 8%.
- **This is the one choice I'd test before committing** — 20–30 hand-transcribed real calls would settle it.

---

## Text-to-speech: Cartesia `sonic-3` (French), via LiveKit Inference

**Alternatives:** Deepgram Aura-2, ElevenLabs, OpenAI, Azure, AWS Polly.

**Why.** The greeting is French-only — *"Bonjour. the restaurant."* — at a venue where two-thirds of calls are in French. It has to sound like a French speaker said it. Cartesia `sonic-3` does, it is fast, and it bills **through LiveKit Inference on the LiveKit credentials the agent already needs**. That last part is the whole reason it won: no extra vendor account, no extra key to expire unattended. `AGENT_TTS_PROVIDER` and `CARTESIA_API_KEY` are **not read by anything** — the routing is entirely `_has_livekit_cloud()` in `agent.py`.

**Cost:** ~$50/1M chars. At this venue's ~90 talk-minutes/month that is under $2, so it does not move the economics.

**What we gave up — a real problem for Montreal.** None of the fast providers (Cartesia, Deepgram, ElevenLabs, OpenAI) has a genuine **Québécois** voice; `sonic-3` speaks European French. Real fr-CA voices exist essentially only on **Azure** and **AWS Polly (Gabrielle)**, both slower stacks with their own account. Whether a Parisian accent actually costs trust in Montreal is **untested** — we can't A/B on real diners. Worth revisiting once there's volume.

### Two earlier claims in this file that were wrong

1. It said Deepgram TTS is English-only, verified as *"58 models, every id ends in `-en`"*. That was true of **Aura-1**. **Aura-2 does support `fr`/`fr-FR`** (`aura-2-agathe-fr`, `aura-2-hector-fr`) — see `STACK_DECISION.md` §3.4. Aura-2 is ~40% cheaper than Cartesia and equally zero-account, so it is a live candidate if we ever run a listening test. It is not currently wired.
2. It said English used Deepgram and French used something else. There is **one voice now**, French, for every call.

---

## LLM: Google Gemini 2.5 Flash, swappable

**Alternatives tried in this project:** Groq (Llama 3.3 70B), xAI Grok, OpenAI, Cerebras, Anthropic, and six OpenAI-compatible providers still wired in `agent.py`.

**Why Gemini 2.5 Flash.** Cheap, fast enough, and reliable at the only thing we ask of it: pick the right tool with the right arguments. Roughly $1.50 CAD/month at this venue's volume.

**Production note: we do not ship on a free tier.** Gemini's free tier is a development convenience and it is thin — it rate-limited us mid-way through the first live voice test. An unattended restaurant phone line cannot depend on a quota that can lapse without notice. **Billing must be enabled before the line goes live.** This is an open item.

**Why not Groq, which this document used to recommend.** The Groq investigation is preserved in `LLM_BENCHMARK.md` and its findings still stand on their own terms — the binding free-tier limit is tokens not requests, and downgrading to an 8B model makes throttling *worse* because it has half the TPM. What changed is that Gemini is cheaper at our volume and did not need the 70B's paid plan to be usable. `scripts/benchmark_llm.py` still runs and still targets Groq; treat it as the harness that produced that record, not as current configuration.

**Known open, both measured:**
- **First token is 1.16–1.48s** against a 200–700ms budget. It feels slightly slow.
- **Prompt caching is not firing** (`prompt_cached_tokens: 0`), so every turn pays full prefill. The prompt is already ordered for it — the volatile date line sits at the **end**, which takes the shared prefix across days from 0% to 99%. Note that Anthropic's cache has a **4,096-token minimum** and our prefix is ~3,690, so trimming the prompt would lock caching out entirely on that provider. See `ANTHROPIC_CACHE_MIN_TOKENS` in `prompts.py`.

**Key design point:** the LLM is the *most* replaceable part. It only converses and calls tools; it never does math, dates, or availability. That's deliberate — see below.

---

## The architectural decision that matters most: `ReservationService`

**What it is.** One Python interface (`check_availability`, `create_booking`,
`cancel_booking`, …). Everything above it — the agent, the tools, the conversation
logic — talks *only* to that interface. Two implementations exist: a local fake and
the real Libro API.

**Why it matters**
- We built and tested the entire agent for days **before** we had any Libro access.
- When the real API turned out to be **completely different** from the documented one (per-endpoint API versions, "services" that are actually 15-minute slots, a datetime derived from a relationship rather than a field), **only one file changed.** The agent, tools, prompts and every test were untouched.
- It makes the system **testable without the network** — the whole suite runs in ~6 seconds with no API keys.
- It's the anti-lock-in mechanism: swapping Libro for OpenTable is one new file.

**What we gave up:** one extra layer of indirection, and domain models that must be translated at the boundary. Trivially worth it — this is the decision I'd defend hardest in an interview, because it's the one that actually paid off under pressure.

**Related and equally deliberate: the LLM never does math.** Dates ("this Friday"), phone-number formatting, and table-capacity logic are all deterministic Python. LLMs are unreliable at calendar arithmetic and there's no reason to gamble on it — same input, same output, every time, and unit-testable.

---

## Python

**Alternative:** Node/TypeScript (LiveKit supports both).

**Why:** the voice/AI ecosystem is Python-first — LiveKit's own examples, evals, and most plugins land there first. And it's the natural language for the deterministic logic layer.

**Gave up:** TypeScript's type safety would have caught a couple of our bugs at compile time rather than runtime.

---

## FastAPI + SQLite (the mock Libro service)

**Why FastAPI:** async (matches the agent), automatic validation, and it can serve the future operator webapp too — one framework instead of two.

**Why SQLite:** zero setup, a file, perfect for a fake service.

**Reversed decision — SQLite stays, for production too.** An earlier version of this
document recommended Postgres for the operator webapp. That was the right answer to
the wrong question. Postgres is a *service*: something to run, back up, patch, and
upgrade. For one restaurant with a handful of calls a day and a "never touch it
again" mandate, that is pure operational cost for capacity we will never use.
SQLite is a file that needs nothing from anyone. If this ever grows to many
restaurants with concurrent writers, revisit — that is the trigger, not taste.

**httpx** over `requests` because it's async and supports in-process ASGI transport — that's how our tests hit the mock API with no network at all.

---

## Telephony (chosen, not yet wired): Twilio

**Alternatives:** Telnyx, Plivo, Vonage, LiveKit's own numbers.

**Why Twilio:** LiveKit's first-party numbers are **US-only**, and we need a Montreal 514/438 number. Twilio is the best-documented SIP path, ~$1.15/month plus ~$0.0045/min.

**Gave up:** Telnyx is slightly cheaper. Not worth optimizing at this volume.

---

## The operator dashboard — built, `src/resto_agent/dashboard.py`

| Choice | What we did | Why |
|---|---|---|
| Backend | **FastAPI** | Already in the stack; same language as the agent; async |
| Database | **SQLite (WAL) locally, Supabase Postgres in production** | WAL is what lets the dashboard read a live database while a call is in progress without ever blocking the agent. Supabase `ca-central-1` keeps guest PII in Canada under Law 25. |
| Frontend | **Server-rendered HTML**, no React | It's a call list, a transcript view, and a few counters for one restaurant. A React/Next.js build adds a toolchain, a deploy target, and hours of work for a page that renders a table. **Reach for React when there's real client-side state — there isn't.** |
| Charts | Plain HTML/CSS bars | Same reasoning |
| Access | Token via `hmac.compare_digest`, phone numbers masked unless `?full=1` | It shows guest names. |

The honest interview answer here is *"we chose the boring option deliberately, because the complexity budget belongs in the voice pipeline, not in the admin page."*

The honest interview answer here is *"we chose the boring option deliberately, because the complexity budget belongs in the voice pipeline, not in the admin page."*

---

## What is actually proven, and what is not

Be precise about this — it is easy to over-claim, and the mock makes it easy to
fool yourself.

| Claim | Status |
|---|---|
| Booking against **real Libro**, real restaurant | ✅ **Proven.** Booking `<redacted>` created on `api.libroreserve.com` (restaurant <redacted>), requested 11:30 EDT returned as `2026-09-08T15:30:00Z` — exact — then cancelled and independently verified. |
| Conversation logic, dates, phone parsing, the cascade | ✅ Proven by 231 tests. |
| **Voice in, tools out** | ✅ **Proven.** A spoken call reached `check_availability` with every argument correct (`party_size=2`, `part_of_day="dinner"`, `date="tomorrow"`, `preferred_time="seven"`). Voice was clear, the agent confirmed the details back, and language switching worked mid-call. |
| **Voice → real Libro, end to end** | ❌ **NOT proven.** The live voice test above ran on the **default mock backend**, and the LLM hit a free-tier rate limit during it. Nobody has yet spoken to the agent while it wrote to `api.libroreserve.com`. That is `docs/QA_SCRIPT.md` section F, and it has not been run. **Do not claim this.** |
| **Table merging / combining tables** | ❌ **NOT proven, and not real.** That runs on `mock_libro/floorplan.py`, an *invented* floor plan. Real Libro does its own seating; the live adapter never calls it and never returns `arrangement="merged"`. Do not cite the demo as evidence about the venue's dining room. |
| French-Canadian speech accuracy | ❌ Unvalidated. Chosen on reputation. `sonic-3` speaks European French. |
| Latency under real phone conditions | ⚠️ **Measured, and over budget.** 1.16–1.48s to first token against 200–700ms. |
| **The greeting-abandonment fix** | ❌ Unvalidated. We shortened the greeting on a theory about length and language; 19% zero-turn hangups is the number to beat and we have no A/B. |
| **Deployed and answering the phone** | ❌ Not yet. It works on a real call; it is not live. |

---

## Where this stack is weakest (say this before they find it)

1. **We're on a reverse-engineered private API.** Libro's official partner route never responded, so we integrate against the dashboard's own undocumented API. It can change without notice. Mitigated by isolating it behind one adapter, but it's the biggest business risk.
2. **French-Canadian speech accuracy is unvalidated.** We chose the STT on reputation, not local evidence, and the TTS voice is European French.
3. **No conversation-level tests.** 231 tests prove the *logic*; nothing proves the *conversation*. The SDK ships the tooling; we haven't used it. `docs/QA_SCRIPT.md` is the manual stand-in and has not been fully worked through.
4. **Soft dependency on LiveKit Cloud** — hosted turn detection and the Cartesia voice both route through it. Without LiveKit credentials the agent falls back to VAD turn-taking and Deepgram's English voice.
5. **Latency is over budget** — 1.16–1.48s to first token — and prompt caching isn't firing. Both measured, neither fixed.
6. **Cost honesty:** verified recurring cost is **~$20 CAD/month** against the incumbent's **~$275 CAD/month**. That is a real saving, but it excludes maintenance, which is currently unpriced because it's being done for free. **The durable justification is control and data ownership** — the restaurant owns the prompts, the logic, and the call records. Lead with that, and let the cost number support it rather than carry it.
