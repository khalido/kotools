"""Offline tests for ko.gdocs pure helpers (no live auth needed)."""

from __future__ import annotations

import pytest

from ko import gdocs


def test_error_types():
    assert issubclass(gdocs.DocsNotFound, gdocs.DocsError)
    assert issubclass(gdocs.DocsPermissionDenied, gdocs.DocsError)


def test_doc_id_url_or_bare():
    url = "https://docs.google.com/document/d/1AbC_def-9/edit?tab=t.0"
    assert gdocs.doc_id(url) == "1AbC_def-9"
    assert gdocs.doc_id("  1AbC_def-9 ") == "1AbC_def-9"


def test_hex_to_rgb():
    assert gdocs._hex_to_rgb("#ffffff") == {"red": 1.0, "green": 1.0, "blue": 1.0}
    assert gdocs._hex_to_rgb("000000") == {"red": 0.0, "green": 0.0, "blue": 0.0}  # tolerates no '#'
    g = gdocs._hex_to_rgb("#D9D9D9")
    assert g["red"] == g["green"] == g["blue"] and 0.8 < g["red"] < 0.86
    for bad in ("#fff", "#gggggg", "nope"):
        with pytest.raises(gdocs.DocsError):
            gdocs._hex_to_rgb(bad)


def test_find_tables_walks_body():
    document = {
        "body": {
            "content": [
                {"startIndex": 1, "paragraph": {}},
                {"startIndex": 12, "table": {"rows": 4, "columns": 3}},
                {"startIndex": 90, "paragraph": {}},
                {"startIndex": 95, "table": {"rows": 2, "columns": 2}},
            ]
        }
    }
    assert gdocs._find_tables(document) == [
        {"start": 12, "rows": 4, "cols": 3},
        {"start": 95, "rows": 2, "cols": 2},
    ]
    assert gdocs._find_tables({"body": {"content": []}}) == []


# --- tabs -----------------------------------------------------------------


class _FakeExec:
    def __init__(self, result):
        self._r = result

    def execute(self, num_retries=0):
        return self._r


class _FakeDocuments:
    def __init__(self, get_result, cap):
        self._get_result, self._cap = get_result, cap

    def get(self, **kwargs):
        self._cap["get_kwargs"] = kwargs
        return _FakeExec(self._get_result)

    def batchUpdate(self, documentId, body):
        self._cap["batch"] = {"documentId": documentId, "body": body}
        return _FakeExec(self._cap.get("batch_result", {"replies": [{}]}))


class _FakeService:
    def __init__(self, get_result, cap, batch_result=None):
        if batch_result is not None:
            cap["batch_result"] = batch_result
        self._docs = _FakeDocuments(get_result, cap)

    def documents(self):
        return self._docs


def _patch_docs(monkeypatch, get_result, cap, batch_result=None):
    monkeypatch.setattr(
        gdocs, "get_docs_service", lambda readonly=True: _FakeService(get_result, cap, batch_result)
    )


def test_walk_tabs_nested():
    tabs = [
        {
            "tabProperties": {"tabId": "t.0", "title": "Alpha", "index": 0},
            "childTabs": [{"tabProperties": {"tabId": "t.1", "title": "Alpha sub", "index": 0}}],
        },
        {"tabProperties": {"tabId": "t.2", "title": "Beta", "index": 1, "nestingLevel": 0}},
    ]
    flat = gdocs._walk_tabs(tabs)
    assert [(t.tab_id, t.title, t.nesting_level) for t in flat] == [
        ("t.0", "Alpha", 0),
        ("t.1", "Alpha sub", 1),  # nesting from recursion depth (no nestingLevel in props)
        ("t.2", "Beta", 0),  # nesting from the API's nestingLevel
    ]


def test_list_tabs_and_no_tabs_fallback(monkeypatch):
    cap = {}
    _patch_docs(monkeypatch, {"tabs": [{"tabProperties": {"tabId": "t.0", "title": "One", "index": 0}}]}, cap)
    tabs = gdocs.list_tabs("docid")
    assert [(t.tab_id, t.title) for t in tabs] == [("t.0", "One")]
    assert cap["get_kwargs"]["includeTabsContent"] is True  # tabs need the flag

    _patch_docs(monkeypatch, {"title": "My Doc"}, {})  # no tabs field → single implicit tab
    implicit = gdocs.list_tabs("docid")
    assert len(implicit) == 1 and implicit[0].title == "My Doc" and implicit[0].tab_id == ""


