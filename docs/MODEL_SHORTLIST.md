# Model shortlist for the harness — primary sources only

**Date:** 2026-07-29 · **Scope:** choose 3–4 models to run on `scripts/benchmark_llm.py`
against the 24 real cases. This document does **not** pick the model. It picks what to test
and says why.

Every figure below carries a source URL and a source-type tag. Tags used:

| Tag | Meaning |
|---|---|
| **[VENDOR]** | The provider's own documentation, pricing page, or model list |
| **[BFCL]** | The Berkeley Function Calling Leaderboard's own score data file |
| **[3P-MEAS]** | Artificial Analysis — independent measurement org, not vendor-official |
| **[PEER]** | arXiv / ACL / peer-reviewed |
| **[DERIVED]** | Arithmetic on figures above, shown |
| **[NOT FOUND]** | No acceptable primary source exists. Stated, never filled in. |

No number in this document comes from an aggregator. Where an aggregator was the only
source, the row says **NOT FOUND** instead.

---

## 1. The shortlist

| # | Model | Provider / endpoint | One-line reason | **What we are testing it FOR** |
|---|---|---|---|---|
| 1 | `gemini-2.5-flash` | Google Gemini API | The only candidate where **both** a sub-700ms TTFT *and* a prompt cache that actually applies to our 3,690-token prefix are established from acceptable sources. | **The default hypothesis.** Does the one model that clears both hard constraints on paper also pick the right tool with the right arguments? If yes, we stop. |
| 2 | `claude-haiku-4-5` | Anthropic Messages API | Highest BFCL v4 score of any small model by a wide margin — rank 6 overall, ahead of Gemini 3 Pro (FC) and every OpenAI model. | **The accuracy ceiling.** Two vendor-documented penalties (1.06s TTFT, prompt caching that *cannot fire* at our prefix size) say it should lose. We are testing whether that accuracy lead is real on our 24 cases and whether the latency penalty survives contact with our actual 3,690-token prompt. |
| 3 | `qwen3.7-plus` | Alibaba Model Studio / DashScope, Singapore (`ap-southeast-1`) | The Qwen family holds the **best Live Acc (82.01%) and best Non-Live AST (88.77%) of any model on BFCL v4** — the two sub-scores that map to our job — and DashScope is the only provider whose explicit cache threshold comfortably fits our prefix. | **The non-US cost/quality challenger.** No TTFT figure exists for any DashScope Qwen model from an acceptable source, so producing that number *is* the test. Secondary: does the open-weight family's AST strength carry over to the served API model? |
| 4 | `kimi-k2.6` | Moonshot AI | Highest BFCL Live Acc of any non-US model that is **not** in thinking mode (78.68%, tying Haiku 4.5), with automatic context caching that the vendor doc explicitly says covers *tool definitions*. | **The second non-US shot, on the caching axis.** Moonshot caches our exact payload shape with zero code change and no documented minimum. Tests whether "automatic caching of tool schemas" delivers a real TTFT win on a 40%-tool-schema prompt. |

**Free rider — add if a fifth slot is cheap:** `gemini-2.5-flash-lite`. Same provider, same
client, same key, one env-var change. It has the **lowest measured TTFT of any model
Artificial Analysis tracks (0.37s)** and a strong Non-Live AST (86.60%), but a
Relevance-Detection score of 43.75% [BFCL] — i.e. it may fail to call a tool when it should.
It costs nothing to include and it brackets the latency/accuracy trade-off at the floor.

### Why the list is shaped this way

Two findings from primary sources reshaped the answer, and neither appears in the previous
two reports. Both are in §2.

The owner's pushback on a US-only shortlist is answered on evidence: **two of the four slots
are non-US**, and the case for both is built from BFCL sub-scores and vendor caching docs,
not from balance. The reason a Google model still anchors the list is narrow and factual:
**of every candidate, only the Gemini Flash family has a TTFT inside the 200–700ms budget
from any acceptable source.** Every other measured candidate is over 700ms. That is not a
judgement about quality — it is the state of the published measurements, and the fastest way
to change it is to run the harness against the two non-US entries, for which no TTFT figure
exists at all.

