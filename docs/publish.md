# ko publish — design notes

Living doc for the `ko publish` tool: how it works today and where it's heading.
Thinking-aloud roadmap, not a spec. We build a better publish tool over time.

## What it is

Publish a folder to Cloudflare as a static Workers site, get a URL. The published
folder is a **self-describing wrangler project** — it carries its own `wrangler.jsonc`
(name + assets + custom-domain route), so re-publishing is just `wrangler deploy` in
that folder: same name → same Worker → same URL. `ko publish` is a thin orchestrator
over exactly that; you can run `wrangler deploy` by hand and get the identical result.

## How it works today (v1, static)

- `ko publish new <dir>` scaffolds: `index.html` (Tailwind Play CDN + Alpine), `style.css`,
  `CLAUDE.md`, `.gitignore`, and `wrangler.jsonc`.
- `ko publish [dir]` runs `wrangler deploy` inside the folder. Name is sticky via
  `wrangler.jsonc`; an existing config is respected (hand-edit it freely); `--name` rewrites it.
- **wrangler resolution:** `KO_WRANGLER` → repo-local `node_modules/.bin/wrangler` (pinned `^4`)
  → PATH → `npx wrangler@4`. So a pip/uv-installed kotools (no `node_modules`) still works via npx.
- **Settings pickup (no kotools-level wrangler config needed):** domain from `[publish] domain`
  in `~/.config/ko/config.toml`; account auth from `CLOUDFLARE_API_TOKEN` in env; per-site identity
  from that folder's `wrangler.jsonc`. Same whether wrangler is repo-local or npx.

## The artifact-as-subfolder convention

A project folder (e.g. `labs/robot-arm/`) holds research, docs, PDFs. The **publishable
artifact** lives in a *subfolder* — `ko publish new labs/robot-arm/site` — so publishing
doesn't entangle the research. An agent working in the project builds the artifact in that
subfolder, then publishes it (e.g. a public proposal page to send a collaborator).

## Levels (roadmap)

1. **Markdown** (simplest, not built yet): publish a folder of `.md`. One file → render it;
   many → an auto index that links into each. Pairs with Cloudflare's *markdown for agents*
   (agents send `Accept: text/markdown`, Cloudflare serves markdown, ~80% fewer tokens) —
   <https://blog.cloudflare.com/markdown-for-agents/>.
2. **Static** (today): HTML/CSS/JS, Tailwind + Alpine, zero build.
3. **Hono** (backend, `--hono`, deferred): API routes, D1/R2, optional PIN auth. Static via
   `assets = { directory = "public" }` + a worker for the dynamic bits; deploy with `npm run deploy`.

## Hono path (the `--hono` scaffold, deferred)

- Getting started on Workers: <https://hono.dev/docs/getting-started/cloudflare-workers>
- Static assets: `assets = { directory = "public" }` in the wrangler config; worker serves the rest.
- **Basic-auth PIN** (simple gate for a semi-private artifact — e.g. a proposal you only want
  link-holders to see): <https://hono.dev/docs/middleware/builtin/basic-auth>
- Client components beyond Alpine (JSX DOM): <https://hono.dev/docs/guides/jsx-dom>
- Agent-friendly full index: <https://hono.dev/llms.txt>

## Private pages (deferred — two mechanisms, don't conflate)

Want a `ko publish --private` for semi-private artifacts. Two real options:

1. **Cloudflare Access + one-time PIN** (Zero Trust; the robust one). NOT a static shareable
   PIN — you protect the route with an Access *application* + a *policy* that allows specific
   **emails**; a visitor enters their email and Cloudflare emails *them* a fresh 6-digit code.
   Fully automatable on the same account/token (needs **Access: Apps and Policies: Edit** added
   to the token). One-time per-account: enable the OTP identity provider
   (`POST /accounts/{id}/access/identity_providers` `{type: "onetimepin"}` —
   <https://developers.cloudflare.com/cloudflare-one/integrations/identity-providers/one-time-pin/>).
   Then per publish: create an Access app for `<name>.khalido.dev` + a policy allow-listing
   the emails. Good for "only me / people I name." Free up to 50 users.
2. **Static shared PIN via Hono basic-auth** — one code anyone with the link can use. Simpler
   "here's a URL + a PIN, forward it" sharing model, but needs the `--hono` worker path (a
   static site can't gate itself). <https://hono.dev/docs/middleware/builtin/basic-auth>

Sketch: `ko publish --private [--allow you@example.com]` → Access OTP (option 1). A `--pin`
flavor → Hono basic-auth (option 2). Both deferred until the static/md core is settled.

## Open questions / later

- A small set of reusable components (shared CSS/Alpine snippets, or Hono JSX) vs keep-it-blank.
- Teardown: `ko publish rm <name>` (wrangler delete).
- `--temp` ephemeral deploys (`wrangler deploy --temporary`, ~60-min URL, no domain).

## References

- Workers static assets — <https://developers.cloudflare.com/workers/static-assets/>
- Wrangler config — <https://developers.cloudflare.com/workers/wrangler/configuration/>
- Hono on Workers — <https://hono.dev/docs/getting-started/cloudflare-workers>
- Hono llms.txt (agent reference) — <https://hono.dev/llms.txt>
- Markdown for agents — <https://blog.cloudflare.com/markdown-for-agents/>
