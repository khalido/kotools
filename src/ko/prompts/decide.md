---
name: decide
description: How to word a `ko decide` question so Jev answers what you meant
---
# Typed decisions with `ko decide`

`ko decide` asks one question about some text and returns a typed answer with probabilities — not prose. The default model is **Jev** (TypeSafe's decision model, through OpenRouter on your existing key): about 100 ms and well under a cent a call. The question is the argument; the text being judged comes from stdin or `-f` (a file, URL or YouTube link, read like `ko llm -f`).

Docs: https://docs.typesafe.ai (start with *How to build with TypeSafe*), and Jev's known weak spots: https://docs.typesafe.ai/model-jaggedness/jev-1.13

## The four kinds of question

| Kind | Flag | You get | Use it for |
|---|---|---|---|
| yes/no | (none) | `0.93` — P(yes) | "is this a paywall page?", "does this email need a reply?" |
| pick one | `--choice a,b,c` | every option with its probability, likeliest first | one label: reply/read/archive/spam |
| scale | `--score l0,l1,l2` | the nearest level + its exact position (`skim 1.02`) | ordered degree: skip < skim < read < study (2–10 levels) |
| tags | `--tags a,b,c` | every tag with its own P(yes) | several may apply: topics, flags |

`--choice` is one answer out of many (probabilities add up to 1). `--tags` is a separate yes/no per tag (they don't). If two of your options can both be true, you want `--tags`.

Give an option its meaning after `=`: `--choice "reply=needs an answer from Ko,read=worth reading later,archive"`. A name alone is fine when it's obvious. Meanings can't contain commas.

## Wording that works

- **Ask one thing.** "Is this a good article?" weighs five things and comes back unsure. Ask three questions (is it about X? is it original? is it long-form?) and combine the numbers yourself.
- **Jev reads literally.** It answers the question you wrote, not the one you meant. Write the exact condition, including the edge case: "Does this email ask Ko to do something by a date?" beats "Is this urgent?".
- **Score levels describe situations, not degrees.** "read=Ko would act on it this week" gives Jev something to match; "read=important" doesn't. Lowest level first.
- **Give it a way out.** If nothing may fit, add an option for that: `--choice "billing,bug,account,none=none of these"`.
- **Send only what the question needs.** Accuracy drops as the text fills with irrelevant detail. Cut the page to the part being judged when you can.
- **Keep numbers, counting and dates in code.** Jev isn't a calculator and reads dates as text. Compute first, then ask about the result.
- **The text is data, not instructions** — but text written to steer the answer can still move it. Don't make a Jev verdict the only guard on anything that matters.

## Reading the numbers

- A yes/no near 0.5 means *unsure*, not *medium*. Pick your own bar per use: acting on its own wants ≥0.9; flagging for a look can take ≥0.6.
- `--json` adds `confidence` (how clear-cut the pick was) and the exact model version that answered. `jev-latest` moves; once you've tuned a threshold, pin it: `-m typesafe:jev-1.13`.
- `-m <any ko llm model>` asks the same question of a language model — you get the bare answer without probabilities. Useful to compare, or when the judgement needs reasoning Jev can't do.

## When not to use it

Anything that has to be *written* (a summary, a reply, an extraction of free text) is `ko llm`. Jev picks, ranks and checks; it doesn't generate.
