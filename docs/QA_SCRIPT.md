# QA script — what to say, and what should happen

Work down this list out loud in `console` mode. Each case is drawn from what
**actually happened** in the venue's 84 real calls, not from imagination.

```powershell
python -m resto_agent.agent console      # mock restaurant — nothing is real
```

**Report back only the ones that fail.** Paste what you said and what it said.

---

## A. The everyday path (must all pass)

| Say | Should happen |
|---|---|
| *"Bonjour, une table pour deux demain à sept heures."* | Offers 7 PM — **not 7 AM** |
| *"Do you have anything Friday evening for four?"* | Offers dinner times only, no lunch |
| *"Une table pour deux ce soir."* | Offers tonight's remaining times |
| Give a name and number when asked | **Reads the number back digit by digit** before booking |

**The one to watch:** it must never say "booked" before the tool returns.

---

## B. Times and dates (where voice agents usually break)

| Say | Should happen |
|---|---|
| *"Sept heures"* (no AM/PM) | **7 PM.** 1–8 with no context is always evening |
| *"Dix-huit heures trente"* | 6:30 PM |
| *"Neuf heures"* | 9 **AM** — 9+ stays morning without context |
| *"Demain soir"* | Tomorrow, dinner. **Must not ask you what date that is** |
| *"Vendredi prochain"* | Next Friday, not this one |
| *"Le 15"* | The 15th of this month, or next if it's passed |

If it ever asks *"what date is tomorrow?"* — that's a bug, tell me.

---

## C. When it can't give you what you asked for

This is the **43% of real calls** the old system transferred. It should never
dead-end you.

| Say | Should happen |
|---|---|
| Ask for a time that's full | Offers **two nearby times**, not a brush-off |
| *"Non, ça ne marche pas"* to those | Offers **another day** |
| Refuse that too | Offers to **take your number for a callback** |

**It should never say "let me transfer you" as its first move.**

---

## D. The things it must refuse

| Say | Should happen |
|---|---|
| *"Une table pour huit"* | **Cannot book it.** Offers a person — Libro can't hold 7+ |
| *"C'est combien pour un plateau du chef?"* | **Refuses to quote prices**, points at the online menu |
| *"Je veux commander des plats à emporter"* | Offers the website **or** a callback — not a table |
| *"Vous avez des options végétaliennes?"* | *"Not always fully"* — not a flat yes |
| *"Vous êtes ouverts dimanche midi?"* | **Closed Sunday lunch.** Dinner only |
| Ask something not in the FAQ | Takes a message. **Must not invent an answer** |

Prices and invented facts are the two that would actually embarrass the owner.

---

## E. Being difficult (real callers are)

| Do this | Should happen |
|---|---|
| **Interrupt the greeting** — start talking immediately | It stops and listens. No talking over you |
| Start French, switch to English mid-call | Follows you |
| Mumble a number, then correct it | Uses the correction, doesn't re-ask twice |
| Say *"okay"* / *"merci"* mid-flow | Keeps going — those aren't goodbyes |
| Go silent for 10 seconds | Waits, doesn't spiral |
| *"Are you a robot?"* | Says yes plainly |

---

## F. Then, and only then — one real booking

Once A–E look right:

```
AGENT_RESERVATION_BACKEND=libro-private
```

Book **one** table, **far out** (2031), by voice. Then:

```powershell
python scripts/verify_booking.py       # confirm it exists
python scripts/calls.py                # confirm the agent logged it
```

Cancel it, verify the cancellation, and check the Libro dashboard with your own
eyes.

**Standing rule:** far-future date → cancelled in the same session → verified
visually. Never a near-term date on the real floor.

---

## What I'd expect to be imperfect

Being honest about where I'd look first if something feels off:

- **Latency.** First token measured 1.16–1.48s against a 200–700ms budget. It
  will feel slightly slow. That's a known gap, not a surprise.
- **Prompt caching isn't firing** (`prompt_cached_tokens: 0`), so every turn pays
  full prefill.
- **Section C** (the cascade) has the most logic behind it and the least real-world
  exercise. If anything is subtly wrong, I'd bet on that.
