"""YAML front matter (`---` … `---` at the top of a markdown file), for `ko pdf`, `ko publish`
and `ko prompt`. One parser so the three agree. Fetched web pages don't use it: their front
matter is untrusted, and `fetch._text_title` reads only a title and never raises."""

from __future__ import annotations

import re

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


def split(text: str, source: str = "") -> tuple[dict[str, str], str]:
    """(front matter, body). Keys lowercased; values stay exactly as written (YAML's BaseLoader:
    no yes→True, 0123→83 or 12:30→750), lists joined as "a, b". No block, or a `---` pair that
    isn't key: value front matter (two horizontal rules), means ({}, text). Bad YAML, or an
    unquoted " #" that YAML would cut off as a comment, raises FrontMatterError naming `source`
    and the line, rather than quietly dropping or shortening a value."""
    m = _BLOCK.match(text)
    if not m:
        return {}, text
    block = m.group(1)
    if not block.strip():
        return {}, text[m.end() :].lstrip("\r\n")
    try:
        data = yaml.load(block, Loader=yaml.BaseLoader)  # safe: BaseLoader builds only str/list/dict
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        # +1 for 0-based, +1 for the opening ---
        raise _bad(source, mark.line + 2 if mark else None, getattr(e, "problem", None) or str(e)) from None
    if not isinstance(data, dict):
        return {}, text
    if h := _HASH_IN_VALUE.search(block):
        raise _bad(source, block.count("\n", 0, h.start()) + 2, '" #" in a value starts a YAML comment')
    meta = {str(k).lower(): ", ".join(map(str, v)) if isinstance(v, list) else str(v) for k, v in data.items()}
    return meta, text[m.end() :].lstrip("\r\n")
