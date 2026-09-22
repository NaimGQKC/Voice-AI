"""Measure what actually matters for a voice agent: time-to-first-token, and
whether the model picks the right tool with the right arguments.

    python scripts/benchmark_llm.py --list                 # inspect the prompt set, no keys needed
    python scripts/benchmark_llm.py --schemas              # dump the tool schemas it will send
    python scripts/benchmark_llm.py                        # run every model whose key is present
    python scripts/benchmark_llm.py --model groq-70b --repeat 3
    python scripts/benchmark_llm.py --sleep 2.5 --json out/bench.json

Why this exists
---------------
`llama-3.3-70b-versatile` on Groq measured a **3.55s worst-case first token**
in this project. Published third-party benchmarks put the same model at ~0.95s.
That 4x gap is the whole question, and it has two very different answers:

  * If it is **inference**, the fix is a smaller/faster model — and we pay for
    that in tool-calling accuracy.
  * If it is **queueing** (free-tier rate limiting), the fix is a paid plan on
    the *same* model, and downgrading would be a pure loss.

Groq answers this directly: every response carries `x_groq.usage` with
`queue_time`, `prompt_time` and `completion_time` broken out. This harness
records all three, so a single run separates "the model is slow" from "we are
being made to wait". See docs/LLM_BENCHMARK.md.

Design notes
------------
* **No keys, no crash.** With no API keys set it prints what is missing and
  exits 0. `--list` and `--schemas` work with no keys and no network at all.
* **Real prompts, real tools.** The system prompt comes from
  `resto_agent.prompts`; the tool schemas are extracted *from the AST of*
  `src/resto_agent/tools.py`, so they cannot drift from the agent and importing
  `livekit-agents` is not required.
* **Percentiles, not averages.** A model with a fine average and a 3s p90 is
  unusable on a phone — the worst case is what makes a caller hang up.
* **Raw HTTP on purpose.** One code path for Groq, OpenAI and Anthropic keeps
  the timing instrumentation identical across providers, and keeps this file
  runnable on the repo's core dependencies (`httpx`) with no SDK installs.

Nothing here is hardcoded: every key is read from the environment.
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import json
import math
import os
import re
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

try:
    import httpx
except ImportError:  # pragma: no cover - dependency check
    print("httpx is required:  pip install -e .", file=sys.stderr)
    raise SystemExit(1)

TOOLS_PY = ROOT / "src" / "resto_agent" / "tools.py"

#: Fixed "today" so relative dates ("tomorrow evening") resolve identically on
#: every run and results stay comparable across days.
BENCH_TODAY = "2026-07-25"  # a Saturday

GREEN, RED, YELLOW, DIM, BOLD, END = (
    "\033[92m", "\033[91m", "\033[93m", "\033[2m", "\033[1m", "\033[0m"
)


# --------------------------------------------------------------------------
# Candidate models
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Candidate:
    key: str                 # short name used on the command line
    provider: str            # "openai-compat" | "anthropic"
    model: str               # provider's model id
    base_url: str
    env_key: str
    label: str
    notes: str = ""


CANDIDATES: tuple[Candidate, ...] = (
    Candidate(
        key="groq-70b",
        provider="openai-compat",
        model="llama-3.3-70b-versatile",
        base_url="https://api.groq.com/openai/v1",
        env_key="GROQ_API_KEY",
        label="Groq · Llama 3.3 70B",
        notes="current production model; the 3.55s worst case came from here",
    ),
    Candidate(
        key="groq-8b",
        provider="openai-compat",
        model="llama-3.1-8b-instant",
        base_url="https://api.groq.com/openai/v1",
        env_key="GROQ_API_KEY",
        label="Groq · Llama 3.1 8B",
        notes="the naive 'just use a smaller model' fix",
    ),
    Candidate(
        key="openai-4o-mini",
        provider="openai-compat",
        model="gpt-4o-mini",
        base_url="https://api.openai.com/v1",
        env_key="OPENAI_API_KEY",
        label="OpenAI · GPT-4o mini",
        notes="tool-calling reference point (BFCL ~83%)",
    ),
    # Structure left open for Anthropic, as asked. Model id verified against the
    # current model catalog: `claude-haiku-4-5` (full id claude-haiku-4-5-20251001).
    Candidate(
        key="claude-haiku",
        provider="anthropic",
        model="claude-haiku-4-5",
        base_url="https://api.anthropic.com/v1",
        env_key="ANTHROPIC_API_KEY",
        label="Anthropic · Claude Haiku 4.5",
        notes="never yet exercised in this project; adapter is here so it can be",
    ),
)


# --------------------------------------------------------------------------
# Tool schemas, extracted from the real tools.py (no livekit import needed)
# --------------------------------------------------------------------------
_PY_TO_JSON = {"str": "string", "int": "integer", "float": "number", "bool": "boolean"}


def _split_docstring(doc: str) -> tuple[str, dict[str, str]]:
    """Return (description, {arg_name: arg_description}) from a Google-style docstring."""
    lines = doc.expandtabs().splitlines()
    head: list[str] = []
    args: dict[str, str] = {}

    i = 0
    while i < len(lines) and lines[i].strip() != "Args:":
        head.append(lines[i].strip())
        i += 1

    if i < len(lines):
        i += 1  # skip the "Args:" line
        body = [ln for ln in lines[i:] if ln.strip()]
        base_indent = min((len(ln) - len(ln.lstrip()) for ln in body), default=0)
        current: str | None = None
        for raw in lines[i:]:
            if not raw.strip():
                continue
            indent = len(raw) - len(raw.lstrip())
            m = re.match(r"([A-Za-z_]\w*):\s*(.*)$", raw.strip())
            if m and indent <= base_indent:
                current = m.group(1)
                args[current] = m.group(2).strip()
            elif current:
                args[current] = (args[current] + " " + raw.strip()).strip()

    description = " ".join(x for x in head if x).strip()
    return re.sub(r"\s+", " ", description), {k: re.sub(r"\s+", " ", v) for k, v in args.items()}


def load_tool_schemas(path: Path = TOOLS_PY) -> list[dict[str, Any]]:
    """Build OpenAI-format tool schemas from the AST of the agent's real tools.

    Parsing rather than importing keeps this script runnable without the heavy
    ``[agent]`` extras, and guarantees the benchmark tests the tools the agent
    actually exposes.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    cls = next(
        (n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "ReservationAgent"),
        None,
    )
    if cls is None:
        raise RuntimeError(f"ReservationAgent not found in {path}")

    schemas: list[dict[str, Any]] = []
    for node in cls.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        decorators = {
            d.id if isinstance(d, ast.Name) else getattr(d, "attr", "")
            for d in node.decorator_list
        }
        if "function_tool" not in decorators:
            continue

        description, arg_docs = _split_docstring(ast.get_docstring(node) or "")

        a = node.args
        params = [p for p in a.args if p.arg != "self"] + list(a.kwonlyargs)
        defaults: dict[str, bool] = {}
        pos = [p for p in a.args if p.arg != "self"]
        n_defaults = len(a.defaults)
        for idx, p in enumerate(pos):
            defaults[p.arg] = idx >= len(pos) - n_defaults
        for p, d in zip(a.kwonlyargs, a.kw_defaults):
            defaults[p.arg] = d is not None

        properties: dict[str, Any] = {}
        required: list[str] = []
        for p in params:
            ann = ast.unparse(p.annotation) if p.annotation else "str"
            properties[p.arg] = {
                "type": _PY_TO_JSON.get(ann, "string"),
                "description": arg_docs.get(p.arg, ""),
            }
            if not defaults.get(p.arg, False):
                required.append(p.arg)

        schemas.append({
            "type": "function",
            "function": {
                "name": node.name,
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            },
        })
    return schemas


