# Stack decision — verified

**Date:** 2026-07-25 · **Venue:** one restaurant, Montreal · **Benchmark to beat:** $200/month (confirmed invoice) · **Budget ceiling:** $100/month

Every number below is tagged **✅ verified** (source links at the bottom) or
**⚠️ estimate**. Nothing is blurred between the two. Volume figures come from the
venue's own 84-call log and are used as given, not re-derived.

**Decision criteria, in the owner's stated priority order:**

1. **Management burden — dominant.** *"All-in-one, as little management from me as
   possible — I can't monitor this stuff."* Every extra account, key, dashboard or
   server is a tax, **and a thing that can fail without anyone noticing.** An
   option that is better in isolation but adds a vendor account is penalised
   heavily.
2. **Cost** — under $100/month.
3. **French must sound native-ish to a Québécois caller** (two-thirds of calls).
4. **Unattended for months** — anything that can silently expire is a defect.

Criterion 1 decides more of this document than criteria 2 and 3 combined. Where
it conflicts with 3, that conflict is called out explicitly rather than papered over.

---

# 1. Read only this

## Recommendation

| Layer | Choice | Cost/month | Extra accounts | Why |
|---|---|---|---|---|
| **Agent hosting** | **LiveKit Cloud — Ship tier** | **$50.00** ✅ | **0** | Free tier cold-starts **10–20s** ✅. Ship is the only tier with cold-start prevention ✅. `lk agent deploy` — no Dockerfile, no pipeline, no machine. |
| **STT** | Deepgram **Nova-3 Multilingual** via LiveKit Inference | $0.64 ✅ rate | **0** | Only Nova-3 variant doing live FR/EN code-switching. *Current plan quotes the wrong model — §5.* |
| **LLM** | Gemini 2.5 Flash via LiveKit Inference | $1.09 ⚠️ | **0** | Cheap, fast, adequate tool-calling. *Current plan understates this ~8× — §5.* |
| **TTS** | Cartesia **`sonic-3`** (`fr`) via LiveKit Inference | $1.32 ⚠️ | **0** | Zero-account French. **No fr-CA exists in LiveKit Inference at all** ✅ — §3.4 answers whether that matters. |
| **Telephony** | **Twilio** 514/438 → LiveKit SIP | $1.65 ✅ | 1 (unavoidable) | LiveKit's own numbers are **US-only** ✅. $1.15/mo + $0.0045/min. Set once, never touched. |
| **Database** | **None.** Push by SMS/email at capture | $0.00 | **0** | Fewest parts, and what he asked for. If ever needed: **Supabase `ca-central-1` (Montréal)** — *not* Turso ✅. §3.6. |
| | *Ship includes $5/mo inference credit; usage is $3.05* | *−$3.05* | | No overage. |
| | **TOTAL** | **≈ $51.65 / month** | **2 vendors** | |

**Two vendor accounts total: LiveKit and Twilio. Plus Libro, which he signed up
for.** No server, no OS, no Docker host, no database, no key rotation, no
dashboard to watch. Twilio is configured once at setup and never touched again.

## The total, in both scenarios

| Scenario | LiveKit | Inference | Twilio | **Total** | Verdict |
|---|---|---|---|---|---|
| **A — Build (free)** | $0 | $3.05 vs a **$2.50 hard cap** ✅ | $1.65 | **~$4.70 nominal** | ❌ **Not viable.** 10–20s cold start, *and* the cap **fails requests** mid-month with no bill and no alert. |
| **B — Ship ($50/mo)** ← chosen | $50.00 | $0 (inside $5 credit) | $1.65 | **~$51.65** | ✅ Always warm. 39% inference headroom, 45× agent-minute headroom. |

**vs the $200/month incumbent: ~3.9× cheaper, saving ~$148/month (~$1,780/year).**
Half the $100 ceiling, with room to double call volume without changing tier.

## The "all-in-one" argument, stated plainly

**With LiveKit Cloud, our Libro integration code runs *inside* the hosted agent.**
`reservation/libro_private.py` and `tools.py` are shipped by `lk agent deploy` as
part of the agent bundle. There is nothing else to host.

**A managed voice platform (Vapi, Retell, Bland) would be *less* all-in-one, not
more.** Those platforms manage the voice loop, but custom tools are HTTP
webhooks — so the Libro integration would have to live on a server we run, with a
public URL, TLS, and uptime to watch. You would end up managing *both* a SaaS
account *and* a webhook host, and paying a per-minute platform fee on top.

For a project whose whole point is a custom booking integration, **LiveKit Cloud
is the genuinely all-in-one option and the managed platforms are not.** This
inverts the usual intuition and is worth saying out loud, because "just use Vapi"
is the obvious-sounding advice that criterion #1 appears to favour and actually
doesn't. (`ARCHITECTURE_DECISIONS.md` reached the same conclusion; this confirms it.)

## Top 3 risks

1. **Nobody has heard the French yet.** Two-thirds of calls are French and **no
   fr-CA voice exists anywhere in LiveKit Inference** ✅ — every French option is
   Metropolitan `fr`/`fr-FR`. §3.4 argues the penalty is **real but mild and
   specific** (competent-sounding, less warm) and that keeping the zero-account
   setup is the right call — but it is **untested by ear**. *Gate go-live on a
   20-minute listening test. It is a one-line config change if he says no.*
