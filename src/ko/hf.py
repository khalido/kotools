"""Hugging Face paper pages (hf.co/papers). No auth needed for reads.

The AI-papers layer ko arxiv lacks: a community-curated daily feed (upvotes ≈
HN points for AI papers), hybrid semantic search, and per-paper links to code /
models / datasets. Same ids as arxiv, so results compose with `ko arxiv fetch`.

- `top` — Daily Papers feed, trending order
- `search` — hybrid semantic + full-text search
- `info` — structured metadata (upvotes, github + stars, AI summary, linked artifacts)
- `get` — paper as markdown (only for papers indexed on hf.co/papers)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

import httpx


HF = "https://huggingface.co"
DEFAULT_TOP_N = 10
DEFAULT_SEARCH_N = 10

_ID_RE = re.compile(r"\d{4}\.\d{4,5}(?:v\d+)?")


@dataclass
class Paper:
    id: str
    title: str
    upvotes: int
    published_at: datetime
    summary: str
    # metadata-only extras (info endpoint); empty for top/search hits
    ai_summary: str = ""
    github_repo: str = ""
    github_stars: int = 0
    project_page: str = ""
    linked_models: list[str] = field(default_factory=list)
    linked_datasets: list[str] = field(default_factory=list)
    linked_spaces: list[str] = field(default_factory=list)

    @property
    def hf_url(self) -> str:
        return f"https://huggingface.co/papers/{self.id}"


def paper_id(ref: str) -> str:
    """Extract the arxiv id from an id, arxiv URL, or hf.co/papers URL."""
    m = _ID_RE.search(ref)
    if not m:
        raise ValueError(f"no arxiv id found in {ref!r}")
    return m.group()


def _get(path: str, params: dict | None = None) -> httpx.Response:
    resp = httpx.get(f"{HF}{path}", params=params, timeout=30, follow_redirects=True)
    resp.raise_for_status()
    return resp


def _paper(d: dict) -> Paper:
    return Paper(
        id=str(d.get("id") or ""),
        title=(d.get("title") or "").replace("\n", " "),
        upvotes=d.get("upvotes") or 0,
        published_at=datetime.fromisoformat(d["publishedAt"].replace("Z", "+00:00")),
        summary=d.get("summary") or "",
        ai_summary=d.get("ai_summary") or "",
        github_repo=d.get("githubRepo") or "",
        github_stars=d.get("githubStars") or 0,
        project_page=d.get("projectPage") or "",
        linked_models=[m["id"] for m in d.get("linkedModels") or []],
        linked_datasets=[m["id"] for m in d.get("linkedDatasets") or []],
        linked_spaces=[m["id"] for m in d.get("linkedSpaces") or []],
    )


def top(n: int = DEFAULT_TOP_N, date: str | None = None) -> list[Paper]:
    """Daily Papers feed, trending order. `date` is YYYY-MM-DD (default: latest)."""
    params: dict = {"limit": n, "sort": "trending"}
    if date:
        params["date"] = date
    data = _get("/api/daily_papers", params).json()
    return [_paper(item["paper"]) for item in data]


def search(query: str, n: int = DEFAULT_SEARCH_N) -> list[Paper]:
    """Hybrid semantic + full-text search over title, authors, and content."""
    data = _get("/api/papers/search", {"q": query, "limit": n}).json()
    return [_paper(item["paper"]) for item in data]


def info(ref: str) -> Paper:
    """Structured metadata for one paper. Raises for papers not indexed on hf.co."""
    pid = paper_id(ref)
    try:
        return _paper(_get(f"/api/papers/{pid}").json())
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            raise RuntimeError(
                f"{pid} is not indexed on hf.co/papers — try `ko arxiv fetch {pid}`"
            ) from None
        raise


def get(ref: str) -> str:
    """Paper content as markdown (from the arxiv HTML version).

    For papers not indexed on hf.co/papers the endpoint silently serves the
    HTML page instead — we detect that and raise (use `ko arxiv fetch` there).
    """
    pid = paper_id(ref)
    try:
        text = _get(f"/papers/{pid}.md").text
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            raise RuntimeError(
                f"{pid} is not indexed on hf.co/papers — try `ko arxiv fetch {pid}`"
            ) from None
        raise
    # indexed papers without an arxiv HTML version fall back to the HF page HTML
    if text.lstrip()[:15].lower().startswith(("<!doctype", "<html")):
        raise RuntimeError(
            f"no markdown version for {pid} — try `ko arxiv fetch {pid}`"
        )
    return text
