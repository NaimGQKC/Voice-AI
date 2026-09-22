"""Tests for phone-number normalization."""

from __future__ import annotations

from resto_agent.phone import normalize_phone, spoken_phone


def test_various_formats_normalize_to_e164():
    for raw in [
        "514-555-1234",
        "(514) 555-1234",
        "514.555.1234",
        "5145551234",
        "1 514 555 1234",
        "+1 (514) 555-1234",
    ]:
        assert normalize_phone(raw) == "+15145551234", raw


def test_spelled_out_digits():
    assert normalize_phone("five one four five five five one two three four") == "+15145551234"


def test_invalid_numbers_return_empty():
    assert normalize_phone("") == ""
    assert normalize_phone("12345") == ""          # too short
    assert normalize_phone("014-555-1234") == ""    # area code starts with 0
    assert normalize_phone("514-055-1234") == ""    # exchange starts with 0


def test_spoken_phone_readback():
    assert spoken_phone("+15145551234") == "514-555-1234"
