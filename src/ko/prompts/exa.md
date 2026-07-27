---
name: exa
description: When to reach for `ko exa` (semantic search) vs fetch/papers, plus the agent defaults
---
# Searching the web with `ko exa`

Exa is a **neural/semantic** search engine — it matches on meaning, not keywords, and can
return clean page content in the same call. Reach for it when you want *pages about an idea*
(labs, faculty, blog posts, company/people pages), not a known URL. Docs: https://exa.ai/docs

## exa vs the neighbours
- **`ko exa search "<idea>"`** — find pages by meaning. The seed-finder for web research.
- **`ko fetch <url>`** — you already have the URL; want the markdown. (arxiv/PDF/YouTube-aware.)
- **`ko papers search`** — scholarly literature + citation graph (OpenAlex). Prefer it for papers;
  Exa's `--category 'research paper'` is the web-flavoured alternative.

## Agent defaults (what to actually type)
- Default is a query-relevant **summary** per hit — good for scanning. For an agent budget,
  add **`--highlights`**: the most-relevant sentences, bounded (~10x fewer tokens than full text).
- **`--long`** pulls a full-text excerpt when you need to read, not just triage.
- **`--type`**: `auto` (default, let Exa pick) is right almost always. `neural` if you want the
  legacy embedding search (it's the only type that returns a real relevance `score`); `fast`/`instant`
  for latency; `deep`/`deep-reasoning` for hard multi-hop research (slower, pricier).
- **`--category`**: focus the index — `'research paper'`, `news`, `company`, `pdf`, `'personal site'`,
  `'financial report'`, `people`. `--category 'research paper'` upgrades the old `--domains .edu` trick.
- **`--domains .edu`** / `--domains <uni>` still works for labs/faculty/admissions pages.

## Freshness (`--max-age-hours`)
Controls how stale a *cached* page may be before Exa re-crawls it live:
`0` = always live-crawl (freshest, slowest), `-1` = cached only (fastest), omitted = live-crawl
only as a fallback. Pair with `--category news` for "what happened today".

## Fetching known URLs
`ko exa get <url> …` → clean markdown for URLs you already have. A dead/blocked URL is reported on
stderr (`no content for: … (error)`) and skipped — exit 0 if *any* URL succeeded, exit 1 if none do.

## Notes
- `--json` on `search` for structured hits (url, title, score, date, summary/highlights/text).
- `score` is 0.0 unless `--type neural`; `text` is null unless `--long` — both by design, not bugs.
- Needs `EXA_API_KEY` (`ko doctor` checks it). Deep-search types and large `--long` fetches cost more.