def to_anthropic_tools(schemas: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "name": s["function"]["name"],
            "description": s["function"]["description"],
            "input_schema": s["function"]["parameters"],
        }
        for s in schemas
    ]


# --------------------------------------------------------------------------
# The prompt set — drawn from this agent's actual job
# --------------------------------------------------------------------------
ArgCheck = Callable[[dict[str, Any]], list[str]]


@dataclass(frozen=True)
class Case:
    id: str
    lang: str            # en | fr
    category: str
    turns: tuple[dict[str, Any], ...]
    #: Tool names that count as correct. ``None`` in the tuple means "answering
    #: without a tool call is acceptable here".
    accept: tuple[str | None, ...]
    #: Tool names that are *wrong* here — calling one is a hard failure even if
    #: some accepted tool was also called.
    forbid: tuple[str, ...] = ()
    check: ArgCheck | None = None
    why: str = ""


def _s(v: Any) -> str:
    return str(v or "").lower()


def _need(args: dict, name: str, *substrings: str) -> list[str]:
    """`args[name]` must be present and contain one of `substrings` (if given)."""
    val = args.get(name)
    if val in (None, "", 0):
        return [f"missing {name}"]
    if substrings and not any(sub in _s(val) for sub in substrings):
        return [f"{name}={val!r} lacks any of {substrings}"]
    return []


def _party(n: int) -> ArgCheck:
    def check(args: dict) -> list[str]:
        got = args.get("party_size")
        try:
            got = int(got)
        except (TypeError, ValueError):
            return [f"party_size={args.get('party_size')!r} not an int"]
        return [] if got == n else [f"party_size={got}, expected {n}"]
    return check


def _all(*checks: ArgCheck) -> ArgCheck:
    def check(args: dict) -> list[str]:
        out: list[str] = []
        for c in checks:
            out += c(args)
        return out
    return check


def _has(name: str, *substrings: str) -> ArgCheck:
    return lambda args: _need(args, name, *substrings)


# A prior availability result, so booking cases have a real ISO slot to quote.
_AVAIL_TOOL_ID = "call_avail_1"
_AVAIL_RESULT = (
    "Available tomorrow (Sunday July 26) for 2: 6:00 PM, 6:30 PM, 7:00 PM, 8:15 PM. "
    "Exact ISO slots: 2026-07-26T18:00:00-04:00, 2026-07-26T18:30:00-04:00, "
    "2026-07-26T19:00:00-04:00, 2026-07-26T20:15:00-04:00."
)
_FULL_RESULT = (
    "Nothing available Saturday August 1 for 4 guests at any seating. "
    "The closest options are Friday July 31 at 7:00 PM or Sunday August 2 at 6:30 PM."
)


def _availability_turns(user: str, tool_result: str = _AVAIL_RESULT) -> list[dict[str, Any]]:
    """A caller turn, the agent's availability check, and the tool's answer."""
    return [
        {"role": "user", "content": user},
        {
            "role": "assistant",
            "content": "Let me check that for you.",
            "tool_calls": [{
                "id": _AVAIL_TOOL_ID,
                "type": "function",
                "function": {
                    "name": "check_availability",
                    "arguments": json.dumps(
                        {"date": "tomorrow", "party_size": 2, "part_of_day": "dinner",
                         "preferred_time": "7"}
                    ),
                },
            }],
        },
        {"role": "tool", "tool_call_id": _AVAIL_TOOL_ID, "content": tool_result},
    ]


