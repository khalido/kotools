"""YouTube → transcript via youtube-transcript-api. No auth, no download.

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
        YouTubeTranscriptApi,
        CouldNotRetrieveTranscript,
        NoTranscriptFound,
        TranscriptsDisabled,
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
