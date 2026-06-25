"""Offline tests for ko.gcal pure helpers (no live auth needed)."""

from __future__ import annotations

from datetime import date, datetime

from ko import gcal


def test_error_type():
    assert issubclass(gcal.CalError, RuntimeError)


def test_parse_when():
    all_day, val = gcal.parse_when("2026-06-26")
    assert all_day is True and val == date(2026, 6, 26)

    all_day, val = gcal.parse_when("2026-06-26T15:30")
    assert all_day is False and isinstance(val, datetime) and val.hour == 15 and val.tzinfo is not None

    assert gcal.parse_when("today")[0] is True
    assert gcal.parse_when("tomorrow")[0] is True


def test_event_mapping_timed_and_all_day():
    timed = gcal._event(
        {
            "id": "e1",
            "summary": "Standup",
            "start": {"dateTime": "2026-06-26T09:00:00+10:00"},
            "end": {"dateTime": "2026-06-26T09:30:00+10:00"},
            "location": "Zoom",
        },
        "cal1",
        "Work",
    )
    assert timed.all_day is False
    assert timed.summary == "Standup" and timed.location == "Zoom" and timed.calendar_name == "Work"

    allday = gcal._event(
        {"id": "e2", "start": {"date": "2026-06-26"}, "end": {"date": "2026-06-27"}},
        "cal2",
        "Holidays",
    )
    assert allday.all_day is True and allday.summary == "(no title)" and allday.start == "2026-06-26"


def test_tz_name_default_and_override(monkeypatch):
    monkeypatch.setattr(gcal.config, "get", lambda *a, **k: None)
    assert gcal.tz_name() == "Australia/Sydney"
    monkeypatch.setattr(gcal.config, "get", lambda *a, **k: "Europe/Berlin")
    assert gcal.tz_name() == "Europe/Berlin"
