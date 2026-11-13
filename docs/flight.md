# `ko flight` — design (2026-11-13)

`ko flight EK604 [date]` → what a search engine's flight card shows. **Built OAG-only** (KO, 2026-11-13: fallbacks later). The fallback sections below are the plan for that pass; `--source` and AeroDataBox are not built.

## What OAG's free tier returns (3 live calls, 2026-11-13)

Request: `GET https://api.oag.com/flight-instances/?version=v2&CarrierCode=EK&FlightNumber=604&DepartureDateTime=<date or from/to>&CodeType=IATA,ICAO&Content=Status&Limit=10`, header `Subscription-Key: $OAG_FLIGHT_INFO_KEY`. `DepartureDateTime` is the origin's **local** date, or a range of up to 31 days.

| Call | Result |
|---|---|
| EK604, 13 Nov | `200`, `data: []`. |
| any EK from DXB, 13 Nov, `Limit=3` | 366 flights. Free tier **includes `Content=Status`**: gate, terminal, estimated + actual gate and runway times, state. |
| EK604, 13–20 Nov | 4 instances: DXB 02:45 → KHI 05:45 on 14, 17, 18, 19 Nov. The 14th has `state: Scheduled`; later dates have no `statusDetails` at all. |

So EK604 doesn't run every day, and the number lookup works. OAG returns codes only: no airport names, cities, IANA zones or airline names. Responses carry `cache-control: max-age=60`; no quota header. Raw responses are the test fixtures in `tests/fixtures/flight/`.

Trimmed live sample (EK2330, a flydubai codeshare):

```json
{"carrier": {"iata": "EK", "icao": "UAE"}, "flightNumber": 2330, "sequenceNumber": 1,
 "departure": {"airport": {"iata": "DXB"}, "terminal": "2",
   "date": {"local": "2026-11-13", "utc": "2026-11-12"}, "time": {"local": "00:30", "utc": "20:30"}},
 "arrival":   {"airport": {"iata": "DAC"}, "terminal": "1",
   "date": {"local": "2026-11-13", "utc": "2026-11-13"}, "time": {"local": "06:55", "utc": "00:55"}},
 "elapsedTime": 265, "aircraftType": {"iata": "7M8"},
 "codeshare": {"operatingFlight": {"code": "FZ", "serviceNumber": "501"}},
 "statusDetails": [{"state": "InGate", "updatedAt": "2026-11-13T01:57:47.531",
   "departure": {"actualTerminal": "2", "gate": "F10",
     "estimatedTime": {"outGateTimeliness": "Delayed", "outGateVariation": "00:45:00",
                       "outGate": {"local": "2026-11-13T01:15:00+04:00", "utc": "..."}},
     "actualTime":    {"outGate": {"local": "2026-11-13T01:16:00+04:00", "utc": "..."}}},
   "arrival": {"actualTerminal": "1",
     "estimatedTime": {"inGate": {"local": "2026-11-13T08:00:00+06:00", "utc": "..."}},
     "actualTime":    {"inGateVariation": "00:55:00",
                       "inGate": {"local": "2026-11-13T07:50:00+06:00", "utc": "..."}}}}]}
```

## Fallback (not built): AeroDataBox (RapidAPI, free "Basic")

| | Verdict |
|---|---|
| **AeroDataBox** | **Pick.** `GET https://aerodatabox.p.rapidapi.com/flights/number/EK604/2026-11-14`, headers `X-RapidAPI-Key` + `X-RapidAPI-Host`. Has every card field: airport name, city, `timeZone`, scheduled/revised/predicted/runway times in UTC and local, terminal, gate, baggage belt, status. 400 units a month; this endpoint is TIER 2 = 2 units, so ~200 lookups. Schedules up to 365 days ahead; live data about a day ahead. Free terms: non-commercial, attribution required. |
| FlightAware AeroAPI Personal | Runner-up. $5 free credit ≈ 1,000 calls, personal use only. Only 10 days back to 2 days ahead, times in UTC. |
| AirLabs | Reject: no date parameter, returns only "the nearest" flight. |
| aviationstack | Reject: 100 calls a month, and lookup by date is a paid feature. |
| OpenSky | Reject as a card source: ADS-B positions by ICAO callsign, no schedule, terminal, gate or status; flights by callsign only for past days. Could add "in the air now" later. |
| Scraping Google's flight box | Reject: against Google's terms, bot wall for scripts, unlabelled markup that breaks silently. |

Sign-up (KO): rapidapi.com account (email or Google) → subscribe to AeroDataBox → Basic plan (free) → copy the RapidAPI key into `AERODATABOX_API_KEY`. Whether RapidAPI asks for a card on a free plan isn't stated on its pages.

Not yet seen live: AeroDataBox's `local` string format (the spec says `date-time`). The mapper normalises it, and the first live call becomes a fixture.

## Design

**Command:** `ko flight <CODE> [DATE]`
- `CODE`: `EK604`, `ek604`, or ICAO `UAE604`. Three leading letters = ICAO, else a 2-character IATA code. Anything else exits 2.
- `DATE`: `YYYY-MM-DD`, `today`, `tomorrow` or `yesterday`, read as the origin's local departure date (OAG's meaning).
- **No date:** one OAG call for yesterday → tomorrow, then pick the instance that hasn't arrived yet and departs soonest, else the latest one. That's how a search card behaves, and it avoids "today in whose zone?" when KO is in Sydney and the flight leaves Dubai.
- Flags: `--json` only. `--source oag|aerodatabox` comes with the fallback. `ko flight EK 604` (with a space) also works.

