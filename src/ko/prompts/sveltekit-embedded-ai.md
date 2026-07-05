---
name: sveltekit-embedded-ai
description: How I embed an AI agent in a SvelteKit app — Vercel AI SDK v6, ToolLoopAgent, skills, scripts, croner jobs
---
# SvelteKit embedded AI — kickoff

Assumes the `sveltekit-app` brief's stack is already in place (Svelte 5, Drizzle, Better Auth).
This is the AI layer on top. Production reference: `~/code/everx` (full pattern — skills, scripts,
jobs, MCP audit). Simpler variant: `~/code/chota-bot` (single agent instance, `prepareCall`).
**Read `everx/docs/ai-sdk-v6-reference.md` first** — it's a verified v6 cheat-sheet covering all
the renames that bite when copying from older docs.

## Stack
- `npm install ai @ai-sdk/svelte zod` — that's the full AI layer when using AI Gateway.
  `ai@^6`, `@ai-sdk/svelte@^4`. Direct provider (no gateway): also install `@ai-sdk/google`, etc.
- **AI Gateway** (`createGateway({ apiKey: env.AI_GATEWAY_API_KEY })` from `ai`) — one key, all
  providers. Model ID in `CHAT_MODEL` env var (never hardcoded); fallback chain: DB admin override
  → env → hard-default string. Browse tool-capable models at `https://ai-gateway.vercel.sh/v1/models`.
  Slug format: `google/gemini-3.5-flash`, `anthropic/claude-sonnet-4-5`.

## Agent factory — per-request, not a singleton

`ToolLoopAgent` from `ai`. Build one **per request** so the system prompt carries live context
(user stats, open tasks, today's date). Agent definition files under `chat/agents/` export
`buildInstructions(ctx)` — pure functions composing blocks (role, live snapshot, current user,
tools guide, skills section, response style). One file per agent; shared runtime in `chat/agent.ts`.

```ts
// $lib/server/chat/agent.ts
export async function createMyAgent(user: PromptUser) {
  const instructions = buildInstructions({ user, stats: await getUserStats(user.id), now: new Date() });
  return new ToolLoopAgent({
    model: gateway(await resolveModelId()),
    instructions,
    tools: { ...tools, run_script: makeRunScriptTool({ userId: user.id }) },
    stopWhen: stepCountIs(8),
    onFinish: async ({ totalUsage, steps }) => { /* log model/tokens/cost/tools/ms */ },
  });
}
```

Simpler (chota-bot): one module-level `ToolLoopAgent` + `prepareCall` hook rebuilds the system
prompt each LLM call (`prepareCall: async (settings) => ({ ...settings, instructions: await buildSystemPrompt() })`).

Server route: `createAgentUIStreamResponse({ agent, uiMessages: messages, abortSignal: request.signal })`.
Returns a streaming response. The `id` field from the POST body is the chat_session primary key;
use it to persist turn history + token cost in an `onFinish` callback.

## Svelte client — `Chat` class, not `useChat`

`useChat` is React. Svelte 5 uses the `Chat` **class** from `@ai-sdk/svelte`.

```svelte
<script lang="ts">
  import { Chat } from '@ai-sdk/svelte';
  const chat = new Chat({ id: crypto.randomUUID(), onError: (e) => console.error(e) });
  const isStreaming = $derived(chat.status === 'submitted' || chat.status === 'streaming');
</script>
```

**Don't destructure `chat`** — `const { messages } = chat` breaks Svelte reactivity. Always
`chat.messages`. For a dynamic `id`, pass `get id() { return sessionId }` (getter, not a value).
Message parts: iterate `message.parts` — `part.type === 'text'` for text, `part.type === 'tool-<name>'`
for tool outputs. States: `input-streaming` → `input-available` → `output-available` → `output-error`.
Write one `tool-*.svelte` per tool that renders `part.output` at `output-available`.

## Tools

Shared stateless object in `tools.ts`; one `tool()` per file in `tools/`. Use `inputSchema: z.object({})`
(v6 — NOT `parameters`). `.describe()` on every field. Return small JSON — the model sees it.
`load_skill` lives in the shared object. Write tools that need `userId` are built per-request via
a factory and spread-merged in the agent factory; read-only tools never need the acting user.

## Skills — progressive disclosure

Folder per skill under `chat/skills/`. Each contains a `SKILL.md` with YAML frontmatter
(`name` + `description`) and a markdown body (the playbook). `skills/index.ts` globs them:
`import.meta.glob('./*/SKILL.md', { query: '?raw', eager: true })`. Only descriptions go into the
system prompt via `skillsPromptSection()`. The full body is returned by the `load_skill` tool on
demand — a 2KB playbook costs zero tokens until used. New skill = new folder + `SKILL.md`. No
manual registration.

The `SKILL.md` body documents any scripts the skill calls (see below) and when to use them. The
agent only learns a script exists after loading its skill.

## Scripts — deterministic pipelines

Pre-built pipelines the agent triggers via the `run_script` tool with typed params. More
token-efficient than orchestrating N tool calls; blast radius is controlled.

```ts
// skills/deal-summary/scripts/generate-deal-summary.ts
export default defineScript({
  name: 'generate-deal-summary',
  description: 'Assemble a deal into share-ready markdown. Params: { dealId }.',
  params: z.object({ dealId: z.string() }),
  run: async ({ dealId }, { userId }) => { /* DB queries + render */ return { markdown }; },
});
```

`skills/scripts.ts` globs `./*/scripts/*.ts` (excluding `.test.ts`) — no registration needed.
`makeRunScriptTool({ userId })` validates params via zod before calling `run`; returns the error
string if validation fails so the agent can self-correct.

## Croner jobs — in-process scheduler

`$lib/server/scheduler.ts`: `defineJob(name, cronPattern, fn)`, `bootJobs()`, `stopJobs()`.
Each job is one file in `jobs/` that calls `defineJob` at module top — no exports, no registry:

```ts
// jobs/heartbeat.ts
import { defineJob } from '$lib/server/scheduler';
defineJob('heartbeat', '0 * * * *', () => 'alive');   // return value = summary string
```

`defineJob` wraps croner with `timezone: 'Australia/Sydney'`, `protect:` (logs + drops overlapping
ticks), `catch:` (logs error, process lives). **The wrapper writes the log row per run** — don't
hand-log inside `fn`. Wire boot + shutdown in `hooks.server.ts init`:
`bootJobs()` (lazy `import.meta.glob('./jobs/*.ts')`); `stopJobs()` on SIGTERM + SIGINT.
Croner's timers outlive adapter-node's HTTP close unless stopped — deploys hang without this.
Patterns are 5-field minutes: `'0 5 * * *'` = daily at 05:00. Ref: https://croner.56k.guru/usage/pattern/

## Audit logging

Every tool call → one log row: tool name, args (compact), ok, durationMs. Every job fire → one log
row: name, durationMs, summary, error. Write to the unified `log` table so AI tool calls appear in
the same activity feed as human edits. Per chat turn: log model, tokens, cost, tools used,
duration in the agent's `onFinish`.

## The renames that bite (v5 → v6)

`parameters` → `inputSchema` · `maxSteps: 5` → `stopWhen: stepCountIs(5)` · `maxTokens` →
`maxOutputTokens` · `toDataStreamResponse()` → `toUIMessageStreamResponse()` · `useChat()` →
`Chat` class. Load `everx/docs/ai-sdk-v6-reference.md` before writing any agent code.
AI SDK agents docs: https://ai-sdk.dev/docs/agents/overview