2. **Quebec Law 25 applies to the whole system, not just the database.** Caller
   names and phone numbers go to US-hosted STT/LLM/TTS on **every call**. S.17
   requires a privacy impact assessment, an "adequate protection" finding, and a
   **written agreement** ✅. A Canadian database does not remove this. **Legal
   question — §3.7. Get 30 minutes of Québec privacy counsel before go-live.**
3. **Voice → real Libro has still never run end to end** (`ROADMAP.md` Phase 1).
   Every figure here is list-rate arithmetic against a system nobody has spoken
   to while it booked a real table. No bill has ever arrived.

---

# 2. The cold-start question — **answered**

> **Q: Do free-tier (Build) LiveKit agents cold-start, and if so how long?**
>
> **A: Yes. Up to 10–20 seconds. Cold-start prevention is a paid feature starting
> at Ship ($50/month).**

Two primary sources, both LiveKit's own.

**LiveKit docs, *Quotas and limits* → *Agent cold starts*** ✅, verbatim:

> "Projects on the Build plan might have their deployed agents shut down after
> all active sessions end. The agent automatically starts again when a new
> session begins. **This can cause up to 10 to 20 seconds of delay before the
> agent joins the room.**"

**LiveKit pricing feature matrix** ✅:

| Feature | Build | Ship | Scale | Enterprise |
|---|---|---|---|---|
| Cold start prevention | **No** | **Yes** | Yes | Yes |

That settles it on its own. The owner has chosen warm agents, so Ship is the
baseline. But the free tier has a **second, independent disqualifier** the
current plan does not mention — worth recording so nobody is tempted back.

## The Build tier's hard cap

**LiveKit docs, *Quotas and limits*** ✅, verbatim:

> "For projects on the free Build plan, **the included allowance acts as a hard
> cap — after you exceed it, new requests fail rather than incurring overage
> charges.**"

Build includes **$2.50/month** of Inference credit ✅. Our measured volume needs more:

| Component | Rate | Monthly | Basis |
|---|---|---|---|
| STT — Nova-3 Multilingual | $0.0058/min ✅ | $0.64 | 111 talk-min |
| LLM — Gemini 2.5 Flash, **input only** | $0.30/1M ✅ | $0.84 | 102 calls × 8 turns × 3,450 tok = 2.82M |
| TTS — Cartesia Sonic | $50/1M chars ✅ | $1.32 | 44 TTS-min × 600 chars/min ⚠️ |
| **Floor** | | **$2.80** | **already over the $2.50 cap** |
| LLM output ⚠️ | $2.50/1M ✅ | +$0.24 | ~120 tok/turn ⚠️ |
| **Realistic** | | **$3.05** | |

The floor line is the robust one: **even assuming zero output tokens and the most
conservative character rate, we exceed the cap.** The input figure derives from
the measured 3,450 tokens/turn × 8 turns/call — not a guess. Even swapping to the
cheapest French TTS available (Deepgram Aura-2, $30/1M chars ✅) only reaches
~$2.52 — still over.

**So Build fails twice:** it cold-starts, *and* it runs out of credit partway
through a normal month, at which point new requests fail — the phone line goes
down with no warning, no bill, and nobody watching. For an owner who has said
plainly that he cannot monitor this, a silent hard stop is the worst possible
failure mode.

Ship's **$5.00** credit ✅ covers $3.05 with ~39% headroom, **and overage on Ship is
billed rather than blocked** — a soft failure instead of a hard one. That
difference matters more than the money.

**Inference rates are identical on Build and Ship** ✅ — only Scale ($500/mo) gets
model discounts. There is no per-unit penalty for Ship; you are buying warmth,
headroom, and a failure mode that degrades instead of stopping.

---

# 3. Layer by layer

## 3.1 Agent hosting — and the other ways to get a warm agent

The owner asked specifically whether anything else gets always-warm agents with
*less management burden* than Ship. Ranked by burden, not price:

| Option | Extra accounts | Dockerfile? | Deploy pipeline? | Something to watch | Warm? | Cost/mo |
|---|---|---|---|---|---|---|
| **LiveKit Cloud Ship** ✅ | **0** | **No** — `lk agent deploy` | **No** | **Nothing** | ✅ Yes ✅ | **$50.00** ✅ |
| Fly.io, `min_machines_running=1` | +1 | **Yes** ✅ | Yes | Machine health, base image drift, volume | Yes | ~$2–4 ⚠️ **+ LiveKit still needed** |
| Railway | +1 | No (Nixpacks ⚠️) | Yes (git push) | Build breakage, usage billing | Yes | ~$5–10 ⚠️ **+ LiveKit** |
| Render | +1 | No (buildpacks ⚠️) | Yes | Same | Yes | ~$7 ⚠️ **+ LiveKit** |
| Cloudflare Workers | +1 | — | Yes | — | ❌ Not viable — no long-lived process or media stack | — |
| Keep-alive hack on Build | 0 | No | No | **A cron he must monitor** | ❌ Doesn't work — see below | $0 |

**Three reasons none of the self-hosted options beat Ship on burden:**

- **They do not remove LiveKit — they add to it.** SIP trunking, media transport
  and turn detection stay on LiveKit Cloud regardless. You add an account; you
  remove none. Every "cheaper" row above is *plus* a LiveKit account.
- **They all require a build and a deploy step.** LiveKit's own self-hosting docs
  state that deploying to your own infrastructure "generally requires a simple
  Dockerfile that builds and runs an agent server" ✅. Fly.io requires a
  Dockerfile ⚠️; Railway and Render auto-detect Python but still need a connected
  repo and a deploy on every change ⚠️. That is a pipeline that breaks when a base
  image or a buildpack moves — and breaks silently, months later, on a redeploy.
