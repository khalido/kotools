# ko

Ko's personal opinionated CLI. Thin wrappers over SDKs I use often — equally ergonomic for humans and AI agents.

Current subcommands:

- `ko exa` — semantic web search + URL → markdown (via [Exa](https://exa.ai))
- `ko arxiv` — arxiv search + paper-to-markdown
- `ko gsheets` — read Google Sheets via OAuth

## Install

```bash
# from the repo
uv tool install --editable /path/to/ko

# or one-off
uvx --from /path/to/ko ko --help
```

(Once published to PyPI: `uv tool install ko-tools` — the package is `ko-tools`, the command it installs is still `ko`.)

## Quick start

```bash
# Exa — needs EXA_API_KEY
export EXA_API_KEY=...
ko exa search "claude code hooks" --since 3

# arxiv — no auth needed
ko arxiv search "tool use benchmark" --since 12 --long
ko arxiv fetch 2604.02460 -o paper.md

# Google Sheets — needs one-off OAuth (see below)
# (example ID is Google's public sample sheet)
ko gsheets info 1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms
ko gsheets get 1Bxi... 'Class Data!A1:F6'
ko gsheets get 1Bxi... 'Class Data!A1:F6' --json
```

## Google Sheets setup (one-off)

`ko gsheets` runs as *you* against *your* Google account (OAuth user flow). Scopes are read-only by default.

1. **Create a Google Cloud project** (if you don't have one). https://console.cloud.google.com/projectcreate
2. **Enable the APIs.** APIs & Services → Library → enable *Google Sheets API* and *Google Drive API*.
3. **Create OAuth credentials.** APIs & Services → Credentials → Create Credentials → OAuth client ID → Application type: **Desktop app**. Download the JSON.
4. **Save the JSON** to `~/.config/ko/google_client.json` (or set `KO_GOOGLE_CLIENT_FILE=<path>`).
5. **Run `ko gsheets auth`.** A browser window opens; approve; you're done. The refresh token is cached at `~/.config/ko/google_token.json`.

Logout / re-auth: `ko gsheets auth --logout`.

**Why OAuth and not a service account?** A service account needs every sheet explicitly shared with its email address — fine for bots, tedious for a personal read-anywhere CLI. OAuth gives you access to anything the signed-in Google account can see. If that's too broad, use a service account instead (future `ko gsheets` flag).

**Can I scope to one folder?** No — Google's OAuth scopes are per-API (`drive.readonly`), not per-resource. Service accounts with individual share grants are the workaround if you need tighter control.

## Output conventions

- **Default output is human-readable** and designed to pipe (`ko gsheets get` emits TSV, `ko arxiv search` emits short line format).
- **`--json` everywhere** structured data would help. Agents should prefer `--json`.
- **Errors go to stderr.** Empty results are not errors — exit `0` with a friendly message.
- **Exit codes:** `0` success, `1` runtime error (auth, network, API), `2` usage error.

## Dev

```bash
cd ko
uv sync
uv run pytest
uv run ko --help
```

Python 3.14, `uv`, `ruff`, `pytest`, `typer`.

## License

MIT.
