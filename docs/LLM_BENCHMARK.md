# The 3.55s first token: a benchmark harness, and what we can prove without it

> **Historical record. We ship Gemini 2.5 Flash, not Groq.** This document argues
> for keeping `llama-3.3-70b-versatile` and moving to a paid Groq plan. That
> question was overtaken: Gemini is cheaper at this venue's volume and usable
> without the paid tier the Groq argument depended on.
>
> The *findings* below still stand and are worth keeping — the binding free-tier
> limit is tokens rather than requests, and downgrading to an 8B model makes
> throttling worse because it has half the TPM. The *recommendation* is dead.
> `scripts/benchmark_llm.py` still runs and still targets Groq; it is the harness
> that produced this record, not current configuration.
>
> Current measured latency, on Gemini, on real calls: **1.16–1.48s TTFT** against
> a 200–700ms budget. Still over. Still unfixed.

**Status: the benchmark has NOT been run. There are no API keys in this environment.**

There are no measured TTFT numbers in this document, because inventing them would be
worse than having none. What follows is (a) a harness that produces those numbers the
moment a key exists, (b) the facts I *could* establish offline, and (c) an argument —
with a stated confidence level — about what the 3.55s actually is.

Everything below is labelled. **[MEASURED]** = I ran it and observed it.
**[VENDOR-DOCUMENTED]** = stated in a provider's own technical docs. **[VENDOR MARKETING]**
= a promotional claim, treated as unreliable. **[DERIVED]** = arithmetic on the two above.
**[UNVERIFIED]** = secondary source I could not confirm at the primary.

---

## 1. What was built

`scripts/benchmark_llm.py` — one file, no new dependencies (`httpx` is already a core
dependency), runnable with or without keys.

Per candidate model it measures:

| Metric | Why it's the one that matters |
|---|---|
| **Time to first token** | The caller hears silence until this fires. The whole budget. |
| **Total completion time** | Bounds when TTS can finish; secondary for voice. |
| **Tool selected** | The agent's entire job. Graded against an accept-list *and a forbid-list*. |
| **Tool arguments** | A right tool with a wrong `party_size` is still a wrong answer. |
| **`queue_time` / `prompt_time` / `completion_time`** | **The crux.** Groq returns these per request. They separate *waiting* from *computing*. |
| **HTTP 429 count** | Direct evidence of rate limiting. |

It reports **p50 / p90 / worst-case**, never a bare average, because a model with a
good mean and a 3s p90 is unusable on a phone.

### Running it

```bash
python scripts/benchmark_llm.py --list           # the 24 prompts, no key needed
python scripts/benchmark_llm.py --schemas        # the tool schemas it will send
python scripts/benchmark_llm.py --system-prompt  # the real system prompt + its size

export GROQ_API_KEY=...                          # never hardcoded; read from env only
python scripts/benchmark_llm.py --repeat 5 --sleep 3 --json out/bench.json
python scripts/benchmark_llm.py --model groq-70b --repeat 10 --sleep 0   # provoke queueing
```

With no keys it prints which env vars are missing and **exits 0** — safe to commit, safe
in CI, and immediately useful later.

### Candidates supported

| Key | Model | Env var |
|---|---|---|
| `groq-70b` | `llama-3.3-70b-versatile` | `GROQ_API_KEY` |
| `groq-8b` | `llama-3.1-8b-instant` | `GROQ_API_KEY` |
| `openai-4o-mini` | `gpt-4o-mini` | `OPENAI_API_KEY` |
| `claude-haiku` | `claude-haiku-4-5` | `ANTHROPIC_API_KEY` |

Adding a fifth is one entry in the `CANDIDATES` tuple, plus an adapter only if the wire
format is new.

### The prompt set: 24 cases, 6 French

Drawn from this agent's real job, using the **real system prompt** from
`resto_agent.prompts` and the **real tool schemas**, which the script extracts by parsing
the AST of `src/resto_agent/tools.py`. Parsing rather than importing means the schemas
cannot drift from the agent, and the benchmark runs without the heavy `[agent]` extras.