- **The $2.50 inference cap follows you.** Moving the agent process does not move
  STT/LLM/TTS billing. To escape the cap you would bring your own Deepgram +
  Google + Cartesia keys — **three more accounts, three more keys, three more
  things that expire unattended.** That is exactly the architecture this plan was
  written to escape, and it is the single worst outcome under criterion #1.

**Why the keep-alive hack does not work.** Keeping a Build agent warm means
holding a synthetic session open. Agent session minutes are metered at 1,000/month
free ✅; a continuously-held session is ~43,200 minutes/month — **43× over the
cap**, which is a hard stop ✅. Even a 30-second ping every 10 minutes is ~2,160
min/month, still over. And the docs only say agents *"might"* be shut down ✅ —
there is no documented keep-warm contract, so you would be engineering against
undefined behaviour, with a cron job that fails silently. It fails on cost, on
correctness, and hardest on criterion #1.

**Conclusion: Ship is both the warmest and the lowest-burden option. It is the
only row in that table with a zero in every burden column.** The $47/month
premium over the free tier buys the elimination of a Dockerfile, a deploy
pipeline, a machine, and three vendor accounts. At a $100 ceiling that is not a
close call.

## 3.2 Speech-to-text

All options below run **through LiveKit Inference — zero extra accounts.**

| Model | Rate ✅ | Monthly @ 111 min | Bilingual FR/EN? |
|---|---|---|---|
| Deepgram **Nova-3 Multilingual** | $0.0058/min | **$0.64** | ✅ **Yes — chosen** |
| Deepgram Nova-3 Monolingual | $0.0048/min | $0.53 | ❌ No code-switching |
| Deepgram Nova-3 *Medical* | $0.0077/min | $0.85 | Wrong domain |
| Deepgram Flux (Multilingual) | $0.0078/min | $0.87 | Yes |
| AssemblyAI Universal-Streaming-Multilingual | $0.0025/min | $0.28 | Yes — cheapest, untested here |
| ElevenLabs Scribe v2 Realtime | $0.0105/min | $1.17 | Yes |

**Correction:** `COST.md` quotes Nova-3 at **$0.0077/min** — that is **Nova-3
*Medical*** ✅. The multilingual model we need is **$0.0058/min**, a 25%
overstatement in our favour.

**Gave up:** AssemblyAI is less than half the price ✅ (saves ~$0.36/mo). Not worth
switching — Deepgram is trained on telephony audio, and the French-Canadian
accuracy question is unresolved for *both*. Revisit only if the accuracy test
says Deepgram is bad. Since both are inside Inference, switching is a config line.

**Still unvalidated:** French-Canadian STT accuracy. Unchanged from
`ARCHITECTURE_DECISIONS.md`; still worth 20–30 hand-transcribed real calls.

## 3.3 LLM

Gemini 2.5 Flash via LiveKit Inference ✅ — $0.30/1M input, $2.50/1M output,
$0.03/1M cached input. Zero extra accounts.

| | Tokens/month | Cost |
|---|---|---|
| Input (102 × 8 × 3,450) | 2,815,200 | $0.84 ✅ rate |
| Output (~120/turn ⚠️) | 97,920 | $0.24 |
| **Total** | | **$1.09** ⚠️ |

**Correction:** `COST.md` lists **$0.14/month**. The real figure is **~$1.09** —
roughly **8× higher**. The error comes from LiveKit's pricing-calculator default of
*3,000 input tokens per minute* ✅, when this agent runs ~25,000 tokens/minute
(8 turns × 3,450 tokens over a ~1.09-minute average call). **Anyone quoting from
LiveKit's own calculator will underestimate this system's LLM cost ~8×.**

**The lever.** Gemini's *cached* input rate is **10× cheaper** ✅ and our
3,450-token prefix is fixed and identical every turn — exactly what context
caching is for. If it applies, LLM drops from $1.09 to ~$0.32. **⚠️ Unverified
whether LiveKit Inference enables Gemini implicit caching** — one question to
support. Separately, trimming the 3,450-token prompt cuts latency and cost on any
provider, as `ARCHITECTURE_DECISIONS.md` already noted; now quantified.

## 3.4 Text-to-speech — the criterion-1 vs criterion-3 collision

### First, the question that decides it: which options cost an account?

Per the owner's instruction, this is the primary lens:

| Option | Runs via **LiveKit Inference**? | Extra account | Extra key to rotate | Extra dashboard | Genuine fr-CA? |
|---|---|---|---|---|---|
| **Cartesia `sonic-3` (`fr`)** | ✅ **Yes** | **0** | **0** | **0** | ❌ |
| Deepgram `aura-2` (`fr`) | ✅ **Yes** | **0** | **0** | **0** | ❌ |
| ElevenLabs (`fr`) | ✅ **Yes** | **0** | **0** | **0** | ❌ |
| Inworld / Rime / Fish / xAI (`fr`) | ✅ **Yes** | **0** | **0** | **0** | ❌ |
| **Azure AI Speech `fr-CA`** | ❌ **No — plugin only** ✅ | **+1** Azure subscription | +1 Speech key | Azure portal | ✅ **Yes** |
| **AWS Polly `fr-CA`** | ❌ **No — plugin only** ✅ | **+1** AWS account | +1 IAM credential | AWS console | ✅ **Yes** |

LiveKit's TTS documentation states the distinction plainly ✅: models served
through Inference need "**no separate provider API key**… usage and rate limits
are managed through LiveKit Cloud," whereas "**Plugins require your own API key
and account.**" Azure and AWS appear **only** in the plugin list ✅.

