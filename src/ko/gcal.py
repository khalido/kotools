"""Google Calendar reader/writer — a small agenda + quick-add over the Calendar v3 API.

Read your agenda across calendars, add an event, list calendars. Reads use the read-only scope,
writes the read+write one (one `ko gsheets auth` covers both). Times render in your local zone
(config `[cal] timezone`, default Australia/Sydney).

Deliberately minimal: editing/deleting events, RSVPs, Meet links, recurrence rules — the web UI
wins at those. This is "what's on" and "put this on my calendar".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from googleapiclient.errors import HttpError

from ko import config
from ko.google_auth import GoogleError, get_calendar_service, raise_for_status

DEFAULT_TZ = "Australia/Sydney"


@dataclass
class Calendar:
    id: str
    name: str
    primary: bool
    role: str


@dataclass
class CalEvent:
    id: str
    summary: str
    start: str  # "YYYY-MM-DD" (all-day) or RFC3339 (timed)
    end: str
    all_day: bool
    calendar_id: str
    calendar_name: str
    location: str | None = None
    html_link: str | None = None


class CalError(GoogleError):
    pass


def _handle(e: HttpError, context: str) -> None:
    raise_for_status(
        e, context, not_found=CalError, permission=CalError, hint="Did you `ko cal auth` (read+write)?"
    )


def tz_name() -> str:
    return config.get("cal", "timezone") or DEFAULT_TZ


def tz() -> ZoneInfo:
    return ZoneInfo(tz_name())


def list_calendars() -> list[Calendar]:
    """Every calendar on the account (primary, shared, subscribed)."""
    svc = get_calendar_service()
    out: list[Calendar] = []
    token = None
    while True:
        try:
            resp = svc.calendarList().list(pageToken=token).execute(num_retries=3)
        except HttpError as e:
            _handle(e, "calendarList")
        for c in resp.get("items", []):
            out.append(
                Calendar(
                    id=c["id"],
                    name=c.get("summary", c["id"]),
                    primary=c.get("primary", False),
                    role=c.get("accessRole", ""),
                )
            )
        token = resp.get("nextPageToken")
        if not token:
            break
    return out


def resolve_calendar_ids(names: list[str], calendars: list[Calendar] | None = None) -> list[str]:
    """Map calendar names (case-insensitive) or raw ids to ids — for "only these calendars"
    allowlists (config or `--calendar`). `calendars` defaults to list_calendars() (pass it to
    avoid the call / for tests). Raises CalError naming what's available if one can't be matched."""
    cals = calendars if calendars is not None else list_calendars()
    by_name = {c.name.lower(): c.id for c in cals}
    ids = {c.id for c in cals}
    out: list[str] = []
    missing: list[str] = []
    for name in names:
        key = name.strip()
        if key in ids:
            out.append(key)
        elif key.lower() in by_name:
            out.append(by_name[key.lower()])
        else:
            missing.append(name)
    if missing:
        avail = ", ".join(sorted(c.name for c in cals)) or "(none)"
        raise CalError(f"no calendar named {missing}. Available: {avail}")
    return out


def _start_key(ev: CalEvent) -> datetime:
    """A comparable aware datetime for sorting mixed all-day + timed events. String-sorting
    ev.start is wrong: '...+10:00' vs '...Z' offsets (and date vs datetime) don't order
    lexicographically. All-day events anchor to midnight in the local zone."""
    s = ev.start
    try:
        if len(s) == 10:  # all-day "YYYY-MM-DD"
            return datetime.fromisoformat(s).replace(tzinfo=tz())
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return datetime.max.replace(tzinfo=timezone.utc)


def _event(raw: dict, cal_id: str, cal_name: str) -> CalEvent:
    s, e = raw.get("start", {}), raw.get("end", {})
    return CalEvent(
        id=raw.get("id", ""),
        summary=raw.get("summary", "(no title)"),
        start=s.get("date") or s.get("dateTime", ""),
        end=e.get("date") or e.get("dateTime", ""),
        all_day="date" in s,
        calendar_id=cal_id,
        calendar_name=cal_name,
        location=raw.get("location"),
        html_link=raw.get("htmlLink"),
    )


def list_events(
    days: int = 7,
    calendar_ids: list[str] | None = None,
    time_min: datetime | None = None,
    time_max: datetime | None = None,
) -> list[CalEvent]:
    """Events from now (or time_min) over the next `days`, across all calendars (or calendar_ids),
    sorted by start. Recurring events are expanded (singleEvents=True)."""
    svc = get_calendar_service()
    now = datetime.now(tz())
    tmin = (time_min or now).isoformat()
    tmax = (time_max or (now + timedelta(days=days))).isoformat()
    cals = {c.id: c.name for c in list_calendars()}
    ids = calendar_ids or list(cals)
    events: list[CalEvent] = []
    for cid in ids:
        token = None
        while True:
            try:
                resp = (
                    svc.events()
                    .list(
                        calendarId=cid,
                        timeMin=tmin,
                        timeMax=tmax,
                        singleEvents=True,
                        orderBy="startTime",
                        pageToken=token,
                        maxResults=250,
                    )
                    .execute(num_retries=3)
                )
            except HttpError as e:
                _handle(e, cid)
            for raw in resp.get("items", []):
                events.append(_event(raw, cid, cals.get(cid, cid)))
            token = resp.get("nextPageToken")
            if not token:
                break
    events.sort(key=_start_key)
    return events


def search_events(
    query: str, days: int = 60, past: bool = False, calendar_ids: list[str] | None = None
) -> list[CalEvent]:
    """Events whose summary contains `query` (case-insensitive substring), within `days` of now.
    Forward by default ("when's the next X"); past=True searches backward and returns most-recent
    first ("when was my last X"). Substring keeps it simple — expand abbreviations upstream."""
    q = query.strip().lower()
    if not q:
        return []
    if past:
        now = datetime.now(tz())
        events = list_events(
            time_min=now - timedelta(days=days), time_max=now, calendar_ids=calendar_ids
        )
        matches = [e for e in events if q in e.summary.lower()]
        matches.reverse()  # list_events sorts ascending; most recent first for "last X"
        return matches
    return [e for e in list_events(days=days, calendar_ids=calendar_ids) if q in e.summary.lower()]


def parse_when(s: str) -> tuple[bool, date | datetime]:
    """A CLI date/time string -> (all_day, value). Accepts 'today' / 'tomorrow' / 'YYYY-MM-DD'
    (all-day) or 'YYYY-MM-DDTHH:MM' (timed, in the local zone). Raises ValueError otherwise."""
    s = s.strip()
    today = datetime.now(tz()).date()  # the configured zone, not the host's
    if s == "today":
        return True, today
    if s == "tomorrow":
        return True, today + timedelta(days=1)
    if "T" in s:
        dt = datetime.fromisoformat(s)
        return False, (dt.replace(tzinfo=tz()) if dt.tzinfo is None else dt)
    return True, date.fromisoformat(s)


def _event_body(summary: str, when: str, end: str | None = None, minutes: int = 60) -> dict:
    """Build the events.insert body. Pure (no API). All-day when -> all-day event; a timed event
    with no end defaults to +`minutes`. Coerces a mismatched date/datetime end to the start's kind
    so end.date is always a date and end.dateTime always a datetime."""
    all_day, start_val = parse_when(when)
    if all_day:
        end_val = parse_when(end)[1] if end else start_val
        end_date = end_val.date() if isinstance(end_val, datetime) else end_val
        end_day = end_date + timedelta(days=1)  # end.date is exclusive
        return {
            "summary": summary,
            "start": {"date": start_val.isoformat()},
            "end": {"date": end_day.isoformat()},
        }
    end_dt = parse_when(end)[1] if end else (start_val + timedelta(minutes=minutes))
    if not isinstance(end_dt, datetime):  # an all-day end given for a timed start -> midnight, local
        end_dt = datetime(end_dt.year, end_dt.month, end_dt.day, tzinfo=tz())
    return {
        "summary": summary,
        "start": {"dateTime": start_val.isoformat(), "timeZone": tz_name()},
        "end": {"dateTime": end_dt.isoformat(), "timeZone": tz_name()},
    }


def create_event(
    summary: str,
    when: str,
    end: str | None = None,
    calendar_id: str = "primary",
    minutes: int = 60,
) -> CalEvent:
    """Create an event. `when`/`end` per parse_when; a timed event with no end defaults to
    +`minutes`. All-day when -> all-day event. Returns the created CalEvent."""
    body = _event_body(summary, when, end, minutes)
    try:
        raw = (
            get_calendar_service(readonly=False)
            .events()
            .insert(calendarId=calendar_id, body=body)
            .execute(num_retries=3)
        )
    except HttpError as e:
        _handle(e, f"insert event on {calendar_id}")
    return _event(raw, calendar_id, calendar_id)
