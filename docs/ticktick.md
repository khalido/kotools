# TickTick

`ko tt` sits on TickTick's REST Open API. This page records which TickTick surface ko uses and why; `ko tt --help` is the contract.

## References

- Open API docs: https://developer.ticktick.com/docs#/openapi (a docsify page, so fetchers see an empty shell — the raw markdown is https://developer.ticktick.com/docs/openapi.md)
- Hosted MCP: https://help.ticktick.com/articles/7438129581631995904 (`https://mcp.ticktick.com`, Streamable HTTP)
- Official CLI (Node, `@ticktick/ticktick-cli`): https://help.ticktick.com/articles/7465251130025443328
- What's new: https://help.ticktick.com/articles/7082552170989486080

## Auth

One personal API token serves REST, MCP and the CLI: web app → avatar → Settings → Account → API Token, sent as `Authorization: Bearer <token>`. ko reads it as `TICKTICK_API_KEY`. No OAuth, so it works on a headless box.

## Surfaces (measured 2026-10-16)

| | REST `api.ticktick.com/open/v1` | MCP `mcp.ticktick.com` | CLI |
|---|---|---|---|
| Latency | 0.6–0.9s a call | 0.7s handshake + ~0.3s a call | Node process per call |
| Coverage | lists, tasks, notes, search, filter, batch, move, assign, comments, members, completed | 57 tools: the REST set plus habits, focus, countdowns, columns, tags, calendar | tasks, lists, habits, focus, countdowns |

**ko uses REST only.** Search over REST and MCP gave identical results on five queries; REST took ~0.8s, ko's MCP path ~2.3s. In July REST had no search endpoint and the MCP took ~3s and often timed out, so search went over the MCP; both reasons are gone. Reach for the MCP only for something REST lacks (habits, focus, tags).

## Things the docs don't say

- **Notes:** `POST /task` with `"kind": "NOTE"` creates a note. Without `kind`, a task created in a notes list (project `kind: "NOTE"`) is still a plain task (`kind: "TEXT"`). MCP `create_task` documents `kind`.
- **Search** (`POST /task/search`, `{"keywords": …}`) matches body (`content`) as well as title, covers open and completed tasks, and its index trails writes by ~5–10s.
- **Inbox** is missing from `GET /project`, but the literal id `inbox` works on every `/project/{id}` route.
- Markdown quirks in `content`: single `~text~` is underline (strikethrough is `~~text~~`), `::text::` is highlight, headings stop at three levels.
