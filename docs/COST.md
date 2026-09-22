# What this costs, and what it costs to *manage*

> ## ⚠️ The current answer, before you read any further
>
> | | |
> |---|---|
> | **Recurring** | **~$20 CAD/month** ($17.20 + 15% contingency) |
> | **One-time** | **$160 CAD** (one month of Claude Max, incl. taxes) |
> | **Incumbent** | ~$275 CAD/month |
> | Development time | **not charged** |
>
> The line items live in `scripts/build_client_report.py`, which generates the
> PDF sent to the owner. **That script is the source of truth** — it is the
> number the client has actually been quoted.
>
> **Two corrections this document does not reflect:**
> 1. **Hosting is ~$9.80 CAD/month, not $4.40.** Fly bills CPU at a flat
>    $0.87/month and RAM at **$6.24/month per GB**, and `fly.toml` asks for 1GB
>    (= $7.11 USD). The earlier figure was the 512MB price.
> 2. The rates below were superseded twice — first by `STACK_DECISION.md`, then
>    by the report. Read this file for the **volume analysis and the method**,
>    which still stand, not for the dollar figures.

Recomputed from the venue's real call log, with published vendor rates. The
owner's constraint drives the design: **"I just want to manage the connection to
Libro. Nothing else. And it should work properly enough."**

That is a stronger requirement than low cost, and it changes the stack.

## Volume — measured, not estimated

Every duration in the 84-call log was summed directly:

```
calls in window     84  over 25.0 days   -> 102 calls/month
total talk time     91.0 min / 25 days   -> 111 min/month
median              54s      (matches the source's stated 54s)
p90                 119s     (matches the source's stated 119s)
mean                65s
```

The median and p90 reproducing the source exactly is the check that the
transcription is right. **111 talk-minutes/month** is the number everything
scales off — my earlier 90 was low.

Also worth noting: **16 of 84 calls had zero user turns.** Those still cost
telephony and a little STT, but no LLM and almost no TTS.

## What the incumbent charges: $200/month (confirmed from the invoice)

For 102 calls. That is **~$1.96 per call**, or **~$1.80 per talk-minute** — for a
system where, on its own data, 43% of calls end in a transfer to a human and only
15% produce a booking.

## What ours costs

Rates are published list prices (see sources at the bottom); **none of this has
been on a real bill yet.**

> **Superseded in detail by `docs/STACK_DECISION.md`**, which verified these rates
> against primary sources and corrected two errors here. Numbers below are the
> corrected ones; that document is authoritative.

| Component | Monthly at 111 min |
|---|---|
| **LiveKit Cloud — Ship tier** (always-warm agents) | **$50.00** |
| STT (Deepgram Nova-3 Multilingual, via Inference) | $0.64 |
| LLM (Gemini 2.5 Flash, via Inference) | $1.09 |
| TTS (Cartesia `sonic-3` French, via Inference) | $1.32 |
| *— covered by Ship's $5/mo inference credit* | *−$3.05* |
| Twilio 514/438 number | $1.65 |
| Database (Supabase free tier, `ca-central-1`) | $0.00 |
| **Total** | **≈ $51.65 / month** |

### The free tier is not viable — two independent reasons

1. **Build-tier agents cold-start 10–20 seconds.** LiveKit's own quotas doc says
   so outright, and their pricing matrix lists cold-start prevention as Ship-only.
   Twenty seconds of dead air before the greeting would recreate the exact
   abandonment problem we spent a workstream fixing.
2. **Build's inference credit is a hard cap, not a discount** — once exceeded,
   *requests fail* rather than incurring overage. Build gives $2.50/month; our
   measured usage is **$3.05**, with a floor of $2.80 even assuming zero output
   tokens. **The phone line would go down mid-month, silently, with no bill to
   warn anyone** — the worst possible failure for an owner who can't monitor it.

### Correction: the LLM line here was ~8× too low

An earlier version of this file used $0.0013/min for the LLM. That rate is
LiveKit's calculator default, which assumes **3,000 tokens/minute**. Our measured
prompt is ~3,450 tokens per *turn* — roughly **25,000 tokens/minute**. The
corrected figure is $1.09/month. Small in absolute terms, but it is the number
that pushes usage past Build's hard cap, so the error mattered.

**That is roughly 30–80× cheaper than $200/month.**

**The owner's stated ceiling is $100/month.** Every scenario below sits comfortably
under it, which means *reliability and low management burden should win over
shaving dollars* — the difference between $5 and $55 is not worth a worse system.

### The caveat that could change it

LiveKit's docs say that **on paid plans "agents are always warm and ready"** —
which implies free-tier agents may **cold-start**. On a restaurant phone line a
cold start means dead air on the first call of the day, and dead air at the start
of a call is precisely what produces the 19% zero-turn abandonment we are trying
to fix.

If warm agents turn out to require the **Ship** tier, that is **$50/month**, and
the total becomes **~$55/month** — still **3.6× cheaper than $200**, still well
inside the $100 ceiling, and it buys away all server management. **If warm agents
cost $50, pay it.**

**This is the single number worth confirming with LiveKit before launch.** Both
outcomes are a large win; it only changes whether the answer is "$5" or "$55".

## The reversal: Cartesia comes back

I removed Cartesia earlier, arguing "one vendor, one key, one bill." Two things
since have made that wrong:

1. **Deepgram TTS cannot speak French at all** (all 58 Aura voices end in `-en`),
   so the "one vendor covers both languages" premise was false.
2. Via **LiveKit Inference, Cartesia needs no separate account or key** — it is
   billed through LiveKit. The extra-credential objection disappears.

And the cost difference is large:

| French-capable TTS | Rate | Monthly (~44 min) |
|---|---|---|
| **Cartesia Sonic** | $0.03/min | **$1.32** |
| ElevenLabs Multilingual v2 | $0.18/min | $7.92 |

ElevenLabs is **6× the price** for the same job. Cartesia also advertises
sub-100ms latency, which matters more here than marginal voice quality.

## SETTLED: European French is accepted

**The owner has decided he is fine with a France-French voice**, provided the call
**opens in French** and the intro is **short**. That closes the largest open
question in the stack.

Why it matters so much: **no `fr-CA` voice exists anywhere in LiveKit Inference** —
verified across all 28 TTS models from 7 providers; French appears only as `fr` /
`fr-FR`. Genuine Québécois TTS exists only on **Azure** and **AWS Polly**, and both
are plugin-only, meaning **+1 cloud account, +1 key to rotate, +1 console** each.

Azure carried a failure mode that disqualified it on its own terms: if the Azure
key ever lapses, **French dies while English keeps working** — a partial, silent
failure that looks healthy while failing two-thirds of callers. Precisely what an
owner who cannot monitor the system would never catch.

**Decision: Cartesia `sonic-3` (`fr`) via LiveKit Inference. Zero extra accounts,
zero extra keys, zero extra dashboards.** No Azure, no AWS.

The research was honest that the concern was *somewhat* overweighted but not
imaginary: Montréal accent-attitude studies suggest European French reads as
competent but slightly less warm — a real if modest cost for a restaurant
greeting, and measured on human speakers rather than TTS. Comprehension is a
non-issue.

**Still gated on a listening test** — nobody has heard this voice yet. But the
decision no longer blocks: if it sounds wrong, the fallback is another
zero-account voice, not a second cloud provider.

This also reinforces the greeting design already shipped: **"<name>, bonjour !"** —
French first, two words, ~0.9s.

## The part the owner actually cares about: management burden

This is what re-shaped the stack.

### Before (what we had built)

| Thing to manage | Why it's work |
|---|---|
| A VPS | OS patches, disk, reboots, "why is it down" |
| Deepgram account + key | rotation, billing, expiry |
| Groq account + key | rotation, billing, expiry |
| LiveKit account + key | |
| Twilio account + key | |
| SQLite on a persistent volume | backups, disk filling |
| **Libro** | ← *the only one he wants* |

**Seven things. Six of them unwanted.**

### After

| Thing to manage | |
|---|---|
| **LiveKit** — hosts the agent *and* provides STT/LLM/TTS on one key | one account, one bill |
| **Twilio** — the phone number only | one account, set once |
| **Libro** | ← the one he signed up for |

**Three things.** No server to patch, no OS, no Docker host, no scaling, no
certificates. LiveKit Cloud handles agent lifecycle, upgrades and isolation.

### The one genuine casualty: our SQLite call log

LiveKit Cloud agents run on **ephemeral storage**. Our `store.py` explicitly
requires a persistent volume — on ephemeral disk, every restart silently drops
the messages the agent promised to pass on, which is exactly the bug that module
was written to fix.

So moving to LiveKit Cloud forces a decision:

**Option A — push, don't store.** Messages, takeout requests and callbacks go out
by SMS/email *the moment they are captured*, and delivery success is what makes
the promise true. No database at all. Libro remains the record for reservations.
*Cost: you lose "show me every call from last week" and the failed-booking log.*

**Option B — a managed SQLite (e.g. Turso/libSQL).** Drop-in for our existing
code, generous free tier, nothing to run. *Cost: one more account — but zero
maintenance.*

**Owner's decision: B**, and it stands even though the research recommended A.
He accepts one extra account to keep a proper record of what the agent does.
Losing the failed-booking log would remove the only way to answer *"did a
reservation not get made?"* — which he asked for explicitly.

**But not Turso — Supabase, in `ca-central-1` (Montréal).** Turso cannot host in
Canada. The database holds guest names and phone numbers, and Quebec's Law 25
governs personal information leaving the province, so Canadian residency is worth
more than the drop-in convenience of libSQL. Supabase's free tier is far above our
~100 rows/month, so this still adds **$0**. `store.py` keeps all storage behind
one module, so swapping the driver is contained.

## Honesty

- **Nothing here has been billed yet.** These are list rates × measured volume.
- The cold-start question is unresolved and is the only thing that moves the
  total materially.
- Cartesia's French has not been heard by anyone on this project.
- The $200 figure is **confirmed from the invoice** (an earlier ~$250 was a recollection).
- **AWS is disfavoured by the owner**, despite Polly having genuine fr-CA voices.
  Treated as a last resort; see docs/STACK_DECISION.md for what that costs us.

## Sources

- [LiveKit pricing](https://livekit.com/pricing) · [Agents on LiveKit Cloud](https://livekit.com/products/agent-cloud-deployment) · [Deploy and scale agents](https://livekit.com/blog/deploy-and-scale-agents-on-livekit-cloud)
- [Deepgram pricing 2026](https://texttolab.com/blog/deepgram-pricing)
- [LiveKit Inference rates](https://www.cekura.ai/blogs/livekit-pricing)
