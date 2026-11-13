"""Flight status by number and date: what a search engine's flight card shows.

One source today: OAG Flight Info (`OAG_FLIGHT_INFO_KEY`, free tier 1,000 calls a month,
`Content=Status` included). OAG returns codes only, so airport names, cities and IANA zones
come from the `airportsdata` package (offline), and the airline name from one OAG Master Data
call per carrier (`OAG_MASTER_DATA_KEY`, optional, cached for good). Responses are cached on
disk briefly so a poller can't burn the monthly quota. Design: docs/flight.md.
"""

from __future__ import annotations

import json
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta, timezone
from functools import lru_cache

from . import config, dirs

OAG = "https://api.oag.com"
_KEY_HINT = "Free tier at https://developers.oag.com (Flight Info API, 1,000 calls a month)."
_MASTER_HINT = "Optional, for airline names: OAG Master Data API at https://developers.oag.com."
LIVE_TTL = 5 * 60  # query window touches today: status moves
FAR_TTL = 6 * 3600  # schedule only
ARRIVED_GRACE = timedelta(minutes=30)  # a flight that just landed still answers "the next one"

# OAG statusDetails.state -> ko status (https://knowledge.oag.com/docs/status-direct-data-fields-explained)
STATES = {
    "Scheduled": "scheduled",
    "OutGate": "departed",
    "InAir": "in_air",
    "Landed": "landed",
    "InGate": "arrived",
    "Canceled": "cancelled",
    "Diverted": "diverted",
}

# ICAO = 3 letters; IATA = 2 characters with at least one letter (9W, U2)
_CODE = re.compile(r"^([A-Z]{3}|[A-Z][A-Z0-9]|[0-9][A-Z])(\d{1,4})([A-Z]?)$")


class FlightError(RuntimeError):
    """OAG refused or failed: one clean line for the CLI."""


@dataclass
class Airport:
    iata: str
    name: str | None = None
    city: str | None = None
    tz: str | None = None


@dataclass
class Stop:
    """One end of a leg. Times are gate times, ISO 8601 with the airport's offset."""

    airport: Airport
    terminal: str | None = None
    gate: str | None = None
    scheduled: str | None = None
    estimated: str | None = None
    actual: str | None = None
    delay_min: int | None = None
    baggage: str | None = None


@dataclass
class Leg:
    departure: Stop
    arrival: Stop
    status: str
    duration_min: int | None = None
    aircraft: str | None = None
    diverted_to: str | None = None
    updated_at: str | None = None


@dataclass
class Airline:
    iata: str
    name: str | None = None


@dataclass
class Flight:
    flight: str
    airline: Airline
    operated_by: str | None
    date: str  # origin's local departure date
    status: str
    departure: Stop  # first leg's
    arrival: Stop  # last leg's
    duration_min: int | None
    aircraft: str | None
    diverted_to: str | None
    legs: list[Leg] = field(default_factory=list)
    source: str = "oag"
    updated_at: str | None = None
    fetched_at: str = ""


# --- input ---


def parse_code(code: str) -> tuple[str, int, str]:
    """'EK604' / 'ek 604' / 'UAE604' / 'BA1A' -> (carrier, number, suffix). ValueError if not a flight."""
    m = _CODE.match(re.sub(r"\s+", "", code).upper())
    if not m:
        raise ValueError(f"'{code}' isn't a flight number (expected e.g. EK604 or UAE604)")
    return m[1], int(m[2]), m[3]


def parse_date(text: str, today: date) -> date:
    """YYYY-MM-DD, today, tomorrow or yesterday. ValueError otherwise."""
    words = {"today": 0, "tomorrow": 1, "yesterday": -1}
    if text.lower() in words:
        return today + timedelta(days=words[text.lower()])
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise ValueError(f"'{text}' isn't a date (YYYY-MM-DD, today, tomorrow or yesterday)") from None


# --- OAG calls ---


def _get(path: str, params: dict, key: str, timeout: float = 20) -> dict:
    import httpx2 as httpx

    try:
        r = httpx.get(f"{OAG}{path}", params=params, headers={"Subscription-Key": key}, timeout=timeout)
    except httpx.HTTPError as e:
        raise FlightError(f"OAG unreachable: {e}") from None
    if r.status_code == 200:
        return r.json()
    try:
        msg = r.json().get("message") or r.text
    except ValueError:
        msg = r.text
    msg = " ".join(str(msg).split())[:200]
    if r.status_code == 401:
        raise FlightError(f"OAG rejected the key (401): {msg}")
    if r.status_code in (403, 429):
        raise FlightError(f"OAG quota or rate limit hit ({r.status_code}): {msg}")
    raise FlightError(f"OAG error {r.status_code}: {msg}")


