---
name: summarize
description: Default prompt for `ko sum` — strip fluff, keep the author's intent, opinions and facts
---
Summarize the input as tight markdown.

Keep: the author's actual claims, opinions, numbers, names, caveats and recommendations —
in their voice, not corporate paraphrase. If they hedge, hedge; if they're blunt, be blunt.

Cut: throat-clearing, restated context, marketing language, anything a busy reader already
knows.

Format: one-line TL;DR first. Then short bullets, or a few short paragraphs if the input is
narrative. Use the input's own key terms. No preamble ("this article discusses"), no closing
recap. Length scales with substance, not input length — a thin listicle can be three lines.
