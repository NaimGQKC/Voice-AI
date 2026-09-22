# Hosting decision — self-hosted agent worker vs LiveKit Cloud Ship

**Date:** 2026-07-25 · **Supersedes §3.1 of `STACK_DECISION.md` only.** Everything
else in that document (models, French, telephony, Law 25) stands unchanged.

**What changed since `STACK_DECISION.md`.** That document optimised for *"as
little management as possible — I can't monitor this stuff"* and taxed every
extra vendor account heavily. The owner has since revised that:

> *"I can take that management on, cause it won't change. The only one that can
> really change is Libro, which I need to account for."*

So this document re-prices the question with **one-time setup treated as cheap**
(a signup, a key, a Dockerfile, a deploy) and **ongoing burden treated as
expensive** (things that break, drift, expire, or need watching). That is a
sharper distinction than the one the previous report used, and it changes the
analysis materially — the previous report's headline argument against
self-hosting ("it adds accounts") is **not the argument that survives.**

Every number is tagged **✅ verified** (primary source, linked at the bottom) or
**⚠️ estimated**. Nothing is blurred.

---

# 1. Read only this

## Options and prices

| # | Option | Monthly cost | Vendor accounts | Ongoing burden | Verdict |
|---|---|---|---|---|---|
| **A** | **LiveKit Cloud Ship** — everything managed, LiveKit Inference for STT/LLM/TTS | **$51.65** | 3 (LiveKit, Twilio, Supabase) | **Effectively none.** No machine, no image, no build. Overages **bill**, they don't block. | ✅ **Recommended** |
| **B** | **Self-hosted worker** (Fly.io `yyz`) + **LiveKit Build (free)** for media/SIP + **own** Deepgram / Cartesia / Gemini keys | **≈ $13.31** ⚠️ | 7 (Fly, LiveKit, Deepgram, Cartesia, Google, Twilio, Supabase) | **Real but bounded.** One container image, 4 more payment methods that can lapse, and **one Build allowance our volume already exceeds** (§4.3). | ⚠️ **Viable and defensible — not the safe pick** |
| **C** | Self-hosted worker + LiveKit Build + **LiveKit Inference** | ~$8 nominal | 5 | **Disqualified.** | ❌ **Dead — §4.2** |
| **D** | Self-hosted worker + LiveKit **Ship** | $51.65 + compute | 4 | Ship already gives warm agents | ❌ Strictly worse than A |
| **E** | Self-hosted worker + Build + Inference for **STT only**, own keys for LLM/TTS | ≈ $12.67 ⚠️ | 6 | Puts STT behind a $2.50 hard cap for a $0.64 saving | ❌ Not worth it — §4.6 |

## Recommendation

**Option A — LiveKit Cloud Ship, ~$51.65/month. The previous report's
recommendation stands.**

But the *reason* it stands has changed, and the margin is much thinner than
`STACK_DECISION.md` claimed. That document said self-hosting loses on account
count. **That argument is now weak, and I am not going to lean on it.** Under the
owner's revised criteria, A wins on one thing only:

> **On Ship, every limit we can hit is billed. On Build, every limit we can hit
> is a wall.** LiveKit states it plainly: *"For projects on the free Build plan,
> the included allowance acts as a hard cap — after you exceed it, new requests
> fail rather than incurring overage charges."* ✅ On paid plans, usage past the
> allowance is billed incrementally ✅.

That is criterion #1 stated in LiveKit's own words, and at our exact volume
**one Build allowance is already exceeded on day one** (§4.3).

**Option B is a legitimate choice, not a mistake.** It saves **~$38/month
(~$456/year)**, it is not blocked by SIP gating, the cold-start problem genuinely
does not apply to it, and it needs **zero code changes** — the agent already
selects providers from environment variables (§5.1). If the owner takes B, §6 is
the runbook, and §6.5 lists the four conditions that must hold.

## The three premises, settled

| # | Question | Answer |
|---|---|---|
| **1** | **Does Build tier support inbound SIP for a production phone line?** | ✅ **YES.** Build includes **1,000 third-party SIP minutes/month** ✅ — we need 111. SIP is **not** paid-gated. **Option B does not collapse.** The only telephony feature gated above Build is *custom SIP domains* (Enterprise-only ✅), which we do not need. |
| **2** | **Does the $2.50 inference cap apply from a self-hosted worker?** | ✅ **YES — and it kills option C.** The meter is *"LiveKit Inference — aggregated usage for all LiveKit Inference models"*, scoped **per project** ✅. Nothing in the metering definition references where the caller runs. Inference authenticates with the project's LiveKit key, so a self-hosted worker bills the same meter. The obvious workaround is also closed in writing: *"Free projects also share allowances and limits across all of a user's free projects; creating additional projects doesn't increase the total."* ✅ |
| **3** | **Do any per-tier limits bite at our volume?** | **One does.** Agent deployments (1), concurrent agent sessions (5), concurrent connections (100), inference concurrency (5), LLM rate limits (100 RPM / 600k TPM) — all ✅ verified, none close. But **voice isolation (Krisp BVC), which this agent uses today, is capped at 100 minutes/month on Build ✅ and we use 111 talk-minutes ✅.** See §4.3. |

## Top 3 risks