CASES: tuple[Case, ...] = (
    # ---------------------------------------------------------------- English
    Case(
        id="en_book_explicit_time",
        lang="en", category="booking · explicit time",
        turns=({"role": "user",
                "content": "Hi, I'd like a table for two tomorrow at seven."},),
        accept=("check_availability",),
        forbid=("book_reservation", "handle_takeout"),
        check=_all(_party(2), _has("date", "tomorrow"), _has("preferred_time", "7", "seven")),
        why="Must check before promising anything; must not book on the first turn.",
    ),
    Case(
        id="en_relative_date",
        lang="en", category="booking · relative date",
        turns=({"role": "user",
                "content": "Do you have anything tomorrow evening for four people?"},),
        accept=("check_availability",),
        forbid=("book_reservation",),
        check=_all(_party(4), _has("date", "tomorrow"),
                   _has("part_of_day", "dinner", "evening", "tonight")),
        why="'tomorrow evening' must go straight to the tool — never ask the caller the date.",
    ),
    Case(
        id="en_relative_weekday",
        lang="en", category="booking · relative weekday",
        turns=({"role": "user",
                "content": "What about this Friday, around eight, for three of us?"},),
        accept=("check_availability",),
        check=_all(_party(3), _has("date", "friday"), _has("preferred_time", "8", "eight")),
        why="Weekday words are resolved by Python, not the model — just pass them through.",
    ),
    Case(
        id="en_ambiguous_hour_pm",
        lang="en", category="booking · ambiguous hour",
        turns=({"role": "user", "content": "Book us for one, there'll be two of us."},),
        accept=("check_availability",),
        forbid=("book_reservation",),
        check=_all(_party(2), _has("preferred_time", "1", "one")),
        why="'one' is 1 PM. The model just has to forward it; the resolver decides AM/PM.",
    ),
    Case(
        id="en_book_after_confirm",
        lang="en", category="booking · commit",
        turns=tuple(_availability_turns(
            "Table for two tomorrow at seven, please."
        ) + [
            {"role": "assistant",
             "content": "I have seven o'clock tomorrow for two. Can I get your first name and "
                        "a phone number?"},
            {"role": "user",
             "content": "It's Marc, and my number is five one four, five five five, "
                        "zero one four seven."},
            {"role": "assistant",
             "content": "Thanks Marc. So that's tomorrow, Sunday July 26th, at 7 PM for two, "
                        "under five one four, five five five, zero one four seven. "
                        "Is that all correct?"},
            {"role": "user", "content": "Yes, that's right."},
        ]),
        accept=("book_reservation",),
        forbid=("check_availability", "take_message"),
        check=_all(_party(2), _has("time", "2026-07-26t19"), _has("first_name", "marc"),
                   _has("phone", "514", "5145550147", "0147")),
        why="Only book after an explicit yes, and only with an ISO slot from the tool result.",
    ),
    Case(
        id="en_fully_booked_cascade",
        lang="en", category="cascade · nothing available",
        turns=tuple([
            {"role": "user", "content": "I need a table for four on Saturday August first."},
            {"role": "assistant", "content": "Let me check that for you.",
             "tool_calls": [{
                 "id": "call_full_1", "type": "function",
                 "function": {"name": "check_availability", "arguments": json.dumps(
                     {"date": "Saturday August 1", "party_size": 4, "part_of_day": "dinner"})},
             }]},
            {"role": "tool", "tool_call_id": "call_full_1", "content": _FULL_RESULT},
        ]),
        accept=(None,),
        forbid=("book_reservation", "take_message", "join_waitlist"),
        why="Offer the two nearby times first. Don't jump to a message or the waitlist.",
    ),
    Case(
        id="en_waitlist_after_cascade",
        lang="en", category="cascade · waitlist",
        turns=tuple([
            {"role": "user", "content": "I need a table for four on Saturday August first."},
            {"role": "assistant", "content": "Let me check that for you.",
             "tool_calls": [{
                 "id": "call_full_2", "type": "function",
                 "function": {"name": "check_availability", "arguments": json.dumps(
                     {"date": "Saturday August 1", "party_size": 4, "part_of_day": "dinner"})},
             }]},
            {"role": "tool", "tool_call_id": "call_full_2", "content": _FULL_RESULT},
            {"role": "assistant",
             "content": "Saturday is full, but I have Friday the 31st at 7, or Sunday the 2nd "
                        "at 6:30. Would either work?"},
            {"role": "user",
             "content": "No, it has to be Saturday. Can you text me if something opens up? "
                        "I'm Dana Whitfield, 438-555-0112."},
        ]),
        accept=("join_waitlist",),
        forbid=("book_reservation", "check_availability"),
        check=_all(_has("name", "dana"), _has("phone", "438", "0112"), _party(4)),
        why="'let me know if something opens' is the waitlist, not a generic message.",
    ),
    Case(
        id="en_takeout",
        lang="en", category="takeout",
        turns=({"role": "user",
                "content": "Hi, I'd like to order some food for pickup tonight."},),
        accept=("handle_takeout",),
        forbid=("check_availability", "book_reservation", "answer_faq"),
        why="One caller in six is ordering food. Never route them into a table booking.",
    ),
    Case(
        id="en_delivery",
        lang="en", category="takeout · delivery",
        turns=({"role": "user",
                "content": "Do you deliver? I want two chicken teriyaki and a spicy tuna roll."},),
        accept=("handle_takeout",),
        forbid=("check_availability", "book_reservation"),
        why="Delivery is the same path as takeout, and the order should be captured verbatim.",
    ),
    Case(
        id="en_faq_hours",
        lang="en", category="faq",
        turns=({"role": "user", "content": "What time do you close on a weeknight?"},),
        accept=("answer_faq",),
        forbid=("check_availability", "take_message"),
        check=_has("topic", "hours"),
        why="Hours are a known fact — read them from the FAQ, never improvise.",
    ),
    Case(
        id="en_faq_parking",
        lang="en", category="faq",
        turns=({"role": "user", "content": "Is there parking near you?"},),
        accept=("answer_faq",),
        check=_has("topic", "parking"),
        why="",
    ),
    Case(
        id="en_faq_unknown_policy",
        lang="en", category="faq · deliberate gap",
        turns=({"role": "user",
                "content": "What's your cancellation policy if I have to cancel same day? "
                           "My name's Priya Raman, 514-555-0166."},),
        accept=("take_message", "answer_faq"),
        forbid=("check_availability", "book_reservation"),
        why="The venue has never defined this. A plausible invented answer is the worst outcome; "
            "take_message is right, answer_faq is tolerable, inventing is not.",
    ),
    Case(
        id="en_party_of_eight",
        lang="en", category="escalation · large party",
        turns=({"role": "user",
                "content": "We're a party of eight for Saturday night — can you fit us in? "
                           "I'm Tom Fielding, 514-555-0188."},),
        accept=(None, "take_message"),
        forbid=("book_reservation", "check_availability", "join_waitlist"),
        why="7+ cannot be checked or booked through Libro at all. This must escalate to a human.",
    ),
    Case(
        id="en_lookup",
        lang="en", category="lookup",
        turns=({"role": "user",
                "content": "Can you check what time my reservation is? It's under "
                           "514-555-0123."},),
        accept=("lookup_reservation",),
        check=_has("phone", "514", "0123"),
        why="",
    ),
    Case(
        id="en_cancel",
        lang="en", category="cancel",
        turns=({"role": "user",
                "content": "I need to cancel tonight's reservation, it's under 514-555-0123."},),
        accept=("cancel_reservation",),
        forbid=("book_reservation", "reschedule_reservation"),
        check=_has("phone", "514", "0123"),
        why="",
    ),
    Case(
        id="en_reschedule",
        lang="en", category="reschedule",
        turns=tuple([
            {"role": "user",
             "content": "I have a booking under 514-555-0123, can I move it to tomorrow "
                        "at seven instead?"},
            {"role": "assistant", "content": "Let me check that for you.",
             "tool_calls": [{
                 "id": "call_avail_3", "type": "function",
                 "function": {"name": "check_availability", "arguments": json.dumps(
                     {"date": "tomorrow", "party_size": 2, "preferred_time": "7"})},
             }]},
            {"role": "tool", "tool_call_id": "call_avail_3", "content": _AVAIL_RESULT},
            {"role": "assistant", "content": "Seven o'clock tomorrow is open. Shall I move it?"},
            {"role": "user", "content": "Yes please."},
        ]),
        accept=("reschedule_reservation",),
        forbid=("book_reservation", "cancel_reservation"),
        check=_all(_has("new_time", "2026-07-26t19"), _has("phone", "514", "0123")),
        why="Move, don't cancel-and-rebook; and use the ISO slot the tool returned.",
    ),
    Case(
        id="en_allergy_note",
        lang="en", category="booking · allergy",
        turns=tuple(_availability_turns("Two people tomorrow at seven, please.") + [
            {"role": "assistant", "content": "Seven o'clock tomorrow works for two. "
                                             "Can I get your first name and a number?"},
            {"role": "user",
             "content": "Sarah, 514-555-0177. One of us has a severe shellfish allergy."},
            {"role": "assistant",
             "content": "Got it — a severe shellfish allergy. So tomorrow at 7 PM for two, "
                        "under Sarah at five one four, five five five, zero one seven seven. "
                        "Correct?"},
            {"role": "user", "content": "Correct."},
        ]),
        accept=("book_reservation",),
        check=_all(_party(2), _has("first_name", "sarah"), _has("note", "shellfish")),
        why="Losing the allergy in the note is the most serious mistake on this call.",
    ),
    Case(
        id="en_midcall_acknowledgement",
        lang="en", category="conversation · not a goodbye",
        turns=tuple(_availability_turns("Anything tomorrow evening for two?") + [
            {"role": "assistant",
             "content": "I have 6, 6:30, 7 or 8:15 tomorrow evening. Which suits you?"},
            {"role": "user", "content": "Okay, great, thanks."},
        ]),
        accept=(None,),
        forbid=("book_reservation", "take_message", "check_availability", "handle_takeout"),
        why="Mid-call 'okay thanks' is acknowledgement, not a goodbye and not a booking.",
    ),

    # ----------------------------------------------------------------- French
    Case(
        id="fr_book_explicit_time",
        lang="fr", category="booking · explicit time",
        turns=({"role": "user",
                "content": "Bonjour, je voudrais réserver une table pour deux personnes "
                           "demain à dix-neuf heures."},),
        accept=("check_availability",),
        forbid=("book_reservation",),
        check=_all(_party(2), _has("date", "demain", "tomorrow")),
        why="Bill 96 makes French service a statutory right — French must work as well as English.",
    ),
    Case(
        id="fr_relative_date",
        lang="fr", category="booking · relative date",
        turns=({"role": "user",
                "content": "Est-ce que vous avez de la place demain soir pour quatre ?"},),
        accept=("check_availability",),
        check=_all(_party(4), _has("date", "demain", "tomorrow"),
                   _has("part_of_day", "dinner", "souper", "soir", "evening")),
        why="'demain soir' is the French version of the relative-date case.",
    ),
    Case(
        id="fr_takeout",
        lang="fr", category="takeout",
        turns=({"role": "user",
                "content": "Allô, je voudrais commander pour emporter, s'il vous plaît."},),
        accept=("handle_takeout",),
        forbid=("check_availability", "book_reservation"),
        why="",
    ),
    Case(
        id="fr_faq_hours",
        lang="fr", category="faq",
        turns=({"role": "user", "content": "Quelles sont vos heures d'ouverture le dimanche ?"},),
        accept=("answer_faq",),
        forbid=("check_availability",),
        check=_has("topic", "hours"),
        why="",
    ),
    Case(
        id="fr_large_party",
        lang="fr", category="escalation · large party",
        turns=({"role": "user",
                "content": "Nous serions huit personnes samedi soir. Je m'appelle "
                           "Étienne Loiselle, mon numéro est le 438-555-0144."},),
        accept=(None, "take_message"),
        forbid=("book_reservation", "check_availability", "join_waitlist"),
        why="The party-size ceiling must hold in French too.",
    ),
    Case(
        id="fr_cancel",
        lang="fr", category="cancel",
        turns=({"role": "user",
                "content": "J'aimerais annuler ma réservation de ce soir, c'est au nom du "
                           "514-555-0198."},),
        accept=("cancel_reservation",),
        forbid=("book_reservation",),
        check=_has("phone", "514", "0198"),
        why="",
    ),
)


