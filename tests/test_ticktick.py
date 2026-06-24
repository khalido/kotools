"""Offline tests for ko.ticktick — resolve logic + missing-key guard. No MCP calls."""

import pytest

from ko import ticktick
from ko.ticktick import Project


def test_resolve_list_matches():
    projects = [Project(id="1", name="Shopping"), Project(id="2", name="Work Tasks")]
    assert ticktick.resolve_list("1", projects).id == "1"  # exact id
    assert ticktick.resolve_list("shopping", projects).id == "1"  # case-insensitive name
    assert ticktick.resolve_list("work", projects).id == "2"  # unique substring
    assert ticktick.resolve_list("nope", projects) is None


def test_missing_key_raises(monkeypatch):
    monkeypatch.delenv("TICKTICK_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="TICKTICK_API_KEY"):
        ticktick.list_projects()
