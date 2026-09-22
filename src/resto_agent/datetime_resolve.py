"""Deterministic natural-language date resolution.

The single biggest source of live voice-agent bugs is date handling: callers say
"this Friday", "tomorrow", "the 5th", and the model has to turn that into a real
date. Rather than trust the LLM's arithmetic, we resolve dates deterministically
here (relative to "today" in the restaurant's timezone) and let the model pass
either natural language or an absolute date.

Conventions (documented so behavior is predictable):
  * A bare or "this" weekday means the *next* occurrence, today included
    ("Friday" on a Friday = today).
  * "next <weekday>" means the following week's occurrence (this + 7 days).
  * Month/day and numeric M/D with no year roll forward if already past.
"""

from __future__ import annotations

import datetime as dt
import re

WEEKDAYS = {
    "monday": 0, "mon": 0, "tuesday": 1, "tue": 1, "tues": 1, "wednesday": 2,
    "wed": 2, "thursday": 3, "thu": 3, "thurs": 3, "friday": 4, "fri": 4,
    "saturday": 5, "sat": 5, "sunday": 6, "sun": 6,
}

MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9, "october": 10,
    "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
}

#: Don't accept bookings further out than this.
#:
#: The owner reports Libro's window is "2-3 months, I need to check" — so 90 days
#: is the conservative reading of an unconfirmed answer. Erring short is the safe
#: direction: refusing a bookable date is a mildly annoyed caller, while accepting
#: a date Libro rejects fails *after* we have told the guest they have a table.
#: TODO: confirm the real Libro horizon and widen if it is 3 months.
MAX_HORIZON_DAYS = 90


def _roll_forward(year: int, month: int, day: int, today: dt.date) -> dt.date | None:
    try:
        candidate = dt.date(year, month, day)
    except ValueError:
        return None
    # If a year wasn't specified and the date is already past, try next year.
    if candidate < today:
        try:
            candidate = dt.date(year + 1, month, day)
        except ValueError:
            return None
    return candidate