Coverage: booking with an explicit time · relative date (`tomorrow evening`) · relative
weekday · the ambiguous-hour PM rule (`book us for one`) · commit-to-booking after an
explicit yes · a fully-booked cascade · the waitlist step after that cascade · takeout ·
delivery · two FAQ topics · the deliberately-absent cancellation policy · **a party of
eight that must escalate** · lookup · cancel · reschedule · an allergy note that must
survive into `book_reservation` · a mid-call "okay, thanks" that must **not** be treated
as a goodbye · and six French cases covering booking, relative dates, takeout, FAQ, large
party, and cancellation.

Two grading details that matter:

- Multi-turn fixtures. You cannot test `book_reservation` from a cold open — the model
  needs a prior availability result to quote an ISO slot from. Those cases carry the
  assistant turn and the tool result.
- **Forbid-lists, not just accept-lists.** For the party of eight, *not booking* is the
  requirement. `book_reservation`, `check_availability` and `join_waitlist` are all hard
  failures there (`MAX_ONLINE_PARTY = 6` is a Libro API ceiling — 7+ literally cannot be
  checked). Accepting "no tool call" or `take_message` reflects the real requirement.

### Verification performed

I could not call a real API, so I verified the harness against local mock servers:

- **[MEASURED]** Groq-shaped SSE: streaming parse, fragmented tool-call argument
  reassembly, `x_groq.usage` capture, rate-limit headers, and TTFT correctly **excluding**
  a leading role-only chunk that carries no content (329ms measured against a 300ms
  simulated delay).
- **[MEASURED]** HTTP 429 path recorded as a failed sample with its status preserved, and
  counted separately in the summary.
- **[MEASURED]** Anthropic Messages API path: the different wire format
  (`content_block_start` / `input_json_delta`), the OpenAI→Anthropic message translation
  (role alternation preserved through tool turns), and token accounting.
- **[MEASURED]** Grading: correct call passes; wrong `party_size`, non-ISO time, wrong
  name and missing phone are each reported individually; a forbidden tool fails even
  when well-formed.

The Anthropic adapter has still never touched the real endpoint. Treat its first live run
as unproven.

---

## 2. What I established without running it

**[MEASURED]** The fixed per-turn prompt overhead:

| Component | Size |
|---|---|
| System prompt (`system_instructions`, multilingual, with FAQ digest) | 7,829 chars ≈ **1,995 tokens** |
| 9 tool schemas as sent on the wire | ≈ **1,457 tokens** |
| **Fixed prefix, resent on every single turn** | **≈ 3,450 tokens** |

(Estimated at 4 chars/token; the harness records the provider's exact `prompt_tokens`.
A mock returning 3,455 was consistent with the estimate.)

This number is the hinge of the entire analysis, for two reasons:

1. **[VENDOR-DOCUMENTED]** Groq: *"Input token count is the primary driver of TTFT
   performance"*, with *"70B and above: exponential TTFT increases at maximum context"*.
   Our prompt is ~3.5x the 1k-token workload the published benchmarks use (below).
2. It is spent **again on every turn**, because — see §3 — Groq's prompt caching does not
   cover this model.

---

## 3. The crux: free tier vs paid

This is the question worth getting right, because the two answers point in opposite
directions. If 3.55s is inference, we need a faster model and we pay in tool accuracy. If
it is queueing, the fix is a paid plan on the *same* model, and downgrading is a pure loss.

### 3a. What Groq's own documentation says

**[VENDOR-DOCUMENTED]** (console.groq.com, retrieved 2026-07-25):

> `TTFT = Queueing Time + Prompt Prefill Time`

Groq itself decomposes the number we care about into a queueing term and a compute term.
It returns both per request: every response carries `queue_time`, `prompt_time`,
`completion_time` and `total_time` under `x_groq.usage`. **This means the question is
directly answerable with a single instrumented run** — no inference required.

**[VENDOR-DOCUMENTED]** The default service tier, `on_demand`, is described as giving
*"the predictable high speeds of Groq's LPU **with occasional queue latency during peak
times**"*. Queueing on the default tier is documented behaviour, not speculation.

