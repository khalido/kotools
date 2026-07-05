---
name: research-papers
description: How I research scientific papers with ko — snowball the citation graph, ML/AI lens
---
# Researching papers with ko — kickoff

How to do a literature scan / "what's the state of the art on X" using `ko`. Bias: I bring
**ML/AI to bear on whatever field the question is in**, so weight toward arXiv + ML venues +
released code, but the workflow is general. `ko`'s paper tools wrap the three genuinely-free
composable APIs the whole ecosystem is built on — **OpenAlex, Semantic Scholar, arXiv** — plus
Hugging Face for ML signal. Everything else (Elicit, Undermind, Connected Papers) is a paid UI
on top of these; you have the primitives directly.

## The tools, and when to reach for each
- **`ko papers search "<topic>"`** — cross-publisher **relevance** search (OpenAlex). Your seed-finder
  and the backbone. Covers journals *and* arXiv preprints. TSV: `year cites oa_status title doi journal`.
- **`ko papers cites <id>` / `refs <id>`** — the **snowball engine**. `cites` = who built on it (forward),
  `refs` = what it builds on (backward). Both most-cited first. Takes a DOI, arxiv id, or OpenAlex W-id.
- **`ko papers get <id>`** — one paper: full text via its open-access copy if any, else a metadata card
  (authors, journal, S2 tldr, abstract). `--info` for card-only.
- **`ko papers similar <id>`** — S2 recommendations (needs a free `S2_API_KEY`; skip if unset).
- **`ko arxiv search "<topic>"`** — arXiv-native relevance search. `--recent` sorts newest-first for
  *browsing* an area (pair with a category, e.g. `ko arxiv search "cat:cs.LG" --recent`). A bit slow (~2-5s).
- **`ko arxiv fetch <id>`** — an arXiv paper → clean markdown (from LaTeX; better than any PDF parse).
- **`ko hf top` / `ko hf search "<topic>"`** — Hugging Face Daily Papers: the de-facto ML front page,
  community-upvoted, tagged by lab, with links to **code / models / datasets**. `top` = today's trending.
- **`ko exa search "<topic>"`** — web/blog/X context, author threads, lab pages (`--domains .edu`).
- **`ko fetch <url>`** — any URL (or DOI: `ko fetch https://doi.org/…`) → markdown; PDFs auto-parsed.

## The workflow (a spiral, not a waterfall)
1. **Orient with surveys first.** `ko papers search "<topic> survey"` or `"<topic> review"`. Read 2–3
   surveys from *different* groups (each has bias) — their intros name the 3–5 seminal works and their
   reference lists are a pre-screened canon. `ko papers get <survey-doi>` to read one.
2. **Pick 1–3 seed papers** you trust completely: recent-ish, well-cited *for their age*, reputable venue,
   directly on-topic. Quality over quantity.
3. **Snowball both directions** from each seed:
   - Backward: `ko papers refs <seed>` → foundational/historical work. Go ≥2 levels.
   - Forward: `ko papers cites <seed>` → recent work, critiques, applications.
   - New in-scope papers become the next round's seeds. **Stop at saturation** — when a pass adds <~5%
     new, or the same papers/authors keep recurring.
4. **The best seed is the one that keeps reappearing** across the reference lists you're collecting.
5. **Key players fall out of the corpus**: authors recurring most across refs = the field's figures;
   sort by `journal` to find the 2–3 venues to monitor.
6. **Read** the shortlist: `ko papers get` / `ko arxiv fetch` / `ko fetch <doi>`. Three-pass read —
   5-min relevance scan, then figures/connections, then the argument.

## ML/AI-specific
- **Two modes, don't conflate.** *Staying current* = `ko hf top` (daily), `ko arxiv search "cat:cs.LG" --recent`,
  `ko x list ai` (Twitter is where papers circulate *before* indexing). *Deep dive* = the snowball above.
- **Released code is the #1 ML quality signal.** `ko hf search` surfaces linked repos/models/datasets —
  prefer papers that ship code. (Papers with Code shut down July 2024; HF Daily Papers is the replacement
  for signal.)
- Top venues: NeurIPS / ICML / ICLR / CVPR / ACL / EMNLP / AAAI **main track**. A workshop paper at these is
  *not* the same as main-track. In ML, don't filter to journals-only — the conference paper is the real one.
- **Run ≥2 discovery tools and merge.** Different tools return near-disjoint sets (`ko papers search` vs
  `ko hf search` vs `ko arxiv search` will each surface things the others miss). Cross them, dedupe by DOI.

## Quality heuristics & pitfalls
- **Citation count is visibility, not quality** — it's lagging (2–5 yr to accrue), prestige-biased (big-lab
  preprints over-cited), and field-incomparable. Only compare *within* sub-field and age. Prefer papers
  cited *approvingly, with technical engagement, by independent groups* (not the authors' own follow-ups).
- **Is the result real?** (strongest first) independent replication → reproducibility badge → code+data with
  fixed seeds → **multi-seed results with variance** + ablations + strong baselines whose numbers match what
  those baseline papers reported. Red flags: single run/no variance, "code on request", one dataset/seed,
  0.1–0.5% gains called significant, baseline numbers that don't match the originals.
- **Recency bias is real** — a well-validated 2023 result can beat a hyped 2025 one with 3× the cites. Check
  whether recent claims have been *stress-tested* by follow-up (that's what `ko papers cites` is for).
- **Preprint red flag**: on arXiv 6+ months with no publication → likely failed/never-submitted review. But a
  preprint *with code + independent reproduction + uptake* beats a weak low-tier journal paper.
- **Verify every citation's DOI** before you rely on it (LLM citation-hallucination is ~1-in-277 now).
  `ko papers get <doi> --info` confirms a paper is real and gives you the true title/authors/venue.

## ko gotchas (from the tools themselves)
- `ko papers cites <arxiv-id>` can 404 for *famous* papers — a preprint merged into its published record loses
  its standalone arXiv DOI. Fix: use the journal DOI, or `ko papers search "<title>"` → take the W-id.
- OpenAlex records occasionally mis-merge (right abstract, wrong title). If a `get` looks off, `search` the
  title and use the W-id.
- `--json` on every read command for structured output; TSV otherwise (`cut -f N` safe).

## Output
A tight synthesis, not a link dump: the 3–5 papers that matter with one line each (why it matters + DOI),
the key authors/venues, what's *settled* vs *contested*, and — for ML — which results shipped runnable code.
