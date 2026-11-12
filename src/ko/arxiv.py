"""arxiv search + fetch.

Two things we do a lot: find recent papers on a topic, and pull a specific
paper into markdown so we can read or feed it to an agent. Thin wrapper over
the `arxiv` library (search) and `arxiv2md` (paper → markdown).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

DEFAULT_MAX_RESULTS = 20
DEFAULT_RECENT_SINCE_MONTHS = 18  # date window applied only in --recent (newest-first) mode


@dataclass
class SearchResult:
    id: str
    title: str
    authors: list[str]
    published: datetime
    summary: str
    pdf_url: str

    @property
    def short_id(self) -> str:
        # arxiv entry_id is like http://arxiv.org/abs/2501.11120v1 → 2501.11120v1
        return self.id.rsplit("/", 1)[-1]


def search(
    query: str,
    max_results: int = DEFAULT_MAX_RESULTS,
    recent: bool = False,
    since_months: int | None = None,
) -> list[SearchResult]:
    """Search arxiv.

    **Relevance-ranked by default** — a topical query ("language model agents")
    returns the best-matching papers regardless of age. `recent=True` sorts
    newest-first instead, for browsing what's just dropped in an area (pair it with
    a category query like `cat:cs.LG`). `since_months` restricts to the last N months
    — a hard filter in relevance mode, an early-stop in recent mode (default 18 there).

    (Note: date-sort was the old default and it effectively ignored the query — arxiv's
    API returns newest-overall when sorted by date, so topical searches came back junk.)
    """
    import arxiv  # the library loads on use: ~20 ms, and the CLI reads DEFAULT_MAX_RESULTS at startup

    if recent and since_months is None:
        since_months = DEFAULT_RECENT_SINCE_MONTHS
    cutoff = (
        datetime.now(UTC) - timedelta(days=30 * since_months)
        if since_months
        else None
    )
    sort = arxiv.SortCriterion.SubmittedDate if recent else arxiv.SortCriterion.Relevance
    search_query = arxiv.Search(
        query=query,
        max_results=max_results * 3 if cutoff else max_results,  # over-fetch when date-filtering
        sort_by=sort,
        sort_order=arxiv.SortOrder.Descending,
    )

    out: list[SearchResult] = []
    for r in client_results(search_query):
        if cutoff and r.published < cutoff:
            if recent:
                break  # date-sorted → everything after is older too
            continue  # relevance-sorted → keep scanning for in-window matches
        out.append(
            SearchResult(
                id=r.entry_id,
                title=r.title.strip().replace("\n", " "),
                authors=[a.name for a in r.authors],
                published=r.published,
                summary=r.summary.strip().replace("\n", " "),
                pdf_url=r.pdf_url,
            )
        )
        if len(out) >= max_results:
            break
    return out


def client_results(search_query):
    """Thin seam over arxiv.Client().results — one place to mock in tests / tune retries."""
    import arxiv

    return arxiv.Client().results(search_query)


def _arxiv2md_bin() -> str:
    """Locate the arxiv2md script: same venv as us first (uv tool installs
    don't put dependency scripts on PATH), then PATH as fallback."""
    sibling = Path(sys.executable).parent / "arxiv2md"
    if sibling.exists():
        return str(sibling)
    found = shutil.which("arxiv2md")
    if found:
        return found
    raise RuntimeError(
        "arxiv2md not found (should ship with the arxiv2markdown dependency)"
    )


def fetch(arxiv_id: str) -> str:
    """Fetch a paper as markdown via the `arxiv2md` CLI. Returns the markdown."""
    result = subprocess.run(
        [_arxiv2md_bin(), arxiv_id, "--remove-refs", "--frontmatter", "-o", "-"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:  # surface arxiv2md's own message, not a bare CalledProcessError
        msg = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"arxiv2md failed for {arxiv_id}: {msg}".rstrip(": "))
    return _fix_authors(result.stdout, arxiv_id)


def _fix_authors(md: str, arxiv_id: str) -> str:
    """arxiv2md reads authors off the rendered page, where affiliations and labels sit between
    the names ("Ashish Vaswani", "Affiliation:", "Google Brain", ...). The arxiv API has the
    clean list; if it can't be reached, arxiv2md's line stays."""
    import json
    import re

    import arxiv

    try:
        paper = next(client_results(arxiv.Search(id_list=[arxiv_id])))
    except Exception:
        return md
    line = "authors: " + json.dumps([a.name for a in paper.authors], ensure_ascii=False)
    return re.sub(r"(?m)^authors: .*$", lambda _: line, md, count=1)
