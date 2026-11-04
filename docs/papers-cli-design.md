# ko papers — design (built 2026-07-03)

Cross-publisher literature scouting, implemented as designed in `src/ko/papers.py`. One deviation found in live testing: OpenAlex has occasional bad-merge records (e.g. `doi:10.1002/jemt.20118` returns the right abstract under a wrong title/author). That is upstream data and not fixable here. If a record looks off, `search` for the title and use the W-id.

Motivation came from the UTS TMOS lab project. `ko arxiv` covers arXiv, but real gaps showed up: no fetch path for DOI-only papers (Wiley/MDPI/ACS/IOP), `ko fetch` 403s on publisher sites, and no citation-graph queries ("who cites the eXfoliator paper?"). The APIs below were **verified live** during design (2026-07-03).

## Verdict

**OpenAlex is the backbone**: search, DOI lookup, OA-URL resolution and the citation graph, with no key, ~10k req/day, and a polite pool via `?mailto=`. **Semantic Scholar is enrichment only** (`tldr`, `similar`) and needs a free `S2_API_KEY`. Without a key it 429s after ~2 calls (verified), so it's opt-in and degrades quietly to an empty tldr. **Skip Unpaywall**: OpenAlex's `open_access.oa_url` is Unpaywall's data, with Unpaywall as the upstream. **Skip Crossref, CORE and Europe PMC** (subsumed, or too narrow). No new deps (httpx was already present).

## Commands

```
ko papers search "<query>" [--n 10] [--json]   # OpenAlex; TSV: year|cites|oa_status|title|doi|journal
ko papers get <doi|arxiv-id>                   # metadata + tldr → OA pdf → existing fetch/liteparse pipeline
ko papers cites <id> [--n] [--json]            # OpenAlex filter=cites:{openalex_id}
ko papers refs <id> [--n] [--json]             # referenced_works → batch resolve (filter=openalex:W1|W2|…)
ko papers similar <id> [--n 5] [--json]        # S2 recommendations (needs S2_API_KEY)
```

`get` also takes `--info` (card only), `-o <path>` (write the full text there) and `--json` (implies `--info`). Every `<id>` accepts a DOI, a doi.org URL, an arxiv id or an OpenAlex W-id.

`get` flow: OpenAlex `/works/doi:{doi}` → try each open-access candidate in `Work.full_text_urls` through the existing liteparse pipeline until one yields text. Direct PDFs from `best_oa_location`/`locations[]` go first; the `open_access.oa_url` landing page goes last. Trying every OA location, not just OpenAlex's single `oa_url` pick, is what dodges bot-blocks. A green-OA Nature paper carries the Nature PDF *plus* an arXiv PDF *plus* an OSTI mirror, so if the publisher blocks us, a repository copy still lands (added 2026-07-03 after the Sonnet-agent review). If every candidate fails, or the paper is genuinely paywalled, `get` prints a **metadata card**: title, year, authors, journal, DOI, S2 tldr, and the abstract reconstructed from `abstract_inverted_index` (invert word→positions, sort, join; 5 lines). Often enough.

Deferred access pathways (reviewed 2026-07-03, not built): an **EZproxy deep-link** print (`ezproxy.lib.<uni>/login?url=<doi>` for the owner to click; no uni login yet) and a **`--scihub` opt-in** fallback (off by default). The full OA-location chain covered more than expected, so both were skipped.

## Also: DOI routing in fetch.py

Shipped in `fetch.py`. `ko fetch https://doi.org/...` used to land trafilatura on a publisher landing page and return nothing. Now DOI detection (`_DOI_RE`, `(?:doi\.org/|doi:|^)(10\.\d{4,}/[^\s?#]+)`) runs before the existing routing, resolves `open_access.oa_url` via OpenAlex, tries that first, and falls through to the old path.

## Key endpoints/fields (verified)

OpenAlex (`api.openalex.org`, add `&mailto=`):
- `GET /works/doi:{doi}` — `doi, title, publication_year, cited_by_count,
  open_access.{is_oa,oa_status,oa_url}, primary_location.source.display_name,
  ids.openalex, referenced_works, abstract_inverted_index`
- `GET /works?search={q}&filter=type:article|preprint&sort=relevance_score:desc&per_page={n}&select=…` (preprints included — arXiv/bioRxiv would vanish under bare `type:article`)
- `GET /works?filter=cites:{openalex_id}&sort=cited_by_count:desc` (verified: 743 citers resolved)
- Batch: `GET /works?filter=openalex:W1|W2|…` (≤50 per call)
- Rate: headers show 1000/window ≈ 10k/day free.

Semantic Scholar (`api.semanticscholar.org`, header `x-api-key`):
- `GET /graph/v1/paper/DOI:{doi}?fields=tldr` → `tldr.text` (also accepts `arXiv:{id}`, `CorpusId:{n}`)
- `GET /recommendations/v1/papers/forpaper/{id}?fields=title,year,externalIds,citationCount&limit={n}`
- 1 req/s with key; unusable without (429 after ~2 calls).

## Implementation as built

- `src/ko/papers.py`, 290 lines (the sketch said ~200): id resolution (doi vs arxiv), OpenAlex search/cites/refs, abstract reconstruction, `_s2_tldr` (empty without a key), and thin public `search/get/cites/refs/similar`.
- `cli_web.py`: the `ko papers` Typer sub-app, 5 commands, TSV default + `--json`.
- `agents/_toolsets.py`: `papers_search`, `papers_cites` and `papers_refs` in the `papers` toolset, so the research agent gets the citation graph.
- Config: `S2_API_KEY` from env or `config.toml [keys]`; `ko doctor` lists it as optional.
- Not added: author search (the query covers it), local caching/indexing, venue/date filter flags (OpenAlex filters slot in later without reshaping the module).
