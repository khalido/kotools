---
name: sveltekit-app
description: SvelteKit app — stack, structure, and the parts that bite
---
# SvelteKit app — kickoff

How I build a data-heavy SvelteKit app (CRM/dashboard kind). Reference implementation:
`~/code/everx` — read its `docs/` for the full version of anything below.

## Scaffold
Use the `sv create` command in `~/.claude/CLAUDE.md` (TS, Tailwind, Drizzle/SQLite,
Better Auth, Vitest, ESLint/Prettier). Then add: shadcn-svelte, TanStack Table, LayerChart.

## Stack — the picks that matter
- **Svelte 5 runes only** (`$state`/`$derived`/`$effect`/`$props`). No stores for component state.
- **Tailwind 4** via `@tailwindcss/vite` (no PostCSS config). `tw-animate-css`, not `tailwindcss-animate`.
- **shadcn-svelte** for every UI primitive — never hand-roll a `<table>`/button/dialog.
  **Load the `shadcn-svelte` skill** — it carries the component docs + CLI; lean on it, don't
  reinvent. `baseColor: slate`, then override to my palette in oklch CSS vars in `routes/layout.css`.
- **Drizzle + better-sqlite3.** SQLite file, single source of schema.
- **Better Auth** (email+password). Role hierarchy lives in `$lib/auth-roles.ts`, one map.
- **LayerChart v2** for charts (NOT ECharts — legacy). `mode-watcher` for dark mode. Lucide icons.

## Structure
- `src/lib/components/ui/` shadcn (generated — don't edit), `components/data-table/` table wrappers,
  `server/db/` Drizzle, `server/auth.ts`.
- Routes: data loads in `+page.server.ts` (server-first, never client fetch); mutations via
  `/api/*` `+server.ts`. No form actions except login.
- kebab-case files, PascalCase imports, snake_case DB columns (Drizzle maps the boundary).

## The parts that bite
- **`building` guard (critical).** Wrap `db/index.ts` and `auth.ts` init in `if (building) return stub`
  — else `vite build` crashes importing server modules without env vars.
- **Data tables:** TanStack `createSvelteTable`. Two files per table page: `columns.ts` (ColumnDef[]) +
  `+page.svelte`. `DataTableFacetedFilter` for multi-select. Right-rail stats read
  `table.getFilteredRowModel().rows` — no second fetch.
- **Charts:** `Chart.Container` (shadcn) + LayerChart component. Colors from `var(--chart-N)` only,
  never hex. Shape data in the loader, not the component. Guard the empty state.
- **Inline edit:** set `editing=false` before any async, gate with a `saving` flag (Enter+blur double-fire).
- **Prod migrations:** `drizzle-kit migrate`, NOT `push` (push re-diffs and dies on table recreation).

## Deploy — Railway
`adapter-node` + Docker (`node:lts-trixie-slim`, not Alpine — better-sqlite3 needs glibc), SQLite on a
Railway Volume. `start.sh` chowns the volume as root then drops to `node`. `railway.toml`:
`startCommand = ""`, healthcheck `/health` running `SELECT 1`. Full guide: `everx/docs/railway.md`.

## Skills (load these — don't reinvent)
- **`shadcn-svelte`** — components, the CLI, design-system presets. The first thing to load for any UI work.
- **`use-railway`** — deploy/infra. **`code-review`** before commit.

## Docs to load
- LayerChart v2 llms: https://www.layerchart.com/llms.txt (index) · /docs/llms.txt (full).
  NOT `next.layerchart.com` — it still serves 200s, but documents the `next` dist-tag, frozen
  at 2.0.0-next.66 (2026-07-01) while `latest` is 2.2.0 (2026-08-12). Verified 2026-08-20.
- shadcn charts: https://shadcn-svelte.com/charts/
- Context7 for fresh Drizzle / Better Auth docs; pin the Svelte MCP and run svelte-autofixer on components.

## Voice (client-facing copy)
Direct, declarative, concrete. No "leverage", "robust", "seamlessly". Reference: `~/code/khalido.dev`.
