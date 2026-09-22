"""Tests for deterministic natural-language date resolution."""

from __future__ import annotations

import datetime as dt

from resto_agent import datetime_resolve as dr

# A fixed reference: Wednesday, 2026-07-01.
TODAY = dt.date(2026, 7, 1)


def r(text: str):
    return dr.resolve_date(text, today=TODAY)


def test_iso_passthrough():
    assert r("2026-08-15") == dt.date(2026, 8, 15)
    assert r("book me for 2026-08-15 please") == dt.date(2026, 8, 15)


def test_today_and_tomorrow():
    assert r("today") == TODAY
    assert r("tonight") == TODAY
    assert r("this evening") == TODAY
    assert r("tomorrow") == dt.date(2026, 7, 2)
    assert r("day after tomorrow") == dt.date(2026, 7, 3)


def test_in_n_days():
    assert r("in 5 days") == dt.date(2026, 7, 6)


def test_bare_weekday_is_next_occurrence_including_today():
    # 2026-07-01 is a Wednesday.
    assert r("wednesday") == TODAY
    assert r("this friday") == dt.date(2026, 7, 3)
    assert r("monday") == dt.date(2026, 7, 6)


def test_next_weekday_is_following_week():
    assert r("next wednesday") == dt.date(2026, 7, 8)
    assert r("next friday") == dt.date(2026, 7, 10)


def test_month_day_both_orders():
    assert r("July 5") == dt.date(2026, 7, 5)
    assert r("July 5th") == dt.date(2026, 7, 5)
    assert r("the 5th of August") == dt.date(2026, 8, 5)
    assert r("Dec 24, 2026") == dt.date(2026, 12, 24)


def test_month_day_rolls_to_next_year_if_past():
    # June already passed relative to July 1.
    assert r("June 10") == dt.date(2027, 6, 10)


def test_numeric_md():
    assert r("8/15") == dt.date(2026, 8, 15)
    assert r("08/15/2026") == dt.date(2026, 8, 15)


def test_unparseable_returns_none():
    assert r("whenever you like") is None
    assert r("") is None


def test_infer_part_of_day():
    assert dr.infer_part_of_day("tonight") == "dinner"
    assert dr.infer_part_of_day("dinner on Friday") == "dinner"
    assert dr.infer_part_of_day("lunch tomorrow") == "lunch"
    assert dr.infer_part_of_day("around noon") == "lunch"
    assert dr.infer_part_of_day("Friday") == ""


def test_validate_horizon():
    assert dr.validate_horizon(dt.date(2026, 6, 30), today=TODAY) == "past"
    assert dr.validate_horizon(TODAY, today=TODAY) == ""
    assert dr.validate_horizon(dt.date(2027, 6, 1), today=TODAY) == "too_far"


# --- spoken time parsing, with the restaurant AM/PM rule -------------------

def test_hour_one_to_eight_defaults_to_pm():
    """The rule that prevents booking breakfast: 1-8 with no context is PM."""
    assert dr.resolve_time("7") == (19, 0)
    assert dr.resolve_time("1") == (13, 0)
    assert dr.resolve_time("8") == (20, 0)
    assert dr.resolve_time("seven") == (19, 0)


def test_nine_to_eleven_stay_am_without_context():
    assert dr.resolve_time("9") == (9, 0)
    assert dr.resolve_time("10") == (10, 0)


def test_explicit_period_wins():
    assert dr.resolve_time("7am") == (7, 0)
    assert dr.resolve_time("8 in the morning") == (8, 0)
    assert dr.resolve_time("7pm") == (19, 0)
    assert dr.resolve_time("10 tonight") == (22, 0)


def test_minutes_and_24h():
    assert dr.resolve_time("7:30") == (19, 30)
    assert dr.resolve_time("19:00") == (19, 0)
    assert dr.resolve_time("seven thirty") == (19, 30)
    assert dr.resolve_time("half past 8") == (20, 0)
    assert dr.resolve_time("noon") == (12, 0)


def test_french_numerals():
    """Québec callers say 'dix-huit heures', not '6 PM'."""
    assert dr.resolve_time("dix-huit heures") == (18, 0)
    assert dr.resolve_time("dix-neuf heures") == (19, 0)
    assert dr.resolve_time("dix heures") == (10, 0)   # longest-match, not 'dix-huit'
    assert dr.resolve_time("sept heures") == (19, 0)  # 1-8 rule applies in FR too


def test_unparseable_time_is_none():
    assert dr.resolve_time("") is None
    assert dr.resolve_time("whenever") is None