---

## 2. The two findings that changed the answer

### 2.1 Our prefix is 406 tokens too short for Anthropic's cache to fire

Anthropic's prompt-caching doc lists a per-model **minimum cacheable prompt length**. For
Claude Haiku 4.5 it is **4,096 tokens** [VENDOR].

> "4,096 tokens for Claude Haiku 4.5"
> — <https://platform.claude.com/docs/en/build-with-claude/prompt-caching.md>

Our fixed prefix is **≈3,690 tokens** (`docs/MODEL_BRIEF.md`, measured from the real wire
payload). **[DERIVED]** 3,690 < 4,096, so a `cache_control` breakpoint on that prefix
**silently does nothing** — no error, `cache_creation_input_tokens: 0`. We would pay full
input price and full prefill time on every one of ~8 turns per call, forever, while believing
caching was on.

Compare the same threshold at the other providers:

| Provider | Minimum cacheable prefix | Our 3,690-token prefix | Source |
|---|---|---|---|
| Google (Gemini 2.5 Flash) | **2,048** tokens | ✅ clears by 1,642 | [VENDOR] <https://ai.google.dev/gemini-api/docs/caching> |
| Alibaba DashScope — explicit | **1,024** tokens | ✅ clears | [VENDOR] <https://www.alibabacloud.com/help/en/model-studio/context-cache> |
| Alibaba DashScope — implicit | **256** tokens | ✅ clears | [VENDOR] same |
| OpenAI | **1,024** tokens | ✅ clears | [VENDOR] <https://developers.openai.com/api/docs/guides/prompt-caching> |
| Moonshot / Kimi | no minimum documented | — | [VENDOR] <https://platform.moonshot.ai/docs/guide/use-context-caching-feature-of-kimi-api> |
| **Anthropic (Haiku 4.5)** | **4,096** tokens | ❌ **fails by 406** | [VENDOR] <https://platform.claude.com/docs/en/build-with-claude/prompt-caching.md> |

This is actionable, not just a disqualifier: the prefix is **11% short**. Padding it past
4,096 — or restoring content we were about to cut — would switch Haiku's caching on and drop
that prefix to 10% of input price. That is a deliberate design choice the harness run should
inform, and it is the opposite of the "cut the prompt" recommendation in the previous report.

### 2.2 BFCL's headline ranking is the wrong column for us

BFCL v4's `Overall Acc` folds in two agentic categories — **Web Search (200 cases)** and
**Memory (465 cases)** — that our agent does not do. `docs/MODEL_BRIEF.md` explicitly rules
out web search, memory management, and multi-step agentic planning.

Ranking by `Overall Acc` therefore actively misleads for this decision. The columns that map
to our job are **Non-Live AST** (schema-correct calls), **Live Acc** (real user-contributed
single-turn function calls), and **Relevance / Irrelevance Detection** (call a tool when you
should; *don't* when you shouldn't — this is our party-of-eight escalation case).

Sorted by `Overall Acc` vs. sorted by `Live Acc`, the list reorders sharply:

| Model | Overall Acc (rank) | Live Acc | Non-Live AST |
|---|---|---|---|
| Qwen3-32B (FC) | 48.71% (**#29**) | **82.01%** | **88.77%** |
| GLM-4.6 (FC thinking) | 72.38% (#4) | 80.90% | 87.56% |
| Claude-Haiku-4-5 (FC) | 68.70% (#6) | 78.68% | 86.50% |
| Kimi-K2-Instruct (FC) | 59.06% (#11) | 78.68% | 81.60% |
| Mistral-small-2506 (FC) | 37.15% (**#51**) | 77.28% | 73.60% |
| Gemini-2.5-Flash (FC) | 56.24% (#15) | 74.39% | 84.96% |
| GPT-5-mini (FC) | 55.46% (#17) | 58.62% | 69.85% |

All figures [BFCL], <https://gorilla.cs.berkeley.edu/data_overall.csv>.

The model ranked **#29 overall leads the entire 109-model board on both columns we care
about**, and it is a Qwen model. This is precisely the effect the owner predicted —
non-US models under-indexed by headline rankings — and it is visible only by pulling the
leaderboard's own per-category data rather than its top line.

**How the BFCL data was obtained** (the previous reports could not read it): the leaderboard
page renders via JavaScript, but `index_main.js` fetches a plain CSV at
`./data_{dataset}.csv`. `https://gorilla.cs.berkeley.edu/data_overall.csv` returns all 109
rows and 35 columns directly. Per-category files also exist: `data_live.csv`,
`data_non_live.csv`, `data_multi_turn.csv`, `data_agentic.csv`.

---

## 3. Per-model evidence

### 3.1 `gemini-2.5-flash` — Google

| Metric | Value | Source type | Source |
|---|---|---|---|
| BFCL v4 Overall Acc (FC) | 56.24% (rank 15 / 109) | [BFCL] | <https://gorilla.cs.berkeley.edu/data_overall.csv> |
| BFCL Non-Live AST | 84.96% | [BFCL] | same |
| BFCL Live Acc | 74.39% | [BFCL] | same |
| BFCL Multi-Turn | 36.25% | [BFCL] | same |
| BFCL Relevance / Irrelevance Detection | 75.00% / **93.67%** (best irrelevance of all candidates) | [BFCL] | same |
| BFCL harness latency (mean / p95) | 2.99s / 5.62s — **end-to-end request, not TTFT** | [BFCL] | same |
| **TTFT** | **0.51s** (non-reasoning), Google's API | [3P-MEAS] | <https://artificialanalysis.ai/models/gemini-2-5-flash> |
| Price in / out | $0.30 / $2.50 per 1M | [VENDOR] | <https://ai.google.dev/gemini-api/docs/pricing> |
| Cached-token price | $0.03 per 1M (**90% discount**) | [VENDOR] | same |
| Caching: automatic? | **Implicit — automatic, no code change, cannot be disabled** | [VENDOR] | <https://ai.google.dev/gemini-api/docs/caching> |
| Caching: minimum | **2,048 tokens** → our 3,690 clears | [VENDOR] | same |
| Caching: TTL | [NOT FOUND] — doc states no TTL for implicit caching | [NOT FOUND] | — |
| Caching: hit guaranteed? | **No** — "hit probability is not guaranteed" is Alibaba's phrasing; Google says "try to send requests with similar prefix in a short amount of time" | [VENDOR] | same |
| Rate-limit dimensions | RPM, **TPM (input)**, RPD — "exceeding any of them will trigger a rate limit error" | [VENDOR] | <https://ai.google.dev/gemini-api/docs/rate-limits> |
| Per-tier RPM/TPM values | [NOT FOUND] — table is JS-rendered; only batch-enqueued figures were extractable | [NOT FOUND] | — |
| Do cached tokens count toward TPM? | [NOT FOUND] — the rate-limits page does not state it | [NOT FOUND] | — |
| Data residency (Canada) | [NOT FOUND] for the Gemini Developer API. *Not investigated:* Vertex AI offers `northamerica-northeast1` (Montreal); confirm before relying on it. | [NOT FOUND] | — |

**Read:** the only candidate where TTFT and caching are both established and both inside
budget. The 93.67% irrelevance-detection score is the single best number any candidate posts
on the metric that maps to our forbid-list cases. The weak spot is Multi-Turn (36.25%) — our
calls run ~8 turns, so the harness's multi-turn fixtures are the ones to watch.

---

### 3.2 `claude-haiku-4-5` — Anthropic

| Metric | Value | Source type | Source |
|---|---|---|---|
| BFCL v4 Overall Acc (FC) | **68.70% (rank 6 / 109)** — best of any small model | [BFCL] | <https://gorilla.cs.berkeley.edu/data_overall.csv> |
| BFCL Non-Live AST | 86.50% | [BFCL] | same |
| BFCL Live Acc | 78.68% | [BFCL] | same |
| BFCL Multi-Turn | 53.62% — best of the four shortlisted | [BFCL] | same |
| BFCL Relevance / Irrelevance | 62.50% / 85.11% | [BFCL] | same |
| BFCL harness latency (mean / p95) | **1.68s / 3.15s — fastest of any top-10 model** (end-to-end, not TTFT) | [BFCL] | same |
| BFCL Prompt-mode score (same model) | 25.26% (rank 87) — **use FC mode, not prompt mode** | [BFCL] | same |
| **TTFT** | **1.06s** — Anthropic's API. **Over the 700ms budget.** | [3P-MEAS] | <https://artificialanalysis.ai/models/claude-4-5-haiku> |
| Price in / out | $1.00 / $5.00 per 1M | [VENDOR] | <https://platform.claude.com/docs/en/build-with-claude/prompt-caching.md> |
| Cache write / read multipliers | 1.25× (5m), 2× (1h) write; **0.1× read** | [VENDOR] | same |
| Caching: automatic? | **Explicit** — requires `cache_control: {type:"ephemeral"}` breakpoints, max 4 | [VENDOR] | same |
| Caching: minimum | **4,096 tokens — our 3,690-token prefix does NOT qualify** | [VENDOR] | same |
| Caching: TTL | 5 min default; `ttl:"1h"` option | [VENDOR] | same |
| Do cache reads count toward ITPM? | **No.** "`cache_read_input_tokens` … Do NOT count toward ITPM" (Haiku 4.5 carries no † marker) | [VENDOR] | <https://platform.claude.com/docs/en/api/rate-limits.md> |
| Rate limits, Haiku 4.5, Start tier | 1,000 RPM · **2,000,000 ITPM** · 400,000 OTPM | [VENDOR] | same |
| Rate-limit headroom | **[DERIVED]** our peak is ~25,000 tokens/min → **0.0125× the Start-tier ITPM.** The Groq TPM trap cannot recur here. | [DERIVED] | brief + row above |
| API compatibility | Anthropic Messages API — *not* OpenAI-compatible. Harness already has an adapter, **never run against the live endpoint** (`docs/LLM_BENCHMARK.md`). | [VENDOR] | — |
| Data residency (Canada) | `inference_geo` supports `"us"` / `"global"`. **No Canadian option found.** | [NOT FOUND] | — |

**Read:** the accuracy case is the strongest on the list and it is not close — rank 6 overall,
and it beats every shortlisted model on Multi-Turn, which is the category our 8-turn calls
most resemble. Against it: TTFT 1.06s is over budget, and caching cannot fire. Both penalties
are falsifiable by the harness — AA measures at a ~10k-token default workload (§5), and our
prompt is 3,690 tokens, so 1.06s is likely pessimistic for us. **Two failure modes to watch:
the Anthropic adapter's first live run is unproven, and the prompt-mode score (25.26%) shows
this model collapses if tool-calling is not wired as native FC.**

---

### 3.3 `qwen3.7-plus` — Alibaba Model Studio / DashScope

⚠️ **The candidate named in the brief, `qwen-plus`, is a legacy alias.** Alibaba's current
model overview lists `qwen3.7-max`, `qwen3.7-plus`, `qwen3.6-flash` [VENDOR,
<https://www.alibabacloud.com/help/en/model-studio/models>]. `qwen-plus` still appears in the
Qwen Plus family list on the context-cache page, so it likely still resolves, but
`qwen3.7-plus` is the current-generation equivalent.

| Metric | Value | Source type | Source |
|---|---|---|---|
| BFCL v4 — `qwen3.7-plus` or any DashScope Qwen API model | **[NOT FOUND] — not measured.** BFCL evaluates open-weight Qwen3 checkpoints only. | [NOT FOUND] | <https://gorilla.cs.berkeley.edu/data_overall.csv> |
| BFCL — Qwen3-32B (FC), open weights | Overall 48.71% · **Live 82.01% (best on board)** · **Non-Live AST 88.77% (best on board)** · Relevance **93.75%** · Irrelevance 76.37% · Multi-Turn 47.87% | [BFCL] | same |
| BFCL — Qwen3-235B-A22B-Instruct-2507 (FC) | Overall 47.99% · Live 68.91% · **Non-Live AST 37.40%** (likely format failures) · Relevance 87.50% | [BFCL] | same |
| BFCL harness latency, Qwen3-32B | 169.87s mean / 473.49s p95 — self-hosted, thinking-mode run. **Says nothing about DashScope's API.** | [BFCL] | same |
| **TTFT for any Qwen Plus/Flash model** | **[NOT FOUND]** | [NOT FOUND] | — |
| TTFT, Qwen3 Max (nearest measured relative) | 2.41s, Alibaba's API — **well over budget**; Max tier, not Plus/Flash | [3P-MEAS] | <https://artificialanalysis.ai/models/qwen3-max> |
| Caching: implicit | Automatic, **cannot be disabled**; min **256 tokens**; cached input billed at **20%** of standard; validity "indeterminate"; **"hit probability is not guaranteed"** | [VENDOR] | <https://www.alibabacloud.com/help/en/model-studio/context-cache> |
| Caching: explicit | `cache_control` marker; min **1,024 tokens**; creation billed at **125%**, cached input at **10%**; **5-minute validity, resets on hit** | [VENDOR] | same |
| Caching: mutual exclusivity | "Explicit cache and implicit cache are mutually exclusive." | [VENDOR] | same |
| Cache-hit reporting | `usage.prompt_tokens_details.cached_tokens` (OpenAI-compatible shape) | [VENDOR] | same |
| API compatibility | **OpenAI-compatible AND Anthropic-compatible** endpoints, both offered | [VENDOR] | <https://www.alibabacloud.com/help/en/model-studio/models> |
| Tool-call streaming parity | [NOT FOUND] — no vendor statement on tool-call delta shape under streaming | [NOT FOUND] | — |
| Data residency | Singapore (`ap-southeast-1`), China (Beijing), **Germany (Frankfurt)**, US. **No Canadian region.** | [VENDOR] | <https://www.alibabacloud.com/help/en/model-studio/context-cache> |
| Rate limits (RPM/TPM) | [NOT FOUND] | [NOT FOUND] | — |

**Read:** the best-fitting cache economics of anything on the list — explicit caching at a
1,024-token minimum and a 10% read rate means our full 3,690-token prefix caches with a
5-minute TTL that *resets on every hit*, i.e. it stays warm for the whole call. Dual
OpenAI/Anthropic compatibility makes it a genuine one-env-var swap. The evidence gap is
entirely TTFT, and the harness is the instrument that closes it. **Be honest about the
transfer risk: the 82.01% Live Acc belongs to an open-weight checkpoint, not to the model
DashScope serves.** If TTFT comes back over budget, swap to `qwen3.6-flash` before dropping
the provider.

---

### 3.4 `kimi-k2.6` — Moonshot AI

| Metric | Value | Source type | Source |
|---|---|---|---|
| BFCL v4 — Kimi K2 Instruct (FC) | Overall 59.06% (rank 11) · Non-Live AST 81.60% · **Live 78.68%** · Multi-Turn 50.63% · Relevance 75.00% · Irrelevance 87.34% | [BFCL] | <https://gorilla.cs.berkeley.edu/data_overall.csv> |
| BFCL — `kimi-k2.6` / `kimi-k2.7` specifically | **[NOT FOUND]** — only K2-Instruct is on the board | [NOT FOUND] | same |
| BFCL harness latency, K2-Instruct | 6.40s mean / 13.78s p95 (end-to-end, not TTFT) | [BFCL] | same |
| **TTFT** | 1.41s — **"median across providers serving the model", not Moonshot's own API.** Over budget as measured. | [3P-MEAS] | <https://artificialanalysis.ai/models/kimi-k2> |
| Caching: automatic? | **"automatically enabled for all model requests … no manual cache creation or management required"** | [VENDOR] | <https://platform.moonshot.ai/docs/guide/use-context-caching-feature-of-kimi-api> |
| Caching: covers tool schemas? | **Yes, named explicitly:** "repeated initial contexts (such as system prompts, knowledge documents, **or tool definitions**)" | [VENDOR] | same |
| Caching: minimum / discount / TTL | [NOT FOUND] | [NOT FOUND] | — |
| Current model IDs | `kimi-k2.7-code`, `kimi-k2.6`, `kimi-k2.5` (per Alibaba's cross-listing) | [VENDOR] | <https://www.alibabacloud.com/help/en/model-studio/context-cache> |
| Rate limits, data residency, streaming parity | [NOT FOUND] | [NOT FOUND] | — |

**Read:** the only vendor doc on the list that *names tool definitions* as cached content —
directly relevant when 40% of our prompt is tool schemas. Ties Haiku 4.5 on Live Acc. The
1.41s TTFT is the weakest link, and it is a cross-provider median rather than a measurement
of Moonshot's own endpoint, so it is the least trustworthy latency figure in this document.

---

### 3.5 Assessed and NOT shortlisted

| Candidate | Verdict | Evidence |
|---|---|---|
| `gpt-4o-mini` / OpenAI small | **Drop.** `gpt-4o-mini` **does not appear anywhere on the BFCL v4 leaderboard** (all 109 rows checked — no GPT-4o family entry). Its TTFT is 1.23s [3P-MEAS, <https://artificialanalysis.ai/models/gpt-4o-mini>], over budget. Successors score poorly on our columns: GPT-5-mini (FC) Live 58.62% / Non-Live AST 69.85%, GPT-5-nano (FC) Live 59.44%, GPT-4.1-mini (FC) Live 68.84% — all below every shortlisted model. GPT-5-mini's BFCL latency is 8.32s mean / 19.80s p95. | [BFCL] + [3P-MEAS] |
| `deepseek-chat` | **Drop, and note the model name is stale.** DeepSeek's own model-list endpoint documentation returns only `deepseek-v4-flash` and `deepseek-v4-pro` — **`deepseek-chat` is not among them** [VENDOR, <https://api-docs.deepseek.com/api/list-models>]. Both current models default to **thinking mode** [VENDOR, <https://api-docs.deepseek.com/quick_start/pricing>], which is disqualifying for a TTFT budget. The BFCL entry (DeepSeek-V3.2-Exp) is a different model and scores Non-Live AST 34.85% / Live 53.66% — the weakest AST of any candidate. **No TTFT for any DeepSeek model on Artificial Analysis.** Caching is excellent ($0.0028 vs $0.14 per 1M = 98% off, automatic) but cannot rescue the rest. | [VENDOR] + [BFCL] |
| Z.ai / GLM | **Drop on a specific, falsifiable ground.** GLM-4.6 is BFCL rank 4 at 72.38% — *higher than Haiku* — but **only in `(FC thinking)` mode**; the non-thinking variant is not on the board at all. Its non-reasoning TTFT is **2.69s** [3P-MEAS, <https://artificialanalysis.ai/models/glm-4-6>], ~4× over budget. So the score we would want requires the mode that destroys the latency. `glm-5.1` now supersedes it and has no BFCL entry. **Re-open if a non-thinking GLM posts a sub-700ms TTFT.** | [BFCL] + [3P-MEAS] |
| `mistral-small-latest` | **Drop — every headline number describes a model that retires this week.** BFCL's `Mistral-small-2506` and Artificial Analysis's 0.67s TTFT both refer to Mistral Small 3.2, which Mistral lists as **deprecated 2026-04-30 and retired 2026-07-31** [VENDOR, <https://docs.mistral.ai/getting-started/models/models_overview/>]. `mistral-small-latest` now resolves to **Mistral Small 4** (v26.03), a "hybrid model unifying instruct, reasoning, and coding" with **no BFCL v4 entry and no TTFT measurement**. The retiring model also posted **BFCL Multi-Turn 11.50%** against our 8-turn calls. Painful to drop — Mistral is the obvious French-native play — but there is currently no acceptable evidence about the model we would actually ship. **Re-open the moment Mistral Small 4 appears on either source.** | [VENDOR] + [BFCL] |

---

## 4. French generation quality — what we could and could not establish

**We could not establish a ranking of these models on French generation quality from any
acceptable source. This is the largest unfilled gap in the document.**

What exists:

- **COLE: A Comprehensive Benchmark for French Language Understanding Evaluation**
  ([PEER], <https://arxiv.org/abs/2510.05046>) — 23 tasks, **94 models**, with "a particular
  focus on linguistic phenomena relevant to the French language". Findings: a significant gap
  between closed- and open-weight models, and **"understanding of regional language
  variations"** named as a key challenging frontier. **But COLE measures NLU — sentiment,
  paraphrase, grammatical judgement, reasoning — not generation.** Our requirement is that
  replies *read* naturally in spoken Québec French, including spoken times
  ("dix-huit heures trente"). COLE does not test that.
- **Test Set Quality in Multilingual LLM Evaluation** ([PEER],
  <https://arxiv.org/abs/2508.02635>) — manual analysis of French and Telugu evaluation sets
  found errors that shifted model scores by **almost 10 percentage points** after correction.
  This is a direct caution against trusting any single French leaderboard number.
- Whether COLE covers **Québécois** register specifically: **[NOT FOUND]** from the abstract.
  Would require reading the full paper.

**Consequence for the harness:** the 6 French cases in `scripts/benchmark_llm.py` are, as far
as primary sources go, **the only French evidence we will have.** Treat them as load-bearing,
not as a sanity check. Recommend a human Québécois read of the French outputs as a graded
dimension alongside tool correctness — this is the one criterion the harness cannot score
automatically and no published benchmark will settle.

⚠️ Several French "benchmarks" circulate that are not peer-reviewed. None were used here.

---

## 5. Methodology caveats that affect how you read §3

**Artificial Analysis TTFT — what it actually measures.** Per AA's own methodology page
([3P-MEAS], <https://artificialanalysis.ai/methodology/performance-benchmarking>):

- Three workloads: ~1,000 input tokens; **~10,000 input tokens (the default benchmark)**;
  ~100,000 input tokens.
- Tested **8× per day**, reported as the **median (P50) over the past 72 hours**.
- Measured from **a VM in Google Cloud `us-central1-a`** (Iowa).
- AA notes that "longer prompts can result in both longer time to first token" and that TTFT
  "is sensitive to server location as it includes network latency."

**[DERIVED]** Our prompt is ~3,690 tokens — between AA's 1k and 10k workloads, and the
headline figures come from the 10k default. **The quoted TTFTs are therefore likely
pessimistic for our payload**, which matters most for Haiku 4.5 (1.06s) and Kimi (1.41s).
Separately, Iowa ≠ Montreal, so regional latency from eastern Canada is unmeasured for every
model. **p50/p90 latency spreads are behind AA's paid tier and were not obtained.**

**BFCL's `Latency` columns are not TTFT.** They are end-to-end request time for the whole
benchmark call. Do not compare them against the 200–700ms budget. They are useful only as a
relative sanity check — and on that check Haiku 4.5 (1.68s mean / 3.15s p95) is by a wide
margin the fastest of any top-10 model, which cuts against its AA TTFT figure.

**BFCL Relevance Detection is a small category.** 43.75% for Flash-Lite is 7/16 cases. Treat
single-digit differences in that column as noise; treat 40-point gaps as signal.

---

## 6. What the previous two reports got wrong or could not support

Recorded so we do not re-import the errors. `docs/LLM_BENCHMARK.md` was scrupulously labelled
and most of its Groq analysis holds; these are the items that do not carry forward.

1. **"No verified BFCL scores (gorilla.cs.berkeley.edu renders its table via JS)"**
   — `docs/LLM_BENCHMARK.md` §3f. **Superseded.** The data is a plain CSV at
   `https://gorilla.cs.berkeley.edu/data_overall.csv`, discovered by reading `index_main.js`.
   All 109 rows and 35 columns are now available. Every BFCL figure in the previous reports
   was tagged `[UNVERIFIED — secondary aggregator]`; none of them need to be.

2. **The Llama 3.1 BFCL figures (~76.1% / ~84.8%) were aggregator-sourced and are not
   supported by BFCL v4.** BFCL v4 lists `Llama-3.3-70B-Instruct (FC)` at **31.90%** overall
   and `Llama-3.1-8B-Instruct (Prompt)` at **25.83%** — nowhere near the quoted numbers. The
   aggregator figures were almost certainly BFCL **v1/v2** AST-only scores. **Do not carry
   the "~9-point gap, not 24" conclusion forward** — it rests on numbers that do not exist in
   the current benchmark.

3. **"Artificial Analysis … I could not fetch artificialanalysis.ai directly (HTTP 403)"**
   — `docs/LLM_BENCHMARK.md` §3d. **Not reproducible.** `artificialanalysis.ai` returns 200.
   Model pages embed their headline metrics in JSON-LD FAQ blocks that are readable without
   JS or an API key. The Groq Llama 3.3 70B "~0.95s TTFT" figure was therefore recorded
   second-hand when it did not need to be.

4. **The AA methodology was described as "1,000-token input and 100-token output".** AA's
   current methodology page states the **~10,000-token workload is the default benchmark**,
   with 1k and 100k as the other two. The previous report's inference — "our workload is 3.5×
   the input AA tests" — is **backwards**: our 3,690 tokens are ~2.7× *smaller* than AA's
   default. The direction of the correction to any AA latency figure flips.

5. **Prompt caching was analysed only for Groq, and the per-model minimum was never
   considered.** §2.1 is the consequence: the previous recommendation to cut the FAQ digest
   from the system prompt would push our prefix *further below* Anthropic's 4,096-token
   threshold, permanently foreclosing caching on Haiku. Any prompt-size decision must now be
   made against the threshold table, not in the abstract.

6. **Model names in the brief have drifted.** Three of the seven named candidates no longer
   refer to what the brief assumes: `deepseek-chat` is absent from DeepSeek's model list,
   `qwen-plus` is a legacy alias behind `qwen3.7-plus`, and `mistral-small-latest` now
   resolves to a model with no published evidence at all. Neither previous report checked the
   candidate names against the vendors' live model lists.

7. **Cost was over-weighted relative to the brief.** **[DERIVED]** 102 calls × 8 turns ×
   3,690 tokens ≈ **3.0M input tokens/month**. At Gemini 2.5 Flash's $0.30/1M that is
   **$0.90/month uncached, ~$0.09 cached**; at Haiku 4.5's $1.00/1M, **~$3.01/month**. Against
   a $50 budget, **the entire cost axis is decision-irrelevant** and should not break any tie.

---

## 7. Stated plainly: what could not be established

1. **No TTFT for `qwen3.7-plus`/`qwen-plus`, or for any DeepSeek model, from any acceptable
   source.** Two of four shortlisted models have no latency evidence whatsoever. This is the
   **single biggest gap**, and it is why those slots exist.
2. **No French *generation* quality evidence for any candidate.** COLE is NLU-only; no
   peer-reviewed source ranks these models on natural spoken French, and Québécois register is
   unaddressed. §4.
3. **No p50/p90 TTFT distributions** — AA's variance and percentile views are paywalled. Every
   latency figure here is a single median.
4. **No TTFT measured from eastern Canada.** All AA figures originate in Iowa.
5. **No TTFT measured at our prompt size or with 9 tool schemas.** No published source
   measures TTFT as a function of tool-schema payload, so **"does a large tool-schema payload
   disproportionately hurt certain providers?" is unanswered** — the harness is the only way.
6. **Gemini per-tier RPM/TPM values, and whether Gemini's cached tokens count toward TPM.**
   The dimensions are documented; the numbers are JS-rendered.
7. **Rate limits and data residency for Moonshot; rate limits for DashScope.**
8. **Tool-call streaming parity.** No vendor states whether its OpenAI-compatible endpoint
   emits tool-call deltas in OpenAI's exact shape. This is a known divergence area and the
   harness's fragmented-argument reassembly path (already mock-tested per
   `docs/LLM_BENCHMARK.md`) is the check.
9. **No Canadian data residency found for any of the four.** Anthropic offers `us`/`global`
   `inference_geo`; DashScope offers Singapore/Frankfurt/Beijing/US. If Canadian residency is
   a hard requirement, none of these pass as configured and Vertex AI's
   `northamerica-northeast1` region needs separate investigation.
10. **BFCL scores exist for the *served* API model in only 2 of 4 cases** (Gemini 2.5 Flash,
    Claude Haiku 4.5). For Qwen and Kimi the scores describe adjacent open-weight or
    prior-generation checkpoints and **must not be quoted as if they described the shipped
    model.**
