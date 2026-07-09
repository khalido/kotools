"""Offline tests for ko.yt — URL parsing, transcript formatting, and error handling.

No network calls here: all API interactions are monkeypatched or the function
being tested is pure (video_id is a pure parser — no external calls).
"""

from __future__ import annotations

import pytest

from ko import yt


# ---------------------------------------------------------------------------
# video_id — pure URL parser, table-tested
# ---------------------------------------------------------------------------

VALID_IDS = [
    # bare 11-char id
    ("dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    # watch?v=
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://youtube.com/watch?v=dQw4w9WgXcQ&t=30s&feature=share", "dQw4w9WgXcQ"),
    # youtu.be short link
    ("https://youtu.be/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://youtu.be/dQw4w9WgXcQ?t=10", "dQw4w9WgXcQ"),
    # /shorts/
    ("https://www.youtube.com/shorts/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    # /live/
    ("https://www.youtube.com/live/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    # /embed/
    ("https://www.youtube.com/embed/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    # without scheme
    ("youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    # whitespace stripped
    ("  dQw4w9WgXcQ  ", "dQw4w9WgXcQ"),
]


@pytest.mark.parametrize("raw, expected", VALID_IDS)
def test_video_id_valid(raw: str, expected: str) -> None:
    assert yt.video_id(raw) == expected


INVALID_IDS = [
    "not-a-url",
    "https://vimeo.com/123456789",
    "",
    "short",               # too short for an id
    "https://youtube.com/channel/UCxxxxxx",  # channel, not a video
    # regression: an 11-char id-charset run inside a NON-youtube string must not "parse" —
    # the old permissive regex grabbed these and could fetch someone else's transcript
    "definitely-not-a-video",
    "https://www.youtube.com/watch?list=PL1234567890ABCDEF",  # playlist URL, no v=
    "dQw4w9WgXcQtoolong",  # bare id must be EXACTLY 11 chars
    "https://example.com/watch?v=dQw4w9WgXcQ",  # right shape, wrong domain
]


@pytest.mark.parametrize("raw", INVALID_IDS)
def test_video_id_invalid(raw: str) -> None:
    with pytest.raises(ValueError):
        yt.video_id(raw)


# ---------------------------------------------------------------------------
# transcript — monkeypatched API call
# ---------------------------------------------------------------------------

class _FakeSnippet:
    def __init__(self, text: str, start: float, duration: float) -> None:
        self.text = text
        self.start = start
        self.duration = duration


class _FakeFetched:
    def __init__(self, snippets) -> None:
        self.snippets = snippets
        self.is_generated = True

    def __iter__(self):
        return iter(self.snippets)


class _FakeTranscript:
    def fetch(self, preserve_formatting=False):
        return _FakeFetched([
            _FakeSnippet("Hello world.", 0.0, 2.0),
            _FakeSnippet("This is a test.", 2.0, 2.0),
            # gap > 2s — paragraph break
            _FakeSnippet("New paragraph.", 10.0, 2.0),
        ])

    @property
    def is_generated(self):
        return True


class _FakeTranscriptListOk:
    def find_manually_created_transcript(self, langs):
        from youtube_transcript_api import NoTranscriptFound
        raise NoTranscriptFound("dQw4w9WgXcQ", langs, None)

    def find_generated_transcript(self, langs):
        return _FakeTranscript()


def test_transcript_text_and_paragraphs(monkeypatch) -> None:
    """Transcript assembles plain text with paragraph breaks on >2s gaps."""

    class _FakeApi:
        def list(self, vid):
            return _FakeTranscriptListOk()

    import youtube_transcript_api as _yta
    monkeypatch.setattr(_yta, "YouTubeTranscriptApi", _FakeApi)

    result = yt.transcript("dQw4w9WgXcQ")
    assert "Hello world." in result
    assert "New paragraph." in result
    # paragraph break between snippets with >2s gap
    assert "\n\n" in result


def test_transcript_no_transcript_raises_yt_error(monkeypatch) -> None:
    """When all transcript sources fail, YtError is raised with a clean message."""
    from youtube_transcript_api import TranscriptsDisabled

    class _FakeApiDisabled:
        def list(self, vid):
            raise TranscriptsDisabled(vid)

    import youtube_transcript_api as _yta
    monkeypatch.setattr(_yta, "YouTubeTranscriptApi", _FakeApiDisabled)

    with pytest.raises(yt.YtError, match="captions disabled"):
        yt.transcript("dQw4w9WgXcQ")


def test_snippets_prefers_manual_over_generated(monkeypatch) -> None:
    """snippets() prefers manual subtitles when both manual and auto exist."""
    calls = []

    class _ManualTranscript:
        def fetch(self, preserve_formatting=False):
            calls.append("manual")
            return _FakeFetched([_FakeSnippet("Manual.", 0.0, 1.0)])

    class _AutoTranscript:
        def fetch(self, preserve_formatting=False):
            calls.append("auto")
            return _FakeFetched([_FakeSnippet("Auto.", 0.0, 1.0)])

    class _BothList:
        def find_manually_created_transcript(self, langs):
            return _ManualTranscript()

        def find_generated_transcript(self, langs):
            return _AutoTranscript()

    class _ApiWithBoth:
        def list(self, vid):
            return _BothList()

    import youtube_transcript_api as _yta
    monkeypatch.setattr(_yta, "YouTubeTranscriptApi", _ApiWithBoth)

    result = yt.snippets("dQw4w9WgXcQ")
    assert calls == ["manual"]
    assert result[0].text == "Manual."
