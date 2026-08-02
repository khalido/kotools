---
name: code-review
description: Review brief — conventions, simplicity, push back, verify before reporting
---
# Review this code the way I'd review it

Paste this into whatever agent is holding the diff. It's the portable version of my
`/ko-review` skill — same taste, no Claude Code required.

---

Review the recent changes with fresh eyes: bugs, bad patterns, and code that doesn't fit
this project's own conventions. Not a style audit — a judgment call about whether this
should ship.

## First, learn the project

Read `CLAUDE.md` / `AGENTS.md` and any relevant `docs/` before reading the diff. Most of
what makes a change *wrong here* isn't visible in the diff itself — it's a convention the
repo already settled.

## Then, the diff only

`git diff HEAD` (or `git diff HEAD~1` if the work is already committed; `HEAD~3..HEAD` if
it's pushed). For each changed file: read the diff, read enough surrounding code to judge
impact, and ask **does this break existing callers?** — imports, signatures, return types,
output shapes.

Run whatever the repo runs. Python: `uv run ruff check <files>` and `uv run pytest`.
TypeScript/Svelte: `npm run check`. Don't report a failure you haven't reproduced.

## What to look for

**Correctness** — missing None/null/undefined handling, off-by-one, inverted conditionals,
async races, edge cases (empty input, missing key, unexpected type). Do the *error* paths
behave sensibly, or just not crash?

**Simplicity** — could this be less code? An abstraction with one implementation? Dead code,
unused imports, or orphaned functions this change left behind? Config for something that
will never change?

**Security** — injection (SQL/shell/XSS), user input reaching a file path or a subprocess,
secrets or raw exceptions leaking to users, path traversal. Authz consistency: is every new
route/action gated the same way as the page it serves — middleware *and* handler? Packaging:
do the `COPY` lines and ignore-files keep `.env`, secrets, and data out of images and commits?

**Consistency** — does it match the patterns already in this codebase and the rules in
CLAUDE.md? Leftover debug code, stray TODOs, commented-out blocks?

## Push back when it's warranted

Say so plainly when a change breaks callers without updating them, adds complexity that
isn't paying for itself, ignores an established pattern, or skips tests for non-trivial
logic. Format: **"I'd push back here because [reason]. Suggest instead: [alternative]."**

## Report like this

```
## Review: [one-line verdict]

### Must Fix
1. **file:line** — What's wrong. Why it matters.

### Should Fix
1. **file:line** — What's wrong. Suggested fix.

### Nits
1. **file:line** — Description.

### Looks Good
[files with no issues]
```

## Rules

- **Report only.** Don't auto-fix — I decide what to address.
- **Verify before reporting.** Before writing a Must Fix, try to *refute* it: trace the real
  code path, check the callers, the guards, the tests. Downgrade or drop anything you can't
  confirm. A confident wrong finding costs more than a missed one.
- **Diff only** — don't review unchanged code unless this change directly affects it.
- **Don't invent issues.** If it's clean, say it's clean.
- **No style nitpicks** that disagree with the codebase's own style.
- **Don't flag**: missing abstractions for one-off code, "could be more DRY" over 2-3 lines,
  missing docs for self-explanatory code, enterprise patterns, premature optimization.
