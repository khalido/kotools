# ko publish — guide

`ko publish` scaffolds a website into a folder and publishes it to Cloudflare (Workers).
Built for agents + the owner to spin up a doc/site/app and get a live URL fast.

**The loop:** `ko publish new <dir> [pathway]` → edit the files (each scaffold drops a
`CLAUDE.md` with that pathway's specifics — *read it*) → `ko publish preview <dir>` to view it
locally (wrangler dev, real http) → `ko publish <dir>` → live URL. Re-publishing the same folder
overwrites the **same URL**. Preview over `file://` won't work (ES modules + `fetch()` are blocked).

## Pathways (pick one at scaffold time)

| Command | What you get | Use for |
|---|---|---|
| `ko publish new <dir>` | static HTML page (Tailwind + Alpine, no build) | a quick custom page |
| `ko publish new <dir> --md` | markdown doc site: write `.md`, `README.md` is the nav hub | docs, pitches, proposals |
| `ko publish new <dir> --bare` | just a `CLAUDE.md` of hints — build from scratch | full control |
| `ko publish new <dir> --hono` | a Hono **worker** (API routes, D1/R2, server-side) | apps / backends |
| `… --hono --pin` | the worker, gated behind a generated 6-digit PIN | semi-private share links |

Each folder's `CLAUDE.md` is the **source of truth** for how to work in it — this guide is the map.

## Visuals (in `--md`, and the `public/` doc of `--hono`)

Markdown is the structure; **the agent generates the rich visuals as components** (we don't ship a
diagram DSL). Raw HTML/SVG renders inline (`html: true`), and these are loaded:

- **Tailwind** (Play CDN) — utility classes on any inline HTML.
- **Alpine** — inline interactivity (`x-data`, `@click`, `x-text`). <https://alpinejs.dev/start-here>
- **Diagrams / infographics** → hand-write inline `<svg>`. No library.
- **Data charts** (bar/line/scatter from numbers) → **Chart.js, per-page**:
  `<script src="https://cdn.jsdelivr.net/npm/chart.js/dist/chart.umd.min.js"></script>`.
- Plus: syntax highlighting on code fences, a sidebar TOC, print/PDF CSS, and OG/share-card meta.

## Deploy

- `ko publish <dir>` → `<name>.khalido.dev` (custom domain; first deploy's cert can take ~1 min).
- **Name** defaults to the folder — or its **parent** when the folder is generic (`robot-arm/publish`
  → `robot-arm`). Override with `--name`. It's sticky (lives in the folder's `wrangler.jsonc`).
- **Won't silently overwrite** an existing Worker name (checks the account); `--force` to take over.
- Prints the URL (+ PIN if gated) and a liveness check.

## Setup (Cloudflare — first time, per account)

`ko publish` deploys to *your* Cloudflare account. To set it up (or hand the tool to someone else):

1. **API token** — dashboard → My Profile → API Tokens → Create → start from the
   **"Edit Cloudflare Workers"** template. Because custom domains auto-create DNS, add one
   permission: **Zone → DNS → Edit** for the zone of your publish domain. (Using Workers AI?
   also add **Account → Workers AI → Edit**.) Scope it to your account / that zone.
2. **Account ID** — dashboard → Workers & Pages (right sidebar), or `wrangler whoami`.
3. **Set both** — `KO_CLOUDFLARE_API_TOKEN` + `KO_CLOUDFLARE_ACCOUNT_ID` in env (or under
   `[publish]` in `~/.config/ko/config.toml`). `ko doctor` shows whether they're found.
4. **Domain** — `ko` defaults to `khalido.dev`; a different user sets their own with
   `[publish] domain = "example.com"` in config.toml (the zone must be in your Cloudflare account).
   No domain in your account? You still get a free `*.workers.dev` URL.

**wrangler** itself is pinned repo-local (`npm install` in the kotools repo); `--hono` sites also
`npm install` their own deps automatically on first deploy.

## Not yet (ask Ko to extend the tool, don't work around it)

- Private pages via Cloudflare Access (email OTP) — vs the simple shared-PIN `--pin` above.
- A kotools cloud backend (Worker + D1 + R2) for shared state.
- AI on a published site (a simple `ask(prompt)` helper; Workers AI or OpenRouter) — see [`publish-ai.md`](publish-ai.md).

## References
- Workers static assets — <https://developers.cloudflare.com/workers/static-assets/>
- Hono on Workers — <https://hono.dev/docs/getting-started/cloudflare-workers> · <https://hono.dev/llms.txt>
- Tailwind — <https://tailwindcss.com> · Alpine — <https://alpinejs.dev/start-here> · Chart.js — <https://www.chartjs.org/docs/latest/>