**[VENDOR-DOCUMENTED]** Free-plan limits, from Groq's rate-limits page:

| Model | RPM | RPD | **TPM** | TPD |
|---|---|---|---|---|
| `llama-3.3-70b-versatile` | 30 | 1,000 | **12,000** | 100,000 |
| `llama-3.1-8b-instant` | 30 | 14,400 | **6,000** | 500,000 |

**[VENDOR-DOCUMENTED]** *"You can hit any limit type depending on which threshold you
reach first."*

**[VENDOR-DOCUMENTED]** Prompt caching — which would otherwise make our 3,450-token
prefix nearly free — **is supported only on `openai/gpt-oss-20b`, `gpt-oss-120b` and
`gpt-oss-safeguard-20b`**. It does **not** cover either Llama model. So every turn
re-prefills and re-bills the full prefix. (Cached tokens also would not count against
rate limits — a benefit we cannot currently access on Llama.)

### 3b. The arithmetic — and this is where it gets damning

**[DERIVED]** At ~3,450 tokens per turn against a 12,000 TPM cap:

> **12,000 ÷ 3,450 ≈ 3.5 requests per minute** before the token cap binds.

The requests-per-minute cap is 30. **The token cap binds roughly 8.6x sooner than the
request cap.** Anyone reasoning about Groq's free tier from "30 req/min" — as this repo's
own code comments do — is looking at the wrong limit entirely.

Now compare that to what a phone call actually demands. A conversational turn every
5–10 seconds is **6–12 requests per minute**. A live call therefore needs roughly
**2–3.5x the token budget the free tier allows.** Sustained over a call, the excess must
go somewhere: queue time, or 429s.

**[DERIVED]** The daily cap is worse. `docs/REAL_CALL_FINDINGS.md` records 84 calls over
30 Jun – 25 Jul 2026 — **~3.2 calls/day**, with the hardest observed call running 15 turns.
Including conversation-history growth, a 12-turn call costs roughly 12 × ~4,000 ≈
**48,000 tokens**. Against a 100,000 TPD cap:

> **The free tier affords about two calls per day. The venue takes about three.**

The free tier cannot serve this restaurant's *actual, measured* volume — not at peak, on
average. That is independent of latency and is on its own disqualifying.

### 3c. A specific, testable explanation for 3.55s

**[MEASURED]** `livekit-agents` 1.6.6, installed here, defaults to:

```python
DEFAULT_API_CONNECT_OPTIONS = APIConnectOptions(max_retry=3, retry_interval=2.0, timeout=10.0)
```

**[DERIVED]** If a turn is rejected or stalls and is retried after the 2.0s interval, and
the retry then completes normally in ~1.5s, the observed end-to-end first-token time is
**≈ 3.5s**. That lands on the measured 3.55s closely enough to be worth taking seriously
— and it is a *discrete* failure (one retry), which fits a **worst-case** number sitting
far above a normal-case one, much better than inference slowness would. Inference slowness
scales smoothly; retries produce exactly this kind of bimodal outlier.

This is a hypothesis consistent with the arithmetic, **not proof**. I did not have the
original logs, and I could not confirm whether the 3.55s figure was captured from
LiveKit's `LLMMetrics.ttft` (which may or may not span a retry) or measured another way.
**Finding that measurement's provenance is cheap and should be step one.**

### 3d. Published benchmarks, and why they don't apply to us

**[UNVERIFIED — secondary aggregators]** Artificial Analysis puts Groq's Llama 3.3 70B at
**~0.95s TTFT**, fastest-tier among providers. I could not fetch artificialanalysis.ai
directly (HTTP 403), so this is second-hand.

**[VENDOR-DOCUMENTED]** What *is* confirmable is the methodology: Artificial Analysis
measures with a **1,000-token input** and 100-token output, tested 8 times a day. Our
workload is **~3,450 input tokens plus 9 tool schemas** — 3.5x the input, on a model
family Groq itself describes as having *exponential* TTFT growth with context. **The 0.95s
figure is not a prediction for our prompt.** Part of the gap between 0.95s and 3.55s is
plainly prompt size, before any queueing.

