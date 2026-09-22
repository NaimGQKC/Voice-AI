# The 19% who never say a word

**Our measured number:** 16 of 84 calls (19%) had **zero user turns** — the caller
heard the greeting and hung up without speaking. Six were under 15 seconds; the
shortest was 6 seconds. Add the 16 who hung up mid-flow and **38% of calls ended
with the caller giving up**.

This is the largest single loss in the corpus and it happens before the agent has
done anything. No amount of booking logic fixes it.

## What the industry says causes it

The consistent finding across sources: **the loss is concentrated in the first
few seconds, and the causes are acoustic and temporal, not conversational.**

- *"Most callers hang up in the first five seconds because the greeting is too
  long, too vague, or the voice sounds robotic."* — and voice quality is called
  out as the single biggest factor in whether callers stay on the line
  ([UpFirst](https://upfirst.ai/blog/ai-receptionist-tips))
- **Latency under 700ms** is described as the single most important technical
  factor; over a second "feels like a robot reading a script"
  ([UpFirst](https://upfirst.ai/blog/ai-receptionist-tips))
- **60% of callers are gone before one minute**, with the first 30–60 seconds
  taking the bulk of abandoners
  ([Ringly](https://www.ringly.io/blog/call-abandonment-rate-statistics-2026))
- Touch-tone IVR — the thing callers *fear* they've reached — drives **67%
  abandonment within 90 seconds**, which is likely what a slow or menu-like
  opening is being mistaken for
- **Barge-in** (letting the caller talk over the agent and having it stop
  immediately) is repeatedly named as the feature that most separates a natural
  agent from a frustrating one
  ([BenchLM](https://benchlm.ai/blog/posts/best-llm-voice-agents))
- Typical human-run call centres sit at **5–8% abandonment**; 20%+ is considered
  bad even in the worst-performing industries

So 19% *before the caller speaks at all* is not a normal operating cost. It is a
defect.

## The five plausible causes, in the order I'd bet on

**1. The greeting is too long.** We have direct evidence this failure mode is
real for us: our own agent once took **13 seconds** to finish greeting because
the model padded the disclosure. A caller who has to wait 13 seconds before they
can say "table for two" has already decided this is a machine that will waste
their time. *Target: under 3 seconds, and say the useful part first.*

**2. The caller can't interrupt it.** If barge-in isn't active during the
greeting, the caller physically cannot start talking. They interpret that as
"this thing doesn't listen" and hang up. This is the cheapest fix on the list.

**3. It answers in the wrong language.** **Two-thirds of this venue's calls are
in French.** If a francophone caller hears an English greeting, the "this won't
understand me" judgment is made instantly. This is specific to *our* venue and
would not show up in any generic benchmark.

**4. Dead air before or after the greeting.** Time from the caller hearing
ring-stop to hearing a human-sounding voice. Silence after pickup reads as a
dropped call.

**5. It doesn't sound like a person.** Least actionable, most expensive to fix,
and I'd want evidence before spending on it — the cheaper four should be
eliminated first.

## What I would actually do, in order

**Step 1 — measure, don't guess.** We cannot fix this blind, and guessing is what
produced our 13-second greeting in the first place. Log per call:

- time from answer to first audible word
- greeting duration
- whether the caller spoke, and at what second
- whether they interrupted the greeting
- the language detected

That is a small addition to the `calls` table we already have. **Without it, any
change is unfalsifiable.**

**Step 2 — the three cheap fixes**, all low-risk:
- Hard-cap the greeting with `session.say()` verbatim (already done) and shorten
  it further; front-load "<name>, how can I help?" and move the AI disclosure to a
  second clause the caller can talk over
- Confirm barge-in is enabled *during the greeting*, not just during replies
- Greet bilingually for a venue that is two-thirds French

**Step 3 — re-measure.** The target is zero-user-turn calls under 10%.

## The honest caveat

Most sources above are vendor blogs, and vendors selling voice agents have an
obvious interest in the claim "AI voice agents reduce abandonment by 60%." I'd
treat the *direction* of their advice as reliable — short greeting, low latency,
barge-in, natural voice — and treat the specific percentages as marketing until
we reproduce them on our own line.

**The one number I trust completely is our own: 19%.** It came from this venue's
real calls, and it is the one to beat.