**Under criterion #1 this table is close to decisive.** Everything in the top four
rows costs nothing to manage. The bottom two cost a second billing relationship.

### The failure mode that makes the account tax worse than it looks

If the Azure (or AWS) subscription lapses — expired card, spending cap, a key
rotated by Microsoft, a policy change — **French TTS dies while English keeps
working.** The agent still answers the phone. It still sounds healthy. It fails
only on the two-thirds of calls that are French.

That is a **partial, silent failure**, and it is precisely the kind the owner has
told us he will not catch. A single-vendor setup fails loudly and completely, or
not at all. Given "I can't monitor this stuff," a failure mode that *looks fine*
is worse than one that stops.

### The owner's direct question 1: is AWS Polly's Québécois actually good?

**What is verified ✅:**
- Polly's fr-CA voices **Gabrielle** (F) and **Liam** (M) run on the **Generative**
  engine — AWS's newest and largest TTS model, described in AWS's own docs as "a
  billion-parameter transformer" producing "the most human-like, emotionally
  engaged, and adaptive conversational voices" (**vendor claim, not an independent
  benchmark**).
- Both reached general availability on the generative engine on **26 August 2025** ✅.
- A third fr-CA voice, **Chantal**, exists but is **standard-engine only** ✅ — the
  old, robotic tier. Do not use it.
- Generative voices **are available in `ca-central-1` (Montréal)** ✅ — so unlike
  most options, Polly fr-CA could run inside Canada, which helps under Law 25.

**Honest answer: structurally it is the best-positioned fr-CA option on the market
— genuine fr-CA, newest engine, can run in Montréal — but I found no independent
quality benchmark or MOS score for it, and nobody on this project has heard it.**
I will not tell you it sounds good on the strength of AWS marketing copy. It is
plausibly the best-sounding Québécois TTS available; that is as far as the
evidence goes.

You can hear Gabrielle without opening an AWS account — several third-party TTS
demo sites expose Polly voices ⚠️. Worth 5 minutes during the listening test, if
only to calibrate what "good fr-CA" sounds like, even though we are not
recommending AWS.

*Per instruction AWS is excluded by owner preference. What that costs us: the only
generative-engine fr-CA voices on the market. Azure is our fr-CA fallback instead.
I do **not** think the gap justifies overriding him — see the next two sections —
but he should know what the exclusion buys and costs.*

### The owner's direct question 2: what are Azure's downsides?

Concretely, beyond the account itself:

- **The heaviest signup of any vendor here.** An Azure subscription is a Microsoft
  billing relationship with resource groups, regions and a Speech resource to
  create — genuinely more complex than a Cartesia or Deepgram signup, and the
  portal is not friendly to occasional visitors.
- **The free tier is a trap in exactly the way LiveKit Build is.** Azure Speech's
  F0 tier has hard monthly character quotas that stop serving when exhausted ⚠️.
  Production would need the paid S0 tier — another card on file, another bill that
  can fail.
- **Manual key rotation.** Speech resources issue two keys intended to be rotated
  by hand ⚠️. Nobody is going to do that here, which is fine until it isn't.
- **Latency.** LiveKit states that Inference models run "on LiveKit's
  infrastructure to minimize latency" ✅. A plugin call to Azure is an extra hop
  out of that network. **⚠️ The actual added latency is unmeasured** — I will not
  invent a number — but `ARCHITECTURE_DECISIONS.md` already flagged that the
  fr-CA-capable stacks are the slower ones, and every 100ms is audible.
- **The silent partial failure described above.**
- **Split billing and split observability.** Call costs and TTS errors land in two
  places instead of one. LiveKit's agent observability would no longer see the
  whole pipeline.

Azure's fr-CA voices themselves are real and current ✅: `fr-CA-SylvieNeural`,
`fr-CA-JeanNeural`, `fr-CA-AntoineNeural`, `fr-CA-ThierryNeural`, plus higher-end
`fr-CA-Sylvie:DragonHDLatestNeural` and `fr-CA-Thierry:DragonHDLatestNeural`. The
technology is not the problem. The operational tail is.

### The owner's direct question 3: is European French actually a problem?

**Short answer: it is a real effect, but milder and more specific than the fear —
and it is probably not a hang-up driver.** This is the question that decides
whether we keep the simple setup, so here is what the evidence actually supports.

**What is not in doubt:** comprehension is not the issue. Québécois and European
French share enough vocabulary and syntax for ordinary conversation ⚠️, and
Québécois listeners are exposed to European French constantly through media. A
caller booking a table will understand a Parisian voice without difficulty. **The
"they won't understand it" version of this worry is wrong.**

**What the research does suggest** — and this is the part worth taking seriously.
A Montréal study (Mauchand & Pell, *Canadian Journal of Behavioural Science*) ran
an Implicit Association Test plus Stereotype Content questionnaires on Québécois-
and French-accented speech with francophone participants in Montréal ⚠️. The
reported pattern: **European-French speakers were rated comparatively high on
competence but notably lower on warmth**, while Québécois speakers rated
comparatively warmer. Separately, Kircher's work in the *Journal of French
Language Studies* found attitudes toward Quebec French have improved on the
*solidarity* dimension since the 1980s while the *status* dimension has not
changed ⚠️ — i.e. European French still carries more prestige, Quebec French more
in-group warmth.

