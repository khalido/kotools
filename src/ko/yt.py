"""YouTube → transcript via youtube-transcript-api (no auth, no download), plus video and
channel metadata via the YouTube Data API v3 (`YOUTUBE_API_KEY`: public data only, 1–2 quota
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

import re
from dataclasses import dataclass
from functools import lru_cache

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
    """Transcript not available or video inaccessible."""


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


def snippets(vid: str, languages: tuple[str, ...] = ("en",)) -> list[Snippet]:
    """Raw transcript snippets (start time + text) for a video.

    Prefers manual subtitles over auto-generated when both exist. Falls back to
    auto-generated if no manual transcript is found for the requested languages.
    Raises YtError if no transcript is available at all.
    """
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
        raise YtError(f"no transcript available for {vid} (captions disabled)") from None
    except CouldNotRetrieveTranscript as e:
        raise YtError(f"no transcript available for {vid} ({_reason(e)})") from None

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
                raise YtError(f"no transcript available for {vid} (captions disabled?)") from None
            t = all_t[0]

    try:
        fetched = t.fetch()
    except CouldNotRetrieveTranscript as e:
        raise YtError(f"no transcript available for {vid} ({_reason(e)})") from None

    return [Snippet(start=s.start, text=s.text, duration=s.duration) for s in fetched.snippets]


def transcript(vid: str, languages: tuple[str, ...] = ("en",)) -> str:
    """Plain text transcript for a video. Paragraphs joined, no timestamps.

    Prefers manual over auto-generated subtitles. Raises YtError when none available.

    Returns a single string suitable for piping into `ko llm`.
    """
    raw = snippets(vid, languages)
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
    "Create an API key restricted to YouTube Data API v3 in your Google Cloud project "
    "(console.cloud.google.com/apis/credentials) and set it in ~/.config/ko/config.toml [keys]."
)
_CHANNEL_ID_RE = re.compile(r"UC[A-Za-z0-9_-]{22}")
_DURATION_RE = re.compile(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?")


@dataclass
class Video:
    id: str
    title: str
    channel: str
    channel_id: str
    published_at: str  # RFC 3339, UTC, exact to the second
    duration: str  # h:mm:ss
    views: int | None  # None when the channel hides the count
    captions: bool
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

    @property
    def url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.id}"


@dataclass
class Channel:
    id: str
    title: str
    uploads: list[Upload]
    hidden: int  # entries among those read with no publish time: private, deleted or not yet live


@lru_cache
def _api():
    from googleapiclient.discovery import build

    key = config.require_key("YOUTUBE_API_KEY", _KEY_HINT)
    return build("youtube", "v3", developerKey=key, cache_discovery=False)


def _call(request, context: str) -> dict:
    """Run one API request; a quota, key or HTTP failure becomes a YtError naming the cause."""
    from googleapiclient.errors import HttpError

    from .google_auth import NETWORK_ERRORS

    try:
        return request.execute(num_retries=2)
    except NETWORK_ERRORS as e:
        raise YtError(f"network error talking to YouTube for {context}: {type(e).__name__}") from None
    except HttpError as e:
        reason = e.reason or str(e.resp.status)
        if e.resp.status == 403 and "quota" in reason.lower():
            reason += " (the daily quota resets at midnight Pacific time)"
        raise YtError(f"YouTube API error for {context}: {reason}") from None


def _duration(iso: str) -> str:
    """ISO 8601 duration (PT1H2M3S, P1DT2H) → h:mm:ss. Live streams report P0D → 0:00:00."""
    m = _DURATION_RE.fullmatch(iso)
    if not m:
        raise YtError(f"unexpected duration format from YouTube: {iso!r}")
    d, h, mi, sec = (int(x or 0) for x in m.groups())
    return f"{d * 24 + h}:{mi:02d}:{sec:02d}"


def info(url_or_id: str) -> Video:
    """One video's metadata (1 quota unit)."""
    vid = video_id(url_or_id)
    resp = _call(_api().videos().list(part="snippet,contentDetails,statistics", id=vid), vid)
    items = resp.get("items") or []
    if not items:
        raise YtError(f"no such video, or it is private: {vid}")
    it = items[0]
    sn, cd, st = it["snippet"], it["contentDetails"], it.get("statistics", {})
    return Video(
        id=vid,
        title=sn["title"],
        channel=sn["channelTitle"],
        channel_id=sn["channelId"],
        published_at=sn["publishedAt"],
        duration=_duration(cd["duration"]),
        views=int(st["viewCount"]) if "viewCount" in st else None,
        captions=cd.get("caption") == "true",
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


def channel(ref: str, last: int = 10) -> Channel:
    """A channel's newest `last` uploads, newest first (2 quota units per 50 videos)."""
    if last < 1:
        raise ValueError("--last must be 1 or more")
    api = _api()
    resp = _call(api.channels().list(part="snippet,contentDetails", **_channel_lookup(ref)), ref)
    items = resp.get("items") or []
    if not items:
        raise YtError(f"no such channel: {ref}")
    ch = items[0]
    playlist = ch["contentDetails"]["relatedPlaylists"]["uploads"]
    uploads: list[Upload] = []
    hidden, token = 0, None
    while len(uploads) < last:
        page = _call(
            api.playlistItems().list(
                part="snippet,contentDetails", playlistId=playlist, maxResults=50, pageToken=token
            ),
            ref,
        )
        for it in page.get("items", []):
            if len(uploads) >= last:
                break
            published = it["contentDetails"].get("videoPublishedAt")
            if not published:
                hidden += 1
                continue
            uploads.append(
                Upload(
                    id=it["contentDetails"]["videoId"],
                    title=it["snippet"]["title"],
                    published_at=published,
                    description=it["snippet"].get("description", ""),
                )
            )
        token = page.get("nextPageToken")
        if not token:
            break
    uploads.sort(key=lambda u: u.published_at, reverse=True)
    return Channel(id=ch["id"], title=ch["snippet"]["title"], uploads=uploads[:last], hidden=hidden)