def _instances(carrier: str, number: int, start: date, end: date, now: datetime) -> tuple[dict, str]:
    """Raw OAG response for departures start..end (local dates), via a short disk cache.
    Returns (response, fetched_at)."""
    path = dirs.cache_dir() / "flight" / f"{carrier}{number}_{start}_{end}.json"
    today = now.date()
    ttl = LIVE_TTL if start <= today + timedelta(days=1) and end >= today - timedelta(days=1) else FAR_TTL
    if path.exists() and time.time() - path.stat().st_mtime < ttl:
        try:
            cached = json.loads(path.read_text())
            return cached["response"], cached["fetched_at"]
        except (ValueError, KeyError):
            pass  # a bad cache file is just a miss
    key = config.require_key("OAG_FLIGHT_INFO_KEY", _KEY_HINT)
    params = {
        "version": "v2",
        "CarrierCode": carrier,
        "FlightNumber": number,
        "DepartureDateTime": f"{start}/{end}",
        "CodeType": "IATA,ICAO",
        "Content": "Status",
        "Limit": 10,  # free-tier maximum
    }
    response = _get("/flight-instances/", params, key)
    fetched_at = _iso_utc(now)
    dirs.write_atomic(path, json.dumps({"response": response, "fetched_at": fetched_at}))
    return response, fetched_at


def airline_name(code: str) -> str | None:
    """Carrier name from OAG Master Data, cached for good; None without the optional key."""
    path = dirs.cache_dir() / "flight" / "carriers.json"
    try:
        names = json.loads(path.read_text())
    except (OSError, ValueError):
        names = {}
    if code in names:
        return _name_case(names[code]) or None
    try:
        key = config.require_key("OAG_MASTER_DATA_KEY", _MASTER_HINT)
    except config.MissingKeyError:
        return None
    try:
        params = {"version": "v1", "CarrierCode": code, "CodeType": "IATA,ICAO", "Limit": 1}  # free tier: 1
        data = _get("/carriers", params, key, timeout=5)  # optional: don't hold the card up
    except FlightError as e:
        print(f"airline name unavailable: {e}", file=sys.stderr)
        return None
    rows = data.get("data") or []
    names[code] = (rows[0].get("name") or "") if rows else ""  # raw; "" = unknown, don't ask again
    dirs.write_atomic(path, json.dumps(names, indent=1, sort_keys=True))
    return _name_case(names[code]) or None


def _name_case(raw: str) -> str:
    """OAG names are upper case: 'EMIRATES' -> 'Emirates', 'JET2.COM' -> 'Jet2.com'; a word with
    no vowel is an acronym and stays ('KLM ROYAL DUTCH AIRLINES' -> 'KLM Royal Dutch Airlines')."""
    return " ".join(w if not set(w.lower()) & set("aeiou") else w.capitalize() for w in raw.split())


# --- mapping ---


@lru_cache
def _airports() -> dict:
    import airportsdata

    return airportsdata.load("IATA")


def airport(code: str | None) -> Airport:
    """Name, city and zone for an IATA code; unknown code -> nulls, not an error."""
    row = _airports().get(code or "") or {}
    return Airport(iata=code or "", name=row.get("name"), city=row.get("city"), tz=row.get("tz"))


