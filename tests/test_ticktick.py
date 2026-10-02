"""Offline tests for ko.ticktick — resolve/match/date logic + the REST request shapes.

No network: `httpx.request` and the MCP call are monkeypatched.
"""

import json

import httpx2 as httpx
import pytest

from ko import ticktick
from ko.ticktick import Project, Task


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("TICKTICK_API_KEY", "test-token")
    monkeypatch.setattr(ticktick, "tz_name", lambda: "Australia/Sydney")


class _Resp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.content = b"x" if payload is not None else b""
        self.text = json.dumps(payload) if payload is not None else ""

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=None, response=self)

    def json(self):
        return self._payload


def _fake_http(monkeypatch, payload, status=200):
    """Patch httpx.request; return a list recording (method, url, body, headers) per call."""
    calls = []

    def fake(method, url, json=None, headers=None, timeout=None):
        calls.append((method, url, json, headers))
        return _Resp(payload, status)

    monkeypatch.setattr(ticktick.httpx, "request", fake)
    return calls


# --- resolving ---


def test_resolve_list_matches():
    projects = [Project(id="1", name="Shopping"), Project(id="2", name="Work Tasks")]
    assert ticktick.resolve_list("1", projects).id == "1"  # exact id
    assert ticktick.resolve_list("shopping", projects).id == "1"  # case-insensitive name
    assert ticktick.resolve_list("work", projects).id == "2"  # unique substring
    assert ticktick.resolve_list("nope", projects) is None


def test_resolve_list_ignores_the_glued_on_emoji():
    """TickTick glues the list's icon onto its name — '🛒Shopping' must match 'Shopping'."""
    projects = [Project(id="1", name="🛒Shopping"), Project(id="2", name="🗒Notes")]
    assert ticktick.resolve_list("Shopping", projects).id == "1"  # exact, emoji aside
    assert ticktick.resolve_list("notes", projects).id == "2"
    assert ticktick.resolve_list("🛒Shopping", projects).id == "1"  # pasted back verbatim


def test_find_tasks_id_wins_then_substring():
    tasks = [
        Task(id="abc", title="buy onions", priority="—", due=None, tags=[]),
        Task(id="def", title="Onion soup recipe", priority="—", due=None, tags=[]),
    ]
    assert [t.id for t in ticktick.find_tasks("abc", tasks)] == ["abc"]  # exact id, alone
    assert [t.id for t in ticktick.find_tasks("onion", tasks)] == ["abc", "def"]
    assert [t.id for t in ticktick.find_tasks("soup", tasks)] == ["def"]
    assert ticktick.find_tasks("zzz", tasks) == []


# --- dates ---


def test_parse_due_shapes():
    all_day, when = ticktick.parse_due("2026-07-29")
    assert all_day and when == "2026-07-29T00:00:00+1000"  # TickTick's offset has no colon
    timed, when = ticktick.parse_due("2026-07-29T18:30")
    assert not timed and when == "2026-07-29T18:30:00+1000"
    assert ticktick.parse_due("today")[0] is True
    assert ticktick.parse_due("tomorrow")[0] is True
    with pytest.raises(ValueError):
        ticktick.parse_due("next tuesday")


def test_fmt_due_renders_in_the_local_zone():
    # UTC 14:00 on the 28th IS the 29th in Sydney — the raw value shows the wrong day.
    assert ticktick._fmt_due("2026-07-28T14:00:00.000+0000", True) == "2026-07-29"
    assert ticktick._fmt_due("2026-07-29T08:30:00.000+0000", False) == "2026-07-29 18:30"
    assert ticktick._fmt_due(None, False) is None


# --- reads ---


def test_list_projects_prepends_the_virtual_inbox(monkeypatch):
    _fake_http(
        monkeypatch,
        [{"id": "p1", "name": "🛒Shopping"}, {"id": "p2", "name": "old", "closed": True}],
    )
    projects = ticktick.list_projects()
    assert [(p.id, p.name) for p in projects] == [("inbox", "Inbox"), ("p1", "🛒Shopping")]


def test_open_tasks_filters_on_status_zero(monkeypatch):
    calls = _fake_http(monkeypatch, [{"id": "t1", "title": "x", "projectId": "p1", "priority": 5}])
    tasks = ticktick.open_tasks(["p1"])
    assert calls[0][:2] == ("POST", f"{ticktick.API}/task/filter")
    assert calls[0][2] == {"status": [0], "projectIds": ["p1"]}
    assert tasks[0].priority == "high" and tasks[0].project_id == "p1"


# --- writes ---


def test_create_task_body(monkeypatch):
    calls = _fake_http(monkeypatch, {"id": "new", "title": "milk", "projectId": "p1"})
    ticktick.create_task(
        "milk", projectId="p1", content="# note", due="2026-07-29", priority=3, tags=["food"]
    )
    method, url, body, headers = calls[0]
    assert (method, url) == ("POST", f"{ticktick.API}/task")
    assert headers["Authorization"] == "Bearer test-token"
    assert body == {
        "title": "milk",
        "priority": 3,
        "projectId": "p1",
        "content": "# note",
        "tags": ["food"],
        "isAllDay": True,
        "dueDate": "2026-07-29T00:00:00+1000",
        "timeZone": "Australia/Sydney",
    }


def test_create_task_defaults_to_the_inbox(monkeypatch):
    """No project (or the virtual 'inbox' id) → omit projectId, which TickTick reads as Inbox."""
    calls = _fake_http(monkeypatch, {"id": "new", "title": "t"})
    ticktick.create_task("t")
    ticktick.create_task("t", projectId="inbox")
    assert "projectId" not in calls[0][2] and "projectId" not in calls[1][2]


def test_complete_task_hits_the_complete_route(monkeypatch):
    calls = _fake_http(monkeypatch, None)
    ticktick.complete_task("p1", "t1")
    assert calls[0][:2] == ("POST", f"{ticktick.API}/project/p1/task/t1/complete")


def test_api_error_is_one_clean_line(monkeypatch):
    _fake_http(monkeypatch, {"msg": "nope"}, status=401)
    with pytest.raises(RuntimeError, match="TickTick API 401.*TICKTICK_API_KEY"):
        ticktick.list_projects()


def test_missing_key_raises(monkeypatch):
    monkeypatch.delenv("TICKTICK_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="TICKTICK_API_KEY"):
        ticktick.list_projects()


# --- search (the one MCP call) ---


def test_search_maps_hits_to_list_names(monkeypatch):
    async def fake_call(tool, args):
        assert (tool, args) == ("search_task", {"query": "onions"})
        web = "https://ticktick.com/webapp"
        return {
            "results": [
                {"id": "t1", "title": "Onions", "url": f"{web}/#p/p1/tasks/t1"},
                {"id": "t2", "title": "Onion", "url": f"{web}/#p/inbox9/tasks/t2"},
            ]
        }

    monkeypatch.setattr(ticktick, "_mcp_call", fake_call)
    monkeypatch.setattr(ticktick, "list_projects", lambda: [Project(id="p1", name="🛒Shopping")])
    shopping, inbox = ticktick.search("onions")
    assert (shopping.project, shopping.title) == ("🛒Shopping", "Onions")
    assert inbox.project == "Inbox"  # an inbox<uid> project id isn't in GET /project