1. **Build's 100-minute voice-isolation cap is below our measured volume.** ✅
   Our agent calls `noise_cancellation.BVC()` (`agent.py:433`), which LiveKit
   meters as *voice isolation*: **100 min on Build, 1,000 min on Ship** ✅. At 111
   talk-minutes we cross it in the last ~10% of every month. **⚠️ LiveKit does
   not document what "requests fail" means for this specific feature** — whether
   the filter silently stops applying or the session errors. **This is the single
   most important unverified thing in this document.** Under option B, either
   switch to unmetered background noise suppression (`NC()`, "Yes" on all plans
   ✅, one line) and accept worse noisy-caller audio, or pay for Ship.
2. **Option B replaces one billing relationship with five.** Fly, Deepgram
   (prepaid credits), Cartesia ($5/mo subscription), Google Cloud, plus Twilio
   and Supabase. Each is a card that can expire. Every one of these failures is
   *loud* — the line stops working — but **nobody is listening to the line.** A
   loud failure nobody hears is operationally silent, and the owner has said he
   will not be watching. Build also carries **no support entitlement**: LiveKit's
   ToS grants community/email support to *"Customers on the Ship or Scale
   subscription plan"* ✅.
3. **Supabase Free pauses projects after a quiet week — and this applies to
   both options.** *"Supabase pauses Free Plan projects that show low activity
   over a 7-day period… Typically a few user requests to the database each day
   over the previous week is enough to keep the project from being paused."* ✅
   At 102 calls/month with only ~15% producing a booking, our write rate is
   **right at that threshold**, and a holiday closure would trip it. A warning
   email arrives first ✅, to an owner who has said he will not be monitoring.
   **Fix: have the agent issue one trivial query per day from its own process
   (§6.4) — no cron service, no extra account.**

## Against the incumbent

| | Monthly | vs $200 incumbent ✅ | Annual saving |
|---|---|---|---|
| **A — Ship** | $51.65 | **3.9× cheaper** | ~$1,780 |
| **B — self-hosted** | ~$13.31 ⚠️ | **15× cheaper** | ~$2,240 |

Both are far inside the $100 ceiling. The gap between them is **~$456/year** —
enough to be worth a decision, not enough to justify contorting the design.

---

# 2. What the owner's revision does and does not change

His distinction is correct and I am applying it literally.

**Costs that are genuinely one-time, and which I am now discounting:**

| Item | Why it's one-time |
|---|---|
| Creating Deepgram / Cartesia / Google / Fly accounts | Signup, once. |
| Writing a Dockerfile | LiveKit ships a working one in `agent-starter-python` ✅. Once. |
| Wiring `fly deploy` to the repo | Once. |
| Pasting 5 API keys as secrets | Once. |
| Creating the LiveKit SIP inbound trunk + dispatch rule | *"Trunks are long-lived configuration objects that LiveKit caches and reuses. Create an inbound trunk once and reuse it for every call"* ✅ |

The previous report priced all of the above as a permanent tax. **That was too
aggressive, and it is the main correction this document makes.**

**Costs that are genuinely ongoing, and which I am now weighting heavily:**

| Item | Why it recurs |
|---|---|
| 4 extra payment methods that can expire or be declined | Recurs every card renewal, forever. |
| A base image that ages (CVEs, Python EOL, `livekit-agents` pinning) | Recurs on every redeploy — and **only** on redeploy, so it breaks months later, in a hurry. |
| Living inside hard-capped free allowances | Recurs monthly; the failure is a wall, not a bill. |
| No support channel on Build | Recurs at exactly the wrong moment. |
| Supabase Free pause threshold | Recurs weekly. |

**What does *not* recur, and which I want to state plainly because it is the
strongest point in B's favour:** the LiveKit agent worker is a plain
long-running Python process registered over an outbound WebSocket ✅. It has no
inbound ports ✅, no persistent volume requirement ✅ (our DB is remote), and no
scaling logic at our volume. This is about as boring as a self-hosted service
gets. The owner is right that it "won't change."

---

# 3. The cold-start premise — confirmed

> **Q: The 10–20s cold start is a property of LiveKit's Build-tier *hosted*
> agents. Does it apply to a worker we run ourselves?**
>
> **A: No.**

LiveKit's cold-start text is scoped to Cloud-hosted deployments, verbatim ✅:

> *"Projects on the Build plan might have their **deployed agents** shut down
> after all active sessions end. The agent automatically starts again when a new
> session begins. This can cause up to 10 to 20 seconds of delay before the agent
> joins the room."*

It sits under the heading **"Agent deployment"**, alongside the build-context
size limit for `lk agent deploy` and the agent-session concurrency limit — all
Cloud-hosting concerns ✅. Meanwhile the self-hosting guide describes the
opposite lifecycle ✅:

> *"Agent servers use a WebSocket connection to register with LiveKit server and
> accept incoming jobs. This means that agent servers do not need to expose any
> inbound hosts or ports to the public internet."*

A registered worker is *our* process on *our* host. LiveKit cannot stop it; it
can only stop dispatching to it.

**⚠️ Honest caveat: LiveKit nowhere states "self-hosted agents never cold
start."** This is the necessary reading of two documents plus how a process
works, not a direct quotation. I am confident in it, but it is inference, not a
quote.

**The real cold-start risk under option B moves to the *host*.** Any platform
that scales to zero reintroduces the exact problem. Concretely ✅:

- **Fly.io** — `auto_stop_machines` *"default if not set is `off`"* ✅. A machine
  with no proxied services is never auto-stopped. **Safe by default**, but see
  the restart-policy trap in §6.2.
- **Render Free** — *"Render spins down a Free web service that goes 15 minutes
  without receiving any inbound traffic"* ✅ and *"Do not use them for production
  applications"* ✅. **Render Free is disqualified.** Render **Starter** ($7/mo
  ✅) is always-on.
- **Railway** — Hobby has a $5/mo minimum with $5 of included usage ✅; services
  do not sleep.

---

# 4. Pricing each option honestly

Volume used throughout, from the venue's own call log: **102 calls/month, 111
talk-minutes/month, ~44 TTS minutes, ~8 turns/call, ~3,450 tokens of fixed
prompt per turn** → 2,815,200 input tokens/month, ~97,920 output tokens ⚠️.

## 4.1 Option A — LiveKit Cloud Ship

| Line | Monthly | Basis |
|---|---|---|
| LiveKit Ship | **$50.00** ✅ | Pricing page, *"STARTING AT $50/mo"* |
| LiveKit Inference (STT + LLM + TTS) | **$0.00** | $3.05 ⚠️ usage inside the **$5.00** included credit ✅ |
| Third-party SIP minutes (111) | $0.00 | 5,000 included on Ship ✅ |
| Agent session minutes (111) | $0.00 | 5,000 included ✅ |
| Voice isolation / Krisp BVC (111) | $0.00 | **1,000 included on Ship** ✅ |
| Agent session recordings + observability | $0.00 | 5,000 min / 500,000 events included ✅ |
| Twilio 514/438 number | $1.15 ✅ | Canada local number |
| Twilio inbound, 111 min | $0.50 | $0.0045/min local origination ✅ |
| Supabase Free (`ca-central-1`) | $0.00 | Free plan ✅ |
| **Total** | **≈ $51.65** | |

**The previous report's open question #6.10 — "is Ship's $50 actually flat?" — is
now answered.** Checked line by line against the published allowance table, our
usage sits inside **every** Ship allowance with 9× to 1,350× headroom:

| Ship allowance ✅ | Included | We use | Headroom |
|---|---|---|---|
| Agent session minutes | 5,000 | 111 ⚠️ | 45× |
| Third-party SIP minutes | 5,000 | 111 ⚠️ | 45× |
| WebRTC participant minutes | 150,000 | 111 ⚠️ | 1,350× |
| Voice isolation minutes | 1,000 | 111 ⚠️ | 9× |
| Inference credit | $5.00 | $3.05 ⚠️ | 1.6× |
| Agent session recordings | 5,000 min | 111 ⚠️ | 45× |
| Observability events | 500,000 | ~11,100 ⚠️ | 45× |
| Downstream data transfer | 250 GB | ≪1 GB ⚠️ | — |

**$50.00 flat is the right number to quote.** Still worth confirming on the first
invoice, but there is no longer an unexplained variable.

## 4.2 Option C — self-hosted worker + Build + LiveKit Inference: **dead**

The cap applies (premise 2 above). The arithmetic, so nobody re-litigates it:

| Component | Rate ✅ | Monthly | Note |
|---|---|---|---|
| STT — Deepgram Nova-3 Multilingual | $0.0058/min | $0.64 | 111 min |
| LLM — **Gemini 2.5 Flash-Lite** (what `agent.py` actually defaults to) | $0.10 in / $0.40 out per 1M | $0.32 ⚠️ | 2.82M in + 98k out |
| LLM — Gemini 2.5 **Flash** (what `STACK_DECISION.md` recommends) | $0.30 / $2.50 per 1M | $1.09 ⚠️ | |
| TTS — Cartesia `sonic-3` @ 600 chars/min ⚠️ | $50/1M chars | $1.32 | LiveKit's own calculator assumption ✅ |
| TTS — Cartesia `sonic-3` @ 900 chars/min ⚠️ | $50/1M chars | $1.98 | more realistic for conversational French |

| Scenario | Total | vs **$2.50 hard cap** ✅ |
|---|---|---|
| Flash-Lite + 600 chars/min | **$2.28** | fits, **9% headroom** |
| Flash-Lite + 900 chars/min | **$2.94** | **over by 18%** |
| Flash + 600 chars/min | **$3.05** | **over by 22%** |
| Flash + 900 chars/min | **$3.71** | **over by 48%** |

**This is more interesting than `STACK_DECISION.md` reported, and worse.** That
document said Build's cap is exceeded outright. In fact, with the cheaper LLM the
cheapest configuration *just squeezes under* — by 9%, on a character rate that
**nobody has measured** (`STACK_DECISION.md` §6.5 lists it as unverified).

**A 9% margin against a wall that kills the phone line is not a plan.** One
chatty week, one prompt edit, one busy Saturday, and the restaurant's line stops
answering with no bill and no alert. **Option C is disqualified under criterion
#1** — not because the arithmetic certainly fails, but because it is a coin-flip
with the phone line as the stake.

## 4.3 The finding that actually bites option B: voice isolation

`src/resto_agent/agent.py:433` sets `noise_cancellation.BVC()`. LiveKit's feature
matrix meters Krisp **BVC** under **voice isolation**, explicitly ✅ — *"Applies
to Krisp BVC, Krisp BVCTelephony, and ai-coustics Voice Focus 2.1"*:

| Plan ✅ | Voice isolation included |
|---|---|
| **Build** | **100 minutes** |
| Ship | 1,000 minutes (then $0.0012/min) |
| Scale | 10,000 minutes |

**We use 111 talk-minutes/month ⚠️. Build includes 100 ✅.** We are over on day
one, every month, by about 11%.

Separately, LiveKit lists **background noise suppression** (Krisp NC,
ai-coustics QUAIL_L) as **"Yes"** on every plan including Build, with **no minute
allowance shown** ✅ — i.e. unmetered.

**⚠️ What I could not establish:** whether exceeding the voice-isolation
allowance on Build fails the *filter* (degrading to raw audio) or fails the
*session* (dropping the call). The general rule is stated — *"the included
allowance acts as a hard cap — after you exceed it, new requests fail"* ✅ — but
voice isolation is **not listed** in the quotas page's metered-resource table,
which creates a genuine ambiguity between the two LiveKit pages. **Ask support
before shipping option B.** If it drops calls, that is a phone line failing for
the last three days of every month.

**Mitigation under B (one line):** switch `BVC()` → `NC()`. Unmetered, works on
Build. **Cost: BVC/BVCTelephony is materially better than plain NC on telephony
audio** — a caller phoning from a busy street or a car is exactly the case BVC is
built for, and degrading it will cost STT accuracy on the noisiest calls. That is
a real product regression, not a technicality.

## 4.4 Option B — full price, every account included

**Host selection first**, because it is the only decision in B with a clear
winner:

| Host | Always-on? | Cost | **Canadian region?** | Ongoing burden |
|---|---|---|---|---|
| **Fly.io `yyz` (Toronto)** | ✅ Yes — `auto_stop_machines` defaults to `off` ✅ | **~$5.70/mo** ⚠️ *(1GB shared-cpu-1x; **see caveat below**)* | ✅ **Yes — `yyz`, Toronto** ✅ | Container image; restart policy must be set (§6.2) |
| Render Starter | ✅ Yes ($7 tier is not the sleeping Free tier ✅) | **$7.00/mo** ✅ (512 MB / 0.5 CPU) | ❌ **No** ✅ — Oregon, Ohio, Virginia, Frankfurt, Singapore | Container image; 512 MB may be undersized |
| Railway Hobby | ✅ Yes | **~$5/mo** ⚠️ (min. $5 with $5 usage credit ✅; $0.00000386/GB/s RAM + $0.00000772/vCPU/s ✅) | ❌ **No** ✅ — California, Virginia, Amsterdam, Singapore | Container image; usage-metered so cost drifts with load |
| Hetzner Cloud | ✅ Yes | ⚠️ **Could not verify** — pricing page is JS-rendered | ❌ No Canadian location | **Bare VPS — you own the OS, kernel updates, systemd, firewall.** Highest ongoing burden of the four. |

> **⚠️ The Fly.io figure is the weakest number in this document.** Fly's preset
> price table is rendered client-side and I could not extract it from the primary
> source. What Fly's docs *do* state verbatim is *"about $5 per 30 days per GB of
> additional RAM"* ✅ and shared-machine reservation blocks at *"$36/year for
> $5/month of usage"* ✅. The ~$5.70/mo figure for a 1 GB `shared-cpu-1x` comes
> from secondary sources ⚠️. **Confirm in Fly's dashboard before quoting it.**
> Even if it is double, B still wins on price.

**Fly.io wins, and the reason is not price — it is `yyz`.** It is the only one of
the four with a Canadian region ✅. That matters twice: the worker process holds
guest names and phone numbers in memory, so a Canadian host is the strictly
better Law 25 posture (`STACK_DECISION.md` §3.7); and it is the shortest hop to
both the Montreal phone line and Supabase `ca-central-1`. Render and Railway
would put the agent process in the US for no benefit.

**All-in monthly cost of option B:**

| Line | Monthly | Verified? |
|---|---|---|
| Fly.io machine, 1 GB, `yyz`, always-on | **$5.70** | ⚠️ **estimated — see caveat above** |
| LiveKit **Build** (media, SIP, turn detection) | **$0.00** | ✅ Build is $0/mo |
| Deepgram Nova-3 Multilingual, 111 min @ $0.0058 | **$0.64** | ✅ rate; ⚠️ volume |
| **Cartesia Pro** — 100K credits (~133 TTS min) | **$5.00** | ✅ **not usage-based; see below** |
| Google Gemini API, `gemini-2.5-flash-lite` | **$0.32** | ✅ rates; ⚠️ tokens |
| Twilio 514/438 number + 111 inbound min | **$1.65** | ✅ rates |
| Supabase Free (`ca-central-1`) | **$0.00** | ✅ |
| **Total** | **≈ $13.31** | |

*(With `gemini-2.5-flash` instead of Flash-Lite: **≈ $14.08**.)*

**Two things in that table are worth calling out, because a naive estimate gets
both wrong:**

1. **Cartesia is a $5/month subscription, not pay-per-use.** Cartesia's **Free**
   plan is 20K credits/month (~27 TTS min) and — decisively — it does **not**
   include a **commercial use licence**; that appears first on the **Pro $5/mo**
   plan ✅. Our ~44 TTS minutes also exceed the free tier's ~27 ✅. **A restaurant's
   production phone line cannot run on Cartesia Free.** Pro's 100K credits
   (~133 min ✅) covers us with ~3× headroom.