**⚠️ Heavy caveats, stated because this is easy to over-read:** these studies
measure attitudes toward *human speakers* in an intergroup-psychology setting,
not synthetic voices on a restaurant phone line. Effect sizes do not translate to
hang-up rates. Nobody has studied this for TTS. **This is suggestive context, not
evidence about our agent.**

**What it means for us, concretely.** The risk is not "the caller can't
understand" or "the caller is offended." It is that a Parisian voice may land as
*correct but a bit cold* — competent, slightly formal, not local. For most
transactional purposes that is harmless. For a **restaurant greeting**, where
warmth is much of the point, it is a genuine if modest cost. And notably, it
points the opposite way from prestige: European French is not *lower* status, it
is *less warm*.

**Conclusion: the concern has been somewhat overweighted, but it is not
imaginary.** It is mild, it is specific to warmth, and it is worth about
$0/month to test and one config line to fix. That combination argues strongly for
**shipping the zero-account option and letting the owner's ear decide** — rather
than paying an account tax up front against an effect we cannot size.

### Cost comparison, ~44 TTS-minutes/month

TTS bills **per character**, not per minute ✅. At LiveKit's own 600 chars/min
assumption ⚠️ that is ~26,400 chars/month; at a more realistic ~900 chars/min for
conversational French ⚠️, ~39,600.

| Option | Rate ✅ | @600 c/m | @900 c/m | Extra accounts | fr-CA |
|---|---|---|---|---|---|
| Deepgram Aura-2 (`fr`) | $30/1M chars | $0.79 | $1.19 | **0** | ❌ |
| **Cartesia `sonic-3` (`fr`)** | **$50/1M chars** | **$1.32** | **$1.98** | **0** | ❌ |
| ElevenLabs Flash v2.5 | $150/1M chars | $3.96 | $5.94 | **0** | ❌ |
| ElevenLabs Multilingual v2 | $300/1M chars | $7.92 | $11.88 | **0** | ❌ |
| Azure fr-CA | not via Inference | ⚠️ ~$1–2 | ⚠️ | **+1** | ✅ |
| AWS Polly fr-CA (generative) | not via Inference | ⚠️ ~$1–2 | ⚠️ | **+1** | ✅ |

The entire French TTS decision spans about **$1 to $12 per month** against a $200
incumbent and a $100 ceiling. **Cost is not the deciding variable. Account count
versus accent authenticity is** — and criterion #1 says account count wins unless
the accent is genuinely unacceptable.

*(The current plan's $1.32 Cartesia figure is correct ✅ — but only because
$50/1M chars × 600 chars/min happens to equal $0.03/min. It is a **character**
rate. It scales with how talkative the agent is, not how long calls are, so
trimming verbosity cuts this line directly.)*

### Decision

**Ship Cartesia `sonic-3` on `fr` — zero extra accounts — and gate go-live on the
owner hearing it.**

Because: (a) it costs nothing to manage, which is the dominant criterion;
(b) the accent penalty is real but mild and about warmth, not comprehension;
(c) `config.py` already accepts a `provider/model` override in `AGENT_TTS_MODEL`,
so being wrong costs one line; (d) the owner is the only qualified judge — he
knows his callers.

**⚠️ Gotcha — pin the model.** LiveKit's language table lists `fr` for
`cartesia/sonic-3`, `sonic-3-latest`, `sonic-2`, `sonic` and `sonic-turbo` — but
**not** for `cartesia/sonic-3.5` or `cartesia/sonic-latest` ✅. Pin
**`cartesia/sonic-3`** explicitly. **Never use a `-latest` alias on a French
line** — a silent model roll could drop French support.

**Escalation ladder if he rejects the accent:**

1. **Try the other zero-account French voices first** — Deepgram
   `aura-2-agathe-fr` / `aura-2-hector-fr` ✅, ElevenLabs, Inworld, Rime. No new
   accounts, no code change, config only. **Exhaust this rung completely.**
2. Only if *all* are rejected, add **Azure AI Speech** for French only — one extra
   account, `fr-CA-SylvieNeural` or the DragonHD variant. Measure the latency cost
   and accept the silent-partial-failure risk knowingly.
3. AWS Polly Gabrielle is the strongest fr-CA option on paper but is excluded by
   owner preference. Revisit only if Azure is also rejected.

## 3.5 Telephony

**LiveKit's own numbers cannot be used** ✅. Verbatim from LiveKit's telephony
docs: *"LiveKit Phone Numbers provides access to local and toll-free numbers in
the United States."* The pricing matrix lists only "US local" and "US toll-free"
✅. A Montreal 514/438 number must come from a third-party SIP trunk. **The current
plan's claim is confirmed.**

| Provider | Number/mo | Inbound/min | Monthly @ 111 min | Burden |
|---|---|---|---|---|
| **Twilio** ✅ | **$1.15** | **$0.0045** (local origination) | **$1.65** | Best-documented LiveKit SIP path; configured once |
| Telnyx ⚠️ | ~$1.00 | ~$0.0075 | ~$1.83 | Cheaper number, but Elastic SIP is reportedly channel-priced at **$12/channel/month** ⚠️ — would be far worse. Unconfirmed. |
| Plivo / Vonage | ⚠️ not verified | ⚠️ | ⚠️ | Less LiveKit documentation |

**Correction:** `COST.md` quotes "~$0.0085/min → ~$2.10/mo". The verified rate for
local origination in Canada is **$0.0045/min** ✅ (`ARCHITECTURE_DECISIONS.md` had
this right), giving **$1.65/month**.