def build_system_prompt(today: str = BENCH_TODAY, multilingual: bool = True) -> str:
    from resto_agent.prompts import system_instructions  # noqa: PLC0415 - keeps --help key-free

    return system_instructions(multilingual=multilingual, today=today)


# --------------------------------------------------------------------------
# One measured request
# --------------------------------------------------------------------------
@dataclass
class Sample:
    case_id: str
    lang: str
    category: str
    model_key: str
    repeat: int
    ok: bool = False
    error: str = ""
    http_status: int | None = None
    ttft_s: float | None = None          # request sent -> first content/tool-call delta
    total_s: float | None = None         # request sent -> stream complete
    tool_called: str | None = None
    tool_args: dict[str, Any] = field(default_factory=dict)
    text: str = ""
    tool_correct: bool | None = None
    arg_failures: list[str] = field(default_factory=list)
    # Server-side breakdown — this is the free-vs-paid evidence
    queue_time_s: float | None = None
    prompt_time_s: float | None = None
    completion_time_s: float | None = None
    server_total_s: float | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    ratelimit_remaining_tokens: str | None = None
    ratelimit_reset_tokens: str | None = None
    region: str | None = None

    def grade(self, case: Case) -> None:
        if not self.ok:
            return
        called = self.tool_called
        if called in case.forbid:
            self.tool_correct = False
            self.arg_failures.append(f"called forbidden tool {called!r}")
            return
        self.tool_correct = called in case.accept
        if self.tool_correct and case.check and called is not None:
            self.arg_failures = case.check(self.tool_args)