2. **Bringing your own Gemini key saves nothing on rate.** Google's direct paid
   rate for Gemini 2.5 Flash is **$0.30/1M input, $2.50/1M output, $0.03/1M
   cached** ✅ — *identical* to LiveKit Inference's ✅. The only reason to BYO is to
   escape the $2.50 cap, plus access to context caching (§7, item 3). Gemini's
   **free** tier is not an option: it is rate-limited by requests-per-day and its
   own pricing page marks it *"Used to improve our products: Yes"* ✅.

**Saving vs option A: ~$38.34/month, ~$460/year.**

## 4.5 What option B consumes from LiveKit Build

This is the load-bearing table for B, and every row is verified:

| Build allowance ✅ | Included | We use ⚠️ | Headroom | Failure if exceeded |
|---|---|---|---|---|
| **Third-party SIP minutes** | **1,000** | **111** | **9×** | Hard cap ✅ |
| **WebRTC participant minutes** *(pricing page: "Self-hosted agents count against WebRTC participant minutes")* ✅ | **5,000** | **111–222** | **23–45×** | Hard cap ✅ |
| **Voice isolation (BVC)** | **100** | **111** | ❌ **0.9× — OVER** | §4.3 |
| Background noise suppression (NC) | Unmetered ("Yes") | — | — | — |
| Agent session minutes *("an agent deployed to **LiveKit Cloud**")* ✅ | 1,000 | **0** — self-hosted agents are not Cloud-deployed | n/a | n/a |
| Agent deployments | 1 | **0** | n/a | n/a |
| Concurrent connections (participants) | 100 | ~2–6 ⚠️ | ~20× | Hard cap |
| Hosted turn detector v1 (`inference.TurnDetector()`, `agent.py:336`) | **7,500 requests** *(Cloud-deployed agents: free/unmetered)* ✅ | ~816–1,020 ⚠️ | ~8× | **Graceful** — *"falls back to the local `v1-mini` model automatically"* ✅ |
| Adaptive interruption handling | **40,000 requests** *(each 100 ms of overlapping speech = 1 request)* ✅ | ~1,600 ⚠️ | ~25× | ⚠️ undocumented |
| Agent observability events | 100,000 | ~11,100 ⚠️ | 9× | Hard cap |
| Downstream data transfer | 50 GB | ≪1 GB ⚠️ | — | Hard cap |
| LiveKit Inference | $2.50 | **$0** (BYO keys) | n/a | n/a |

**Read that table carefully — it is the honest case both for and against B.**
Nine of eleven applicable rows have 8–45× headroom, which is genuinely
comfortable. One row (turn detection) degrades gracefully by design. **One row is
already over.**

Note the two asymmetries that only appear when you self-host: the hosted turn
detector and adaptive interruption handling are **free and unmetered for
Cloud-deployed agents** but **allowance-limited for everyone else** ✅. Our volume
fits with room to spare, but it means B is metered on axes A is not.

## 4.6 Option E — Build + Inference for STT only

Deepgram STT via Inference is $0.64/month ⚠️ against the $2.50 cap — **3.9×
headroom**, and it removes the Deepgram account. Tempting, and much safer than
option C.

**Still no.** It puts the one component whose failure means *the agent cannot
hear the caller at all* behind a hard cap, to save one signup the owner has
already said he'll do. If B is chosen, bring your own Deepgram key and leave the
Build inference meter at zero, so that no wall exists anywhere in the audio path.

---

# 5. Ongoing burden of option B, specifically

The owner's real criterion. Setup is excluded; only what recurs is listed.

## 5.1 What is genuinely zero

- **No code changes.** `agent.py` already selects providers from env vars:
  `AGENT_LLM_PROVIDER` (`groq`/`cerebras`/`xai`/`openai`/`livekit`/`google`),
  `AGENT_TTS_MODEL` (a value containing `/` routes via LiveKit Inference, otherwise
  Deepgram), and the Deepgram plugin path is already the default for STT/English
  TTS. Both the Inference path and the BYO-plugin path are already written and
  exercised. **Switching from A to B, or back, is an environment-variable
  change.** This is the strongest single fact in B's favour and it is worth
  more than the price difference.
- **No persistent volume.** LiveKit: *"Agent server and job processes have no
  particular storage requirements beyond the size of the Docker image itself"* ✅.
  Our state lives in Supabase. Nothing to back up, nothing to fill.
- **No inbound networking, no TLS, no certificates, no domain.** *"Agent servers
  do not need to expose any inbound hosts or ports to the public internet"* ✅.
  This eliminates the single largest category of self-hosting maintenance.
- **No scaling.** Peak concurrency is ~2–3 calls ⚠️.

## 5.2 What recurs

