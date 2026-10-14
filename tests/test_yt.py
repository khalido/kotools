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


# --- metadata (YouTube Data API) ---


@pytest.mark.parametrize(
    ("iso", "expected"),
    [("PT3M34S", "0:03:34"), ("PT1H2M3S", "1:02:03"), ("PT45S", "0:00:45"), ("P1DT2H", "26:00:00"), ("P0D", "0:00:00")],
)
def test_duration(iso: str, expected: str) -> None:
    assert yt._duration(iso) == expected


def test_duration_rejects_unknown_format() -> None:
    with pytest.raises(yt.YtError):
        yt._duration("3 minutes")


@pytest.mark.parametrize(
    ("ref", "query"),
    [
        ("@mkbhd", {"forHandle": "@mkbhd"}),
        ("mkbhd", {"forHandle": "@mkbhd"}),
        ("https://www.youtube.com/@mkbhd/videos", {"forHandle": "@mkbhd"}),
        ("UCBJycsmduvYEL83R_U4JriQ", {"id": "UCBJycsmduvYEL83R_U4JriQ"}),
        ("https://www.youtube.com/channel/UCBJycsmduvYEL83R_U4JriQ", {"id": "UCBJycsmduvYEL83R_U4JriQ"}),
        ("https://www.youtube.com/user/marquesbrownlee", {"forUsername": "marquesbrownlee"}),
    ],
)
def test_channel_lookup(ref: str, query: dict) -> None:
    assert yt._channel_lookup(ref) == query


def test_channel_lookup_refuses_custom_url() -> None:
    with pytest.raises(yt.YtError, match="@handle"):
        yt._channel_lookup("https://www.youtube.com/c/Something")


def test_channel_skips_private_and_keeps_newest_first(monkeypatch) -> None:
    def item(vid, published):
        cd = {"videoId": vid} | ({"videoPublishedAt": published} if published else {})
        return {"snippet": {"title": vid, "description": ""}, "contentDetails": cd}

    pages = [
        {"items": [{"id": "UC1", "snippet": {"title": "Chan"}, "contentDetails": {"relatedPlaylists": {"uploads": "UU1"}}}]},
        {"items": [item("old", "2026-01-01T00:00:00Z"), item("gone", None), item("new", "2026-02-01T00:00:00Z")]},
        {"items": [{"id": "new", "contentDetails": {"duration": "PT1M"}}, {"id": "old", "contentDetails": {"duration": "PT2M"}}]},
    ]
    monkeypatch.setattr(yt, "_api", lambda: _Fake())
    monkeypatch.setattr(yt, "_call", lambda request, context: pages.pop(0))
    monkeypatch.setattr(yt, "_is_short", lambda vid: False)
    ch = yt.channel("@chan", last=5)
    assert [(u.id, u.duration) for u in ch.uploads] == [("new", "0:01:00"), ("old", "0:02:00")]
    assert ch.hidden == 1


class _Fake:
    """Any attribute chain returns itself, so `api.channels().list(...)` builds a dummy request."""

    def __getattr__(self, name):
        return lambda *a, **k: self


def test_chapters_follow_youtube_rule() -> None:
    desc = "Review of the car.\n\n0:00 Intro\n1:30 - Range\n(12:05) Charging\n1:02:03 Verdict\nThanks!"
    got = yt.chapters(desc)
    assert [(c.start, c.seconds, c.title) for c in got] == [
        ("0:00", 0, "Intro"), ("1:30", 90, "Range"), ("12:05", 725, "Charging"), ("1:02:03", 3723, "Verdict"),
    ]
    assert yt.chapters("1:00 a\n2:00 b\n3:00 c") == []  # must start at 0:00
    assert yt.chapters("0:00 a\n2:00 b") == []  # fewer than three


def test_utc_reads_dates_and_refuses_naive_times() -> None:
    assert yt._utc("2026-10-01").isoformat() == "2026-10-01T00:00:00+00:00"
    assert yt._utc("2026-10-01T09:00:00Z").hour == 9
    assert yt._utc("2026-10-01T19:00:00+10:00").hour == 9
    with pytest.raises(ValueError, match="time zone"):
        yt._utc("2026-10-01T09:00:00")
    with pytest.raises(ValueError, match="ISO"):
        yt._utc("last week")


def test_channel_since_drops_older(monkeypatch) -> None:
    def item(vid, published):
        return {"snippet": {"title": vid, "description": ""}, "contentDetails": {"videoId": vid, "videoPublishedAt": published}}

    pages = [
        {"items": [{"id": "UC1", "snippet": {"title": "Chan"}, "contentDetails": {"relatedPlaylists": {"uploads": "UU1"}}}]},
        {"items": [item("new", "2026-10-05T00:00:00Z"), item("old", "2026-09-01T00:00:00Z")], "nextPageToken": "more"},
        {"items": [{"id": "new", "contentDetails": {"duration": "PT1M"}}]},
    ]
    monkeypatch.setattr(yt, "_api", lambda: _Fake())
    monkeypatch.setattr(yt, "_call", lambda request, context: pages.pop(0))
    monkeypatch.setattr(yt, "_is_short", lambda vid: False)
    ch = yt.channel("@chan", last=10, since="2026-10-01")
    assert [u.id for u in ch.uploads] == ["new"]
    assert pages == []  # stopped paging once a page reached the cutoff


