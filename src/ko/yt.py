"""YouTube → transcript via youtube-transcript-api (no auth, no download), plus video and
channel metadata via the YouTube Data API v3 (`GOOGLE_CLOUD_API_KEY`: public data only, 1–2 quota
units a call out of 10,000 a day).

Hits YouTube's transcript endpoint directly — fast, no yt-dlp, no ffmpeg.
Manual subtitles are preferred over auto-generated ones when both exist (manual
subtitles are curated; auto-generated has more filler noise). If neither is
available the module raises YtError with a clean, actionable message.

v1.2.4 API shape:
  YouTubeTranscriptApi().fetch(video_id, languages=('en',))
  -> FetchedTranscript(snippets=[FetchedTranscriptSnippet(text, start, duration), ...], is_generated=bool)

For --json: each snippet is {"start": float, "text": str, "duration": float}.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

from . import config

# A URL must carry a youtube domain to be searched for an id; a bare id must be
# EXACTLY 11 id-chars. Anything looser silently grabs 11-char runs from arbitrary
# strings (playlist ids, prose) and fetches the wrong transcript.
_URL_RE = re.compile(
    r"""
    (?:https?://)?                          # optional scheme
    (?:www\.|m\.)?                          # optional www. / m.
    (?:
        youtube\.com/(?:
            watch\?(?:[^#]*&)?v=            # watch?v= (any params before v=)
            | shorts/                       # /shorts/
            | live/                         # /live/
            | embed/                        # /embed/
            | v/                            # /v/ (old embed)
        )
        | youtu\.be/                        # youtu.be short link
    )
    ([A-Za-z0-9_-]{11})                     # the 11-char video id
    (?![A-Za-z0-9_-])                       # ...and not a prefix of something longer
    """,
    re.VERBOSE,
)
_BARE_ID_RE = re.compile(r"[A-Za-z0-9_-]{11}")


class YtError(RuntimeError):
    """Transcript not available or video inaccessible. `code` is what a caller branches on
    (`--json` errors carry it): ip_blocked, no_captions, private, not_found, quota, network, error."""

    def __init__(self, msg: str, code: str = "error"):
        super().__init__(msg)
        self.code = code


# youtube-transcript-api exception class → YtError code
_CODES = {
    "IpBlocked": "ip_blocked",
    "RequestBlocked": "ip_blocked",
    "TranscriptsDisabled": "no_captions",
    "NoTranscriptFound": "no_captions",
    "VideoUnavailable": "not_found",
    "InvalidVideoId": "not_found",
    "AgeRestricted": "private",
    "VideoUnplayable": "private",
}


def _reason(e: Exception) -> str:
    """Terse cause from the library's exception CLASS — its str() is a multi-paragraph
    support blurb (github-issue boilerplate) that must never reach ko's stderr."""
    name = type(e).__name__
    return {
        "TranscriptsDisabled": "captions disabled",
        "NoTranscriptFound": "no transcript in the requested languages",
        "VideoUnavailable": "video unavailable",
        "InvalidVideoId": "invalid video id",
        "AgeRestricted": "age-restricted video",
    }.get(name, re.sub(r"(?<!^)(?=[A-Z])", " ", name).lower())


@dataclass
class Snippet:
    start: float
    text: str
    duration: float


def video_id(url_or_id: str) -> str:
    """Extract the 11-char YouTube video id from a URL or bare id.

    Accepts: bare 11-char id, youtube.com/watch?v=, youtu.be/, /shorts/, /live/, /embed/.
    Raises ValueError for strings that don't match.

    >>> video_id("dQw4w9WgXcQ")
    'dQw4w9WgXcQ'
    >>> video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=30s")
    'dQw4w9WgXcQ'
    """
    url_or_id = url_or_id.strip()
    if _BARE_ID_RE.fullmatch(url_or_id):
        return url_or_id
    m = _URL_RE.search(url_or_id)
    if m:
        return m.group(1)
    raise ValueError(f"cannot extract a YouTube video id from: {url_or_id!r}")


def _cache(kind: str, name: str) -> Path:
    """~/.cache/ko/yt/<kind>/<name>: transcripts and write-ups don't change, so they're kept until
    deleted or `--refresh`ed. Video metadata isn't cached: view counts move."""
    from .dirs import cache_dir

    return cache_dir() / "yt" / kind / name


def _write(path: Path, data) -> None:
    """Write a cache file whole or not at all, so a killed run can't leave half a JSON behind."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    tmp.replace(path)


def snippets(vid: str, languages: tuple[str, ...] = ("en",), *, refresh: bool = False) -> list[Snippet]:
    """Raw transcript snippets (start time + text) for a video, cached by id.

    Prefers manual subtitles over auto-generated when both exist. Falls back to
    auto-generated if no manual transcript is found for the requested languages.
    Raises YtError (with a `code`) if no transcript is available at all.
    """
    path = _cache("transcript", f"{vid}-{'-'.join(languages)}.json")
    if path.exists() and not refresh:
        return [Snippet(**s) for s in json.loads(path.read_text())]
    got = _fetch_snippets(vid, languages)
    _write(path, [asdict(s) for s in got])
    return got


def _fetch_snippets(vid: str, languages: tuple[str, ...]) -> list[Snippet]:
    from youtube_transcript_api import (
        CouldNotRetrieveTranscript,
        NoTranscriptFound,
        TranscriptsDisabled,
        YouTubeTranscriptApi,
    )

    api = YouTubeTranscriptApi()

    try:
        transcript_list = api.list(vid)
    except TranscriptsDisabled:
        raise YtError(f"no transcript available for {vid} (captions disabled)", "no_captions") from None
    except CouldNotRetrieveTranscript as e:
        raise YtError(f"no transcript available for {vid} ({_reason(e)})", _CODES.get(type(e).__name__, "error")) from None

    # Prefer manual subtitles; fall back to auto-generated.
    try:
        t = transcript_list.find_manually_created_transcript(list(languages))
    except NoTranscriptFound:
        try:
            t = transcript_list.find_generated_transcript(list(languages))
        except NoTranscriptFound:
            # No transcript in the requested languages at all — take whatever is first
            all_t = list(transcript_list)
            if not all_t:
                raise YtError(f"no transcript available for {vid} (captions disabled?)", "no_captions") from None
            t = all_t[0]

    try:
        fetched = t.fetch()
    except CouldNotRetrieveTranscript as e:
        raise YtError(f"no transcript available for {vid} ({_reason(e)})", _CODES.get(type(e).__name__, "error")) from None

    return [Snippet(start=s.start, text=s.text, duration=s.duration) for s in fetched.snippets]


def transcript(vid: str, languages: tuple[str, ...] = ("en",), *, refresh: bool = False) -> str:
    """Plain text transcript for a video. Paragraphs joined, no timestamps.

    Prefers manual over auto-generated subtitles. Raises YtError when none available.

    Returns a single string suitable for piping into `ko llm`.
    """
    return join(snippets(vid, languages, refresh=refresh))


def join(raw: list[Snippet]) -> str:
    """Snippets → paragraphs: a new one where the speech pauses for more than 2 s."""
    # Join as paragraphs: a new paragraph when there's a visible gap (>2s) between snippets.
    if not raw:
        return ""
    lines: list[str] = []
    prev_end = raw[0].start + raw[0].duration
    for s in raw:
        if lines and s.start - prev_end > 2.0:
            lines.append("")  # blank line = paragraph break
        lines.append(s.text.strip())
        prev_end = s.start + s.duration
    return "\n".join(lines)


# --- metadata: YouTube Data API v3 (public data, API key) ---------------------------------

_KEY_HINT = (
    "Create an API key with YouTube Data API v3 enabled in your Google Cloud project "
    "(console.cloud.google.com/apis/credentials) and set it in ~/.config/ko/config.toml [keys]."
)
_CHANNEL_ID_RE = re.compile(r"UC[A-Za-z0-9_-]{22}")
_DURATION_RE = re.compile(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?")


@dataclass
class Chapter:
    start: str  # as written in the description, e.g. "1:02:03"
    seconds: int
    title: str


# A chapter line starts with a timestamp: "0:00 Intro", "1:02:03 - Verdict". YouTube only makes
# chapters when the first is 0:00 and there are at least three, so anything else isn't chapters.
_CHAPTER_RE = re.compile(r"^\s*\(?((?:\d+:)?\d{1,2}:\d{2})\)?\s*[-–—:|]?\s*(.+?)\s*$")


def chapters(description: str) -> list[Chapter]:
    """Chapters from a description, by YouTube's own rule (first at 0:00, three or more)."""
    found = []
    for line in description.splitlines():
        m = _CHAPTER_RE.match(line)
        if m:
            parts = [int(p) for p in m.group(1).split(":")]
            secs = sum(p * 60**i for i, p in enumerate(reversed(parts)))
            found.append(Chapter(start=m.group(1), seconds=secs, title=m.group(2)))
    if len(found) < 3 or found[0].seconds != 0:
        return []
    return found


@dataclass
class Video:
    id: str
    title: str
    channel: str
    channel_id: str
    published_at: str  # RFC 3339, UTC, exact to the second
    duration: str  # h:mm:ss
    views: int | None  # None when the channel hides the count
    uploaded_captions: bool  # captions the uploader added; auto-generated ones don't count, so False ≠ no transcript
    language: str | None  # defaultAudioLanguage (e.g. "en", "en-AU"), None when the uploader didn't set it
    chapters: list[Chapter]
    description: str

    @property
    def url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.id}"


@dataclass
class Upload:
    id: str
    title: str
    published_at: str  # the video's own publish time, not when it joined the uploads list
    description: str
    duration: str = ""  # h:mm:ss
    is_short: bool | None = False  # None: 3 minutes or less, and the Shorts check failed

    @property
    def url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.id}"


@dataclass
class Channel:
    id: str
    title: str
    uploads: list[Upload]
    hidden: int  # entries among those read with no publish time: private, deleted or not yet live
    more: bool = False  # stopped at `last` with more uploads still in range
    shorts_skipped: int = 0
    unchecked: int = 0  # short-length uploads kept because the Shorts check failed


@lru_cache
def _api():
    from googleapiclient.discovery import build

    # one Cloud key serves every public-data Google API; YOUTUBE_API_KEY is its first name (2026-10-14)
    key = os.environ.get("YOUTUBE_API_KEY") or config.require_key("GOOGLE_CLOUD_API_KEY", _KEY_HINT)
    return build("youtube", "v3", developerKey=key, cache_discovery=False)


def _call(request, context: str) -> dict:
    """Run one API request; a quota, key or HTTP failure becomes a YtError naming the cause."""
    from googleapiclient.errors import HttpError

    from .google_auth import NETWORK_ERRORS

    try:
        return request.execute(num_retries=2)
    except NETWORK_ERRORS as e:
        raise YtError(f"network error talking to YouTube for {context}: {type(e).__name__}", "network") from None
    except HttpError as e:
        reason, code = e.reason or str(e.resp.status), {404: "not_found"}.get(e.resp.status, "error")
        if e.resp.status == 403 and "quota" in reason.lower():
            reason, code = reason + " (the daily quota resets at midnight Pacific time)", "quota"
        raise YtError(f"YouTube API error for {context}: {reason}", code) from None


def _seconds(iso: str) -> int:
    """ISO 8601 duration (PT1H2M3S, P1DT2H) → seconds. Live streams report P0D → 0."""
    m = _DURATION_RE.fullmatch(iso)
    if not m:
        raise YtError(f"unexpected duration format from YouTube: {iso!r}")
    d, h, mi, sec = (int(x or 0) for x in m.groups())
    return ((d * 24 + h) * 60 + mi) * 60 + sec


def _duration(iso: str) -> str:
    """ISO 8601 duration → h:mm:ss."""
    t = _seconds(iso)
    return f"{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}"


SHORTS_MAX = 180  # seconds: a Short can't be longer (since 2024), so longer videos skip the check


def _is_short(vid: str) -> bool | None:
    """Exact, as YouTube itself decides: /shorts/<id> answers 200 for a Short and redirects to
    /watch for anything else. The Data API has no Shorts field. None when the check fails."""
    import httpx2 as httpx

    try:
        r = httpx.head(f"https://www.youtube.com/shorts/{vid}", follow_redirects=False, timeout=10)
    except httpx.HTTPError:
        return None
    return {200: True}.get(r.status_code, False if r.is_redirect else None)


def infos(urls_or_ids: list[str]) -> tuple[list[Video], list[str]]:
    """Metadata for many videos, 1 quota unit per 50. Returns (found in the order asked,
    ids YouTube didn't return — private, deleted or never existed)."""
    ids = list(dict.fromkeys(video_id(u) for u in urls_or_ids))
    got: dict[str, Video] = {}
    for i in range(0, len(ids), 50):
        chunk = ids[i : i + 50]
        resp = _call(_api().videos().list(part="snippet,contentDetails,statistics", id=",".join(chunk)), chunk[0])
        for it in resp.get("items") or []:
            got[it["id"]] = _video(it)
    return [got[v] for v in ids if v in got], [v for v in ids if v not in got]


def _video(it: dict) -> Video:
    sn, cd, st = it["snippet"], it["contentDetails"], it.get("statistics", {})
    return Video(
        id=it["id"],
        title=sn["title"],
        channel=sn["channelTitle"],
        channel_id=sn["channelId"],
        published_at=sn["publishedAt"],
        duration=_duration(cd["duration"]),
        views=int(st["viewCount"]) if "viewCount" in st else None,
        uploaded_captions=cd.get("caption") == "true",
        language=sn.get("defaultAudioLanguage"),
        chapters=chapters(sn.get("description", "")),
        description=sn.get("description", ""),
    )


def _channel_lookup(ref: str) -> dict:
    """An @handle, channel id, or channel URL → the channels.list query that finds it."""
    ref = ref.strip().rstrip("/")
    m = re.search(r"youtube\.com/(@[^/?#]+|channel/[^/?#]+|user/[^/?#]+|c/[^/?#]+)", ref)
    part = m.group(1) if m else ref
    if part.startswith("channel/"):
        part = part.removeprefix("channel/")
    if _CHANNEL_ID_RE.fullmatch(part):
        return {"id": part}
    if part.startswith("user/"):
        return {"forUsername": part.removeprefix("user/")}
    if part.startswith("c/"):
        raise YtError(f"custom /c/ URLs can't be looked up by the API — use the channel's @handle: {ref}")
    return {"forHandle": part if part.startswith("@") else f"@{part}"}


def channel(ref: str, last: int | None = None, since: str | None = None, *, shorts: bool = False) -> Channel:
    """A channel's newest uploads, newest first, each with its duration; Shorts left out unless
    `shorts`. `since` (an ISO date or time; a bare date is UTC midnight) drops older ones; `last`
    caps the count after both filters — 10 by default, no cap with `since`. 2 quota units per 50
    uploads read, plus one request to youtube.com per upload of 3 minutes or less."""
    if last is None:
        last = 10_000 if since else 10
    if last < 1:
        raise ValueError("--last must be 1 or more")
    cutoff = _utc(since) if since else None
    api = _api()
    resp = _call(api.channels().list(part="snippet,contentDetails", **_channel_lookup(ref)), ref)
    items = resp.get("items") or []
    if not items:
        raise YtError(f"no such channel: {ref}")
    ch = items[0]
    out = Channel(id=ch["id"], title=ch["snippet"]["title"], uploads=[], hidden=0)
    playlist = ch["contentDetails"]["relatedPlaylists"]["uploads"]
    token, done = None, False
    while not done:
        page = _call(
            api.playlistItems().list(part="snippet,contentDetails", playlistId=playlist, maxResults=50, pageToken=token),
            ref,
        )
        fresh: list[Upload] = []
        for it in page.get("items", []):
            published = it["contentDetails"].get("videoPublishedAt")
            if not published:
                out.hidden += 1
            elif cutoff and _utc(published) < cutoff:
                done = True  # the uploads list runs newest first: everything after is older
            else:
                fresh.append(Upload(it["contentDetails"]["videoId"], it["snippet"]["title"], published,
                                    it["snippet"].get("description", "")))
        if fresh:
            resp = _call(api.videos().list(part="contentDetails", id=",".join(u.id for u in fresh)), ref)
            secs = {it["id"]: _seconds(it["contentDetails"]["duration"]) for it in resp.get("items") or []}
        for u in sorted(fresh, key=lambda u: u.published_at, reverse=True):
            t = secs.get(u.id, 0)
            u.duration = f"{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}"
            u.is_short = _is_short(u.id) if 0 < t <= SHORTS_MAX else False
            if u.is_short and not shorts:
                out.shorts_skipped += 1
                continue
            if u.is_short is None:
                out.unchecked += 1
            if len(out.uploads) >= last:
                out.more, done = True, True
                break
            out.uploads.append(u)
        token = page.get("nextPageToken")
        done = done or not token
    return out


def _utc(value: str) -> datetime:
    """An ISO date or date-time → an aware UTC datetime. A bare date means UTC midnight; a
    time without a zone is refused rather than guessed."""
    try:
        dt = datetime.fromisoformat(value)  # 3.11+ reads a trailing Z
    except ValueError:
        raise ValueError(f"not an ISO date or time: {value!r} (e.g. 2026-10-01 or 2026-10-01T09:00:00Z)") from None
    if dt.tzinfo is None:
        if len(value) > 10:
            raise ValueError(f"give a time zone, e.g. {value}Z: {value!r}")
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


# --- transcript status and the condensed write-up -------------------------------------------

EXCERPT_WORDS = 100  # per part: start, middle and end
CONDENSE_MODEL = "vercel:google/gemini-3.8-flash"  # 2026-11-04: every drive event at half 3.5 Flash's price; Flash-Lite misses them


def transcript_status(vid: str, *, full: bool = False, refresh: bool = False) -> dict:
    """What `info` says about a video's free transcript: `available`, then either `excerpt`
    (start … middle … end, or the whole thing when short) or `text` (with `full`), or the
    failure's `code` and `reason`."""
    try:
        text = transcript(vid, refresh=refresh)
    except YtError as e:
        return {"available": False, "code": e.code, "reason": str(e)}
    words = text.split()
    out: dict = {"available": True, "words": len(words)}
    if full:
        out["text"] = text
    elif len(words) <= 3 * EXCERPT_WORDS:
        out["excerpt"] = text
    else:
        mid, n = len(words) // 2, EXCERPT_WORDS
        parts = (words[:n], words[mid - n // 2 : mid + n // 2], words[-n:])
        out["excerpt"] = " … ".join(" ".join(p) for p in parts)
    return out


@dataclass
class Condensed:
    video_id: str
    text: str
    model: str  # the model that actually answered
    input: str  # what it was given: "video"
    input_tokens: int
    output_tokens: int
    usd: float | None
    created: str
    prompt_sha: str
    cached: bool = False


def condensed_model() -> str:
    """`-s`'s model: KO_YT_MODEL → `[yt] model` in config.toml → Gemini 3.8 Flash. Runs on Google's
    free tier when GEMINI_FREE_API_KEY is set (public video, so its training terms don't matter),
    else through the AI Gateway; an explicit `-m` always goes where it says."""
    return config.setting("KO_YT_MODEL", "yt", "model", CONDENSE_MODEL)


def condense(url_or_id: str, *, model: str | None = None, refresh: bool = False) -> Condensed:
    """The video written up by a model that watches it (the `yt-condense` prompt: every point, no
    filler, on-screen text marked), cached by video, model and prompt. Costs tokens on a miss."""
    from . import llm

    vid = video_id(url_or_id)
    body, path = _condensed_path(vid, model)
    if path.exists() and not refresh:
        return Condensed(**json.loads(path.read_text()), cached=True)
    llm.last_cost = None
    try:
        text = _watch(vid, body, model)
    except Exception as e:  # a cut-short answer is still billed: say what it cost
        spent = f" ({llm.last_cost.note} spent)" if llm.last_cost else ""
        raise YtError(f"write-up of {vid} failed: {e}{spent}") from None
    c = llm.last_cost
    done = Condensed(vid, text, c.model, "video", c.input_tokens, c.output_tokens, c.usd,
                     datetime.now(UTC).isoformat(timespec="seconds"), path.stem.rsplit("-", 1)[-1])
    _write(path, {k: v for k, v in asdict(done).items() if k != "cached"})
    return done


def _watch(vid: str, body: str, model: str | None) -> str:
    """One write-up run. With no `-m` and GEMINI_FREE_API_KEY set, Google's free tier first; when
    it's busy, out of quota or slow (503/429/timeout — it serves free traffic last), the paid gateway, with a note."""
    import sys

    from pydantic_ai import VideoUrl
    from pydantic_ai.exceptions import (
        ModelConnectionError,  # incl. timeouts
        ModelQuotaExceededError,
        ModelRateLimitError,
        ModelUnavailableError,  # incl. 503 overloaded
    )

    from . import llm, models

    video = [VideoUrl(url=f"https://www.youtube.com/watch?v={vid}")]
    free = None if model else models.free_gemini(condensed_model())
    if free:
        try:
            return llm.run("Write up the attached video.", model=free, system=body, attachments=video)
        except (ModelConnectionError, ModelRateLimitError, ModelQuotaExceededError, ModelUnavailableError) as e:
            print(f"[free tier busy ({type(e).__name__}) — paying via the gateway instead]", file=sys.stderr)
    return llm.run("Write up the attached video.", model=model or condensed_model(), system=body, attachments=video)


def _condensed_path(vid: str, model: str | None) -> tuple[str, Path]:
    """The prompt body and the cache file for a write-up of `vid` by `model` with today's prompt.
    Keyed on the model, not the route: `vercel:google/x` and `openrouter:google/x` share a file."""
    from . import prompt

    model = (model or condensed_model()).split(":", 1)[-1]
    body = prompt.get_prompt("yt-condense").body
    sha = hashlib.sha256(body.encode()).hexdigest()[:8]
    return body, _cache("condensed", f"{vid}-{re.sub(r'[^A-Za-z0-9.-]+', '_', model)}-{sha}.json")


def is_condensed(url_or_id: str, model: str | None = None) -> bool:
    """Whether a write-up is already cached — i.e. asking for it costs nothing."""
    return _condensed_path(video_id(url_or_id), model)[1].exists()
