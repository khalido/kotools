"""Google Calendar reader/writer — a small agenda + quick-add over the Calendar v3 API.

Read your agenda across calendars, add an event, list calendars. Reads use the read-only scope,
writes the read+write one (one `ko gsheets auth` covers both). Times render in your local zone
(config `[cal] timezone`, default Australia/Sydney).

Deliberately minimal: editing/deleting events, RSVPs, Meet links, recurrence rules — the web UI
wins at those. This is "what's on" and "put this on my calendar".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from googleapiclient.errors import HttpError

from ko import config
from ko.google_auth import get_calendar_service

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


class CalError(RuntimeError):
    pass


def _handle(e: HttpError, context: str) -> None:
    if e.resp.status == 403:
        raise CalError(
            f"Permission denied for {context}. Did you `ko cal auth` (read+write)?"
        ) from e
    if e.resp.status == 404:
        raise CalError(f"Not found: {context}") from e
    raise e


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
    events.sort(key=lambda ev: ev.start)
    return events


def parse_when(s: str) -> tuple[bool, date | datetime]:
    """A CLI date/time string -> (all_day, value). Accepts 'today' / 'tomorrow' / 'YYYY-MM-DD'
    (all-day) or 'YYYY-MM-DDTHH:MM' (timed, in the local zone). Raises ValueError otherwise."""
    s = s.strip()
    if s == "today":
        return True, date.today()
    if s == "tomorrow":
        return True, date.today() + timedelta(days=1)
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