def resolve_date(text: str, *, today: dt.date | None = None) -> dt.date | None:
    """Resolve free-text (or ISO) into a date, or None if it can't be parsed."""
    if not text:
        return None
    today = today or dt.date.today()
    s = text.strip().lower()

    # ISO date, possibly embedded (e.g. from a slot string).
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        try:
            return dt.date(int(m[1]), int(m[2]), int(m[3]))
        except ValueError:
            return None

    # Relative keywords.
    if re.search(r"\b(today|tonight|this (evening|afternoon|morning))\b", s):
        return today
    if re.search(r"\b(day after tomorrow|overmorrow)\b", s):
        return today + dt.timedelta(days=2)
    if re.search(r"\b(tomorrow|tmrw|tmw|tmr)\b", s):
        return today + dt.timedelta(days=1)
    m = re.search(r"\bin (\d{1,3}) days?\b", s)
    if m:
        return today + dt.timedelta(days=int(m[1]))

    # Weekday, with optional this/next.
    m = re.search(r"\b(this|next|coming)?\s*(" + "|".join(WEEKDAYS) + r")\b", s)
    if m:
        target = WEEKDAYS[m[2]]
        delta = (target - today.weekday()) % 7
        if m[1] == "next":
            delta += 7
        return today + dt.timedelta(days=delta)

    # Month name + day (either order), optional ordinal + year.
    month_alt = "|".join(MONTHS)
    m = re.search(rf"\b({month_alt})\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s*(\d{{4}}))?", s)
    if not m:
        m2 = re.search(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?({month_alt})(?:,?\s*(\d{{4}}))?", s)
        if m2:
            day, month, year = int(m2[1]), MONTHS[m2[2]], m2[3]
            return _roll_forward(int(year) if year else today.year, month, day, today)
    else:
        month, day, year = MONTHS[m[1]], int(m[2]), m[3]
        return _roll_forward(int(year) if year else today.year, month, day, today)

    # Numeric M/D or M/D/Y (North American ordering).
    m = re.search(r"\b(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?\b", s)
    if m:
        month, day = int(m[1]), int(m[2])
        year = m[3]
        if year:
            y = int(year)
            y += 2000 if y < 100 else 0
        else:
            y = today.year
        result = _roll_forward(y, month, day, today)
        if result:
            return result

    return None


def resolve_time(text: str) -> tuple[int, int] | None:
    """Parse a spoken time into (hour24, minute), or None.

    Applies the restaurant AM/PM rule that matters most on the phone: **an hour of
    1-8 with no other context is ALWAYS PM.** A caller saying "seven" means 19:00
    with overwhelming probability; defaulting to AM books breakfast at a venue that
    doesn't serve it. AM is used only when stated, or when the hour is 9-11 with no
    other signal.
    """
    if not text:
        return None
    s = text.strip().lower()

    # Negative lookbehind for a letter (not \b) so "7am"/"7pm" match — a digit
    # followed by a letter is not a word boundary.
    explicit_pm = bool(re.search(r"(?<![a-z])(pm|p\.m\.?)\b", s)
                       or re.search(r"\b(dinner|supper|tonight|evening|soir)\b", s))
    explicit_am = bool(re.search(r"(?<![a-z])(am|a\.m\.?)\b", s)
                       or re.search(r"\b(breakfast|morning|matin)\b", s))
    noonish = bool(re.search(r"\b(noon|midi|lunch|midday)\b", s))

    # "7:30" / "7h30" first, then a bare hour. No trailing \b — it would fail on
    # "7pm" (digit followed by a letter is not a word boundary).
    m = re.search(r"\b(\d{1,2})\s*[:h.]\s*(\d{2})", s) or re.search(r"\b(\d{1,2})", s)
    if m:
        hour = int(m[1])
        minute = int(m[2]) if m.lastindex and m.lastindex >= 2 else 0
    else:
        # Spelled-out hours — STT usually emits digits, but not always.
        words = {
            "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
            "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
            "twelve": 12, "midi": 12,
            # French — Québec callers say "dix-huit heures", never "6 PM".
            "une": 1, "deux": 2, "trois": 3, "quatre": 4, "cinq": 5,
            "sept": 7, "huit": 8, "neuf": 9, "onze": 11, "douze": 12,
            "treize": 13, "quatorze": 14, "quinze": 15, "seize": 16,
            "dix-sept": 17, "dix-huit": 18, "dix-neuf": 19, "vingt": 20,
            "vingt-et-une": 21, "vingt-deux": 22, "dix": 10,
        }
        w = re.search(r"\b(" + "|".join(words) + r")\b", s)
        if not w:
            return (12, 0) if noonish else None
        hour, minute = words[w[1]], 0
        if re.search(r"\b(thirty|et demie|half)\b", s):
            minute = 30
        elif re.search(r"\b(quarter|et quart)\b", s):
            minute = 15
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None

    if hour > 12:  # already 24-hour
        return (hour, minute)
    if explicit_am:
        return (0 if hour == 12 else hour, minute)
    if explicit_pm:
        return (12 if hour == 12 else hour + 12, minute)
    if hour == 12:
        return (12, minute)
    if 1 <= hour <= 8:
        return (hour + 12, minute)  # the rule: 1-8 with no context is PM
    if noonish and hour <= 2:
        return (hour + 12, minute)
    return (hour, minute)  # 9-11 with no context stays AM


def infer_part_of_day(text: str) -> str:
    """Guess 'lunch' or 'dinner' from phrasing; '' if unclear."""
    s = (text or "").lower()
    if re.search(r"\b(dinner|supper|tonight|evening|pm|night)\b", s):
        return "dinner"
    if re.search(r"\b(lunch|noon|midday|mid-day|brunch|afternoon|morning|am)\b", s):
        return "lunch"
    return ""


def validate_horizon(date: dt.date, *, today: dt.date | None = None) -> str:
    """Return an error reason if the date is out of range, else ""."""
    today = today or dt.date.today()
    if date < today:
        return "past"
    if (date - today).days > MAX_HORIZON_DAYS:
        return "too_far"
    return ""