| Ongoing item | Frequency | What breaks | Loud or silent? |
|---|---|---|---|
| **Fly card expires / payment fails** | Card renewal | Machine suspended → no worker registers → **callers reach dead air** | Loud, but only to callers |
| **Deepgram prepaid credits run dry** | ~Never at $0.64/mo, but auto-load must be enabled | STT dies → agent cannot hear | Loud |
| **Cartesia $5/mo subscription lapses** | Card renewal | TTS dies → agent cannot speak | Loud |
| **Google Cloud billing lapses** | Card renewal | LLM dies → agent cannot think | Loud |
| **Base image ages** (Python EOL, CVEs, `livekit-agents` moves) | Only on redeploy | Build fails, or a new dep breaks at runtime | **Silent for months, then urgent** |
| **Voice isolation cap** | **Monthly, ~day 27** | §4.3 — unknown | ⚠️ **Unknown** |
| **Supabase Free pause** | After any quiet week ✅ | Bookings stop being recorded | **Silent** (email warning only) |
| **Build has no support entitlement** ✅ | At the worst moment | — | — |

**The honest framing on failure modes:** almost every B failure is *loud* — the
phone line stops working. `STACK_DECISION.md`'s scariest scenario (Azure French
dying while English keeps working) **does not apply here**, because Deepgram,
Cartesia and Gemini each serve both languages; if one dies, the whole line dies,
not two-thirds of it. **That is a fair point in B's favour and I want it on the
record.**

The counter is equally simple: a loud failure is only loud if someone is
listening, and the owner has said he is not. The restaurant would learn from a
customer. **The difference between A and B is not the *kind* of failure — it is
that A has three cards to keep alive and B has seven.**

## 5.3 Crash, OOM, and hang behaviour

Three distinct failures; a naive setup only handles one.

1. **Process crash.** Fly's default restart policy is **`on-failure`** ✅ — but it
   takes a `retries` count, and the documented example is `retries = 10` ✅.
   **After the retry budget is spent, Fly stops trying and the machine stays
   down.** A crash loop from, say, a bad Libro response would burn ten restarts in
   seconds and leave the phone line permanently dead. **Set `policy = "always"`
   explicitly.** This is the single most important line in the whole option-B
   configuration.
2. **OOM.** The kernel kills the process, it exits non-zero, and the restart
   policy applies. **⚠️ I could not verify whether Fly emails on OOM** — assume
   not. Right-size the machine instead of relying on notification.
3. **Hang** (deadlock, wedged WebSocket). No restart policy catches this — the
   process is alive and useless. **The fix is free and built in:** LiveKit's agent
   server exposes a health endpoint — *"The default health check server listens on
   `http://0.0.0.0:8081/`"* ✅ — and Fly supports top-level `[checks]` for apps
   with no public services ✅. Wire one to the other and Fly restarts a wedged
   worker on its own.

**Sizing.** LiveKit's published rule is *"4 cores and 8GB per agent server as a
starting rule for most voice AI apps"*, handling *"10-25 concurrent jobs"* ✅ —
that is sized ~10× above our peak. Their load test ran **30 concurrent agents on
one 4-core/8 GB machine at ~3.8 cores and ~2.8 GB peak** ✅, i.e. ~95 MB and ~0.13
cores per concurrent job. At 2–3 concurrent calls that extrapolates to well under
512 MB ⚠️ — **but LiveKit also warns that "the memory requirements might exceed
the amount available on a cloud provider's free tier"** ✅, and their own guidance
is 8 GB. **Start at 1 GB, watch it for one week, and treat any OOM as a signal to
go to 2 GB (+~$5/mo ✅) rather than as a puzzle to solve.** ⚠️ I have not measured
this agent's actual footprint; that is a real gap.

---

# 6. If the owner picks B — the runbook

## 6.1 Fly.io

- Region **`yyz` (Toronto)** ✅ — the only Canadian region among Fly, Render and
  Railway ✅. Nearest to the Montreal line and to Supabase `ca-central-1`.
- Start at **1 GB RAM, `shared-cpu-1x`**, single machine.
- Do **not** configure `[[services]]` — the worker has no inbound traffic, and
  leaving the proxy out of it means `auto_stop_machines` (default `"off"` ✅) can
  never sleep the machine.

## 6.2 `fly.toml` — the two lines that matter

```toml
[[restart]]
  policy = "always"        # NOT the "on-failure" default: that gives up after N retries

[checks]
  [checks.agent_health]
    type = "http"
    port = 8081            # livekit-agents' built-in health server, per LiveKit docs
    path = "/"
    interval = "15s"
    timeout = "10s"
    grace_period = "30s"
```

Without the first, a crash loop kills the line permanently. Without the second,
a hung worker is never noticed.

## 6.3 Accounts and secrets (set once)

`LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`, `DEEPGRAM_API_KEY`,
`GOOGLE_API_KEY`, Supabase URL + service key, Libro credentials. Set via
`fly secrets set`.

There is **no `CARTESIA_API_KEY`** — the voice bills through LiveKit Inference on
the LiveKit credentials above. That is the entire reason Cartesia was chosen over
a provider needing its own account. Then:

- Enable **Deepgram credit auto-load** — otherwise credits run out and STT stops.
- Put **all** cards on the same payment method with a long expiry, and set a
  calendar reminder for that expiry. Four independent renewal dates is the actual
  ongoing cost of B.

## 6.4 Two config changes required before go-live

1. **`noise_cancellation.BVC()` → `NC()`** (`agent.py:433`) — or confirm with
   LiveKit support what happens when Build's 100-minute voice-isolation allowance
   is exceeded (§4.3). Do not ship B without settling this.
2. **Add a daily keep-alive query to Supabase** from the agent process itself (a
   `SELECT 1` on an asyncio interval). Prevents the Free-plan 7-day pause ✅
   without a cron service or another account.

