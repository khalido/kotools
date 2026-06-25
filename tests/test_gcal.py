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


def test_event_body_all_day_timed_and_coercion():
    # all-day: end.date is exclusive (+1 day)
    b = gcal._event_body("Holiday", "2026-07-05")
    assert b["start"] == {"date": "2026-07-05"} and b["end"] == {"date": "2026-07-06"}

    # all-day start with a *timed* --end must still yield a date end (coerced), exclusive
    b = gcal._event_body("Trip", "2026-07-05", end="2026-07-08T15:00")
    assert b["end"]["date"] == "2026-07-09" and "dateTime" not in b["end"]

    # timed: defaults to +minutes, timeZone set
    b = gcal._event_body("Call", "2026-07-05T14:00", minutes=30)
    assert b["start"]["dateTime"].startswith("2026-07-05T14:00")
    assert b["end"]["dateTime"].startswith("2026-07-05T14:30")
    assert b["start"]["timeZone"]


def test_search_events_filters(monkeypatch):
    fake = [
        gcal.CalEvent("1", "Dentist appt", "2026-07-01", "2026-07-01", True, "c", "Cal"),
        gcal.CalEvent("2", "Standup", "2026-07-01", "2026-07-01", True, "c", "Cal"),
    ]
    monkeypatch.setattr(gcal, "list_events", lambda **k: fake)
    out = gcal.search_events("dentist")  # case-insensitive substring
    assert len(out) == 1 and out[0].summary == "Dentist appt"
    assert gcal.search_events("") == []  # empty query short-circuits


def test_search_events_past_is_most_recent_first(monkeypatch):
    fake = [  # list_events returns ascending by start
        gcal.CalEvent("1", "Dentist Jan", "2026-01-01", "2026-01-01", True, "c", "Cal"),
        gcal.CalEvent("2", "Dentist Jun", "2026-06-01", "2026-06-01", True, "c", "Cal"),
    ]
    monkeypatch.setattr(gcal, "list_events", lambda **k: fake)
    out = gcal.search_events("dentist", past=True)
    assert [e.summary for e in out] == ["Dentist Jun", "Dentist Jan"]  # most recent first


def test_tz_name_default_and_override(monkeypatch):
    monkeypatch.setattr(gcal.config, "get", lambda *a, **k: None)
    assert gcal.tz_name() == "Australia/Sydney"
    monkeypatch.setattr(gcal.config, "get", lambda *a, **k: "Europe/Berlin")
    assert gcal.tz_name() == "Europe/Berlin"
