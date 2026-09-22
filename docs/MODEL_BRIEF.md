# Model selection brief — for external deep research

Paste this whole file into the research tool. It is written to be self-contained.

---

## What the system is

A bilingual (Québec French / English) voice AI phone agent for **one restaurant**
in Montreal. It answers the phone, checks availability, books / cancels / changes
reservations in the restaurant's real booking system (Libro), answers FAQs, and
captures callers it can't help. It runs unattended.

**We are choosing the LLM.** Everything else is decided.

## The model's actual job — this is narrower than "be smart"

The LLM does **only two things**:

1. **Converse** — short, natural phone replies, in the caller's language.
2. **Call the right tool with the right arguments.**

It does **no** reasoning about dates, times, phone numbers, party-size limits, or
availability. All of that is deterministic Python by design. So raw reasoning
benchmarks (MMLU, GPQA, math) are close to irrelevant to us.

**What matters, in order:**

1. **Tool-calling accuracy** — right tool, right arguments. A wrong argument
   books a real stranger at a real restaurant on the wrong night.
2. **Time to first token (TTFT)** — see budget below.
3. **French quality** — not just comprehension: replies must read naturally in
   French, including spoken times ("dix-huit heures trente"). Two-thirds of this
   venue's calls are French.
4. **Instruction adherence under a long prompt** — brevity rules, never claiming a
   booking before the tool returns, never inventing facts.
5. Cost — genuinely last. Budget is ~$50/month total and the LLM is a small part.

## Hard numbers (all measured on this system, not estimated)

| | |
|---|---|
| Fixed prompt resent **every turn** | **≈ 3,690 tokens** (system prompt 1,997 + 9 tool schemas 1,689, measured from the real wire payload) |
| Turns per call | ~8 |
| Effective rate during a live call | **~25,000 tokens/minute** |
| Call volume | 102 calls/month, 111 talk-minutes/month, median call 54s |
| Concurrency | Essentially 1. A tiny restaurant; simultaneous calls are rare. |

## The latency budget

End-to-end target is **under 1 second** from caller stopping to hearing a reply.
After VAD, STT, TTS and network, the LLM's share is roughly:

- **200–700ms TTFT** — the usable window
- **>700ms** reads as an unnatural pause
- **>1s** measurably increases hang-ups

Note **TTFT is the metric, not tokens/sec.** Once audio starts playing, generation
speed rarely binds — the caller is already listening.

## What we already investigated, and the conclusions to test

We measured **3.55s worst-case TTFT** on `llama-3.3-70b-versatile` via Groq's free
tier — roughly 5× over budget. Investigation produced three findings:

1. **The binding free-tier limit was tokens, not requests.** Groq free tier gives
   **12,000 TPM** for that model. At 3,450 tokens/turn that is **~3.5 requests per
   minute**, while a live call needs 6–12. The 30 RPM cap never binds.
2. **Downgrading to `llama-3.1-8b-instant` would have been worse** — it has
   **6,000 TPM**, *half*. The naive "use a smaller model" fix is invisible-ly
   backwards unless you look at TPM rather than RPM.
3. **A plausible mechanism for the exact 3.55s:** the LiveKit Agents SDK defaults
   to `retry_interval=2.0`. One retry (2.0s) plus a normal ~1.5s generation ≈
   3.5s — and a discrete retry explains a *bimodal* worst case better than
   inference slowness, which scales smoothly.

**Working hypothesis (~70% confidence, NOT verified): the 3.55s was rate-limit
queueing, not model speed.** Confidence is held down because no independent
reports of multi-second free-tier Groq TTFT could be found.

**We never actually ran the benchmark** — no API keys were available. There are no
measured TTFT numbers for any candidate. A harness exists
(`scripts/benchmark_llm.py`) that measures TTFT, total time, tool selection and
argument correctness across providers, and records Groq's `queue_time` /
`prompt_time` / `completion_time` fields, which decompose TTFT directly.

## Constraints that shape the answer

- **We self-host the agent and use our own API keys.** So we are *not* limited to
  any one platform's model catalog. Any provider with an API is a candidate.
- Runtime is **Python, livekit-agents 1.6.6**, which speaks the OpenAI-compatible
  chat-completions API natively and has first-party plugins for the major
  providers. An OpenAI-compatible endpoint is the path of least resistance.
- Prompt caching would materially change the economics, since **3,450 tokens are
  re-sent every turn**. Groq's caching covers only `gpt-oss-*` models, not Llama.
  Which providers cache, and on which models, is a live question for us.
- Streaming is required.
- We are cost-insensitive within reason (~$50/month total budget).

## What we want the research to answer

1. **Which models are actually best at tool calling** — right tool *and* right
   arguments — at TTFT under ~700ms? Prefer current, verifiable benchmark data
   (e.g. BFCL v4, τ-bench) with sources. Note: we could not verify BFCL scores
   directly because the leaderboard renders via JavaScript.
2. **Real-world TTFT figures by provider and model**, ideally p50/p90, on paid
   tiers — for a ~3,500-token prompt with ~9 tool definitions, not a toy prompt.
3. **Does a large tool-schema payload disproportionately hurt certain providers?**
   Our prompt is ~40% tool schemas.
4. **Which providers offer prompt caching that would apply to a fixed 3,450-token
   prefix**, and what it does to latency and cost.
5. **French quality for conversational generation** — is there a meaningful gap
   between frontier models on natural spoken French? Québécois register ideally,
   but European French is acceptable to us.
6. **Is the "small model = worse tool calling" assumption still true in 2026?**
   Small models have improved fast. If a small, fast model now matches a large one
   on tool calling, that is the ideal answer for a voice agent.
7. **Any provider-specific gotcha** for voice: rate-limit structures that bite
   during a burst of turns, cold starts, regional latency from eastern Canada.

## What "good" looks like for us

> The **smallest, fastest** model that reliably picks the right tool with the
> right arguments and speaks natural French. Not the highest-scoring model on
> general benchmarks.

## Explicitly not needed

Long context (our prompt is small and fixed) · vision · code generation ·
advanced reasoning · agentic multi-step planning (our flows are short and
scripted) · fine-tuning.
