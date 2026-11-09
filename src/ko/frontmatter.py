"""YAML front matter (`---` … `---` at the top of a markdown file), for `ko pdf`, `ko publish`
and `ko prompt`. One parser so the three agree. Fetched web pages don't use it: their front
matter is untrusted, and `fetch._text_title` reads only a title and never raises."""

from __future__ import annotations

import re
import sys

import yaml

from ko.config import ConfigError

_BLOCK = re.compile(r"\A﻿?---[ \t]*\r?\n(.*?)\r?\n?^---[ \t]*(?:\r?\n|\Z)", re.DOTALL | re.MULTILINE)


class FrontMatterError(ConfigError):
    """A file of yours to fix: every command exits on it as `config`, one line, no traceback."""


# An unquoted value with " #" in it: YAML reads the rest as a comment and cuts the value short.
_HASH_IN_VALUE = re.compile(r"^\s*[^#\s'\"][^:]*:\s+[^'\"\s#][^\n]*\s#", re.MULTILINE)


def _bad(source: str, line: int | None, what: str) -> FrontMatterError:
    where = f" line {line}" if line else ""
    return FrontMatterError(f'{source or "front matter"}{where}: {what}. Quote the value, e.g. title: "Phase 2: costs"')


def split(text: str, source: str = "", *, skip_bad: bool = False) -> tuple[dict[str, str], str]:
    """(front matter, body). Keys lowercased; values stay exactly as written (YAML's BaseLoader:
    no yes→True, 0123→83 or 12:30→750), lists joined as "a, b". No block, or a `---` pair that
    isn't key: value front matter (two horizontal rules), means ({}, text). Bad YAML, or an
    unquoted " #" that YAML would cut off as a comment, raises FrontMatterError naming `source`
    and the line, rather than quietly dropping or shortening a value. `skip_bad`: drop such a
    block instead (no metadata, body kept) with a note on stderr."""
    m = _BLOCK.match(text)
    if not m:
        return {}, text
    block, body = m.group(1), text[m.end() :].lstrip("\r\n")
    if not block.strip():
        return {}, body
    try:
        data = yaml.load(block, Loader=yaml.BaseLoader)  # safe: BaseLoader builds only str/list/dict
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        # +1 for 0-based, +1 for the opening ---
        bad = _bad(source, mark.line + 2 if mark else None, getattr(e, "problem", None) or str(e))
        return _skip_or_raise(bad, body, skip_bad)
    if not isinstance(data, dict):
        return {}, text
    if h := _HASH_IN_VALUE.search(block):
        bad = _bad(source, block.count("\n", 0, h.start()) + 2, '" #" in a value starts a YAML comment')
        return _skip_or_raise(bad, body, skip_bad)
    meta = {str(k).lower(): ", ".join(map(str, v)) if isinstance(v, list) else str(v) for k, v in data.items()}
    return meta, body


def _skip_or_raise(bad: FrontMatterError, body: str, skip_bad: bool) -> tuple[dict[str, str], str]:
    if not skip_bad:
        raise bad
    print(f"skipped front matter: {bad}", file=sys.stderr)
    return {}, body
