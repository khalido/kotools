---
name: ko-cli
description: Hand this to an agent so it can drive the `ko` CLI — the contract, the command map, the gotchas
---
# Driving the `ko` CLI

`ko` is Ko's personal CLI: thin, opinionated wrappers over the APIs he uses often, built so
an agent can lean on them from bash. This brief is the map. **Every command's `--help` is
the real contract** — read it before using a command you haven't used, and prefer it to
guessing flags. `ko doctor` tells you what's configured and what isn't.

## The output contract

- **stdout is data, stderr is notes.** Safe to pipe.
- Plain text or **TSV** by default — `cut -f 2` works. `--json` on most reads gives an array
  of objects (a few return one object; `gsheets get` is a 2D array).
- **Exit codes**: `0` success *including empty results*, `1` runtime error (network, auth,
  bad upstream), `2` usage error. Empty is never an error — an empty list is `[]` and exit 0.
- Under `--json`, errors are `{"error": ..., "code": ...}` on stderr.
- **Nothing ever prompts you.** Destructive commands confirm only on a TTY; from a pipe or an
  agent they either proceed or need `--yes`. You will never hang waiting on input.
- Count flags accept `-n` or `--n`.

## What's in the box

**Web + reading** — `ko exa search "<idea>"` (semantic search; `--highlights` for cheap
triage, `--category 'research paper'` for scholarly), `ko fetch <url>` (any URL → markdown;
handles arxiv, PDFs, YouTube, paywalls, dead links via Wayback), `ko doc <file>` (PDF/Office/
image → text, local), `ko yt <url>` (YouTube transcript). Shortcuts: `ko <url>` and `ko <file>`.

**Writing out** — `ko pdf <file.md>` (alias `ko md-to-pdf`): markdown → print-ready A4 PDF,
prints the path it wrote. Default style is a plain note with a page footer; `--style report`
adds a letterhead, Contents and numbered sections. The inverse of `ko doc`.

**Research** — `ko papers search|get|cites|refs|similar` (cross-publisher via OpenAlex; takes
DOIs, arxiv ids, or W-ids), `ko arxiv search|fetch`, `ko hf top|search|get` (HF Daily Papers).

**News + social** — `ko hn top|search|item` (Hacker News), `ko x search|list|user` (X — **costs
real money per read**, mind `--n`).

**Google** — `ko gmail` (read mail, download attachments, create drafts; **sending is off by
default**), `ko cal` (agenda, quick-add), `ko gsheets` (read + write), `ko gdocs` (Markdown ↔
Docs). All share one OAuth token; `ko gsheets auth` grants it.

**Tasks** — `ko tt` / `ko todo` (TickTick: `lists`, `items <list>`, `search`, `add`, `done`).
Task notes are markdown and render in the app.

**LLM + agents** — `ko llm "<prompt>"` (one-shot, stdin-aware, no tools), `ko ai "<prompt>"`
(the default agent, all toolsets + memory), `ko agent research|repo|tv`, `ko models`.

**Misc** — `ko publish` (folder → Cloudflare), `ko mcp` (inspect/call MCP servers), `ko tv`,
`ko brief`, `ko billing`, `ko logs`, `ko prompt`, `ko refs`, `ko doctor`.

## How to use it well

- **Compose, don't parse prose.** `ko hn item 123 | ko llm "summarize the debate"`,
  `ko gsheets get <id> | cut -f 3`, `ko papers search "..." --json | jq`. Commands that
  write a file print its path, so they chain:
  `ko gmail draft new --to x@y.com -s "Status" --body-file note.txt -a "$(ko pdf status.md)"`.
- **Search wide, read narrow.** `ko exa search --highlights` to find candidates, then
  `ko fetch <url>` on the two that matter. Don't pull full text for ten results.
- **Reach for the specific tool.** `ko papers` beats `ko exa` for literature; `ko yt` beats
  `ko fetch` for a video you want transcribed.
- **Money is real.** `ko x` bills per post read; Exa bills per search; LLM calls print their
  cost to stderr. `ko billing` shows what's left — including the API key's own spend cap,
  which can be exhausted while the account still shows credit.

## Gotchas worth knowing

- `ko exa search` returns `score: 0.0` unless `--type neural`, and `text: null` unless
  `--long`. Both are by design, not bugs.
- Exa's scholarly category is `'research paper'`, **not** `publication`.
- `ko gmail draft send` refuses unless explicitly enabled in config — drafting is the
  intended workflow; a human sends.
- A few OpenAlex DOI records are bad merges (right abstract, wrong title). If a record looks
  off, search the title and use the W-id.
- If a command reports a missing key, that's config — `ko doctor` names the env var and where
  to get one. It won't be fixed by retrying or by routing around it.