**[VENDOR MARKETING]** The "sub-100ms first token" claim — which appears in this repo's
own `src/resto_agent/agent.py` docstring — I could not substantiate for any 70B model at any
prompt size. Groq's own materials say "sub-second". Treat sub-100ms as marketing that has
leaked into our code comments.

### 3e. What paying actually buys

**[VENDOR-DOCUMENTED]** The Developer plan grants higher limits plus access to Flex
processing (*"10x their current rate limits"*) and Batch. **[UNVERIFIED]** Secondary
sources put Developer-tier `llama-3.3-70b-versatile` at ~1,000 RPM and 250K–300K TPM;
Groq's own Developer table is behind a JS tab I could not fetch, so treat the specific
numbers as unconfirmed. The *direction* — a large multiple of the free tier — is
vendor-documented.

Critically: paying raises limits. **It does not make the model's inference faster.** If
`queue_time` turns out to be near zero and `prompt_time` is the whole story, upgrading the
plan will not help at all. That is exactly why the measurement below must come first.

### 3f. What I could NOT establish

Stated plainly:

- **No measured TTFT for any candidate.** No keys. Every latency number here is either
  someone else's or arithmetic.
- **No confirmation that our 3.55s included queue time.** Groq's `queue_time` was almost
  certainly available in those responses but was not recorded.
- **No community corroboration.** I searched specifically for reports of multi-second
  free-tier TTFT on Groq and found none. The absence of a public pattern is a genuine
  point *against* the queueing hypothesis and I am not going to bury it.
- **No verified Developer-tier limits** for our specific model (primary source not fetchable).
- **No verified BFCL scores** (gorilla.cs.berkeley.edu renders its table via JS).

---

## 4. The 8B downgrade is worse than the brief assumes — on two independent counts

**Count one: the accuracy premise cites the wrong model.**

The brief compares **Llama-3-8B (~58.9%)** against GPT-4o-mini (~83.4%) — a 24-point gap.
But the candidate we would actually deploy is **`llama-3.1-8b-instant`**, a different and
newer model. **[UNVERIFIED — secondary aggregator]** BFCL figures for the 3.1 generation
are roughly **Llama 3.1 8B Instruct ~76.1%** and **Llama 3.1 70B Instruct ~84.8%**.

If those hold, the real 70B→8B tool-accuracy cost is **~9 points, not 24**. That is still
a real cost and still probably not worth paying — but the argument against downgrading
should rest on a number that describes the model we would actually ship. This is precisely
why the harness grades tool *and* argument correctness on our own 24 cases: BFCL is a
generic benchmark, and 24 real cases from this venue beat it for this decision.

**Count two — and this one is decisive: on the free tier, the 8B model has HALF the token
budget.**

**[VENDOR-DOCUMENTED]** `llama-3.1-8b-instant` free tier: **6,000 TPM**, versus 12,000 TPM
for the 70B. **[DERIVED]** 6,000 ÷ 3,450 ≈ **1.7 requests per minute**.

> If the 3.55s is token-per-minute throttling, **switching to the 8B model makes it
> strictly worse** — half the budget, same 3,450-token prompt, same call cadence. We would
> trade away tool accuracy *and* get more queueing.

That is the trap in the naive fix, and it is invisible unless you look at TPM rather than
RPM.

---

## 5. Recommendation

> **Do not change the model. Move Groq to a paid Developer plan, keep
> `llama-3.3-70b-versatile`, and re-measure with `queue_time` recorded.**
>
> **Confidence: moderate — about 70%** that queueing/rate-limiting is the dominant term in
> the 3.55s worst case, rather than model inference speed.

Why 70% and not higher:

- Groq's own docs define `TTFT = Queueing Time + Prefill Time` and describe queue latency
  on the default tier. **(supports)**
- The TPM arithmetic shows a live call demanding 2–3.5x the free-tier token budget.
  **(strongly supports)**
- The 2.0s LiveKit retry interval reproduces ~3.5s almost exactly, and explains why this
  is a *worst case* rather than a typical case. **(supports)**