**LiveKit also meters third-party SIP minutes** at $0.004/min ✅ — a line the
current plan omits. It costs **$0**: Build includes 1,000 free SIP minutes, Ship
5,000 ✅, against our 111. Stated so it is not a surprise later.

**Decision: Twilio.** Telnyx's possible channel fee could turn a $1.83 line into
$13.83, and the saving if it doesn't is pennies. Not worth investigating at this
volume — and under criterion #1, the better-documented setup path is worth more
than the difference.

## 3.6 Database

`ROADMAP.md` already excludes an operator dashboard and transcript storage from
v1, and `COST.md` already recommends **Option A — push, don't store**. Nothing
found here changes that. **Recommendation: no database.** Captures go out by
SMS/email at capture time; the SMS thread on the manager's phone is the log;
Libro remains the record for reservations. **Zero accounts, zero maintenance** —
the best possible answer under criterion #1.

**But if a database is ever added, the current plan picks the wrong one.**

| Option | Free tier | **Canadian region?** | Verdict |
|---|---|---|---|
| **Supabase** | ⚠️ not verified in detail | ✅ **Yes — `ca-central-1`, "Canada (Central)"**, which is AWS's **Montréal** region ✅ | ✅ **Only option keeping data in Québec** |
| Turso *(current plan)* | 5GB, 500M rows read ⚠️ | ❌ **No** — Tokyo, Mumbai, Ireland, us-east-1/2, us-west-2 ⚠️ | ❌ Drop-in for `store.py`, but cannot host in Canada |
| Neon | 100 CU-hours, 0.5GB ⚠️ | ❌ **No** ✅ — us-east-1/2, us-west-2, eu-central-1, eu-west-2, ap-southeast-1/2, sa-east-1. No `ca-central-1`. | ❌ |
| Cloudflare D1 | ⚠️ not verified | ❌ **No** ✅ — hints are only `wnam, enam, weur, eeur, apac, oc`, and the docs warn a hint **"does not guarantee that D1 runs in your preferred location"** | ❌ Cannot commit to residency at all |

`store.py` holds guest names and phone numbers and says so in its own docstring.
A **Montréal-hosted** database means s.17 of Law 25 is not triggered for storage
at all — strictly less legal work than a US database, which needs a privacy
impact assessment and a written agreement. Turso's advantage was being a drop-in
for `store.py`; that is a few hours of adapter work and does not outweigh
residency.

**Revised: none now; Supabase `ca-central-1` if ever needed.** Storage already
sits behind one module, so this stays cheap to add later.

## 3.7 Quebec Law 25

⚠️ **Not legal advice, and we should not give any.** These obligations belong to
the **restaurant** (the "person carrying on an enterprise"), not to us. Statutory
text is quoted verbatim so it can be handed to counsel.

**Headline: Law 25 does *not* prohibit storing or transferring personal
information outside Québec.** It imposes process requirements. The data
localisation rule in early drafts of Bill 64 is **not** in the enacted law.

**Section 17** ✅, *Act respecting the protection of personal information in the
private sector* (CQLR c. P-39.1), verbatim:

> "**Before communicating personal information outside Québec, a person carrying
> on an enterprise must conduct a privacy impact assessment.** The person must, in
> particular, take into account (1) the sensitivity of the information; (2) the
> purposes for which it is to be used; (3) the protection measures, including
> those that are contractual, that would apply to it; and (4) the legal framework
> applicable in the State in which the information would be communicated…
>
> The information may be communicated if the assessment establishes that it would
> receive **adequate protection**… **The communication of the information must be
> the subject of a written agreement**…
>
> **The same applies where the person carrying on an enterprise entrusts a person
> or body outside Québec with the task of collecting, using, communicating or
> keeping such information on his behalf.**"

That last paragraph is why a Canadian database does not solve this alone.

**On every call, this system sends a caller's name and phone number to US-hosted
processors** — LiveKit (agent + inference), Deepgram (STT), Google (LLM), Cartesia
(TTS), Twilio (telephony). Each is a "person or body outside Québec… keeping such
information on his behalf." **S.17 attaches to the voice pipeline, not merely to
the database.** Choosing Supabase Montréal removes one exposure, not the rest.

Two further sections apply regardless of hosting:

- **Section 3.3** ✅, verbatim: *"Any person carrying on an enterprise must conduct
  a privacy impact assessment for any project to acquire, develop or overhaul an
  information system… involving the collection, use, communication, keeping or
  destruction of personal information."* **Deploying this agent is such a
  project** — a PIA is required even with zero cross-border transfer. The Act adds
  that a PIA *"must be proportionate to the sensitivity of the information"* ✅;
  for names and phone numbers taken to book a table, that is a short document,
  not an audit.
- **Section 8** ✅ — at collection the caller must be informed of, among other
  things, *"the possibility that the information could be communicated outside
  Québec."* **Concrete product consequence:** this likely needs a line in the
  greeting or an equivalent notice. Worth raising with counsel, because the
  greeting is latency-critical — `GREETING_ABANDONMENT.md` exists precisely
  because greeting length costs callers.

**What to do, and stop there:**

1. Flag all three sections to the owner in writing (this document does that).
2. Note that LiveKit's Standard **DPA** is available on all plans including Build
   ✅ — a candidate for the s.17 written agreement, but **whether it satisfies
   Québec's requirement is a legal question we must not answer.**
3. Recommend 30 minutes with Québec privacy counsel before go-live.
4. Keep `DATA_RETENTION.md`'s purge default — minimisation helps under any reading.

