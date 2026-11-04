# ko publish — guide

`ko publish` scaffolds a site into a folder and deploys it to Cloudflare Workers. One command gets an agent (or me) from nothing to a live URL.

**The loop:** `ko publish new <dir> [pathway]` → edit the files (each scaffold drops a `CLAUDE.md` with that pathway's specifics — *read it*) → `ko publish preview <dir>` to view it locally (wrangler dev, real http) → `ko publish <dir>` → live URL. Re-publishing the same folder overwrites the **same URL**. Preview over `file://` won't work: ES modules and `fetch()` are blocked.

Housekeeping: `ko publish list` shows everything published (TSV: name, url, folder, last published; `--cf` also appends account Workers this machine doesn't know). `ko publish rm <name>` deletes the Worker from Cloudflare and forgets it locally. Publishing an md/hono site also generates an agent-facing `llms.txt` index (plus a `_headers` charset file) from the pages.

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

Markdown is the structure; the agent generates the visuals as components. There is no diagram DSL. Raw HTML/SVG renders inline (`html: true`), and these are loaded:

- **Tailwind** (Play CDN) — utility classes on any inline HTML.
- **Alpine** — inline interactivity (`x-data`, `@click`, `x-text`). <https://alpinejs.dev/start-here>
- **Diagrams / infographics** → hand-write inline `<svg>`. No library.
- **Data charts** (bar/line/scatter from numbers) → **Chart.js, per page**: `<script src="https://cdn.jsdelivr.net/npm/chart.js/dist/chart.umd.min.js"></script>`.
- Also built in: syntax highlighting on code fences, a sidebar TOC, print/PDF CSS, and OG/share-card meta.

## Deploy

- `ko publish <dir>` → `<name>.khalido.dev` (custom domain; first deploy's cert can take ~1 min).
- **Name** defaults to the folder, or its **parent** when the folder is generic (`robot-arm/publish` → `robot-arm`). Override with `--name`. It sticks (it lives in the folder's `wrangler.jsonc`).
- **Existing Worker names are never overwritten silently.** Deploy checks the account; `--force` takes the name over.
- On a gated `--hono` site, `--pin new` rotates the PIN (or `--pin <value>` sets one).
- Prints the URL (+ PIN if gated) and a liveness check.

## Setup (Cloudflare — first time, per account)

`ko publish` deploys to your own Cloudflare account. First-time setup, or handing the tool to someone else:

1. **API token** — dashboard → My Profile → API Tokens → Create → start from the **"Edit Cloudflare Workers"** template. Custom domains auto-create DNS, so add **Zone → DNS → Edit** for the zone of your publish domain. (Using Workers AI? Also add **Account → Workers AI → Edit**.) Scope it to your account and that zone.
2. **Account ID** — dashboard → Workers & Pages (right sidebar), or `wrangler whoami`.
3. **Set both** — `KO_CLOUDFLARE_API_TOKEN` and `KO_CLOUDFLARE_ACCOUNT_ID` in env, or under `[publish]` in `~/.config/ko/config.toml`. Wrangler's own `CLOUDFLARE_*` vars work as a fallback. `ko doctor` shows whether they're found.
4. **Domain** — `ko` defaults to `khalido.dev`. Set your own with `[publish] domain = "example.com"` in config.toml (the zone must be in your Cloudflare account). With no domain, you get a free `*.workers.dev` URL.

**wrangler** is pinned repo-local (`npm install` in the kotools repo). `--hono` sites `npm install` their own deps on first deploy.

## Not yet (ask Ko to extend the tool; don't work around it)

- Private pages beyond the shared `--pin`. Two options: Cloudflare Access (email OTP), or (scoped 2026-07-16) **better-auth Google SSO on `--hono` sites**. better-auth is TS-native and mounts on Hono in two pieces: `app.on(["POST","GET"], "/api/auth/*", c => auth.handler(c.req.raw))` plus a session middleware that sets `c.get("user")`. Google comes via `socialProviders.google` (client id/secret; callback `<site>/api/auth/callback/google`), and the sqlite adapter should ride D1. You get a real login instead of Access's per-page OTP friction. Cost: a Google OAuth client and a D1 binding per site, and email-level allow-lists need a hook (the `hd` domain param only covers Workspace). Docs: <https://better-auth.com/llms.txt>.
- A kotools cloud backend (Worker + D1 + R2) for shared state.
- AI on a published site (a simple `ask(prompt)` helper; Workers AI or OpenRouter) — see [`publish-ai.md`](publish-ai.md).

## References
- Workers static assets — <https://developers.cloudflare.com/workers/static-assets/>
- Hono on Workers — <https://hono.dev/docs/getting-started/cloudflare-workers> · <https://hono.dev/llms.txt>
- Tailwind — <https://tailwindcss.com> · Alpine — <https://alpinejs.dev/start-here> · Chart.js — <https://www.chartjs.org/docs/latest/>