async def _run_openai_compat(
    client: httpx.AsyncClient, cand: Candidate, api_key: str,
    system: str, turns: Sequence[dict], tools: Sequence[dict],
    max_tokens: int, timeout: float,
) -> Sample:
    s = Sample(case_id="", lang="", category="", model_key=cand.key, repeat=0)
    body = {
        "model": cand.model,
        "messages": [{"role": "system", "content": system}, *turns],
        "tools": list(tools),
        "tool_choice": "auto",
        "temperature": 0.0,
        "max_tokens": max_tokens,
        "stream": True,
        # Groq streams usage (incl. queue_time) only when asked for it.
        "stream_options": {"include_usage": True},
    }
    started = time.perf_counter()
    tool_name: str | None = None
    tool_args_raw = ""
    text_parts: list[str] = []

    try:
        async with client.stream(
            "POST", f"{cand.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}",
                     "Content-Type": "application/json"},
            json=body, timeout=timeout,
        ) as resp:
            s.http_status = resp.status_code
            s.ratelimit_remaining_tokens = resp.headers.get("x-ratelimit-remaining-tokens")
            s.ratelimit_reset_tokens = resp.headers.get("x-ratelimit-reset-tokens")
            s.region = resp.headers.get("x-groq-region")
            if resp.status_code != 200:
                detail = (await resp.aread()).decode("utf-8", "replace")[:300]
                s.error = f"HTTP {resp.status_code}: {detail}"
                return s

            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    chunk = json.loads(payload)
                except json.JSONDecodeError:
                    continue

                # Groq attaches server-side timings to the final chunk.
                xg = chunk.get("x_groq") or {}
                usage = xg.get("usage") or chunk.get("usage") or {}
                if usage:
                    s.queue_time_s = usage.get("queue_time", s.queue_time_s)
                    s.prompt_time_s = usage.get("prompt_time", s.prompt_time_s)
                    s.completion_time_s = usage.get("completion_time", s.completion_time_s)
                    s.server_total_s = usage.get("total_time", s.server_total_s)
                    s.prompt_tokens = usage.get("prompt_tokens", s.prompt_tokens)
                    s.completion_tokens = usage.get("completion_tokens", s.completion_tokens)

                for choice in chunk.get("choices") or []:
                    delta = choice.get("delta") or {}
                    produced = False
                    if delta.get("content"):
                        text_parts.append(delta["content"])
                        produced = True
                    for tc in delta.get("tool_calls") or []:
                        fn = tc.get("function") or {}
                        if fn.get("name"):
                            tool_name = fn["name"]
                            produced = True
                        if fn.get("arguments"):
                            tool_args_raw += fn["arguments"]
                            produced = True
                    if produced and s.ttft_s is None:
                        s.ttft_s = time.perf_counter() - started

        s.total_s = time.perf_counter() - started
        s.tool_called = tool_name
        s.text = "".join(text_parts)
        if tool_args_raw:
            try:
                s.tool_args = json.loads(tool_args_raw)
            except json.JSONDecodeError:
                s.tool_args = {}
                s.error = "tool arguments were not valid JSON"
        s.ok = not s.error
    except Exception as exc:  # noqa: BLE001 - a benchmark must survive any failure
        s.total_s = time.perf_counter() - started
        s.error = f"{type(exc).__name__}: {exc}"
    return s