**Explicitly not asserted:** whether the vendor set provides "adequate protection"
under s.17; whether a US vendor's standard DPA suffices; whether the restaurant
has a designated person in charge of protection of personal information (s.3.1).
All three need a lawyer.

---

# 4. Management-burden scorecard

The dominant criterion, totalled. What the owner actually has to hold in his head:

| | Recommended stack | Free-tier + BYO keys | Self-host on Fly.io | Vapi / Retell |
|---|---|---|---|---|
| Vendor accounts | **2** (LiveKit, Twilio) | 5 (LiveKit, Twilio, Deepgram, Google, Cartesia) | 3+ | 2 **+ a webhook host** |
| API keys to rotate | **0** | 3 | 3 | 1+ |
| Dashboards to watch | **0** | 3 | 2 | 1 |
| Servers / machines | **0** | 0 | **1** | **1** (for Libro tools) |
| Dockerfile | **No** | No | **Yes** | Depends |
| Deploy pipeline | **No** (`lk agent deploy`) | No | **Yes** | Yes |
| Silent-failure surfaces | **1** | 5 | 3 | 2 |
| Cost/month | **$51.65** | ~$5 | ~$5 + LiveKit | $50+ platform fees ⚠️ |

**The recommended stack is the only column with zeroes across the burden rows.**
That is the case for it, and it costs $47/month more than the cheapest column.

---

# 5. Corrections to the existing documents

| Document | Current claim | Verified reality | Effect |
|---|---|---|---|
| `COST.md` | STT Nova-3 "$0.0077/min" | That is Nova-3 **Medical**; Multilingual is **$0.0058/min** ✅ | −$0.21/mo |
| `COST.md` | LLM "$0.0013/min → $0.14/mo" | **~$1.09/mo** ⚠️ — per-minute figure comes from LiveKit's 3,000-tok/min calculator default; we run ~25,000 tok/min | **+$0.95/mo, ~8×** |
| `COST.md` | Twilio "~$0.0085/min → ~$2.10/mo" | **$0.0045/min → $1.65/mo** ✅ | −$0.45/mo |
| `COST.md` | (omitted) | LiveKit meters **third-party SIP at $0.004/min** ✅; free at our volume | $0 |
| `COST.md` | "Free tier… realistically $2–6/month" | Build **not viable**: 10–20s cold start ✅ **and** a **hard cap** that fails requests ✅ | Total ~$52, not ~$5 |
| `COST.md` | Incumbent "~$250/month (recollection)" | **$200/month, invoice confirmed** | Benchmark is 3.9×, not 40–100× |
| `ARCHITECTURE_DECISIONS.md` | "Deepgram TTS cannot speak French at all — all 58 Aura voices end in `-en`" | **Out of date.** True of Aura-1; **Aura-2 supports `fr`/`fr-FR`** ✅ with `aura-2-agathe-fr` and `aura-2-hector-fr` ✅ | Aura-2 is **40% cheaper** than Cartesia ($30 vs $50/1M chars ✅) and equally zero-account — a live candidate for the listening test |
| `ARCHITECTURE_DECISIONS.md` | LiveKit numbers are US-only | ✅ **Confirmed** | none |
| `ARCHITECTURE_DECISIONS.md` | "Free tiers are a liability, not a saving" | ✅ **Strongly confirmed** — Build's allowance is a hard cap that fails requests silently | Reinforces Ship |
| `ARCHITECTURE_DECISIONS.md` | Vapi "replaces the voice loop, not the ops" | ✅ **Confirmed and sharpened** — with LiveKit Cloud the Libro code runs *inside* the hosted agent; with Vapi it must be separately hosted webhooks | Strengthens the existing decision |
| `COST.md` | Turso if history is wanted | Turso has **no Canadian region** ⚠️; **Supabase does** (`ca-central-1`, Montréal) ✅ | Switch the fallback |

---

# 6. What I could **not** establish

1. **Whether the French sounds acceptable to a Québécois caller.** Not testable
   from a terminal. Nobody on this project has heard any of these voices. **The
   largest open item in this document.** Needs the owner, a browser, 20 minutes.
2. **Any independent quality benchmark for AWS Polly fr-CA or Azure fr-CA.** Found
   none. §3.4 reports engine generation and vendor claims, clearly labelled as such.
3. **Whether LiveKit Inference enables Gemini implicit context caching.** Worth
   ~$0.77/month and some latency. One support question.
4. **Added latency from an Azure TTS plugin call vs LiveKit Inference.** Unmeasured;
   deliberately not estimated.
5. **Real-world TTS character rate.** Used LiveKit's 600 chars/min assumption ⚠️
   with a 900 chars/min sensitivity. Measurable from one call's logs; moves the
   TTS line by up to ~50%.
6. **Output tokens per turn.** Estimated ~120 ⚠️. Measurable in one session.
7. **Turso's exact region list.** Platform API needs auth; the locations doc 404s.
   Region list is secondary-source ⚠️. Does not change the recommendation —
   Supabase's Canadian region is verified and Turso's absence from Canada was
   consistent across every source found.
8. **Free-tier details for Supabase, Cloudflare D1, Azure Speech F0, and Fly.io
   compute pricing.** JavaScript-rendered pricing pages; no reliable figures.
   Marked ⚠️ throughout. None is on the recommended path.
9. **Telnyx / Plivo / Vonage Canadian rates.** Secondary sources only ⚠️. The
   Telnyx channel-fee question is unresolved. Immaterial at 111 min/month.
