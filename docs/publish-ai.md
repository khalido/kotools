# ko publish — AI for published sites (planned, not built)

> **Status: deferred.** Nothing here is in `ko publish` yet (no `ask()` helper, `--with-ai` flag or secret injection in `src/ko/publish.py`). Published sites are gated, low-traffic and personal, so "AI" means *a simple function the page can call*, not an agent platform. **Don't overbuild it.**

## The shape we want

On a `--hono` site, the agent building the site (and the site's frontend) calls one server-side function with a prompt and gets a result back:

```ts
// in src/index.ts — the frontend POSTs { prompt }, gets { text } back
app.post("/api/ask", async (c) => {
  const { prompt } = await c.req.json();
  const text = await ask(c.env, prompt);   // <- the one helper the scaffold ships
  return c.json({ text });
});
```

The frontend then calls `fetch("/api/ask", {...})`. That is the whole MVP. Anything more agentic (tools, multi-step) is opt-in and later. Most of these sites want one LLM shot over data they already hold (the cached `/api/data`): "summarise this", "explain this chart".

Gating is free. A `--pin` site already puts `/api/ask` behind the gate (see [`publish.md`](publish.md)), so the LLM isn't open to the public. Keep the prompt server-side and never ship the key to the browser.

## Two backends

**Workers AI — the zero-key default.** No API key: it's a binding, billed to the account. One line in `wrangler.jsonc` and it works. It's also the answer to "a key available to all my workers", because there is no key.

```jsonc
// wrangler.jsonc
"ai": { "binding": "AI" }
```
```ts
const r = await c.env.AI.run("@cf/zai-org/glm-5.2", { messages });
```
Tradeoff: Cloudflare-hosted models only; quality and latency vary against frontier models.

**OpenRouter — your usual models, costs one secret.** This is the khalido.dev pattern.

```bash
wrangler secret put OPENROUTER_API_KEY   # encrypted, per-worker, NOT committed
```
`@openrouter/sdk` works on Workers. **Gotcha (hit in khalido.dev):** some SDKs use dynamic `import()`, which Workers doesn't support. Import the concrete provider entrypoint directly (`@mariozechner/pi-ai/openai-completions`), not the dynamic-loader index.

## The "global key" — where ko helps

Cloudflare has **no account-wide secret** that auto-injects into every worker. So:
- **Workers AI** needs no key, so it is effectively global once you add the binding. Recommended for simple stuff.
- **OpenRouter** needs the secret per worker. **ko can set it on deploy.** It already holds `OPENROUTER_API_KEY` (env/config), so `ko publish` could push it as a `wrangler secret` when a site opts in (`[publish] inject_secrets = [...]`, or a `--with-ai` flag). Set it once in ko and every site gets the key, done at the CLI layer.

## Possible scaffold work

1. A `--with-ai` flag (or a CLAUDE.md snippet) that adds the Workers AI binding, the `ask()` helper and the `/api/ask` route, so an AI site is one flag.
2. `ko publish` injecting `OPENROUTER_API_KEY` as a secret on deploy for OpenRouter sites.
3. A one-paragraph "Add AI" section in the Hono scaffold's CLAUDE.md pointing at both backends.

## Agentic-site ideas (kept simple, behind the gate)

- **Ask-my-data** — a box that answers questions over the page's cached dataset (`/api/data`). RAG-lite, no vector DB for small data.
- **Explain-this-chart** — click a point, get a one-paragraph LLM explanation.
- **NL filter** — "2-bed under 500k near the coast" becomes a query in the worker.
- **Daily digest** — an LLM summary generated once and cached for the day (reuses the scaffold's `DATA_TTL` cache pattern; `86400`).

## Reference

`~/code/khalido.dev` — a full SvelteKit-on-Workers app using OpenRouter via `@mariozechner/pi-ai`, with the key as `env.OPENROUTER_API_KEY` and the dynamic-import workaround above. Heavier than this scaffold targets, but it's the proven pattern.