async def _run_anthropic(
    client: httpx.AsyncClient, cand: Candidate, api_key: str,
    system: str, turns: Sequence[dict], tools: Sequence[dict],
    max_tokens: int, timeout: float,
) -> Sample:
    """Anthropic Messages API (different wire format from the OpenAI shape).

    Untested against a live key in this repo — see docs/LLM_BENCHMARK.md.
    """
    s = Sample(case_id="", lang="", category="", model_key=cand.key, repeat=0)
    body = {
        "model": cand.model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": _to_anthropic_messages(turns),
        "tools": to_anthropic_tools(tools),
        "temperature": 0.0,
        "stream": True,
    }
    started = time.perf_counter()
    tool_name: str | None = None
    tool_args_raw = ""
    text_parts: list[str] = []

    try:
        async with client.stream(
            "POST", f"{cand.base_url}/messages",
            headers={"x-api-key": api_key, "anthropic-version": "2023-06-01",
                     "Content-Type": "application/json"},
            json=body, timeout=timeout,
        ) as resp:
            s.http_status = resp.status_code
            if resp.status_code != 200:
                detail = (await resp.aread()).decode("utf-8", "replace")[:300]
                s.error = f"HTTP {resp.status_code}: {detail}"
                return s

            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                try:
                    ev = json.loads(line[5:].strip())
                except json.JSONDecodeError:
                    continue
                etype = ev.get("type")

                if etype == "content_block_start":
                    block = ev.get("content_block") or {}
                    if block.get("type") == "tool_use":
                        tool_name = block.get("name")
                        if s.ttft_s is None:
                            s.ttft_s = time.perf_counter() - started
                elif etype == "content_block_delta":
                    d = ev.get("delta") or {}
                    if d.get("type") == "text_delta" and d.get("text"):
                        text_parts.append(d["text"])
                        if s.ttft_s is None:
                            s.ttft_s = time.perf_counter() - started
                    elif d.get("type") == "input_json_delta":
                        tool_args_raw += d.get("partial_json") or ""
                        if s.ttft_s is None:
                            s.ttft_s = time.perf_counter() - started
                elif etype in ("message_start", "message_delta"):
                    usage = (ev.get("message") or {}).get("usage") or ev.get("usage") or {}
                    s.prompt_tokens = usage.get("input_tokens", s.prompt_tokens)
                    s.completion_tokens = usage.get("output_tokens", s.completion_tokens)

        s.total_s = time.perf_counter() - started
        s.tool_called = tool_name
        s.text = "".join(text_parts)
        if tool_args_raw:
            try:
                s.tool_args = json.loads(tool_args_raw)
            except json.JSONDecodeError:
                s.error = "tool arguments were not valid JSON"
        s.ok = not s.error
    except Exception as exc:  # noqa: BLE001
        s.total_s = time.perf_counter() - started
        s.error = f"{type(exc).__name__}: {exc}"
    return s


def _to_anthropic_messages(turns: Sequence[dict]) -> list[dict]:
    """Translate the OpenAI-shaped fixture turns into Anthropic content blocks."""
    out: list[dict] = []
    for t in turns:
        role = t["role"]
        if role == "user":
            out.append({"role": "user", "content": t["content"]})
        elif role == "assistant":
            blocks: list[dict] = []
            if t.get("content"):
                blocks.append({"type": "text", "text": t["content"]})
            for tc in t.get("tool_calls") or []:
                fn = tc["function"]
                blocks.append({
                    "type": "tool_use", "id": tc["id"], "name": fn["name"],
                    "input": json.loads(fn["arguments"]),
                })
            out.append({"role": "assistant", "content": blocks})
        elif role == "tool":
            block = {"type": "tool_result", "tool_use_id": t["tool_call_id"],
                     "content": t["content"]}
            if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list):
                out[-1]["content"].append(block)
            else:
                out.append({"role": "user", "content": [block]})
    return out


# --------------------------------------------------------------------------
# Runner
# --------------------------------------------------------------------------
async def run_candidate(
    cand: Candidate, api_key: str, cases: Sequence[Case], schemas: Sequence[dict],
    *, repeats: int, sleep: float, max_tokens: int, timeout: float, verbose: bool,
) -> list[Sample]:
    system = build_system_prompt()
    samples: list[Sample] = []
    runner = _run_anthropic if cand.provider == "anthropic" else _run_openai_compat

    async with httpx.AsyncClient() as client:
        for r in range(1, repeats + 1):
            for case in cases:
                s = await runner(client, cand, api_key, system, case.turns,
                                 schemas, max_tokens, timeout)
                s.case_id, s.lang, s.category, s.repeat = case.id, case.lang, case.category, r
                s.grade(case)
                samples.append(s)
                if verbose:
                    _print_sample(case, s)
                if sleep:
                    await asyncio.sleep(sleep)
    return samples


def _print_sample(case: Case, s: Sample) -> None:
    if not s.ok:
        print(f"  {RED}[err]{END}  {case.id:<28} {s.error[:90]}")
        return
    mark = f"{GREEN}ok {END}" if s.tool_correct and not s.arg_failures else (
        f"{YELLOW}args{END}" if s.tool_correct else f"{RED}TOOL{END}")
    q = f" queue={s.queue_time_s * 1000:.0f}ms" if s.queue_time_s is not None else ""
    detail = ""
    if not s.tool_correct:
        detail = f"  got={s.tool_called!r} want={case.accept}"
    elif s.arg_failures:
        detail = "  " + "; ".join(s.arg_failures)
    print(f"  [{mark}] {case.id:<28} ttft={(s.ttft_s or 0) * 1000:6.0f}ms "
          f"total={(s.total_s or 0) * 1000:6.0f}ms{q}{detail}")


# --------------------------------------------------------------------------
# Statistics & reporting
# --------------------------------------------------------------------------
def pct(values: Sequence[float], q: float) -> float | None:
    """Nearest-rank percentile. With few samples this is the honest one."""
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    idx = max(0, min(len(vals) - 1, math.ceil(q * len(vals)) - 1))
    return vals[idx]


