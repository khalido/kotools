"""Tests for ko.flight on recorded OAG responses (2026-11-13). The live test is opt-in
(KO_LIVE_TESTS=1): the free tier is 1,000 calls a month."""

import json
import os
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ko import flight

FIX = Path(__file__).parent / "fixtures" / "flight"


def _load(name: str) -> dict:
    return json.loads((FIX / name).read_text())


@pytest.fixture(autouse=True)
def _tmp_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("KO_CACHE_DIR", str(tmp_path))
    monkeypatch.delenv("OAG_MASTER_DATA_KEY", raising=False)


@pytest.fixture
def oag(monkeypatch):
    """Fake OAG: answers from a fixture, records every call."""
    calls = []

    def fake(response):
        def _get(path, params, key, timeout=20):
            calls.append((path, params))
            return response
        monkeypatch.setenv("OAG_FLIGHT_INFO_KEY", "test-key")
        monkeypatch.setattr(flight, "_get", _get)
        return calls

    return fake


@pytest.mark.parametrize(
    ("code", "want"),
    [("EK604", ("EK", 604, "")), ("ek 604", ("EK", 604, "")), ("UAE604", ("UAE", 604, "")),
     ("U2123", ("U2", 123, "")), ("BA1A", ("BA", 1, "A"))],
)
def test_parse_code(code, want):
    assert flight.parse_code(code) == want


@pytest.mark.parametrize("bad", ["EK", "604", "EK12345", "E-604"])
def test_parse_code_rejects(bad):
    with pytest.raises(ValueError):
        flight.parse_code(bad)


def test_parse_date():
    today = date(2026, 11, 13)
    assert flight.parse_date("tomorrow", today) == date(2026, 11, 14)
    assert flight.parse_date("2026-11-20", today) == date(2026, 11, 20)
    with pytest.raises(ValueError):
        flight.parse_date("next friday", today)


def test_live_status_mapping():
    """EK2330 (a flydubai codeshare), arrived 55 min late: every card field from a real response."""
    row = _load("oag_dxb_status.json")["data"][0]
    f = flight._flight([row], "2026-11-13T02:30:31Z", "Emirates")
    assert (f.flight, f.status, f.operated_by, f.duration_min) == ("EK2330", "arrived", "FZ501", 265)
    d, a = f.departure, f.arrival
    assert (d.airport.iata, d.airport.city, d.airport.tz) == ("DXB", "Dubai", "Asia/Dubai")
    assert a.airport.name and a.airport.tz == "Asia/Dhaka"
    assert d.scheduled == "2026-11-13T00:30:00+04:00"  # offset derived from OAG's local/utc pair
    assert a.scheduled == "2026-11-13T06:55:00+06:00"
    assert (d.terminal, d.gate, d.actual, d.delay_min) == ("2", "F10", "2026-11-13T01:16:00+04:00", 46)
    assert (a.estimated, a.actual, a.delay_min) == ("2026-11-13T08:00:00+06:00", "2026-11-13T07:50:00+06:00", 55)
    assert a.gate is None  # optional everywhere
    assert f.updated_at == "2026-11-13T01:57:47Z"
    assert len(f.legs) == 1


def test_schedule_only_instance():
    """Days ahead OAG has no statusDetails: scheduled times only, status 'scheduled'."""
    rows = _load("oag_ek604_schedule.json")["data"]
    f = flight._flight([rows[1]], "x", None)
    assert (f.date, f.status, f.departure.terminal, f.arrival.terminal) == ("2026-11-17", "scheduled", "3", "M")
    assert f.departure.estimated is None and f.departure.delay_min is None
    assert f.arrival.airport.city == "Karachi"
    assert f.airline.name is None


def test_pick_by_date_and_next():
    journeys = flight._journeys(_load("oag_ek604_schedule.json")["data"])
    assert len(journeys) == 4
    assert flight.pick(journeys, date(2026, 11, 17), datetime.now(UTC))[0]["departure"]["date"]["local"] == "2026-11-17"
    assert flight.pick(journeys, date(2026, 11, 15), datetime.now(UTC)) is None
    # no date: the next not yet arrived; 14th arrives 00:45Z, so at 00:50Z it's still the 14th (grace)
    at = datetime(2026, 11, 14, 0, 50, tzinfo=UTC)
    assert flight.pick(journeys, None, at)[0]["departure"]["date"]["local"] == "2026-11-14"
    at = datetime(2026, 11, 14, 2, 0, tzinfo=UTC)
    assert flight.pick(journeys, None, at)[0]["departure"]["date"]["local"] == "2026-11-17"
    at = datetime(2026, 11, 25, tzinfo=UTC)  # all past: the latest
    assert flight.pick(journeys, None, at)[0]["departure"]["date"]["local"] == "2026-11-19"