def _iso_utc(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _scheduled(end: dict) -> datetime | None:
    """OAG gives schedule as separate local and UTC date + time; the offset is their difference."""
    try:
        local = datetime.fromisoformat(f"{end['date']['local']}T{end['time']['local']}")
        utc = datetime.fromisoformat(f"{end['date']['utc']}T{end['time']['utc']}")
    except (KeyError, TypeError, ValueError):
        return None
    return local.replace(tzinfo=timezone(local - utc))


def _stamp(t: dict | None) -> datetime | None:
    """An OAG `{local, utc}` stamp -> aware datetime in local time; None if absent or offset-less."""
    try:
        dt = datetime.fromisoformat(t["local"]) if t and t.get("local") else None
    except ValueError:
        return None
    return dt if dt and dt.tzinfo else None


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _stop(sched_end: dict, status_end: dict, est_key: str) -> Stop:
    scheduled = _scheduled(sched_end)
    estimated = _stamp((status_end.get("estimatedTime") or {}).get(est_key))
    actual = _stamp((status_end.get("actualTime") or {}).get(est_key))
    best = actual or estimated
    delay = round((best - scheduled).total_seconds() / 60) if best and scheduled else None
    return Stop(
        airport=airport((sched_end.get("airport") or {}).get("iata")),
        terminal=status_end.get("actualTerminal") or sched_end.get("terminal") or None,
        gate=status_end.get("gate") or None,
        scheduled=_iso(scheduled),
        estimated=_iso(estimated),
        actual=_iso(actual),
        delay_min=delay,
        baggage=status_end.get("baggage") or None,
    )


def _leg(row: dict) -> Leg:
    details = row.get("statusDetails") or []
    status = max(details, key=lambda d: d.get("updatedAt") or "") if details else {}
    state = status.get("state")
    updated = status.get("updatedAt")
    equipment = ((status.get("equipment") or {}).get("actualAircraftType") or {}).get("iata")
    return Leg(
        departure=_stop(row.get("departure") or {}, status.get("departure") or {}, "outGate"),
        arrival=_stop(row.get("arrival") or {}, status.get("arrival") or {}, "inGate"),
        # an unknown OAG state is passed through lowercased, not hidden
        status=STATES.get(state, state.lower()) if state else "scheduled",
        duration_min=row.get("elapsedTime"),
        aircraft=equipment or (row.get("aircraftType") or {}).get("iata"),
        diverted_to=(status.get("diversionAirport") or {}).get("iata") or None,
        updated_at=f"{updated[:19]}Z" if updated else None,  # OAG sends UTC with no offset
    )


def _journeys(rows: list[dict]) -> list[list[dict]]:
    """Group OAG rows into journeys: a multi-leg flight is consecutive rows, sequenceNumber 1, 2, ..."""
    def dep_utc(r):
        s = _scheduled(r.get("departure") or {})
        return s.astimezone(UTC) if s else datetime.max.replace(tzinfo=UTC)

    out: list[list[dict]] = []
    for r in sorted(rows, key=dep_utc):
        seq = r.get("sequenceNumber") or 1
        if out and seq > (out[-1][-1].get("sequenceNumber") or 1):
            out[-1].append(r)
        else:
            out.append([r])
    return out


def _flight(rows: list[dict], fetched_at: str, name: str | None) -> Flight:
    legs = [_leg(r) for r in rows]
    first, last = rows[0], rows[-1]
    carrier = first.get("carrier") or {}
    code = carrier.get("iata") or carrier.get("icao") or ""
    op = (first.get("codeshare") or {}).get("operatingFlight") or {}
    operated_by = f"{op['code']}{op.get('serviceNumber', '')}{op.get('suffix') or ''}" if op.get("code") else None
    statuses = [leg.status for leg in legs]
    if "cancelled" in statuses:
        status = "cancelled"
    else:  # the first leg still under way or to come; all done -> the last leg's
        status = next((s for s in statuses if s not in ("arrived",)), statuses[-1])
    dep, arr = legs[0].departure, legs[-1].arrival
    span = None
    if dep.scheduled and arr.scheduled:
        span = round((datetime.fromisoformat(arr.scheduled) - datetime.fromisoformat(dep.scheduled)).total_seconds() / 60)
    return Flight(
        flight=f"{code}{first.get('flightNumber')}{first.get('serviceSuffix') or ''}",
        airline=Airline(iata=code, name=name),
        operated_by=operated_by,
        date=((first.get("departure") or {}).get("date") or {}).get("local", ""),
        status=status,
        departure=dep,
        arrival=arr,
        duration_min=span if len(legs) > 1 else (last.get("elapsedTime") or span),
        aircraft=legs[0].aircraft,
        diverted_to=next((leg.diverted_to for leg in legs if leg.diverted_to), None),
        legs=legs,
        updated_at=max((leg.updated_at for leg in legs if leg.updated_at), default=None),
        fetched_at=fetched_at,
    )


def _arrives(journey: list[dict]) -> datetime | None:
    leg = _leg(journey[-1]).arrival
    best = leg.actual or leg.estimated or leg.scheduled
    return datetime.fromisoformat(best) if best else None


def pick(journeys: list[list[dict]], on: date | None, now: datetime) -> list[dict] | None:
    """The journey a flight card means: the one departing on `on` (origin-local), or with no
    date, the next one not yet arrived (30 min grace), else the latest."""
    if on is not None:
        day = on.isoformat()
        return next((j for j in journeys if ((j[0].get("departure") or {}).get("date") or {}).get("local") == day), None)
    for j in journeys:
        arrives = _arrives(j)
        if arrives is None or arrives + ARRIVED_GRACE > now:
            return j
    return journeys[-1] if journeys else None


def lookup(code: str, on: date | None = None, now: datetime | None = None) -> Flight | None:
    """Status of one flight. `on` = origin-local departure date; None = the current or next one.
    None if OAG has no such flight in the window. Raises FlightError / MissingKeyError."""
    carrier, number, suffix = parse_code(code)
    now = now or datetime.now(UTC)
    anchor = on or now.astimezone().date()
    # one call: the day before (late flights, zones ahead of ours) through the day after (next leg)
    response, fetched_at = _instances(carrier, number, anchor - timedelta(days=1), anchor + timedelta(days=1), now)
    rows = [r for r in response.get("data") or [] if (r.get("serviceSuffix") or "") == suffix]
    chosen = pick(_journeys(rows), on, now)
    if chosen is None:
        return None
    first = chosen[0].get("carrier") or {}
    return _flight(chosen, fetched_at, airline_name(first.get("iata") or first.get("icao") or carrier))
