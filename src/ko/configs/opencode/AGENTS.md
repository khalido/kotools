# OpenCode config

Global [OpenCode](https://opencode.ai/v2/docs) v2 config. Source of truth is here; edit here, copy into place, commit.

```bash
cp ~/code/kotools/src/ko/configs/opencode/opencode.json ~/.config/opencode/
opencode reload   # usually automatic
```

No custom agents: the `/opencode` Claude Code skill uses the v2 built-ins — `explore` for opinions (read, glob, grep, webfetch, websearch; no shell, write or MCP) and `build` for delegated work. Built-ins are also the only agents Zen's free tier accepts: custom agents get "OpenCode's free tier can only be used from within OpenCode" (2.0.11).

`opencode.json` holds:

- **Permission denies**, for `build`: shell `railway …` and `git push …`, and every `railway*` MCP tool (hides a project's own Railway MCP). Deny patterns are best-effort — the docs recommend allowlists for strict work.
- **The sol provider pin** on OpenRouter (`only: ["openai"]`).

Conventions:

- **Never add a catch-all allow** (`"*": "allow"` under `bash` or top level). The last matching rule wins, so it re-opens shell for `explore` too — verified 2026-09-21.
- Written in v1 field names (`permission`, `provider`, `options`); v2 normalizes them. `opencode debug config` shows the v2 shape.
- **Nothing private** — this repo is public. No keys, client names or machine-specific paths.

Test behaviour after any change rather than trusting a listing (`opencode debug agents` returns `[]`):

```bash
opencode run "List your exact tool names, then run shell 'echo hi' and report the result." \
  --agent explore --format json < /dev/null | jq -r 'select(.type=="text").part.text'
```