def test_resolve_tab_id_by_title_id_and_misses():
    tabs = [
        gdocs.TabInfo(0, "Acme", "t.0", 0),
        gdocs.TabInfo(1, "Globex", "t.9", 0),
    ]
    assert gdocs._resolve_tab_id(tabs, "t.9") == "t.9"  # raw id passes through
    assert gdocs._resolve_tab_id(tabs, "acme") == "t.0"  # case-insensitive title
    with pytest.raises(gdocs.DocsNotFound):
        gdocs._resolve_tab_id(tabs, "Nope")
    dupes = [gdocs.TabInfo(0, "Same", "t.0", 0), gdocs.TabInfo(1, "same", "t.1", 0)]
    with pytest.raises(gdocs.DocsError):
        gdocs._resolve_tab_id(dupes, "same")  # ambiguous title


def test_replace_text_tabs_criteria(monkeypatch):
    cap = {}
    _patch_docs(monkeypatch, {}, cap, batch_result={"replies": [{"replaceAllText": {"occurrencesChanged": 3}}]})
    n = gdocs.replace_text("docid", "Q2", "Q3", tab_ids=["t.5"])
    assert n == 3
    req = cap["batch"]["body"]["requests"][0]["replaceAllText"]
    assert req["tabsCriteria"] == {"tabIds": ["t.5"]}
    assert req["containsText"] == {"text": "Q2", "matchCase": False}

    cap2 = {}
    _patch_docs(monkeypatch, {}, cap2, batch_result={"replies": [{"replaceAllText": {"occurrencesChanged": 0}}]})
    gdocs.replace_text("docid", "a", "b")  # whole-doc: no tabsCriteria
    assert "tabsCriteria" not in cap2["batch"]["body"]["requests"][0]["replaceAllText"]


def test_append_text_tab_end_index(monkeypatch):
    cap = {}
    doc = {
        "tabs": [
            {
                "tabProperties": {"tabId": "t.0", "title": "Tab 1", "index": 0},
                "documentTab": {"body": {"content": [{"endIndex": 1}, {"endIndex": 42}]}},
            }
        ]
    }
    _patch_docs(monkeypatch, doc, cap)
    gdocs.append_text("docid", "hi", tab="Tab 1")
    ins = cap["batch"]["body"]["requests"][0]["insertText"]
    assert ins["text"] == "hi"
    assert ins["location"] == {"index": 41, "tabId": "t.0"}  # end 42 - 1, scoped to the tab

    cap2 = {}
    _patch_docs(monkeypatch, {"body": {"content": [{"endIndex": 10}]}}, cap2)
    gdocs.append_text("docid", "x")  # whole-doc: index 9, no tabId
    assert cap2["batch"]["body"]["requests"][0]["insertText"]["location"] == {"index": 9}


def test_find_orphan_comments():
    comments = [
        {"author": "Ann", "quoted_text": "the Q2 revenue line"},  # contains the find text
        {"author": "Bo", "quoted_text": "Q2"},  # contained in the find text
        {"author": "Cy", "quoted_text": "unrelated"},
        {"author": "Di", "quoted_text": ""},  # no anchor
    ]
    hits = gdocs.find_orphan_comments(comments, "Q2 revenue")
    assert [c["author"] for c in hits] == ["Ann", "Bo"]
    assert gdocs.find_orphan_comments(comments, "") == []  # empty find never orphans
    assert gdocs.find_orphan_comments([], "anything") == []


