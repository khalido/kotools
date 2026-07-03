"""Cross-publisher paper search + citation graph. OpenAlex backbone, S2 enrichment.

What `ko arxiv`/`ko hf` can't do: DOI-only papers (Wiley/MDPI/ACS/IOP), open-access
URL resolution, and citation-graph queries ("who cites X?"). OpenAlex covers all of
that keyless (~10k req/day; `mailto` joins the polite pool), and its
`open_access.oa_url` IS Unpaywall's data — no separate Unpaywall client. Semantic
Scholar is enrichment only (`tldr`, `similar`): without a free S2_API_KEY it 429s
after ~2 calls (verified 2026-07-03), so it's opt-in and degrades gracefully.

- `search` — relevance-ranked article search
- `get` — one work: metadata, reconstructed abstract, S2 tldr, OA url
- `cites` — works citing a paper, most-cited first
- `refs` — a paper's references, batch-resolved
- `similar` — S2 recommendations (needs S2_API_KEY)
- `oa_url` — best open-access URL for a DOI/arxiv id (used by fetch's DOI routing)

Ids: every function takes a DOI (bare, `doi:`, or doi.org URL), an arxiv id/URL,
or an OpenAlex W-id, interchangeably.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

import httpx


OPENALEX = "https://api.openalex.org"
S2 = "https://api.semanticscholar.org"
MAILTO = "ko@khalido.dev"  # OpenAlex polite pool — better rate limits, no key
DEFAULT_N = 10
DEFAULT_SIMILAR_N = 5

_W_RE = re.compile(r"(?:^|openalex\.org/)(W\d+)$", re.I)
# [^\s?#]+ stops before a query string/fragment so a trailing ?utm=… doesn't 404 the lookup.
_DOI_RE = re.compile(r"(?:doi\.org/|doi:|^)(10\.\d{4,}/[^\s?#]+)", re.I)
_ARXIV_RE = re.compile(r"(?:arxiv\.org/(?:abs|pdf|html)/|^)(\d{4}\.\d{4,5})(?:v\d+)?", re.I)

# list endpoints: skip the heavy fields (abstract index, references)
_LIST_SELECT = (
    "id,doi,title,publication_year,cited_by_count,"
    "open_access,primary_location,authorships"
)


@dataclass
class Work:
    openalex_id: str  # bare W-id
    doi: str  # bare 10.xxxx/… (no https://doi.org/ prefix)
    title: str
    year: int
    cited_by_count: int
    oa_status: str  # gold | green | hybrid | bronze | closed ('' for S2 results)
    oa_url: str  # best open-access copy (Unpaywall data) — '' if paywalled
    journal: str
    authors: list[str] = field(default_factory=list)
    abstract: str = ""  # reconstructed from abstract_inverted_index (get only)
    tldr: str = ""  # S2 one-liner (get only, needs S2_API_KEY)
    referenced_works: list[str] = field(default_factory=list)  # bare W-ids (get only)
    # direct OA PDF links from every location, best first (get only — list endpoints
    # omit `locations`). oa_url is one *landing page*; these are the actual PDFs, which
    # dodge the bot-blocks that stop landing-page extraction cold (e.g. MDPI).
    oa_pdf_urls: list[str] = field(default_factory=list)

    @property
    def full_text_urls(self) -> list[str]:
        """Ordered fetch candidates for the full text: direct PDFs, then the landing page."""
        urls = list(self.oa_pdf_urls)
        if self.oa_url and self.oa_url not in urls:
            urls.append(self.oa_url)
        return urls

    @property
    def doi_url(self) -> str:
        return f"https://doi.org/{self.doi}" if self.doi else ""


def _resolve_id(ref: str) -> str:
    """DOI / doi.org URL / arxiv id/URL / W-id → an OpenAlex `/works/{id}` path id."""
    ref = ref.strip()
    if m := _W_RE.search(ref):
        return m.group(1)
    if m := _DOI_RE.search(ref):
        return f"doi:{m.group(1)}"
    if m := _ARXIV_RE.search(ref):
        return f"doi:10.48550/arxiv.{m.group(1)}"  # arXiv's DataCite DOIs
    raise ValueError(f"no DOI, arxiv id, or OpenAlex id found in {ref!r}")


def _get(path: str, params: dict | None = None) -> dict:
    params = {"mailto": MAILTO, **(params or {})}
    resp = httpx.get(f"{OPENALEX}{path}", params=params, timeout=30, follow_redirects=True)
    if resp.status_code == 404:
        # preprints merged into a published record lack the DataCite DOI —
        # bites hard on famous arxiv papers (e.g. 1706.03762)
        hint = (
            ' — preprint likely merged into the published record; try `ko papers search "<title>"` and use the W-id'
            if "10.48550/arxiv" in path.lower()
            else ""
        )
        raise RuntimeError(f"OpenAlex has no record for {path.rsplit('/', 1)[-1]}{hint}")
    resp.raise_for_status()
    return resp.json()


def _reconstruct_abstract(inv: dict | None) -> str:
    """OpenAlex ships abstracts as word→positions (legal reasons); invert it back."""
    if not inv:
        return ""
    words = sorted((pos, word) for word, positions in inv.items() for pos in positions)
    return " ".join(word for _, word in words)


def _oa_pdf_urls(d: dict) -> list[str]:
    """Direct OA PDF links across all locations, best_oa_location first, deduped."""
    urls: list[str] = []
    for loc in [d.get("best_oa_location") or {}, *(d.get("locations") or [])]:
        u = loc.get("pdf_url")
        if u and loc.get("is_oa") and u not in urls:
            urls.append(u)
    return urls


def _work(d: dict) -> Work:
    src = (d.get("primary_location") or {}).get("source") or {}
    oa = d.get("open_access") or {}
    return Work(
        openalex_id=(d.get("id") or "").rsplit("/", 1)[-1],
        doi=(d.get("doi") or "").removeprefix("https://doi.org/"),
        title=" ".join((d.get("title") or "").split()),  # TSV-safe: no \n or \t
        year=d.get("publication_year") or 0,
        cited_by_count=d.get("cited_by_count") or 0,
        oa_status=oa.get("oa_status") or "closed",
        oa_url=oa.get("oa_url") or "",
        journal=src.get("display_name") or "",
        authors=[
            a["author"]["display_name"]
            for a in d.get("authorships") or []
            if (a.get("author") or {}).get("display_name")
        ],
        abstract=_reconstruct_abstract(d.get("abstract_inverted_index")),
        referenced_works=[w.rsplit("/", 1)[-1] for w in d.get("referenced_works") or []],
        oa_pdf_urls=_oa_pdf_urls(d),
    )


def search(query: str, n: int = DEFAULT_N) -> list[Work]:
    """Relevance-ranked search across all publishers, preprints included
    (OpenAlex types arxiv/bioRxiv-only works as `preprint`, not `article`)."""
    data = _get(
        "/works",
        {
            "search": query,
            "filter": "type:article|preprint",
            "sort": "relevance_score:desc",
            "per_page": n,
            "select": _LIST_SELECT,
        },
    )
    return [_work(d) for d in data["results"]]


def get(ref: str) -> Work:
    """One work: full metadata, reconstructed abstract, and S2 tldr (if keyed)."""
    work = _work(_get(f"/works/{_resolve_id(ref)}"))
    work.tldr = _s2_tldr(work.doi)
    return work


def oa_url(ref: str) -> str:
    """Best open-access URL for a DOI/arxiv id, '' if none (OpenAlex = Unpaywall data)."""
    d = _get(f"/works/{_resolve_id(ref)}")
    return (d.get("open_access") or {}).get("oa_url") or ""


def cites(ref: str, n: int = DEFAULT_N) -> list[Work]:
    """Works citing this paper, most-cited first — the citation graph, forward."""
    rid = _resolve_id(ref)
    wid = rid if rid.startswith("W") else _work(_get(f"/works/{rid}")).openalex_id
    data = _get(
        "/works",
        {
            "filter": f"cites:{wid}",
            "sort": "cited_by_count:desc",
            "per_page": n,
            "select": _LIST_SELECT,
        },
    )
    return [_work(d) for d in data["results"]]


def refs(ref: str, n: int = DEFAULT_N) -> list[Work]:
    """This paper's references, batch-resolved, most-cited first (batch cap: 50).

    Resolve up to 50 refs *then* take the top n — OpenAlex's referenced_works
    order is arbitrary, so slicing first would return an arbitrary n, not the
    most-cited n.
    """
    parent = _work(_get(f"/works/{_resolve_id(ref)}"))
    ids = parent.referenced_works[:50]
    if not ids:
        return []
    data = _get(
        "/works",
        {
            "filter": "openalex:" + "|".join(ids),
            "sort": "cited_by_count:desc",
            "per_page": len(ids),
            "select": _LIST_SELECT,
        },
    )
    return [_work(d) for d in data["results"][:n]]


# --- Semantic Scholar (enrichment only) ---


def _s2_id(ref: str) -> str:
    """ref → an S2 paper id (`arXiv:{id}` or `DOI:{doi}`); W-ids resolve via OpenAlex."""
    if m := _ARXIV_RE.search(ref):
        return f"arXiv:{m.group(1)}"
    if m := _DOI_RE.search(ref):
        return f"DOI:{m.group(1)}"
    if _W_RE.search(ref.strip()):
        doi = _work(_get(f"/works/{_resolve_id(ref)}")).doi
        if doi:
            return f"DOI:{doi}"
    raise ValueError(f"no DOI or arxiv id found in {ref!r}")


def _s2_tldr(doi: str) -> str:
    """S2 tldr for a DOI; '' without a key (keyless S2 429s immediately) or on any error."""
    key = os.environ.get("S2_API_KEY")
    if not key or not doi:
        return ""
    try:
        resp = httpx.get(
            f"{S2}/graph/v1/paper/DOI:{doi}",
            params={"fields": "tldr"},
            headers={"x-api-key": key},
            timeout=15,
        )
        resp.raise_for_status()
        return ((resp.json().get("tldr") or {}).get("text")) or ""
    except httpx.HTTPError:
        return ""


def similar(ref: str, n: int = DEFAULT_SIMILAR_N) -> list[Work]:
    """S2 paper recommendations. Requires S2_API_KEY (free; keyless 429s immediately)."""
    key = os.environ.get("S2_API_KEY")
    if not key:
        raise RuntimeError(
            "`similar` needs S2_API_KEY (free key: https://www.semanticscholar.org/product/api)"
        )
    resp = httpx.get(
        f"{S2}/recommendations/v1/papers/forpaper/{_s2_id(ref)}",
        params={"fields": "title,year,externalIds,citationCount,venue", "limit": n},
        headers={"x-api-key": key},
        timeout=30,
    )
    resp.raise_for_status()
    out = []
    for d in resp.json().get("recommendedPapers") or []:
        ext = d.get("externalIds") or {}
        doi = ext.get("DOI") or (f"10.48550/arxiv.{ext['ArXiv']}" if ext.get("ArXiv") else "")
        out.append(
            Work(
                openalex_id="",
                doi=doi,
                title=" ".join((d.get("title") or "").split()),
                year=d.get("year") or 0,
                cited_by_count=d.get("citationCount") or 0,
                oa_status="",
                oa_url="",
                journal=d.get("venue") or "",
            )
        )
    return out