def test_multi_leg_grouping():
    """sequenceNumber 1, 2 on consecutive rows = one journey; the card spans first to last."""
    base = _load("oag_ek604_schedule.json")["data"][0]
    leg2 = json.loads(json.dumps(base))
    leg2["sequenceNumber"] = 2
    leg2["departure"] = base["arrival"] | {"date": {"local": "2026-11-14", "utc": "2026-11-14"},
                                           "time": {"local": "07:00", "utc": "02:00"}}
    leg2["arrival"] = base["departure"] | {"date": {"local": "2026-11-14", "utc": "2026-11-14"},
                                           "time": {"local": "09:00", "utc": "05:00"}}
    journeys = flight._journeys([leg2, base])
    assert [len(j) for j in journeys] == [2]
    f = flight._flight(journeys[0], "x", None)
    assert (f.departure.airport.iata, f.arrival.airport.iata, len(f.legs)) == ("DXB", "DXB", 2)
    assert f.duration_min == 375  # 22:45Z -> 05:00Z


def test_lookup_caches_and_queries_once(oag):
    calls = oag(_load("oag_ek604_schedule.json"))
    now = datetime(2026, 11, 13, 3, 0, tzinfo=UTC)
    f = flight.lookup("EK604", date(2026, 11, 17), now)
    assert f.flight == "EK604" and f.date == "2026-11-17" and f.fetched_at == "2026-11-13T03:00:00Z"
    _, params = calls[0]
    assert params["DepartureDateTime"] == "2026-11-16/2026-11-18" and params["Content"] == "Status"
    flight.lookup("EK604", date(2026, 11, 17), now)
    assert len(calls) == 1  # second call served from the disk cache


def test_lookup_not_found(oag):
    oag({"data": [], "paging": {}})
    assert flight.lookup("EK604", date(2026, 11, 15)) is None


def test_airline_name_cached(monkeypatch):
    calls = []

    def _get(path, params, key, timeout=20):
        calls.append(params)
        return _load("oag_carrier_ek.json")

    monkeypatch.setattr(flight, "_get", _get)
    assert flight.airline_name("EK") is None  # no key: null, no call
    monkeypatch.setenv("OAG_MASTER_DATA_KEY", "test-key")
    assert flight.airline_name("EK") == "Emirates"
    assert flight.airline_name("EK") == "Emirates"
    assert len(calls) == 1 and calls[0]["Limit"] == 1  # free tier allows 1


def test_name_case():
    assert flight._name_case("KLM ROYAL DUTCH AIRLINES") == "KLM Royal Dutch Airlines"
    assert flight._name_case("JET2.COM") == "Jet2.com"


def test_card_time_keeps_offset_with_fractions():
    from ko.cli_web import _flight_time

    assert _flight_time("2026-11-14T05:45:00.500+05:00", "2026-11-13") == "05:45 +05:00 +1d"


def _cli(*args):
    from ko.cli import app

    return CliRunner().invoke(app, ["flight", *args])


def test_cli_json_and_card(oag):
    oag(_load("oag_ek604_schedule.json"))
    r = _cli("EK", "604", "2026-11-17", "--json")
    assert r.exit_code == 0, r.output
    data = json.loads(r.stdout)
    assert data["flight"] == "EK604" and data["departure"]["scheduled"] == "2026-11-17T02:45:00+04:00"
    assert data["source"] == "oag" and len(data["legs"]) == 1
    r = _cli("EK604", "2026-11-17")
    assert r.exit_code == 0, r.output
    assert "EK604" in r.stdout and "DXB Dubai → KHI Karachi" in r.stdout and "02:45 +04:00" in r.stdout


def test_cli_not_found_vs_error(oag, monkeypatch):
    oag({"data": []})
    r = _cli("EK604", "2026-11-15", "--json")
    assert (r.exit_code, r.stdout.strip()) == (0, "[]")

    def boom(*a, **k):
        raise flight.FlightError("OAG quota or rate limit hit (429): over")

    monkeypatch.setattr(flight, "_get", boom)
    r = _cli("EK604", "2026-11-16", "--json")
    assert r.exit_code == 1
    assert json.loads(r.stderr)["code"] == "error"
    assert _cli("EK", "--json").exit_code == 2
    assert _cli("EK604", "someday").exit_code == 2


@pytest.mark.skipif(
    not (os.environ.get("KO_LIVE_TESTS") and os.environ.get("OAG_FLIGHT_INFO_KEY")),
    reason="spends OAG free-tier quota; set KO_LIVE_TESTS=1 and OAG_FLIGHT_INFO_KEY",
)
def test_live_lookup():
    f = flight.lookup("EK1")  # Dubai-London, daily
    assert f is not None and f.departure.airport.iata == "DXB" and f.departure.scheduled
