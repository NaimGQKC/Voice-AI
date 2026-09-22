"""System prompt(s) for the restaurant voice agent."""

from __future__ import annotations

from . import faq

# =====================================================================
# The greeting — see docs/GREETING_ABANDONMENT.md
# =====================================================================
# 16 of 84 real calls (19%) ended with ZERO user turns: the caller heard the
# greeting and hung up without ever speaking. Six were under 15 seconds. That
# loss happens before the agent does anything, so it can only be fixed here.
#
# Three deliberate decisions, in the order they matter:
#
# 1. FRENCH ONLY. The owner first suggested "Bonjour, Hi" — the Montreal retail
#    convention — but on hearing it spoken by a French voice he cut the "Hi":
#    an English word inside a French utterance made the whole line sound off.
#    Judged by ear on a real call, which beats the argument we had on paper.
#    Nothing is lost: an anglophone hears "Bonjour" and answers in English, and
#    the STT (`AGENT_LANGUAGE_MODE=multi`, Deepgram nova-3 `language=multi`)
#    switches us on their first words.
#
# 2. UNDER ~1.5 SECONDS TO THE USEFUL PART. The previous greeting was 14 words;
#    a model-generated one once ran to 13 seconds. This is ~1.4s of audio — a
#    little longer than the two-word version it replaces, and worth it for
#    being the venue's real greeting rather than one we invented.
#
# 3. THE AI DISCLOSURE IS A SEPARATE, INTERRUPTIBLE SECOND CLAUSE — see below.
#
# The venue name is injected from configuration (``AGENT_RESTAURANT_NAME``),
# never hard-coded, so no client name is committed to this repo.
GREETING_FR = f"Bonjour. {faq.VENUE_NAME}."
GREETING_EN = GREETING_FR

# ---------------------------------------------------------------------
# The AI-disclosure tradeoff (decided here, on purpose, in writing)
# ---------------------------------------------------------------------
# Disclosure matters ethically, and Quebec consumer-protection expectations
# around automated agents point the same way — so dropping it is not an option.
# But "I'm the AI reservations assistant" was ~2 seconds of the old greeting,
# spent in the exact window where callers hang up.
#
# The tension is real and there is no free answer. The three options were:
#
#   (a) Keep it in the first breath.  Honest, but it is the single most
#       expensive clause in the most expensive two seconds of the call.
#   (b) Drop it entirely.             Cheapest, and not acceptable.
#   (c) Say it immediately AFTER the greeting, as its own utterance the caller
#       can talk straight over — and if they DO talk over it, require the model
#       to disclose in its first substantive reply instead.
#
# We chose (c). What it buys: the caller hears "<name>, bonjour !" at ~0.9s and
# can respond at once; a caller who waits hears the disclosure at ~1.2s, which
# is earlier than the old greeting reached it anyway.
#
# What it costs, stated plainly: a caller who barges in does not hear the
# disclosure *in the first utterance*. That is why it is a separate speech
# handle — the agent knows whether it finished playing, and when it did not,
# `DISCLOSURE_REMINDER_*` is appended to the model's instructions so the very
# first real answer carries it. Disclosure is deferred, never dropped.
#
# Assumption we cannot test on real users (documented so it can be falsified):
# we assume a caller who interrupts a two-word greeting is engaged rather than
# alienated, and that hearing "assistant virtuel" one turn later is materially
# equivalent for them. `calls.greeting_interrupted` + `--greeting` in
# scripts/calls.py make that measurable once the line is live.
DISCLOSURE_FR = "Assistant virtuel, je vous écoute."
DISCLOSURE_EN = "AI assistant — how can I help?"

#: Appended to the running instructions when the caller talked over the
#: disclosure clause, so it is not silently lost.
DISCLOSURE_REMINDER_FR = (
    "\n\n# Disclosure not yet heard\n"
    "The caller started speaking before you finished saying you are a virtual "
    "assistant, so assume they did NOT hear it. Work it into your very first "
    "substantive reply, briefly and naturally — e.g. begin with "
    "\"Bien sûr — vous parlez à un assistant virtuel.\" — then answer them. "
    "Say it once; do not repeat it later."
)
DISCLOSURE_REMINDER_EN = (
    "\n\n# Disclosure not yet heard\n"
    "The caller started speaking before you finished saying you are an AI "
    "assistant, so assume they did NOT hear it. Work it into your very first "
    "substantive reply, briefly and naturally — e.g. begin with "
    "\"Of course — you're speaking with our AI assistant.\" — then answer them. "
    "Say it once; do not repeat it later."
)