## 6.5 The four conditions under which I would sign off on B

1. LiveKit support confirms the voice-isolation overage behaviour, **or** the
   agent is switched to unmetered `NC()`.
2. `restart policy = "always"` and the `[checks]` health check are both in
   `fly.toml`.
3. The Supabase keep-alive is in place.
4. The owner accepts that Build carries **no support entitlement** ✅ and that
   four extra cards must be kept alive.

If all four hold, B is a sound design that saves ~$460/year. **It is a real
choice, not a compromise — but A is what I would put on an unattended phone
line, and that is the recommendation.**

---

# 7. What I could not establish

1. **What "requests fail" means for voice isolation on Build.** ⚠️ The general
   hard-cap rule is verified ✅, but voice isolation is absent from the quotas
   page's metered-resource table while present in the pricing matrix with a
   100-minute Build allowance ✅. **The highest-value unanswered question in this
   document, and the only one that could change the recommendation.** One support
   email.
2. **Fly.io's exact machine preset prices.** ⚠️ JS-rendered; not extractable from
   the primary source. Only *"about $5 per 30 days per GB of additional RAM"* ✅
   and the reservation-block rates ✅ are verified. **The ~$5.70/mo figure is
   secondary-source and must be confirmed in the dashboard before it appears in a
   client quote.**
3. **Hetzner Cloud pricing.** ⚠️ Same problem, no figures obtained. Not on the
   recommended path — bare-VPS OS maintenance disqualifies it on criterion #2
   regardless of price.
4. **This agent's actual memory footprint.** ⚠️ Extrapolated from LiveKit's load
   test ✅, not measured. Determines whether B costs ~$5.70 or ~$10.70/mo.
5. **Added latency of a self-hosted worker vs LiveKit Cloud hosting.** ⚠️ Not
   measured, and deliberately not estimated. What *is* verified is the mechanism:
   *"LiveKit Cloud additionally exercises geographic affinity to prioritize
   matching users and agent servers that are geographically closest to each
   other"* ✅ — self-hosting forfeits that and pins you to one region. Fly `yyz`
   makes the loss small for a Montreal line, but non-zero. Criterion #4.
6. **Whether Deepgram's PAYG tier has a minimum credit purchase.** ⚠️ The page
   says *"No minimums. No expiration. No credit card required"* ✅ and grants $200
   of free credit ✅, but the auto-load minimum is not stated.
7. **Real TTS characters/minute and LLM output tokens/turn.** ⚠️ Unchanged from
   `STACK_DECISION.md` §6.5–6.6, and now more consequential: they are what
   decides whether option C's arithmetic clears $2.50 or not (§4.2). **One real
   call's logs settles both.**
8. **Whether Gemini's implicit context caching applies to our fixed 3,450-token
   prefix.** ⚠️ Direct Gemini API publishes a cached-input rate 10× below standard
   ✅. If it triggers, B's LLM line drops further. Not verified, and not counted in
   any total above.
9. **What LiveKit does when the adaptive-interruption allowance (40,000
   requests) is exhausted for a self-hosted agent.** ⚠️ Undocumented. Our
   estimated ~1,600 requests/month ⚠️ is far under, so it is not on the critical
   path.
10. **Any per-project limit on the number of SIP trunks.** None found in the
    quotas documentation ✅ — absence of evidence, not evidence of absence. We need
    exactly one trunk.

**One thing I specifically checked and did not find:** any clause in LiveKit's
Terms of Service restricting the free Build plan to non-production or
non-commercial use ✅. The ToS requires *"testing in a non-production environment
prior to deployment"* ✅ (a pre-deployment obligation, not a plan restriction) and
scopes support entitlements to *"Customers on the Ship or Scale subscription
plan"* ✅. **Running a production phone line on Build appears permitted.** Read
as absence of a prohibition, not as an affirmative grant.

---

# 8. Corrections to `STACK_DECISION.md` §3.1

| Claim in §3.1 | Verified reality | Effect |
|---|---|---|
| Self-hosting options are *"plus a LiveKit account"* and *"you add an account; you remove none"* | ✅ **True but not decisive.** Build genuinely covers media **and SIP** (1,000 min ✅) at $0. The real objection is the hard-cap failure mode, not the account count. | Weakens the original argument |
| *"The $2.50 inference cap follows you"* | ✅ **Confirmed** — the meter is per project, and multi-project splitting is closed in writing ✅ | Kills option C; unchanged |
| Fly.io *"~$2–4"* ⚠️ | ⚠️ Still unverified. Fly's own doc supports only *"about $5 per 30 days per GB of additional RAM"* ✅ | Figure was optimistic |
| Table omits any Canadian-region consideration for the host | ✅ **Fly `yyz` (Toronto) exists; Render and Railway have no Canadian region** ✅ | Decides the host choice under B |
| Table does not mention voice isolation | ✅ **Build includes 100 min; we use 111** ✅ | **The real blocker for B** |
| *"Cheaper" rows priced at ~$5* | **≈ $13.31 all-in** ⚠️ — Cartesia Free lacks a commercial licence, so Pro at **$5/mo** ✅ is mandatory | +~$8/mo; B is still much cheaper |
| §6.10 *"Whether Ship's $50 is flat"* — open | **Closed** — usage sits inside every Ship allowance with 9–1,350× headroom ✅ | Open item resolved |
| §3.4 *"No fr-CA exists in LiveKit Inference at all"* | ✅ True **for TTS**. For **STT**, `deepgram/nova-3` lists `fr-CA` ✅ | Does not change the TTS decision; worth pinning `fr-CA` on STT |

