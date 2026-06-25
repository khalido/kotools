# ko publish — AI for published sites (planned, not built)

> **Status: deferred.** This captures the design we discussed so it's not lost. The published
> sites are gated, low-traffic, personal — so "AI" here means *a simple function the page can
> call*, not an agent platform. **Don't overbuild it.**

## The shape we want

On a `--hono` site, make it trivial for the agent building the site (and the site's frontend) to
do AI: **call one server-side function with a prompt, get a thing back.** Something like:

```ts
// in src/index.ts — the frontend POSTs { prompt }, gets { text } back
app.post("/api/ask", async (c) => {
  const { prompt } = await c.req.json();
  const text = await ask(c.env, prompt);   // <- the one helper the scaffold ships
  return c.json({ text });
});
```

The frontend then just `fetch("/api/ask", {...})`. That's the whole MVP. Anything more agentic
(tools, multi-step) is opt-in and *later* — most of these sites want one shot of an LLM over the
data they already hold (the cached `/api/data`), e.g. "summarise this", "explain this chart".

Gating is free: a `--pin` site already puts `/api/ask` behind the gate (see `docs/publish.md`), so
the LLM isn't open to the public. Keep the prompt server-side; never ship the key to the browser.

## Two backends

**Workers AI — the zero-key default.** No API key at all; it's a binding, billed to the account.
One line in `wrangler.jsonc` and you're done — the simplest possible "AI in a worker", and the
answer to "a key available to all my workers" (there's no key).

```jsonc
// wrangler.jsonc
"ai": { "binding": "AI" }
```
```ts
const r = await c.env.AI.run("@cf/zai-org/glm-5.2", { messages });
```
Tradeoff: limited to Cloudflare-hosted models; quality/latency varies vs. frontier.

**OpenRouter — your usual models, costs one secret.** This is the khalido.dev pattern.

```bash
wrangler secret put OPENROUTER_API_KEY   # encrypted, per-worker, NOT committed
```
`@openrouter/sdk` works on Workers. **Gotcha (hit in khalido.dev):** some SDKs use dynamic
`import()`, which Workers doesn't support — import the concrete provider entrypoint directly
(`@mariozechner/pi-ai/openai-completions`), not the dynamic-loader index.

## The "global key" — where ko helps

Cloudflare has **no account-wide secret** that auto-injects into every worker. So:
- **Workers AI** needs no key → effectively global the moment you add the binding (recommended for simple stuff).
- **OpenRouter** → set the secret per worker. **ko can do this on deploy:** since `ko` already
  holds `OPENROUTER_API_KEY` (env/config), `ko publish` could push it as a `wrangler secret` when a
  site opts in (`[publish] inject_secrets = [...]`, or a `--with-ai` flag). "Set it once in ko,
  every site I publish gets the key" — the global feel, done at the CLI layer.

## Possible scaffold work (when we build it)

1. A `--with-ai` flag (or a CLAUDE.md snippet) that adds the Workers AI binding + the `ask()` helper
   + `/api/ask` route, so an AI site is one flag.
2. `ko publish` injecting `OPENROUTER_API_KEY` as a secret on deploy for OpenRouter sites.
3. A one-paragraph "Add AI" section in the Hono scaffold's CLAUDE.md pointing at both backends.

## Agentic-site ideas (kept simple, behind the gate)

- **Ask-my-data** — a box that answers questions over the page's cached dataset (`/api/data`). RAG-lite, no vector DB for small data.
- **Explain-this-chart** — click a point → a one-paragraph LLM explanation.
- **NL filter** — "2-bed under 500k near the coast" → the worker turns it into a query.
- **Daily digest** — an LLM summary generated once and cached for the day (reuses the `DATA_TTL` cache pattern, `86400`).

## Reference

`~/code/khalido.dev` — a full SvelteKit-on-Workers app using OpenRouter via `@mariozechner/pi-ai`,
key as `env.OPENROUTER_API_KEY`, with the dynamic-import workaround above. Heavier than this
scaffold targets, but it's the proven pattern.