- The free tier's 100K TPD cannot serve the venue's measured ~3.2 calls/day regardless.
  **(supports the recommendation even if it doesn't support the latency hypothesis)**

Why not higher than 70%:

- Our prompt is 3.5x the benchmark workload, on a model Groq says scales *exponentially*
  with context at 70B. A meaningful share of the gap is plainly prefill, not queue — and
  **paying does not reduce prefill.**
- No prompt caching on Llama models, so that prefill is paid every turn and cannot be
  optimised away on Groq without changing model family.
- I found no independent reports of multi-second free-tier TTFT on Groq.

This recommendation is also **robust to being wrong about the latency**: the free tier
cannot serve this venue's real daily volume, and `ARCHITECTURE_DECISIONS.md` already
commits to "free tiers are a liability, not a saving" and "production runs on paid plans
with a card on file." Upgrading is required regardless of what causes the 3.55s. It costs
a couple of dollars a month at this volume. **Do it first, then measure.**

### The measurement that settles it

One run, and the question is closed:

```bash
export GROQ_API_KEY=...
python scripts/benchmark_llm.py --model groq-70b --repeat 5 --sleep 0 --json out/free.json
```

Read the **"Server-side breakdown"** table:

| If… | Then | Do |
|---|---|---|
| `queue p90` is a large fraction of `TTFT p90` (say >40%), and/or 429s appear | **Rate limiting.** Confirmed. | Upgrade the plan. Keep the 70B. Re-run to confirm queue collapses. |
| `queue p90` ≈ 0 and `prefill p50` dominates | **Inference/prompt size.** The plan won't help latency. | Cut the prompt (§6), then re-benchmark 8B and gpt-4o-mini for real, on our 24 cases. |

Then run the identical command on the paid plan and diff the two JSON files. **Same
prompts, same model, same code — only the plan changes.** That is a clean controlled
experiment and it is the only thing that will actually settle this.

Run with `--sleep 0` (bursty, like a real call) and again with `--sleep 3` (throttled
below the TPM cap). If TTFT is fine at `--sleep 3` and bad at `--sleep 0`, that is the
rate-limit hypothesis confirmed on its own, without even needing the paid comparison.

---

## 6. Recommendations for files I did not touch

Per scope, I changed no existing file. These are the changes I would make:

1. **`src/resto_agent/agent.py`** — the `_build_llm` docstring says Groq free is
   *"30 req/min, ~14,400/day"* and *"Sub-100ms first token"*. Both are wrong for our
   model: `llama-3.3-70b-versatile` free is **1,000 RPD** (14,400 is the *8B* figure), and
   the binding constraint is **12,000 TPM**, not RPM. Sub-100ms is unsubstantiated
   marketing. This comment is actively misleading the model decision.

2. **`src/resto_agent/agent.py`** — set `service_tier` explicitly on the Groq client.
   `auto` uses on-demand limits then falls back to Flex, which is the right behaviour for
   a phone line on a paid plan. Also consider passing `APIConnectOptions` with a shorter
   `retry_interval`: 2.0s of silence mid-call is worse than a graceful failure, and the
   agent already has a `_FALLBACK` line for exactly that.

3. **Prompt size is the one lever that helps under either hypothesis.** ~3,450 tokens
   resent per turn is large. The FAQ digest (`_faq_digest()`) inlines all 21 topics into
   the system prompt, but the agent already has an `answer_faq` tool that fetches them.
   That is the same data twice. Cutting it would reduce prefill time *and* TPM pressure
   simultaneously — and unlike everything else here, it helps whether the bottleneck turns
   out to be queueing or compute. Worth a benchmark of its own; the harness will measure it.

4. **`docs/ARCHITECTURE_DECISIONS.md`** — the "Open decision — the model itself" section
   says a smaller model would be "faster *and* cheaper *and* less prone to timeouts —
   three of three on our stated criteria." On the free tier that is **false on the third
   count and possibly the first**: the 8B model has half the TPM. Worth correcting once
   the benchmark settles it.

5. **`pyproject.toml`** — nothing required. The harness deliberately uses only `httpx`,
   already a core dependency, so it runs in the same environment as the test suite.