def test_transcript_status_excerpt_and_codes(monkeypatch) -> None:
    long = " ".join(f"w{i}" for i in range(1000))
    monkeypatch.setattr(yt, "transcript", lambda vid, refresh=False: long)
    st = yt.transcript_status("abcdefghijk")
    assert st["available"] and st["words"] == 1000
    start, mid, end = st["excerpt"].split(" … ")
    assert start.split()[0] == "w0" and end.split()[-1] == "w999" and "w500" in mid
    assert yt.transcript_status("abcdefghijk", full=True)["text"] == long

    def blocked(vid, refresh=False):
        raise yt.YtError("no transcript available (ip blocked)", "ip_blocked")

    monkeypatch.setattr(yt, "transcript", blocked)
    assert yt.transcript_status("abcdefghijk") == {"available": False, "code": "ip_blocked", "reason": "no transcript available (ip blocked)"}


def test_transcript_is_cached(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(yt, "_fetch_snippets", lambda vid, langs: calls.append(vid) or [yt.Snippet(0.0, "hi", 1.0)])
    assert yt.transcript("abcdefghijk") == "hi"
    assert yt.transcript("abcdefghijk") == "hi"
    assert calls == ["abcdefghijk"]
    yt.snippets("abcdefghijk", refresh=True)
    assert len(calls) == 2


def test_condense_caches_by_video_model_and_prompt(monkeypatch) -> None:
    from ko import llm

    runs = []

    def fake_run(prompt, *, model, system, attachments):
        runs.append((model, attachments[0].url))
        llm.last_cost = llm.RunCost("google/gemini-3.5-flash", 100, 20, 0.01, "actual")
        return "write-up"

    monkeypatch.setattr(llm, "run", fake_run)
    a = yt.condense("https://youtu.be/abcdefghijk")
    b = yt.condense("abcdefghijk")
    assert (a.text, a.cached, b.cached) == ("write-up", False, True)
    assert runs == [("openrouter:google/gemini-3.5-flash", "https://www.youtube.com/watch?v=abcdefghijk")]
    yt.condense("abcdefghijk", model="openrouter:other/model")  # another model is another entry
    assert len(runs) == 2


def test_infos_batches_and_reports_missing(monkeypatch) -> None:
    def vid(i):
        return {"id": i, "snippet": {"title": i, "channelTitle": "c", "channelId": "UC", "publishedAt": "2026-01-01T00:00:00Z"},
                "contentDetails": {"duration": "PT1M"}}

    seen = []

    def call(request, context):
        seen.append(context)
        return {"items": [vid("aaaaaaaaaaa")]}

    monkeypatch.setattr(yt, "_api", lambda: _Fake())
    monkeypatch.setattr(yt, "_call", call)
    found, missing = yt.infos(["aaaaaaaaaaa", "https://youtu.be/bbbbbbbbbbb", "aaaaaaaaaaa"])
    assert [v.id for v in found] == ["aaaaaaaaaaa"] and missing == ["bbbbbbbbbbb"]
    assert len(seen) == 1


def test_condense_failure_says_what_it_cost(monkeypatch) -> None:
    from ko import llm

    def cut_short(prompt, *, model, system, attachments):
        llm.last_cost = llm.RunCost("google/gemini-3.5-flash", 168000, 9000, 0.31, "actual")
        raise llm.TruncatedOutput("hit the output-token limit")

    monkeypatch.setattr(llm, "run", cut_short)
    with pytest.raises(yt.YtError, match=r"\$0\.31"):
        yt.condense("abcdefghijk")
    assert not yt.is_condensed("abcdefghijk")


def test_info_refuses_a_big_paid_batch(monkeypatch) -> None:
    from typer.testing import CliRunner

    from ko.cli import app

    monkeypatch.setattr(yt, "infos", lambda urls: pytest.fail("looked up before refusing"))
    ids = [f"{i:011d}" for i in range(6)]
    r = CliRunner().invoke(app, ["yt", "info", *ids, "-s"])
    assert r.exit_code == 2 and "6 new write-ups" in r.output


def test_channel_leaves_out_shorts_and_counts_after(monkeypatch) -> None:
    def item(vid, published):
        return {"snippet": {"title": vid, "description": ""}, "contentDetails": {"videoId": vid, "videoPublishedAt": published}}

    def dur(vid, iso):
        return {"id": vid, "contentDetails": {"duration": iso}}

    pages = [
        {"items": [{"id": "UC1", "snippet": {"title": "Chan"}, "contentDetails": {"relatedPlaylists": {"uploads": "UU1"}}}]},
        {"items": [item("s1", "2026-10-05T00:00:00Z"), item("long", "2026-10-04T00:00:00Z"),
                   item("clip", "2026-10-03T00:00:00Z"), item("long2", "2026-10-02T00:00:00Z")]},
        {"items": [dur("s1", "PT45S"), dur("long", "PT20M"), dur("clip", "PT2M30S"), dur("long2", "PT12M")]},
    ]
    checked = []
    monkeypatch.setattr(yt, "_api", lambda: _Fake())
    monkeypatch.setattr(yt, "_call", lambda request, context: pages.pop(0))
    monkeypatch.setattr(yt, "_is_short", lambda vid: checked.append(vid) or vid == "s1")
    ch = yt.channel("@chan", last=2)
    assert [u.id for u in ch.uploads] == ["long", "clip"]  # -n counts after the Shorts are gone
    assert ch.shorts_skipped == 1 and ch.more
    assert checked == ["s1", "clip"]  # only uploads of 3 minutes or less get the check