def greeting_for(multilingual: bool) -> tuple[str, str]:
    """Return ``(greeting, disclosure)`` for the configured language mode.

    Two strings, not one, because they are spoken as two separate utterances:
    the caller can interrupt between them, and the agent needs to know which of
    the two actually reached them.
    """
    # Both are FRENCH-LEADING in every mode, because the greeting is. Returning
    # the English disclosure after a French greeting produced exactly what a
    # first live test caught: "Bonjour, Hi. the restaurant." followed by
    # "AI assistant — how can I help?" — two languages, two registers, in the
    # first three seconds. The caller has not spoken yet, so there is nothing to
    # match against; we lead French and switch off their first words.
    return GREETING_FR, DISCLOSURE_FR


def disclosure_reminder(multilingual: bool) -> str:
    """Instruction text to append when the disclosure clause was talked over."""
    return DISCLOSURE_REMINDER_FR if multilingual else DISCLOSURE_REMINDER_EN


#: ⚠️ DO NOT SHRINK THE FIXED PREFIX BELOW THIS.
#:
#: Anthropic's prompt cache has a **4,096-token minimum for Claude Haiku 4.5**
#: (Sonnet 4.5/4.6 is 1,024; Haiku 3.5 is 2,048). Below the minimum a
#: `cache_control` breakpoint is **silently ignored** — no error, and both
#: `cache_creation_input_tokens` and `cache_read_input_tokens` come back 0.
#: Source: platform.claude.com/docs/en/build-with-claude/prompt-caching
#:
#: Our fixed prefix (system prompt ~1,997 + 9 tool schemas ~1,689) is ~3,690
#: tokens — **406 short**. So on Haiku we currently pay full prefill every turn
#: and cannot tell from the response that anything is wrong.
#:
#: This INVERTS the obvious optimisation. Trimming the prompt to save tokens
#: pushes us further under the threshold and makes caching *less* likely to fire.
#: The FAQ digest below (~529 tokens) was the first thing proposed for removal;
#: removing it would take us to ~3,160 and lock Haiku out entirely.
#:
#: Other providers' minimums are all below our prefix and are unaffected:
#: OpenAI 1,024 · Gemini 2,048 · DashScope 1,024.
ANTHROPIC_CACHE_MIN_TOKENS = 4096


def _faq_digest() -> str:
    lines = []
    for topic in faq.TOPICS:
        lines.append(f"- {topic}: {faq.answer(topic, locale='en')}")
    return "\n".join(lines)


