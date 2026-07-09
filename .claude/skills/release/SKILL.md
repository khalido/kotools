---
name: release
description: Cut a CalVer release for kotools — roll commits since the last tag into CHANGELOG.md + a GitHub release, bump pyproject, tag. Human-in-the-loop; nothing publishes without approval.
---

# /release

Cut a release for **kotools** (`ko`): summarize what shipped since the last tag, update
CHANGELOG.md, bump the version, tag, publish a GitHub release. Adapted from everx-crm's
release skill; the shared design decisions carry over — don't second-guess them.

The design (decided deliberately):

- **CalVer, PEP 440-normalized**: version `YYYY.M.D` in pyproject (e.g. `2026.7.9`),
  tag `v` + that (`v2026.7.9`). Same-day second release appends `.1`. Diverges from
  everx's zero-padded `vYYYY.MM.DD` because ko is a PyPI package — PEP 440 strips
  leading zeros, and tag == package version keeps `ko --version`, the tag, and PyPI
  telling one story. No SemVer: ko's one user is its author; the tag marks a *period*,
  not an API contract.
- **A release is a communication artifact.** There's no deploy — the audience is
  future-me and agents reading "what changed since I last looked." Same editorial
  discipline as everx, terser is fine.
- **CHANGELOG.md is the canonical record; the GitHub release is drawn from it** —
  per [Keep a Changelog 2.0.0](https://keepachangelog.com/en/2.0.0/), the version's
  changelog section *is* the release-notes draft: copy it in, optionally opening with
  a 1–2 sentence theme line. No separate Highlights/Fixes/Also reshape (everx does
  that for clients; ko's one reader doesn't need the same facts twice in two shapes).
  Categories: `Added` / `Changed` / `Fixed` / `Removed` / `Deprecated` / `Security`
  ("Fixed" = behaviour was wrong; "Changed" = worked as intended, now differs; unsure →
  ask "was the old behaviour a bug?"). WORKLOG.md stays the *dev journal*
  (why/decisions, session-grained); CHANGELOG is release-grained user-facing facts.
- **The version covers the CLI contract** (commands, flags, output shapes, exit codes —
  AGENTS.md). A breaking change to it gets an inline `**Breaking:**` marker under its
  type (`Changed`/`Removed`), never a separate section. Deprecate before removing:
  `Deprecated` in one release (naming the removal version), `Removed` in a later one.
- **LLM editorial rollup, fact-checked by a FRESH subagent.** The editorial pass can
  invert meaning ("shows X" → "no longer shows X"), invent flags, or claim a fix that
  shipped in a prior release. Commit titles are not proof of behaviour — only the code
  is. The verifier is never the author.
- **Human-in-the-loop.** Draft → show the user → only then commit, tag, publish.

## Steps

### 1. Find the range
```bash
git fetch --tags --quiet
git describe --tags --abbrev=0 2>/dev/null   # last tag; empty = first release ever
git status --short                            # must be clean, on main
```
Range is `<last-tag>..HEAD`. **First-ever release**: everything reachable from HEAD —
write it thematically by tool area (papers/x/google/agents/publish/...), not per-commit;
WORKLOG.md is the better raw input than 30+ commit bodies for that one.

### 2. Gather input — full bodies, never raw diffs
```bash
git log <last-tag>..HEAD --format='%h %ci%n%s%n%b%n---'   # subject + body (the why lives in bodies)
git diff --stat <last-tag>..HEAD                          # structural hint only
```
The commit body is *input for judgment*, not output to transcribe. `git show <hash>`
for a single unclear commit; default off.

### 3. Editorial pass (Sonnet subagent, or inline if the range is small)
Turn commits into the draft CHANGELOG section (which doubles as the release notes).
**Curate, don't accumulate**: a changelog entry is a notable difference written for the
reader, often spanning several commits — not a reworded git log. Review **every** commit
for notability, then let the non-notable ones go (internal refactors, test churn) —
what you *include* must be complete and consistent; what you exclude is judgment.
Dependency bumps are not a category: describe the *effect* under the right type if it
matters (`Fixed`/`Changed`), else leave them out. Mark CLI-contract breaks
`**Breaking:**` inline.

**Tight is the style.** The section headers do the categorization work, so each entry
is ONE line (two max for a headline feature): what it is, how to invoke it. Minor
fixes roll into a single closing summary line ("A dozen smaller fixes: x, y, z") —
itemizing them individually is what WORKLOG and git are for. A reader should get the
whole release in a ~15-second skim. Voice: direct, declarative, concrete
(`ko yt <url>` beats "YouTube support"); no "comprehensive/robust/seamlessly".
Flag/command names and output shapes are ko's API surface — name them exactly.

### 4. Fact-check (a FRESH Sonnet subagent — never the editorial author)
Feed it the draft + the repo. Every concrete claim → **CONFIRMED / WRONG / IMPRECISE**
with file/commit evidence:
- **Nouns exist**: every command, flag, env var, config key, path named in the draft
  (`--help` output and the source are the truth).
- **Fix direction matches the code NOW**, not just a related commit title.
- **New this release**: `git merge-base --is-ancestor <hash> <last-tag>` — a headline
  item must be in-range, and genuinely new rather than refined.
- **Overclaims**: literal-truth check on "every", "all", "always", counts.
Fix everything flagged in both drafts before the approval gate.

### 5. Update CHANGELOG.md + pyproject version
Rename `## [Unreleased]` to `## [YYYY.M.D] - YYYY-MM-DD` with the verified facts
(optional 1–2 sentence theme line above the typed sections when the release earns one);
add a fresh empty `## [Unreleased]` on top. Reference links at the bottom, per the spec:
`[Unreleased]` = compare `<new-tag>...HEAD`; each version = compare with the one before;
the **oldest** version links to its tag (nothing earlier to compare with):
```
[Unreleased]: https://github.com/khalido/kotools/compare/v2026.7.9...HEAD
[2026.7.9]: https://github.com/khalido/kotools/releases/tag/v2026.7.9
```
Bump `pyproject.toml` `version = "YYYY.M.D"` — `ko --version` reads it via
importlib.metadata, nothing else to touch. Then `uv sync --quiet` (refreshes uv.lock's
own-package version) and smoke it:
```bash
uv build --no-sources 2>&1 | tail -2
uv run ko --version        # must print the new version
uv run pytest -q           # green before anything outward-facing
```

### 6. Show the user — approval gate
Print: proposed tag, GitHub notes draft, the new CHANGELOG section, and the verify
result (what was flagged and fixed). **Stop. Get explicit approval.**

### 7. Commit, tag, publish (only after approval)
```bash
git add CHANGELOG.md pyproject.toml uv.lock && git commit -m "release: v2026.7.9"
git tag -a v2026.7.9 -m "Release v2026.7.9"
git push origin main --follow-tags
gh release create v2026.7.9 --title "v2026.7.9 — <short theme>" --notes-file - --verify-tag <<'NOTES'
<the approved CHANGELOG section for this version, verbatim>
NOTES
```
**PyPI**: not wired yet. When the trusted-publisher GitHub Action lands (WORKLOG Open:
tag-push OIDC, no long-lived tokens), the `--follow-tags` push above becomes the
publish trigger and this skill needs no changes. Until then, publishing is manual and
optional: `uv publish` (needs a token) — skip unless asked.

### 8. Confirm
```bash
gh release view v2026.7.9 --json url,tagName -q '.url'
uvx --from . ko --version   # the built package installs and reports the new version
```
Report the release URL at wrap-up. (Once PyPI publishing exists, add:
`uvx kotools@latest ko --version` after the index updates.)
