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
