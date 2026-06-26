---
name: ko-publish-site
description: How I build a static site published with ko publish — Hono + CDN, no build
---
# ko-publish static site — kickoff

A small published site (landing page, plan, write-up, mini-tool) built with `ko publish`.
No bundler, no framework — a Hono Worker serving a `public/` folder on Cloudflare. Reference:
`~/code/labs/robot-arm`.

## What ko publish gives you
`ko publish new <dir>` scaffolds a Cloudflare Worker (Hono) + `public/` static site.
`ko publish preview <dir>` runs `wrangler dev` (real http — `file://` breaks ES modules + fetch).
`ko publish <dir>` deploys to `<name>.khalido.dev`. Deploy = `npm install` once, then `wrangler deploy`.

## Stack
- **Hono v4** worker (`src/index.ts`), Cloudflare Assets binding for the static files.
- **Tailwind via CDN** (`<script src="https://cdn.tailwindcss.com">`) — no build step.
- **Chart.js via CDN**, per-page, only where a chart is needed (not LayerChart — that's the app stack).
- Vanilla JS for interactivity, in self-contained files loaded with `defer` that mount into a placeholder
  `<div id="...">`. Alpine.js via CDN is fine for light interactivity; skip it if you wrote a component.

## HTML conventions
- Dark by default: `<html lang="en" class="dark">`, body `text-zinc-200 antialiased`, `color-scheme: dark`.
- Layout: `mx-auto max-w-5xl px-5 py-10` (main) / `max-w-3xl` (sub-pages). Base `font-size: 17px`.
- `:root` vars in `style.css`: `--bg #09090b`, `--border` zinc-800, `--panel` zinc-900/40, `--accent` indigo-400.

## Factor shared CSS into style.css
- `.card` = `border border-zinc-800 bg-zinc-900/40 rounded-xl p-4`; `.card-accent` = indigo tint (highlight).
- `.table-wrap` = `overflow-x-auto border border-zinc-800 rounded-xl` — every table goes inside one
  (scrolls on mobile). `<thead class="bg-zinc-900 text-zinc-400">`, `divide-y divide-zinc-800` on tbody.
- `.glow` = indigo box-shadow for a hero/featured panel. `@media print { body { background: #fff } }`.

## Colour + text
- Zinc palette, indigo accent (not blue). Headings `text-white`, body `text-zinc-400`,
  secondary `text-zinc-500`, footnotes `text-zinc-600`. **No over-grey text — zinc-400 is the floor for body.**
- Status chips: `rounded bg-{c}/20 px-1.5 py-0.5 text-xs font-semibold text-{c}-300` (rose/amber/emerald).

## Hono worker
Order in `src/index.ts`: optional PIN gate → your API routes → catch-all `c.env.ASSETS.fetch(c.req.raw)`.
`run_worker_first: true` in `wrangler.jsonc` so the worker sees every request first. PIN gate via a
`KO_PIN` var (cookie, ~30-day) — remove the var to make it public. D1/R2/KV available as bindings.
For API growth use `app.route()` per feature + `@hono/zod-validator`. Hono llms: https://hono.dev/llms.txt

## SEO
`<meta name="description">`, `og:title`/`og:description`/`og:type` on every page. SVG emoji favicon via
`data:image/svg+xml`. Multi-page = plain `<a>` links; sub-pages link back with `← Back`.
