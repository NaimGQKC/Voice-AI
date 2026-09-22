"""Phone-number normalization for North American numbers.

Callers say numbers many ways ("514-555-1234", "(514) 555 1234", "1 514 555
1234"); speech-to-text transcribes them inconsistently too. We normalize to
E.164 (+1XXXXXXXXXX) so that a number given at booking matches the same number
given later at lookup/cancel. Returns "" when the input isn't a plausible NANP
number, so callers can be re-prompted.
"""

from __future__ import annotations

import re

_DIGITS = re.compile(r"\d")

# Common spoken words that STT may leave as words instead of digits.
_WORD_DIGITS = {
    "oh": "0", "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
}


def _extract_digits(text: str) -> str:
    # Replace spelled-out digits first, then keep numerals.
    tokens = re.split(r"[\s\-.,()]+", text.lower())
    out: list[str] = []
    for tok in tokens:
        if tok in _WORD_DIGITS:
            out.append(_WORD_DIGITS[tok])
        else:
            out.append("".join(_DIGITS.findall(tok)))
    return "".join(out)


def normalize_phone(text: str) -> str:
    """Return the number in E.164 (+1XXXXXXXXXX), or "" if not a valid NANP number."""
    if not text:
        return ""
    digits = _extract_digits(text)

    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        return ""
    # NANP: area code and exchange code both start 2-9.
    if digits[0] in "01" or digits[3] in "01":
        return ""
    return f"+1{digits}"


def spoken_phone(e164: str) -> str:
    """Render '+15145551234' as '514-555-1234' for a natural read-back."""
    digits = "".join(_DIGITS.findall(e164))
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        return e164
    return f"{digits[0:3]}-{digits[3:6]}-{digits[6:10]}"
