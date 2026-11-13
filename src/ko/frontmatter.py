"""YAML front matter (`---` … `---` at the top of a markdown file), for `ko pdf`, `ko publish`
and `ko prompt`. One parser so the three agree. Fetched web pages don't use it yet:
`fetch._text_title` reads only a title from them."""

from __future__ import annotations

import re
import sys

import yaml

_BLOCK = re.compile(r"\A﻿?---[ \t]*\r?\n(.*?)\r?\n?^---[ \t]*(?:\r?\n|\Z)", re.DOTALL | re.MULTILINE)


# An unquoted value of a top-level key with " #" in it: YAML reads the rest as a comment and
# cuts the value short. The key must start at column 0; its value may start on the next line.
# A `|` or `>` block can't match: its indicator sits where the value would have to start.
_HASH_IN_VALUE = re.compile(r"^[^#\s'\"][^:]*:\s+[^'\"\s#][^\n]*\s#", re.MULTILINE)


def split(text: str, source: str = "", *, warn: bool = True) -> tuple[dict[str, str], str]:
    """(front matter, body). Keys lowercased; values stay exactly as written (YAML's BaseLoader:
    no yes→True, 0123→83 or 12:30→750), lists joined as "a, b". No block, or a `---` pair that
    isn't key: value front matter (two horizontal rules), means ({}, text). Bad YAML, or an
    unquoted " #" that YAML would cut off as a comment, or a nested value (key: value pairs
    or lists under a key), drops the block (no metadata, body kept)
    with a note on stderr naming `source` and the line: the content still comes back, and the
    note says what to fix. `warn=False` skips the note (shell completion)."""
    m = _BLOCK.match(text)
    if not m:
        return {}, text
    block, body = m.group(1), text[m.end() :].lstrip("\r\n")
    if not block.strip():
        return {}, body
    try:
        data = yaml.load(block, Loader=yaml.BaseLoader)  # safe: BaseLoader builds only str/list/dict
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)  # line: +1 for 0-based, +1 for the opening ---
        return _skip(source, mark.line + 2 if mark else None, getattr(e, "problem", None) or str(e), body, warn)
    if not isinstance(data, dict):
        return {}, text
    if h := _HASH_IN_VALUE.search(block):
        line = block.count("\n", 0, h.end()) + 2  # the line with the "#"
        return _skip(source, line, '" #" in a value starts a YAML comment', body, warn)
    for k, v in data.items():
        if isinstance(v, dict) or (isinstance(v, list) and any(not isinstance(x, str) for x in v)):
            fix = f"give {k} a plain value or a list of plain values"
            return _skip(source, None, f"{k} holds nested values", body, warn, fix=fix)
    meta = {str(k).lower(): ", ".join(map(str, v)) if isinstance(v, list) else str(v) for k, v in data.items()}
    return meta, body


def _skip(
    source: str, line: int | None, what: str, body: str, warn: bool,
    fix: str = 'quote the value, e.g. title: "Phase 2: costs"',
) -> tuple[dict[str, str], str]:
    if warn:
        where = f" line {line}" if line else ""
        print(f"skipped front matter: {source or 'front matter'}{where}: {what}; {fix}", file=sys.stderr)
    return {}, body