def summarize(cand: Candidate, samples: Sequence[Sample]) -> dict[str, Any]:
    ok = [s for s in samples if s.ok]
    ttfts = [s.ttft_s for s in ok if s.ttft_s is not None]
    totals = [s.total_s for s in ok if s.total_s is not None]
    queues = [s.queue_time_s for s in ok if s.queue_time_s is not None]
    prompts = [s.prompt_time_s for s in ok if s.prompt_time_s is not None]

    def stat(vals: Sequence[float]) -> dict[str, float | None]:
        return {
            "n": len(vals),
            "p50": pct(vals, 0.50),
            "p90": pct(vals, 0.90),
            "max": max(vals) if vals else None,
            "mean": statistics.fmean(vals) if vals else None,
        }

    return {
        "model_key": cand.key,
        "label": cand.label,
        "model": cand.model,
        "provider": cand.provider,
        "requests": len(samples),
        "succeeded": len(ok),
        "failed": len(samples) - len(ok),
        "http_429": sum(1 for s in samples if s.http_status == 429),
        "tool_correct": sum(1 for s in ok if s.tool_correct),
        "fully_correct": sum(1 for s in ok if s.tool_correct and not s.arg_failures),
        "ttft_s": stat(ttfts),
        "total_s": stat(totals),
        "server_queue_s": stat(queues),
        "server_prompt_s": stat(prompts),
        "prompt_tokens_median": (
            statistics.median([s.prompt_tokens for s in ok if s.prompt_tokens])
            if any(s.prompt_tokens for s in ok) else None
        ),
        "errors": sorted({s.error[:120] for s in samples if s.error}),
    }


def _ms(v: float | None) -> str:
    return "—" if v is None else f"{v * 1000:.0f}"


def print_report(summaries: Sequence[dict[str, Any]], budget_ms: int) -> None:
    print(f"\n{BOLD}Time to first token — the number that decides the call{END}")
    print(f"{DIM}Voice budget: {budget_ms}ms for LLM TTFT. Above ~700ms reads as an "
          f"unnatural pause.{END}\n")
    hdr = f"{'model':<28} {'n':>4} {'p50':>8} {'p90':>8} {'worst':>8} {'429s':>5}"
    print(hdr)
    print("-" * len(hdr))
    for s in summaries:
        t = s["ttft_s"]
        p90 = t["p90"]
        colour = GREEN if (p90 or 9) * 1000 <= budget_ms else (
            YELLOW if (p90 or 9) <= 1.0 else RED)
        print(f"{s['label']:<28} {t['n']:>4} {_ms(t['p50']):>8} "
              f"{colour}{_ms(p90):>8}{END} {colour}{_ms(t['max']):>8}{END} "
              f"{s['http_429']:>5}")

    print(f"\n{BOLD}Total completion time{END}")
    print(hdr[:len(hdr) - 6])
    print("-" * (len(hdr) - 6))
    for s in summaries:
        t = s["total_s"]
        print(f"{s['label']:<28} {t['n']:>4} {_ms(t['p50']):>8} {_ms(t['p90']):>8} "
              f"{_ms(t['max']):>8}")

    if any(s["server_queue_s"]["n"] for s in summaries):
        print(f"\n{BOLD}Server-side breakdown (Groq only) — queueing vs inference{END}")
        print(f"{DIM}If queue p90 dominates TTFT p90, the 3.55s is rate limiting, "
              f"not model speed.{END}")
        print(f"{'model':<28} {'queue p50':>10} {'queue p90':>10} {'queue max':>10} "
              f"{'prefill p50':>12}")
        print("-" * 74)
        for s in summaries:
            q, p = s["server_queue_s"], s["server_prompt_s"]
            if not q["n"]:
                continue
            print(f"{s['label']:<28} {_ms(q['p50']):>10} {_ms(q['p90']):>10} "
                  f"{_ms(q['max']):>10} {_ms(p['p50']):>12}")

    print(f"\n{BOLD}Tool selection{END}")
    print(f"{'model':<28} {'ok':>5} {'right tool':>12} {'+right args':>12} {'failed':>8}")
    print("-" * 70)
    for s in summaries:
        n = max(1, s["succeeded"])
        print(f"{s['label']:<28} {s['succeeded']:>5} "
              f"{s['tool_correct']:>7} ({s['tool_correct'] * 100 // n:>2}%) "
              f"{s['fully_correct']:>7} ({s['fully_correct'] * 100 // n:>2}%) "
              f"{s['failed']:>8}")

    for s in summaries:
        if s["errors"]:
            print(f"\n{YELLOW}errors from {s['label']}:{END}")
            for e in s["errors"]:
                print(f"  {e}")


