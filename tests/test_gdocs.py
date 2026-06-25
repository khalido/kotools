"""Offline tests for ko.gdocs pure helpers (no live auth needed)."""

from __future__ import annotations

from ko import gdocs


def test_error_types():
    assert issubclass(gdocs.DocsNotFound, gdocs.DocsError)
    assert issubclass(gdocs.DocsPermissionDenied, gdocs.DocsError)


def test_doc_id_url_or_bare():
    url = "https://docs.google.com/document/d/1AbC_def-9/edit?tab=t.0"
    assert gdocs.doc_id(url) == "1AbC_def-9"
    assert gdocs.doc_id("  1AbC_def-9 ") == "1AbC_def-9"


def test_para_text_plain_and_markdown():
    heading = {
        "paragraphStyle": {"namedStyleType": "HEADING_2"},
        "elements": [{"textRun": {"content": "Title\n"}}],
    }
    assert gdocs._para_text(heading, markdown=False) == "Title\n"
    assert gdocs._para_text(heading, markdown=True) == "## Title\n"

    bullet = {"bullet": {}, "elements": [{"textRun": {"content": "item\n"}}]}
    assert gdocs._para_text(bullet, markdown=True) == "- item\n"

    normal = {"elements": [{"textRun": {"content": "hi\n"}}]}
    assert gdocs._para_text(normal, markdown=True) == "hi\n"
    assert gdocs._para_text(normal, markdown=False) == "hi\n"