---

# Sources

**LiveKit**
- [Pricing — machine-readable](https://livekit.com/pricing.md) · [Pricing](https://livekit.com/pricing) — plan prices; full feature matrix (voice isolation 100 min Build / 1,000 Ship; third-party SIP 1,000 Build / 5,000 Ship; WebRTC 5,000 Build with the "self-hosted agents count against WebRTC participant minutes" note; cold-start prevention Build=No; agent deployments 1/2/4; concurrent agent sessions 5/20; inference credits $2.50/$5.00; custom SIP domains Enterprise-only); all Inference model rates; calculator assumptions (3,000 input + 175 output tokens/min, 600 chars/min)
- [Quotas and limits](https://docs.livekit.io/deploy/admin/quotas-and-limits/) — hard-cap wording; metered-resource table and Build allowances; agent cold starts ("10 to 20 seconds", scoped to deployed agents); agent session minutes defined as *"deployed to LiveKit Cloud"*; turn detector 7,500 free requests with automatic `v1-mini` fallback; adaptive interruption 40,000 requests; free projects share allowances; LLM rate limits 100 RPM / 600,000 TPM; inference STT/TTS concurrency 5
- [Self-hosted deployments](https://docs.livekit.io/deploy/custom/deployments/) — outbound-WebSocket worker registration, no inbound ports; health server on `0.0.0.0:8081`; no storage requirement; 4-core/8 GB sizing rule and the 30-agent load-test result; geographic affinity on LiveKit Cloud
- [Models overview](https://docs.livekit.io/agents/models/) · [LiveKit Inference](https://docs.livekit.io/agents/models/inference/) — zero data retention on every plan; per-model language tables (`cartesia/sonic-3` includes `fr`, `sonic-3.5` does not; `deepgram/nova-3` includes `fr-CA`); billing is usage-based per project
- [SIP inbound trunk](https://docs.livekit.io/sip/trunk-inbound/) — trunks are long-lived and reused; no plan gating stated
- [Terms of Service](https://livekit.io/legal/terms-of-service) — support scoped to Ship/Scale; no free-plan production restriction found

**Model providers**
- [Deepgram pricing](https://deepgram.com/pricing) — Nova-3 Multilingual $0.0058/min streaming PAYG; "No minimums. No expiration."; $200 free credit; *"Rates listed above opt in to the Model Improvement Program"*
- [Cartesia pricing](https://cartesia.ai/pricing) — Free 20K credits (~27 TTS min) **without** a commercial-use licence; Pro $5/mo, 100K credits (~133 TTS min)
- [Gemini API pricing](https://ai.google.dev/gemini-api/docs/pricing) — Gemini 2.5 Flash $0.30 / $2.50 / $0.03 cached per 1M; Flash-Lite $0.10 / $0.40 / $0.01; free tier marked *"Used to improve our products: Yes"*

**Hosting**
- [Fly.io resource pricing](https://fly.io/docs/about/pricing/) — *"about $5 per 30 days per GB of additional RAM"*; shared-machine reservation blocks $36/yr for $5/mo
- [Fly.io regions](https://fly.io/docs/reference/regions/) — `yyz` Toronto, Canada
- [Fly.io configuration reference](https://fly.io/docs/reference/configuration/) — `[[restart]]` policies (`on-failure` is the default, with a `retries` budget); `auto_stop_machines` defaults to `"off"`; top-level `[checks]`
- [Render pricing](https://render.com/pricing) — background workers: Starter $7/mo (512 MB / 0.5 CPU), Standard $25/mo (2 GB / 1 CPU)
- [Render free instances](https://render.com/docs/free) — *"Do not use them for production applications"*; free web services spin down after 15 minutes idle
- [Render regions](https://render.com/docs/regions) — Oregon, Ohio, Virginia, Frankfurt, Singapore
- [Railway pricing](https://railway.com/pricing) — Hobby $5 minimum with $5 included usage; RAM $0.00000386/GB/s; CPU $0.00000772/vCPU/s
- [Railway regions](https://docs.railway.com/reference/deployment-regions) — California, Virginia, Amsterdam, Singapore

**Telephony and database**
- [Twilio SIP Trunking pricing — Canada](https://www.twilio.com/en-us/sip-trunking/pricing/ca) — local origination $0.0045/min; toll-free origination $0.0130/min; local number $1.1500/mo
- [Supabase project pausing](https://supabase.com/docs/guides/platform/free-project-pausing) — Free-plan pause after a low-activity 7-day period; *"a few user requests to the database each day"* avoids it; warning email; 1-year restore window
- [Supabase billing](https://supabase.com/docs/guides/platform/billing-on-supabase) — Free plan limits; paid-plan projects are never paused

**Marked ⚠️ estimated, no primary source obtained:** Fly.io machine preset prices;
Hetzner Cloud pricing; this agent's memory footprint; TTS characters/minute; LLM
output tokens/turn; self-hosted-worker latency delta; Deepgram auto-load minimum.
