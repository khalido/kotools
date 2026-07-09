# Changelog

All notable changes to `ko` (kotools) are documented in this file. It is the
canonical release record — GitHub release notes are drawn from it. WORKLOG.md is
the dev journal (why/decisions); this is what changed, release-grained.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/2.0.0/).
Versioning is [CalVer](https://calver.org/), PEP 440-normalized: `YYYY.M.D`
(tag `vYYYY.M.D`; a second release the same day appends `.1`). The version covers
the **CLI contract** (commands, flags, output shapes, exit codes — see AGENTS.md);
breaking changes to it carry a `**Breaking:**` marker inline under their type.
Cut a release with the repo's `/release` skill.

## [Unreleased]

### Added

- `ko yt <url|id>` — YouTube → transcript (free, no key); `-s` = LLM summary,
  `--json` = timed snippets. `ko <youtube-url>` now returns the transcript.
- `ko brief` — morning brief: calendar + unread gmail + HN + HF papers → one
  cheap-model synthesis; `--raw` skips the LLM.
- `ko agent sessions summarize` — LLM title/takeaway/tags per session into a
  SQLite index; `sessions --tag`/`--search` filter on it.
- `ko hn top --now` — the live HN front page.
- Agent tools: `hn_top`/`hn_front_page`, search filters passed through, and every
  tool docstring now states its silent defaults (windows, caps, coverage).

### Fixed

- Errors are clean one-liners everywhere the contract promises: `ko gmail
  view/thread` no longer tracebacks on a bad id, and bogus hn/x/papers inputs no
  longer leak raw HTTP errors or API URLs.
- `ko exa get` exits 1 when every URL fails; `ko hn search` with no results names
  its 12-month window; `ko x lists --json` sorts biggest-first as documented.
- A dozen smaller fixes: calendar multi-day/multi-line display, `cal find
  --calendar`, `ko doctor` completeness, `ko logs` error field, URLs in `--json`.

<!-- reference links start with the first tagged release; oldest version links to its
tag, later ones compare with the one before (keepachangelog.com/en/2.0.0):
[Unreleased]: https://github.com/khalido/kotools/compare/vYYYY.M.D...HEAD
[YYYY.M.D]: https://github.com/khalido/kotools/releases/tag/vYYYY.M.D -->

