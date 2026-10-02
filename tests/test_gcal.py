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


def test_start_key_orders_mixed_offsets_and_allday():
    # a UTC-marked event at 23:30 is *later* in real time than a +10:00 event at 09:00 the
    # next local morning; naive string sort gets this wrong ('...Z' vs '...+10:00'). All-day
    # anchors to local midnight (a day+ before either), so it sorts first regardless of tz.
    def ev(start: str) -> gcal.CalEvent:
        return gcal.CalEvent(
            id="", summary="", start=start, end="", all_day=len(start) == 10,
            calendar_id="", calendar_name="",
        )

    events = [
        ev("2026-06-27T09:00:00+10:00"),  # = 2026-06-26 23:00 UTC
        ev("2026-06-26T23:30:00Z"),        # 30 min later in real time
        ev("2026-06-26"),                  # all-day → local midnight, the earliest
    ]
    ordered = [e.start for e in sorted(events, key=gcal._start_key)]
    assert ordered == [
        "2026-06-26",
        "2026-06-27T09:00:00+10:00",
        "2026-06-26T23:30:00Z",
    ]


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


def test_window_defaults_to_start_of_day_and_spans_days(monkeypatch):
    """The default window anchors to the START OF TODAY (not 'now', so a 6pm query keeps the
    morning) and spans `days` calendar days → `days` day-headers, not days+1 (timeMax exclusive)."""
    fixed = datetime(2026, 7, 16, 18, 0, tzinfo=gcal.tz())  # 6pm today
    monkeypatch.setattr(gcal, "_now", lambda: fixed)

    tmin, tmax = gcal._window(7, None, None)
    assert tmin.startswith("2026-07-16T00:00:00")  # start of TODAY, not 18:00 → keeps morning events
    assert tmax.startswith("2026-07-23T00:00:00")  # +7 days, exclusive → headers 16..22 = 7, not 8

    # --today: days=1 → a single calendar day (midnight → next midnight)
    tmin1, tmax1 = gcal._window(1, None, None)
    assert tmin1.startswith("2026-07-16T00:00:00") and tmax1.startswith("2026-07-17T00:00:00")

    # explicit bounds override the defaults verbatim
    lo = datetime(2026, 1, 1, tzinfo=gcal.tz())
    hi = datetime(2026, 1, 2, tzinfo=gcal.tz())
    assert gcal._window(7, lo, hi) == (lo.isoformat(), hi.isoformat())


def test_event_all_day_end_is_inclusive():
    """Google's all-day end.date is EXCLUSIVE; the read path strips the +1 so end is the last day."""
    single = gcal._event({"start": {"date": "2026-07-05"}, "end": {"date": "2026-07-06"}}, "c", "Cal")
    assert single.all_day and single.start == "2026-07-05" and single.end == "2026-07-05"  # 1 day, no fake span

    multi = gcal._event({"start": {"date": "2026-07-05"}, "end": {"date": "2026-07-08"}}, "c", "Cal")
    assert multi.start == "2026-07-05" and multi.end == "2026-07-07"  # Jul 5–7, not 5–8


def test_event_all_day_round_trips_create_body():
    """create_event writes an exclusive (+1) end; _event strips it → the same inclusive dates back."""
    body = gcal._event_body("Trip", "2026-07-05", end="2026-07-07")
    ev = gcal._event({"start": body["start"], "end": body["end"]}, "c", "Cal")
    assert ev.start == "2026-07-05" and ev.end == "2026-07-07"


def test_to_local_normalizes_mixed_offsets(monkeypatch):
    """The three timestamp shapes Google mixes (UTC 'Z', another offset, already-local) all
    normalize to one ISO8601 string in the configured tz, with a numeric offset."""
    monkeypatch.setattr(gcal.config, "get", lambda *a, **k: "Australia/Sydney")  # +10:00 in July
    utc = gcal._to_local("2026-07-16T22:00:00Z")          # 22:00 UTC
    other = gcal._to_local("2026-07-17T00:00:00+02:00")   # same instant, +02:00
    local = gcal._to_local("2026-07-17T08:00:00+10:00")   # same instant, already Sydney
    assert utc == other == local == "2026-07-17T08:00:00+10:00"
    assert gcal._to_local("") == ""            # empty passes through
    assert gcal._to_local("garbage") == "garbage"  # unparseable passes through