**Source order and fallback rule:** OAG first. AeroDataBox is called **only when OAG fails** (missing key, 401/403/429/5xx, network), never when OAG answers "no such flight". An empty OAG result is an answer, and AeroDataBox has only ~200 calls a month. Each fallback prints a note to stderr: `OAG failed (429 quota); answered by AeroDataBox`. Until `AERODATABOX_API_KEY` exists, an OAG failure exits 1 and names both: the OAG error, and the AeroDataBox key with its sign-up URL.

**Exit codes:** found → 0 and one object. Not found → 0 with a stderr note via `_no_results`, which prints `[]` under `--json` (jaldi must handle `[]` as well as `{}`). OAG fails → 1, `{error, code}`.

**Cache:** jaldi polling would burn 1,000 calls a month fast (one every 5 minutes for a day = 288). Disk cache per (flight, query window): 5 minutes when the window touches yesterday–tomorrow, 6 hours otherwise. jaldi should poll at most every 15 minutes.

**Names:** airport name, city and IANA zone from the `airportsdata` package (MIT, updated weekly, 7,950 IATA codes, offline; checked: DXB → "Dubai International Airport", Dubai, Asia/Dubai). Used for both sources, so names don't change with the source. Unknown code → null fields, not an error. Airline name: OAG gives none. Proposal: one OAG Master Data call per new carrier (`OAG_MASTER_DATA_KEY`), cached for good; no key → `airline.name: null`. The Master Data endpoint is not verified yet.

### JSON (`--json`)

```json
{
  "flight": "EK604", "airline": {"iata": "EK", "name": "Emirates"},
  "operated_by": null,
  "date": "2026-11-14",
  "status": "scheduled",
  "departure": {"airport": {"iata": "DXB", "name": "Dubai International Airport", "city": "Dubai", "tz": "Asia/Dubai"},
                "terminal": "3", "gate": null,
                "scheduled": "2026-11-14T02:45:00+04:00", "estimated": null, "actual": null, "delay_min": null},
  "arrival":   {"airport": {"iata": "KHI", "name": "Jinnah International Airport", "city": "Karachi", "tz": "Asia/Karachi"},
                "terminal": "M", "gate": null, "baggage": null,
                "scheduled": "2026-11-14T05:45:00+05:00", "estimated": null, "actual": null, "delay_min": null},
  "duration_min": 120, "aircraft": "77W",
  "legs": [ /* one per leg, same departure/arrival/status shape; usually one */ ],
  "source": "oag", "updated_at": null, "fetched_at": "2026-11-13T02:40:00Z"
}
```

- Times are gate times (out of gate, into gate), as a search card shows. Runway times are dropped.
- `departure`/`arrival` at the top level summarise the first and last leg; `legs` is always present.
- `operated_by`: the operating flight when this number is a codeshare (EK2330 → `"FZ501"`).
- `status`: `scheduled`, `departed`, `in_air`, `landed`, `arrived`, `cancelled`, `diverted`, `unknown`. "Delayed" is not a status; it's `delay_min > 0`.

### Field mapping

| ko field | OAG | AeroDataBox |
|---|---|---|
| airport iata | `departure.airport.iata` | `departure.airport.iata` |
| airport name, city, tz | airportsdata | airportsdata |
| scheduled | `date.local` + `time.local`; offset = local − utc of the same pair | `scheduledTime.local` |
| estimated | `statusDetails[].departure.estimatedTime.outGate.local` (arrival: `inGate`) | `revisedTime.local` before departure/arrival, else `predictedTime.local` |
| actual | `…actualTime.outGate.local` / `inGate` | `revisedTime.local` once status is Departed / Arrived |
| delay_min | (actual or estimated) − scheduled, so it agrees with the times shown (OAG's own `…Variation` can differ by a minute) | estimated/actual − scheduled |
| terminal | `statusDetails.actualTerminal`, else schedule `terminal` | `terminal` |
| gate | `statusDetails.departure.gate` / `arrival.gate` | `gate` |
| baggage | `statusDetails.arrival.baggage` | `arrival.baggageBelt` |
| status | `state`: Scheduled→scheduled, OutGate→departed, InAir→in_air, Landed→landed, InGate→arrived, Canceled→cancelled, Diverted→diverted; no `statusDetails`→scheduled | Expected/CheckIn/Boarding/GateClosed/Delayed→scheduled, Departed→departed, EnRoute/Approaching→in_air, Arrived→arrived, Canceled/CanceledUncertain→cancelled, Diverted→diverted, Unknown→unknown |
| duration_min | `elapsedTime` | arrival − departure scheduled |
| aircraft | `aircraftType.iata` | `aircraft.model` |
| operated_by | `codeshare.operatingFlight` | `codeshareStatus` (+ operator, when given) |
| updated_at | `statusDetails.updatedAt` (no offset in OAG: read as UTC) | `lastUpdatedUtc` |

OAG's `statusDetails` is an array; take the entry with the latest `updatedAt`. Every status field is optional: the sample had no arrival gate.

### Plain text

```
EK604  Emirates  Sat 14 Nov  Scheduled
DXB Dubai → KHI Karachi  2h 00m  77W
         sched         est/actual  term  gate
Depart   02:45 +04:00  —           3     —
Arrive   05:45 +05:00  —           M     —
```
A delay shows as `· 45m late` after the status, and a time on a later local day gets `+1d`; an AeroDataBox answer adds `Data: AeroDataBox` (attribution).

### Wiring

`src/ko/flight.py` (OAG → one `Flight` dataclass), CLI command in `cli_web.py` beside `tv`, `ko doctor` rows for `OAG_FLIGHT_INFO_KEY` and `OAG_MASTER_DATA_KEY` (optional; free tier allows `Limit=1`), `airportsdata` added to deps, `tests/test_flight.py` on recorded fixtures with no live calls.
