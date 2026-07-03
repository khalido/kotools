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
from pathlib import Path
from datetime import datetime, timedelta, timezone

import arxiv


DEFAULT_SINCE_MONTHS = 18
DEFAULT_MAX_RESULTS = 20


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
    since_months: int = DEFAULT_SINCE_MONTHS,
    max_results: int = DEFAULT_MAX_RESULTS,
) -> list[SearchResult]:
    """Search arxiv, newest first, stop when results get older than the cutoff.

    Defaults bias toward recent work — most of our searches are about what's
    happening right now. Override since_months for a wider scan.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=30 * since_months)
    client = arxiv.Client()
    search_query = arxiv.Search(
        query=query,
        max_results=max_results * 3,  # over-fetch; filter by date client-side
        sort_by=arxiv.SortCriterion.SubmittedDate,
        sort_order=arxiv.SortOrder.Descending,
    )

    out: list[SearchResult] = []
    for r in client.results(search_query):
        if r.published < cutoff:
            break  # sorted descending, so everything after is older too
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
    )
    if result.returncode != 0:  # surface arxiv2md's own message, not a bare CalledProcessError
        msg = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"arxiv2md failed for {arxiv_id}: {msg}".rstrip(": "))
    return result.stdout