def test_event_timed_normalized_to_configured_tz(monkeypatch):
    """A timed event's start/end are rendered in the configured zone regardless of the wire offset."""
    monkeypatch.setattr(gcal.config, "get", lambda *a, **k: "Australia/Sydney")
    ev = gcal._event(
        {"start": {"dateTime": "2026-07-16T22:00:00Z"}, "end": {"dateTime": "2026-07-16T23:00:00Z"}},
        "c", "Cal",
    )
    assert ev.start == "2026-07-17T08:00:00+10:00" and ev.end == "2026-07-17T09:00:00+10:00"


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


def test_resolve_calendar_ids_by_name_id_and_missing():
    import pytest

    cals = [
        gcal.Calendar(id="abc@group", name="Family", primary=False, role="owner"),
        gcal.Calendar(id="me@x.com", name="Primary", primary=True, role="owner"),
    ]
    # name is case-insensitive; a raw id passes through; order is preserved
    assert gcal.resolve_calendar_ids(["family", "me@x.com"], calendars=cals) == ["abc@group", "me@x.com"]
    with pytest.raises(gcal.CalError) as e:
        gcal.resolve_calendar_ids(["Holidays"], calendars=cals)
    assert "Family" in str(e.value) and "Primary" in str(e.value)  # error names what's available


def test_tz_name_default_and_override(monkeypatch):
    monkeypatch.setattr(gcal.config, "get", lambda *a, **k: None)
    assert gcal.tz_name() == "Australia/Sydney"
    monkeypatch.setattr(gcal.config, "get", lambda *a, **k: "Europe/Berlin")
    assert gcal.tz_name() == "Europe/Berlin"


# --- cli_google helpers (offline, no network) ---


def test_fmt_events_clamps_multiday_allday_to_window_start(capsys):
    """A multi-day all-day event that started before the queried window should appear under
    the window's first day header, not its original start-date header."""
    from ko.cli_google import _fmt_events

    ev = gcal.CalEvent(
        id="e1", summary="Conference", start="2026-07-05", end="2026-07-12",
        all_day=True, calendar_id="c", calendar_name="Cal",
    )
    _fmt_events([ev], window_start="2026-07-09")
    out = capsys.readouterr().out
    # Header should reflect the clamped date (Wed 09 Jul), NOT the original start (Sun 05 Jul)
    assert "09 Jul" in out
    assert "05 Jul" not in out


def test_fmt_events_no_clamp_when_start_within_window(capsys):
    """An event that starts on the queried day should still show its own date header."""
    from ko.cli_google import _fmt_events

    ev = gcal.CalEvent(
        id="e2", summary="Sprint planning", start="2026-07-09", end="2026-07-10",
        all_day=True, calendar_id="c", calendar_name="Cal",
    )
    _fmt_events([ev], window_start="2026-07-09")
    out = capsys.readouterr().out
    assert "09 Jul" in out


def test_fmt_events_no_window_start_uses_original_date(capsys):
    """Without a window_start, the full-agenda view shows events under their real start date."""
    from ko.cli_google import _fmt_events

    ev = gcal.CalEvent(
        id="e3", summary="Holiday", start="2026-07-01", end="2026-07-15",
        all_day=True, calendar_id="c", calendar_name="Cal",
    )
    _fmt_events([ev], window_start=None)
    out = capsys.readouterr().out
    assert "01 Jul" in out


def test_fmt_events_location_newlines_flattened(capsys):
    """Multi-line Google addresses are collapsed to ', '-separated one-liners."""
    from ko.cli_google import _fmt_events

    ev = gcal.CalEvent(
        id="e4", summary="Meeting", start="2026-07-09T10:00:00+10:00", end="2026-07-09T11:00:00+10:00",
        all_day=False, calendar_id="c", calendar_name="Cal",
        location="Level 3\n123 Main St\nSydney NSW 2000",
    )
    _fmt_events([ev])
    out = capsys.readouterr().out
    assert "\n" not in out.split("@", 1)[-1].split("\n")[0]  # no raw newlines in the location part
    assert "Level 3, 123 Main St, Sydney NSW 2000" in out