def print_per_case(summaries_samples: dict[str, list[Sample]]) -> None:
    print(f"\n{BOLD}Per-case tool accuracy{END}")
    keys = list(summaries_samples)
    print(f"{'case':<30} " + " ".join(f"{k:>16}" for k in keys))
    print("-" * (30 + 17 * len(keys)))
    for case in CASES:
        row = f"{case.id:<30} "
        for k in keys:
            got = [s for s in summaries_samples[k] if s.case_id == case.id]
            good = sum(1 for s in got if s.ok and s.tool_correct and not s.arg_failures)
            row += f"{f'{good}/{len(got)}':>16} " if got else f"{'—':>16} "
        print(row)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Benchmark LLM candidates on this agent's real prompts and tools.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Exits 0 with an explanation when no API keys are set, so this is safe "
               "to commit and run later.",
    )
    p.add_argument("--model", action="append", dest="models",
                   help="only run this candidate (repeatable). "
                        f"Choices: {', '.join(c.key for c in CANDIDATES)}")
    p.add_argument("--repeat", type=int, default=1,
                   help="passes over the prompt set. >=3 gives a meaningful p90 (default 1)")
    p.add_argument("--sleep", type=float, default=0.0,
                   help="seconds between requests. Use ~2-4s to stay under a free-tier "
                        "TPM cap, or 0 to deliberately provoke queueing (default 0)")
    p.add_argument("--lang", choices=("en", "fr"), help="restrict to one language")
    p.add_argument("--case", action="append", dest="cases", help="run only this case id")
    p.add_argument("--max-tokens", type=int, default=300)
    p.add_argument("--timeout", type=float, default=30.0)
    p.add_argument("--budget-ms", type=int, default=700,
                   help="TTFT budget used to colour the report (default 700)")
    p.add_argument("--json", dest="json_out", help="write full results to this path")
    p.add_argument("--list", action="store_true", help="print the prompt set and exit")
    p.add_argument("--schemas", action="store_true",
                   help="print the tool schemas extracted from tools.py and exit")
    p.add_argument("--system-prompt", action="store_true",
                   help="print the system prompt (and its size) and exit")
    p.add_argument("--per-case", action="store_true", help="add a per-case accuracy table")
    p.add_argument("-q", "--quiet", action="store_true", help="suppress per-request lines")
    args = p.parse_args(argv)

    # ---- offline modes: everything below here works with no keys and no network
    if args.schemas:
        schemas = load_tool_schemas()
        print(json.dumps(schemas, indent=2))
        print(f"\n{len(schemas)} tools, ~{len(json.dumps(schemas)) // 4} tokens of schema",
              file=sys.stderr)
        return 0

    if args.system_prompt:
        sp = build_system_prompt()
        print(sp)
        print(f"\n{DIM}{len(sp)} chars, ~{len(sp) // 4} tokens{END}", file=sys.stderr)
        return 0

    cases = list(CASES)
    if args.lang:
        cases = [c for c in cases if c.lang == args.lang]
    if args.cases:
        wanted = set(args.cases)
        cases = [c for c in cases if c.id in wanted]
    if not cases:
        print("No cases matched those filters.", file=sys.stderr)
        return 1

    if args.list:
        print(f"{BOLD}{len(cases)} cases "
              f"({sum(1 for c in cases if c.lang == 'fr')} French){END}\n")
        for c in cases:
            accept = ", ".join(str(a) for a in c.accept)
            print(f"{BOLD}{c.id}{END}  [{c.lang}] {DIM}{c.category}{END}")
            print(f"  caller: {c.turns[-1]['content'][:100]}")
            print(f"  expect: {accept}" + (f"   forbid: {', '.join(c.forbid)}" if c.forbid else ""))
            if c.why:
                print(f"  {DIM}why: {c.why}{END}")
            print()
        return 0

    # ---- live run: require keys
    schemas = load_tool_schemas()
    selected = [c for c in CANDIDATES if not args.models or c.key in set(args.models)]
    if args.models:
        unknown = set(args.models) - {c.key for c in CANDIDATES}
        if unknown:
            print(f"Unknown model(s): {', '.join(sorted(unknown))}", file=sys.stderr)
            return 1

    runnable = [(c, os.environ.get(c.env_key, "").strip()) for c in selected]
    have = [(c, k) for c, k in runnable if k]
    missing = [c for c, k in runnable if not k]

    if not have:
        print(f"\n{YELLOW}No API keys found — nothing to measure.{END}\n")
        print("This script is deliberately runnable without keys so it can live in the")
        print("repo and be run the moment credentials exist. To run it, set at least one:\n")
        for c in missing:
            print(f"  {c.env_key:<20} -> {c.label}   ({c.notes or 'candidate'})")
        print("\nThen, for a p90 that means anything:")
        print("  export GROQ_API_KEY=...")
        print("  python scripts/benchmark_llm.py --repeat 5 --sleep 3 --json out/bench.json\n")
        print("With no keys you can still inspect what it would send:")
        print("  python scripts/benchmark_llm.py --list")
        print("  python scripts/benchmark_llm.py --schemas")
        print("  python scripts/benchmark_llm.py --system-prompt\n")
        return 0

    if missing:
        print(f"{DIM}skipping (no key): "
              f"{', '.join(f'{c.label} [{c.env_key}]' for c in missing)}{END}")

    print(f"{BOLD}Benchmarking{END} {len(cases)} cases x {args.repeat} "
          f"= {len(cases) * args.repeat} requests per model")
    print(f"{DIM}system prompt ~{len(build_system_prompt()) // 4} tokens, "
          f"{len(schemas)} tool schemas ~{len(json.dumps(schemas)) // 4} tokens, "
          f"sleep={args.sleep}s{END}\n")

    all_samples: dict[str, list[Sample]] = {}
    summaries: list[dict[str, Any]] = []
    for cand, key in have:
        print(f"{BOLD}{cand.label}{END} {DIM}({cand.model}){END}")
        samples = asyncio.run(run_candidate(
            cand, key, cases, schemas,
            repeats=args.repeat, sleep=args.sleep, max_tokens=args.max_tokens,
            timeout=args.timeout, verbose=not args.quiet,
        ))
        all_samples[cand.key] = samples
        summaries.append(summarize(cand, samples))
        print()

    print_report(summaries, args.budget_ms)
    if args.per_case:
        print_per_case(all_samples)

    if args.json_out:
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "bench_today": BENCH_TODAY,
            "config": {"repeat": args.repeat, "sleep_s": args.sleep,
                       "max_tokens": args.max_tokens, "cases": [c.id for c in cases]},
            "summaries": summaries,
            "samples": {
                k: [{kk: vv for kk, vv in vars(s).items() if kk != "text"} for s in v]
                for k, v in all_samples.items()
            },
        }, indent=2, default=str), encoding="utf-8")
        print(f"\n{DIM}wrote {out}{END}")

    worst = [s for s in summaries if (s["ttft_s"]["p90"] or 0) * 1000 > args.budget_ms]
    if worst:
        print(f"\n{YELLOW}{len(worst)} of {len(summaries)} candidates exceed the "
              f"{args.budget_ms}ms p90 budget.{END}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
