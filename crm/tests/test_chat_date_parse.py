"""Tests for chat.py _parse_date — the richer date-parsing helper used by all chat tools."""
import pytest
from datetime import date, timedelta

from chat import _parse_date


# ── happy path ────────────────────────────────────────────────────────────────

def test_iso_format():
    assert _parse_date("2026-09-15") == date(2026, 9, 15)


def test_today_keyword():
    assert _parse_date("today") == date.today()


def test_today_case_insensitive():
    assert _parse_date("TODAY") == date.today()
    assert _parse_date("Today") == date.today()


def test_yesterday_keyword():
    assert _parse_date("yesterday") == date.today() - timedelta(days=1)


def test_yesterday_case_insensitive():
    assert _parse_date("YESTERDAY") == date.today() - timedelta(days=1)


def test_full_month_name():
    assert _parse_date("September 15, 2026") == date(2026, 9, 15)


def test_abbreviated_month():
    assert _parse_date("Sep 15, 2026") == date(2026, 9, 15)


def test_slash_format():
    assert _parse_date("09/15/2026") == date(2026, 9, 15)


def test_dash_format():
    assert _parse_date("09-15-2026") == date(2026, 9, 15)


def test_strips_whitespace():
    assert _parse_date("  2026-09-15  ") == date(2026, 9, 15)


def test_strips_whitespace_keyword():
    assert _parse_date("  today  ") == date.today()


# ── error cases ───────────────────────────────────────────────────────────────

def test_empty_string_raises():
    with pytest.raises(ValueError, match="empty"):
        _parse_date("")


def test_none_raises():
    with pytest.raises(ValueError):
        _parse_date(None)


def test_invalid_string_raises():
    with pytest.raises(ValueError, match="Cannot parse date"):
        _parse_date("not-a-date")


def test_partial_date_raises():
    with pytest.raises(ValueError):
        _parse_date("2026-13")


def test_nonsense_raises():
    with pytest.raises(ValueError):
        _parse_date("whenever")


# ── app.py _parse_date (simpler version) ─────────────────────────────────────
# Tested indirectly via the contacts API — pass last_contact_date and confirm storage.

def test_app_date_parse_via_api(client, auth):
    """The app.py _parse_date(s) stores ISO dates correctly via the contacts route."""
    resp = client.post("/api/contacts", headers=auth,
                       json={"name": "Dated Person", "last_contact_date": "2026-09-15"})
    assert resp.status_code == 201
    assert resp.get_json()["last_contact_date"] == "2026-09-15"


def test_app_date_parse_null_via_api(client, auth):
    """Empty string last_contact_date is stored as null."""
    resp = client.post("/api/contacts", headers=auth,
                       json={"name": "No Date", "last_contact_date": ""})
    assert resp.status_code == 201
    assert resp.get_json()["last_contact_date"] is None