def test_http_error_msg_extracts_clean_reason():
    """_http_error_msg should return '[<status>] <reason>' without the URL blob."""
    import json
    from unittest.mock import MagicMock

    from googleapiclient.errors import HttpError

    from ko.cli_google import _http_error_msg

    content = json.dumps({"error": {"message": "Invalid id value", "code": 400}}).encode()
    resp = MagicMock()
    resp.status = 400
    resp.reason = "Bad Request"
    e = HttpError(resp=resp, content=content)
    msg = _http_error_msg(e)
    assert msg == "[400] Invalid id value"
    assert "http" not in msg.lower()  # no URL leakage


# --- across accounts: merged view + where new events go ---


def _ev(uid, start, acct, summary="Standup"):
    return gcal.CalEvent(uid + acct, summary, start, start, True, "c", "Cal", account=acct, ical_uid=uid)


def test_merged_dedupes_an_invite_on_two_accounts_and_sorts():
    fake = {
        "kd": [_ev("u1", "2026-07-02", "kd"), _ev("u2", "2026-07-01", "kd", "Lunch")],
        "sfl": [_ev("u1", "2026-07-02", "sfl")],
    }
    events, warnings = gcal.merged(lambda a, ids: fake[a], ["kd", "sfl"])
    assert [(e.summary, e.account) for e in events] == [("Lunch", "kd"), ("Standup", "kd, sfl")]
    assert warnings == []


def test_merged_one_dead_account_is_a_warning_unless_alone():
    import pytest

    def fetch(a, ids):
        if a == "ne":
            raise gcal.CalError("token revoked")
        return [_ev("u1", "2026-07-02", a)]

    events, warnings = gcal.merged(fetch, ["kd", "ne"])
    assert len(events) == 1 and warnings == ["ne: token revoked"]
    with pytest.raises(gcal.CalError, match="token revoked"):
        gcal.merged(fetch, ["ne"])  # asked for just that one: a real error


def test_merged_calendar_names_must_match_somewhere(monkeypatch):
    import pytest

    cals = {
        "kd": [gcal.Calendar("fam@g", "Family", False, "owner")],
        "sfl": [gcal.Calendar("k@sfl", "Work", True, "owner")],
    }
    monkeypatch.setattr(gcal, "list_calendars", lambda a: cals[a])
    seen = []
    gcal.merged(lambda a, ids: seen.append((a, ids)) or [], ["kd", "sfl"], ["family"])
    assert seen == [("kd", ["fam@g"])]  # sfl has no Family: skipped, not an error
    with pytest.raises(gcal.CalError, match="Holidays"):
        gcal.merged(lambda a, ids: [], ["kd", "sfl"], ["Holidays"])


def test_add_target_is_separate_from_the_default_account(monkeypatch, tmp_path):
    import json

    monkeypatch.setenv("KO_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("KO_GOOGLE_ACCOUNT", "kd")  # the mail default
    assert gcal.add_target() == ("kd", "primary")  # nothing set: follow the Google default
    (tmp_path / "settings.json").write_text(json.dumps({"cal": {"account": "sfl", "calendar": "work@g"}}))
    assert gcal.add_target() == ("sfl", "work@g")  # `ko cal use` wins over the mail default
    assert gcal.add_target("ne") == ("ne", "primary")  # -a: that account's primary, not sfl's calendar


def test_cli_cal_add_json_emits_the_event(monkeypatch, tmp_path):
    import json

    from typer.testing import CliRunner

    from ko import cli_google
    from ko.cli import app

    monkeypatch.setenv("KO_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("KO_GOOGLE_ACCOUNT", "kd")
    ev = gcal.CalEvent("e1", "Dentist", "2026-10-01", "2026-10-01", True, "primary", "primary", account="kd")
    monkeypatch.setattr(cli_google.gcal_mod, "create_event", lambda *a, **k: ev)
    r = CliRunner().invoke(app, ["cal", "add", "Dentist", "2026-10-01", "--json"])
    assert r.exit_code == 0, r.output
    assert json.loads(r.stdout)["summary"] == "Dentist" and json.loads(r.stdout)["account"] == "kd"
