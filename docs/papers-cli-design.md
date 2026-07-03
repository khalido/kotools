# ko papers — design (built 2026-07-03)

> Implemented as designed. One deviation found in live testing: OpenAlex has
> occasional bad-merge records (e.g. `doi:10.1002/jemt.20118` returns the right
> abstract under a wrong title/author) — upstream data, not fixable here;
> `search` for the title and use the W-id when a record looks off.

Cross-publisher literature scouting. Motivated by the UTS TMOS lab project, where
`ko arxiv` covers arXiv but real gaps showed up in practice: no fetch path for
DOI-only papers (Wiley/MDPI/ACS/IOP), `ko fetch` 403s on publisher sites, and no
citation-graph queries ("who cites the eXfoliator paper?"). APIs below were
**verified live** during design (2026-07-03).

## Verdict

New `src/ko/papers.py` module. **OpenAlex is the backbone** (search, DOI lookup,
OA-URL resolution, citation graph — no key, ~10k req/day, polite pool via
`?mailto=`). **Semantic Scholar is enrichment only** (`tldr`, `similar`) and needs a
free `S2_API_KEY` — without a key it 429s after ~2 calls (verified), so it's opt-in
and degrades gracefully. **Skip Unpaywall** (OpenAlex's `open_access.oa_url` IS
Unpaywall's data — it's the upstream), **skip Crossref/CORE/Europe PMC** (subsumed /
too narrow). Zero new deps (httpx already present).

## Commands

```
ko papers search "<query>" [--n 10] [--json]   # OpenAlex; TSV: year|cites|oa_status|title|doi|journal
ko papers get <doi|arxiv-id>                   # metadata + tldr → OA pdf → existing fetch/liteparse pipeline
ko papers cites <id> [--n] [--json]            # OpenAlex filter=cites:{openalex_id}
ko papers refs <id> [--n] [--json]             # referenced_works → batch resolve (filter=openalex:W1|W2|…)
ko papers similar <id> [--n 5] [--json]        # S2 recommendations (needs S2_API_KEY)
```

`get` flow: OpenAlex `/works/doi:{doi}` → try each open-access candidate in
`Work.full_text_urls` (direct PDFs from `best_oa_location`/`locations[]` first, the
`open_access.oa_url` landing page last), fetching via the existing liteparse pipeline
until one yields text. Trying every OA location — not just OpenAlex's single `oa_url`
pick — is what dodges bot-blocks: a green-OA Nature paper carries the Nature PDF *plus*
an arXiv PDF *plus* an OSTI mirror, so if the publisher blocks us a repository copy
still lands (added 2026-07-03, from the Sonnet-agent review). If every candidate fails
(or it's genuinely paywalled): print a **metadata card** — title, year, authors,
journal, DOI, S2 tldr, and the abstract reconstructed from `abstract_inverted_index`
(invert word→positions, sort, join — 5 lines). Often enough.

Deferred access pathways (reviewed 2026-07-03, not built): **EZproxy deep-link** print
(`ezproxy.lib.<uni>/login?url=<doi>` for the owner to click — no uni login yet) and a
**`--scihub` opt-in** fallback (off by default; skipped for now — the full OA-location
chain covers more than expected first).

## Also: DOI routing in fetch.py (~15 lines)

`ko fetch https://doi.org/10.1002/jemt.20118` currently lands trafilatura on a Wiley
landing page and returns nothing. Add DOI detection
(`(?:doi\.org/|doi:|^)(10\.\d{4,}/\S+)`) before existing routing → resolve
`open_access.oa_url` via OpenAlex → try that first → fall through to the current path.

## Key endpoints/fields (verified)

OpenAlex (`api.openalex.org`, add `&mailto=`):
- `GET /works/doi:{doi}` — `doi, title, publication_year, cited_by_count,
  open_access.{is_oa,oa_status,oa_url}, primary_location.source.display_name,
  ids.openalex, referenced_works, abstract_inverted_index`
- `GET /works?search={q}&filter=type:article&sort=relevance_score:desc&per_page={n}&select=…`
- `GET /works?filter=cites:{openalex_id}&sort=cited_by_count:desc` (verified: 743 citers resolved)
- Batch: `GET /works?filter=openalex:W1|W2|…` (≤50 per call)
- Rate: headers show 1000/window ≈ 10k/day free.

Semantic Scholar (`api.semanticscholar.org`, header `x-api-key`):
- `GET /graph/v1/paper/DOI:{doi}?fields=tldr` → `tldr.text` (also accepts `arXiv:{id}`, `CorpusId:{n}`)
- `GET /recommendations/v1/papers/forpaper/{id}?fields=title,year,externalIds,citationCount&limit={n}`
- 1 req/s with key; unusable without (429 after ~2 calls).

## Implementation sketch

- `src/ko/papers.py` ~200 lines: `_resolve_id` (doi vs arxiv), `_oa_get/_oa_search/
  _oa_cites/_oa_refs`, `_reconstruct_abstract`, `_s2_tldr` (graceful no-key), thin
  public `search/get/cites/refs/similar`.
- `cli.py`: `papers_app` typer sub-app, 5 commands, TSV default + `--json` (~40 lines).
- `agents/_toolsets.py`: add `papers_search` + `papers_cites` to the existing `papers`
  toolset (~20 lines) — the research agent gets the citation graph for free.
- Config: `S2_API_KEY` env or `config.toml [keys]`; `ko doctor` lists it as optional.
- Not adding: author search (query covers it), local caching/indexing, venue/date
  filter flags (OpenAlex filters slot in later without reshaping the module).