def test_replace_multi_tab_refusal_vs_single_pass(monkeypatch):
    """A bare (no --tab) replace on a MULTI-TAB doc refuses (exit 2) — it would rewrite every tab.
    `--all-tabs` proceeds whole-doc; a single-tab doc keeps working bare, no friction."""
    from typer.testing import CliRunner

    from ko import cli_google
    from ko.cli import app

    calls: list = []
    monkeypatch.setattr(
        cli_google.gdocs_mod, "replace_text", lambda *a, **k: (calls.append((a, k)), 2)[1]
    )
    multi = [gdocs.TabInfo(0, "Acme", "t.0", 0), gdocs.TabInfo(1, "Globex", "t.1", 0)]
    monkeypatch.setattr(cli_google.gdocs_mod, "list_tabs", lambda doc: multi)
    runner = CliRunner()

    # multi-tab, bare → refuse, replace_text never called
    r = runner.invoke(app, ["gdocs", "replace", "docid", "Q2", "Q3"])
    assert r.exit_code == 2 and "2 tabs" in r.output and not calls

    # --all-tabs → whole-doc replace (no tabsCriteria)
    r2 = runner.invoke(app, ["gdocs", "replace", "docid", "Q2", "Q3", "--all-tabs"])
    assert r2.exit_code == 0 and len(calls) == 1 and "tab_ids" not in calls[0][1]

    # single-tab doc → bare replace works with no friction
    calls.clear()
    monkeypatch.setattr(cli_google.gdocs_mod, "list_tabs", lambda doc: [gdocs.TabInfo(0, "Doc", "", 0)])
    r3 = runner.invoke(app, ["gdocs", "replace", "docid", "Q2", "Q3"])
    assert r3.exit_code == 0 and len(calls) == 1


def test_append_multi_tab_refusal_vs_single_pass(monkeypatch):
    """Bare append on a multi-tab doc refuses (it would silently land in tab 0) — require --tab.
    Single-tab docs append bare."""
    from typer.testing import CliRunner

    from ko import cli_google
    from ko.cli import app

    appended: list = []
    monkeypatch.setattr(
        cli_google.gdocs_mod, "append_text", lambda *a, **k: (appended.append((a, k)), 3)[1]
    )
    multi = [gdocs.TabInfo(0, "Acme", "t.0", 0), gdocs.TabInfo(1, "Globex", "t.1", 0)]
    monkeypatch.setattr(cli_google.gdocs_mod, "list_tabs", lambda doc: multi)
    runner = CliRunner()

    r = runner.invoke(app, ["gdocs", "append", "docid", "hello"])
    assert r.exit_code == 2 and "2 tabs" in r.output and not appended

    r2 = runner.invoke(app, ["gdocs", "append", "docid", "hello", "--tab", "Acme"])
    assert r2.exit_code == 0 and len(appended) == 1

    appended.clear()
    monkeypatch.setattr(cli_google.gdocs_mod, "list_tabs", lambda doc: [gdocs.TabInfo(0, "Doc", "", 0)])
    r3 = runner.invoke(app, ["gdocs", "append", "docid", "hi"])
    assert r3.exit_code == 0 and len(appended) == 1


def test_replace_tab_orphan_guard_cli(monkeypatch):
    """The tab-scoped replace refuses when a comment anchor would be orphaned, unless --force."""
    from typer.testing import CliRunner

    from ko import cli_google
    from ko.cli import app

    monkeypatch.setattr(cli_google.gdocs_mod, "resolve_tab", lambda doc, tab: "t.0")
    monkeypatch.setattr(
        cli_google.gdrive_mod, "list_comments", lambda doc: [{"author": "Ann", "quoted_text": "the Q2 line"}]
    )
    calls = []
    monkeypatch.setattr(
        cli_google.gdocs_mod,
        "replace_text",
        lambda *a, **k: (calls.append((a, k)), 1)[1],
    )
    runner = CliRunner()

    # hit → refuse, replace_text never called
    r = runner.invoke(app, ["gdocs", "replace", "docid", "Q2", "Q3", "--tab", "Acme"])
    assert r.exit_code == 1 and not calls and "orphan" in r.output.lower()

    # --force → proceeds
    r2 = runner.invoke(app, ["gdocs", "replace", "docid", "Q2", "Q3", "--tab", "Acme", "--force"])
    assert r2.exit_code == 0 and len(calls) == 1
    assert calls[0][1]["tab_ids"] == ["t.0"]

    # no comments → proceeds without --force
    calls.clear()
    monkeypatch.setattr(cli_google.gdrive_mod, "list_comments", lambda doc: [])
    r3 = runner.invoke(app, ["gdocs", "replace", "docid", "Q2", "Q3", "--tab", "Acme"])
    assert r3.exit_code == 0 and len(calls) == 1