10. **Whether Ship's $50 is flat.** LiveKit's pricing page says **"STARTING AT
    $50/mo"** ✅ and I could not determine what raises it. Our usage is far inside
    every included allowance, so $50 should be the real figure — **confirm at
    signup.** If it meters upward for a reason we haven't found, the total moves.
11. **Any legal conclusion under Law 25.** Deliberately not attempted — §3.7.

---

# 7. What to do next

1. **Sign up for LiveKit Ship**; confirm $50 is flat for our usage (§6.10).
2. **Play the owner French samples** — Cartesia `sonic-3`, Deepgram
   `aura-2-agathe-fr`, and for contrast an Azure `fr-CA-SylvieNeural` and a Polly
   Gabrielle clip from a third-party demo site. Let him pick. **Gate go-live on
   this.** Criterion #3, 20 minutes.
3. **Buy the Twilio 514/438 number** ($1.15/mo), point it at LiveKit SIP. Set once.
4. **Ask LiveKit support** whether Inference enables Gemini implicit caching.
5. **Get 30 minutes of Québec privacy counsel** on ss. 3.3, 8, 17. Bring §3.7.
6. **Do `ROADMAP.md` Phase 1** — one real voice booking — before believing any
   number here. Then measure real TTS characters and output tokens; update §6.5–6.6.

---

# Sources

**LiveKit**
- [Quotas and limits](https://docs.livekit.io/deploy/admin/quotas-and-limits/) — cold starts ("10 to 20 seconds"), Build hard cap, all Build allowances, agent session concurrency
- [Pricing](https://livekit.com/pricing) · [machine-readable](https://livekit.com/pricing.md) — plan matrix, cold-start prevention by tier, all Inference model rates, calculator assumptions
- [TTS models overview](https://docs.livekit.io/agents/models/tts/) — per-model language tables (no `fr-CA` anywhere), Inference vs plugin distinction, plugin provider list
- [Deepgram TTS via Inference](https://docs.livekit.io/agents/models/tts/deepgram/) — `aura-2` supports `fr`, `fr-FR`
- [Telephony introduction](https://docs.livekit.io/telephony/) — LiveKit Phone Numbers are US-only
- [Self-hosted deployments](https://docs.livekit.io/deploy/custom/deployments/) — self-hosting "requires a simple Dockerfile"

**Speech vendors**
- [Deepgram TTS models](https://developers.deepgram.com/docs/tts-models) — `aura-2-agathe-fr`, `aura-2-hector-fr`; no fr-CA
- [Azure AI Speech language support](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/language-support?tabs=tts) — `fr-CA-SylvieNeural`, `fr-CA-JeanNeural`, `fr-CA-AntoineNeural`, `fr-CA-ThierryNeural`, `fr-CA-Sylvie:DragonHDLatestNeural`, `fr-CA-Thierry:DragonHDLatestNeural`
- [AWS Polly available voices](https://docs.aws.amazon.com/polly/latest/dg/available-voices.html) — fr-CA: Chantal (standard), Gabrielle and Liam (neural + generative)
- [AWS Polly generative voices](https://docs.aws.amazon.com/polly/latest/dg/generative-voices.html) — engine description; region list including `ca-central-1`
- [Amazon Polly launches more synthetic generative voices](https://aws.amazon.com/about-aws/whats-new/2025/08/amazon-polly-new-synthetic-generative-voices/) — Gabrielle and Liam GA on the generative engine, 26 Aug 2025

**Telephony**
- [Twilio SIP Trunking pricing — Canada](https://www.twilio.com/en-us/sip-trunking/pricing/ca) — local number $1.1500/mo; local origination $0.0045/min; toll-free origination $0.0130/min

**Database**
- [Neon regions](https://neon.com/docs/introduction/regions) — no Canadian region
- [Supabase regions](https://supabase.com/docs/guides/platform/regions) — `ca-central-1`, "Canada (Central)"
- [Cloudflare D1 data location](https://developers.cloudflare.com/d1/configuration/data-location/) — hints `wnam, enam, weur, eeur, apac, oc`; not guaranteed

**Law**
- [Act respecting the protection of personal information in the private sector, CQLR c. P-39.1](https://www.legisquebec.gouv.qc.ca/en/document/cs/P-39.1) — ss. 3.3, 8, 17, quoted verbatim from the official Légis Québec text

**Accent attitudes (⚠️ context only — human speakers, not TTS; do not over-read)**
- Mauchand & Pell, "French or Québécois? How speaker accents shape implicit and explicit intergroup attitudes among francophones in Montréal," *Canadian Journal of Behavioural Science* — [record](https://www.researchgate.net/publication/355567475_French_or_Quebecois_How_speaker_accents_shape_implicit_and_explicit_intergroup_attitudes_among_francophones_in_Montreal)
- Kircher, "How pluricentric is the French language? An investigation of attitudes towards Quebec French compared to European French," *Journal of French Language Studies* — [record](https://resolve.cambridge.org/core/journals/journal-of-french-language-studies/article/how-pluricentric-is-the-french-language-an-investigation-of-attitudes-towards-quebec-french-compared-to-european-french1/405D10D31EC388224D48A4A9DD02916B)

**Marked ⚠️ estimate, no primary source obtained:** Fly.io / Railway / Render
compute pricing and buildpack behaviour; Turso region list and free-tier limits;
Supabase, Cloudflare D1 and Azure Speech F0 free-tier limits; Telnyx / Plivo /
Vonage Canadian rates; TTS characters per minute; LLM output tokens per turn;
Azure plugin latency.