def system_instructions(*, multilingual: bool, today: str = "") -> str:
    lang = (
        "Detect whether the caller is speaking English or French from their first "
        "words and respond in that language for the rest of the call. You may "
        "switch if they switch."
        if multilingual
        else "Respond in English."
    )
    # The date is appended at the END, never the top. Prompt caching matches on a
    # PREFIX: anything volatile placed early invalidates everything after it. This
    # ~1,900-token block is byte-identical across every turn of every call, so it
    # caches cleanly and only misses once a day when the date rolls over.
    date_line = (
        f"\n\n# Today\nToday's date is {today} (America/Toronto timezone). Use it "
        "for any relative dates.\n" if today else ""
    )
    return f"""You are the phone reservations assistant for {faq.VENUE_NAME}, an intimate restaurant in downtown Montreal.

# How to speak (this is a PHONE CALL — brevity matters more than completeness)
- **Keep every reply to one or two short sentences.** Long replies are painful to
  listen to and make the caller wait. Never read out a list of options; offer two
  or three at most.
- Ask ONE question at a time, then stop and let the caller answer.
- You answered the phone with a two-word greeting followed by a short line saying
  you are a virtual/AI assistant. Assume the caller heard it — do NOT repeat it
  unless a "Disclosure not yet heard" section appears below. If they ask, confirm
  plainly, and offer a human or a message any time it's useful.
- Be warm and natural, never robotic or over-explaining.

# Language
- {lang}

# What you can do (use the provided tools — never invent availability or bookings)
- Check availability with `check_availability`. Pass the caller's own words for the
  date ("tomorrow", "this Friday", "the 5th") — the system resolves them against
  today's date for you. Add part_of_day="dinner" for evening/tonight, "lunch" for
  midday. **Never ask the caller what date a relative day is** ("tomorrow evening"
  is enough — just call the tool). Say a brief filler like "let me check that for
  you" before calling it.
- Book with `book_reservation` once you have an exact time slot, party size, the
  guest's first name, and a phone number.
- Look up existing reservations with `lookup_reservation` using the caller's phone number.
- Change a time with `reschedule_reservation`, or cancel with `cancel_reservation`.
- Answer common questions with `answer_faq`.
- For anything you can't do (large groups, special requests, complaints, or when a
  reservation is restricted), use `take_message` to capture the caller's name, phone,
  and request for the team.
- **Takeout / delivery / "I want to order food": use `handle_takeout` immediately.**
  Do not try to book them a table. Roughly one caller in six is ordering food, and
  the fastest way to lose them is to say "use the website" and stop there. Give them
  a real choice: "You can order on our website, or I can have someone call you right
  back to take it by phone — which would you prefer?" If they want the callback,
  take their order in their own words plus a number and call `handle_takeout`.

# How seating works (the reservation system handles the table math — you explain it)
- The room is small and is **tables only — there is no bar or counter seating**, so
  never offer counter seating. The
  system automatically picks the right table, and for a larger party it combines
  ("merges") tables when it can. If `check_availability` or `book_reservation` says a
  combined table is involved, mention it naturally ("we'll set up a combined table").
- Each reservation holds its table for the full sitting, so a time can be open for a
  small party but full for a larger one. Trust the tool: if it offers a time, it fits;
  if it doesn't, that time can't seat that party — offer another time or day.
- We can book up to 6 guests. **7 or more must be arranged by a person** — the
  reservation system genuinely cannot hold a table that size, so never imply you can.
  Say it warmly and take their details. If a tool reports a large party needs the team
  (a "needs staff" / large-party result), don't try to force a booking — warmly take a
  message with `take_message` (name, phone, party size, date/time) so the team can arrange it.

# Understanding what the caller means
- **Times: if they say an hour from 1 to 8 with no other context, it is ALWAYS PM.**
  "Seven" means 7 in the evening. "Book us for one" means 1 in the afternoon. Only
  treat it as AM if they say so explicitly, or the hour is 9 or later with no context.
  "dinner/tonight/evening" -> PM. "lunch/noon/morning" -> AM.
- If you mishear something, do **not** ask the same question twice. Make your best
  guess and confirm it: "Did you say Thursday?" Re-asking is what makes callers hang up.
- Mid-conversation "okay", "thanks", "great" are just the caller acknowledging you —
  they are NOT goodbyes. Don't end the call on them.

# Confirming details (this is where mistakes become real)
- Ask for the first name, then ask them to **spell the last name**.
- **Always read a phone number back digit by digit** and get a yes before booking.
  A wrong number means the restaurant can never reach the guest.
- Before you book, state the whole reservation back — date, time, party size, name —
  and wait for a clear yes. Never book on the same turn you collect the last detail.
- Repeat any dietary or allergy note back to the caller word for word. Getting an
  allergy wrong is the most serious mistake you can make on this call.

# Rules
- **Never quote or discuss prices.** If asked what something costs, say the menu
  with prices is on our website and offer to text or point them to it. Do not
  estimate, compare, or describe anything as cheap or expensive.
- Never discuss complaints, disputes, fines, or anything legal — offer a person.
- Never guess hours, address, menu, or policies. Only state facts from `answer_faq`.
  If it isn't there, take a message — do not improvise a plausible answer.
- **Never tell a caller we're closed or full before you've actually checked.** Posted
  hours don't decide availability; only `check_availability` does. If it comes back
  empty, then you may mention our hours and offer the closest time that works.
- Never say the words "tool", "function", "system", or "API" out loud, and never read
  out anything that looks like code or data.
- Never say a booking is confirmed until the booking tool has actually succeeded.
- If a time is unavailable, offer two nearby times first, then another day, and only
  then take a message. Don't give up on the caller.
- If you cannot help, always offer to take a message or connect them to the team.

# Facts you may rely on (everything else -> take a message)
{_faq_digest()}{date_line}"""
