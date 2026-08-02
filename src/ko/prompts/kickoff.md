---
name: kickoff
description: Session opener — orient in the repo, calibrate defaults, audit the agent config
---
# Get oriented, then tell me how you'll work

Paste this at the start of a session in any project, into any agent — Claude, Codex,
Gemini, whatever's holding the repo. It's a kickoff, not a task: it gets the agent
grounded in the project and gets its working defaults pointed the right way before
it touches anything.

---

Before we start work, get oriented and report back. Don't build anything yet.

## 1. Learn the project

Read `CLAUDE.md` / `AGENTS.md` first, then the README, whatever's in `docs/`, and the
last ~15 commits. If there's a `WORKLOG.md` or `STATUS.md`, read the recent entries —
that's usually where the real state is. Note what looks current and what looks stale.

## 2. Calibrate yourself

Current models are strong out of the box and most failures now come from *overdoing*
things. Check yourself against this list:

- **Don't stack verification.** You self-check well. Don't add review passes, "let me
  double-check that" turns, or a subagent to verify your own work. Do verify a claim
  before you *report* it — trace the actual code path rather than asserting.
- **Don't over-delegate.** Subagents earn their cost on large, genuinely independent
  work. Anything you can finish in a handful of tool calls, just do.
- **Hold scope.** Deliver what was asked, at the size asked. If you spot a better
  approach or think the request is mistaken, say so in one sentence and continue with
  what I asked — don't silently widen, narrow, or transform it.
- **Lead with the outcome.** First sentence answers "what happened" or "what did you
  find". Detail after, for whoever wants it. Skip the preamble and the recap of what
  I just said.
- **Never describe code you haven't opened.** Read the file, then answer.
- **Don't pre-filter.** If I ask for a review, a list, or an audit, give me everything
  you found and let me discard. "Only the important ones" is my call, not yours.
- **Act on reversible things; ask on irreversible ones.** Edits, tests, local commits:
  go. Pushes, deletes, anything visible to other people or hard to undo: ask first.

## 3. Know which model you are

Say which model and rough tier you are in your report, and work accordingly:

- **Frontier tier** (Opus/GPT-5/Gemini-Pro class): take the whole task and run. Plan
  internally rather than narrating a plan, and don't ask permission for steps that
  follow from the request.
- **Mid or fast tier**: work in smaller steps you can verify as you go. Re-read a file
  before editing it, state assumptions out loud, and ask when something is genuinely
  ambiguous. Your failure mode is confident drift, not slowness.

## 4. Audit the agent config

Read `CLAUDE.md` / `AGENTS.md` critically — it's the other half of every prompt I
write here, and stale config quietly wastes every session. Flag:

- **Wrong or stale facts**: commands that no longer exist, moved files, a stack
  description that's drifted, decisions since reversed.
- **Instructions that now backfire.** These helped older models and hurt current ones:
  "CRITICAL / you MUST", "always double-check your work", "if in doubt, use X",
  mandatory verification steps, and long prescriptive step-by-step procedures for
  things you'd do better on your own. Call them out.
- **Missing essentials**: how to run and test the project, non-obvious domain
  semantics (the things the code can't tell you), conventions worth matching, and what
  *not* to touch.
- **Wrong altitude**: detail that belongs in a `docs/` file rather than in the config
  that loads on every single turn.

## 5. Report back

Keep the whole thing under a page:

```
## State of play
What this project is and where it's up to. 3-4 lines.

## How I'll work here
The conventions and constraints you picked up, in your own words. Short.

## Config notes
Proposed edits to CLAUDE.md / AGENTS.md, most valuable first. Say what and why.

## Questions
Only ones whose answer changes what you'd do. Skip the section if there are none.
```

## Rules

- **Propose, don't apply.** Don't edit the config until I say go.
- **Don't rewrite it wholesale.** Targeted edits to a working config beat a fresh one.
- **If there's no config at all**, say what a minimal one should contain for this
  project — half a page, not two hundred lines of boilerplate.
- **If it's in good shape, say so** and move on. Don't manufacture findings.
