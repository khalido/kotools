"""URL → clean markdown. The universal "thing → text" command, fully local/free.

Deterministic routing, no LLM:
- arxiv.org/abs|pdf links → arxiv2md (LaTeX-source markdown beats any PDF parse — measured)
- PDF (by extension or content-type) → download to ~/Downloads (where I'd look
  for it), parse via liteparse; `save=False` parses from temp and discards
- everything else → trafilatura extraction (best-in-benchmark boilerplate removal)
- dead links (HTTP errors, empty extraction) → Wayback Machine fallback;
  `archive=True` skips straight to it

Wayback notes: availability API picks the snapshot; the `id_` URL suffix is
mandatory (raw HTML without archive toolbar/rewritten links). ~60 req/min limit.
"""

from __future__ import annotations

import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

import httpx
import trafilatura

from . import arxiv as arxiv_mod
from . import doc as doc_mod


DOWNLOADS = Path.home() / "Downloads"
UA = {"User-Agent": "ko-tools/0.1 (+https://github.com/khalido/ko-tools)"}

_ARXIV_RE = re.compile(r"arxiv\.org/(?:abs|pdf|html)/(\d{4}\.\d{4,5})(?:v\d+)?")


@dataclass
class Fetched:
    text: str
    source: str  # "live" | "wayback" | "pdf" | "arxiv"
    note: str = ""  # human context for stderr: saved path, wayback timestamp


def _arxiv_id(url: str) -> str | None:
    m = _ARXIV_RE.search(url)
    return m.group(1) if m else None


def _pdf_name(url: str) -> str:
    stem = Path(httpx.URL(url).path).name or "download"
    stem = re.sub(r"[^A-Za-z0-9._-]", "_", stem)
    return stem if stem.lower().endswith(".pdf") else f"{stem}.pdf"


def _extract(html: str, url: str) -> str | None:
    return trafilatura.extract(
        html, url=url, output_format="markdown", include_links=True, include_tables=True
    )


def _wayback(url: str, date: str | None = None) -> Fetched:
    # query built by hand: the availability API chokes on a percent-encoded
    # url= param (httpx encodes, curl doesn't — verified 2026-06-13)
    query = f"url={url}" + (f"&timestamp={date}" if date else "")
    avail = httpx.get(
        f"https://archive.org/wayback/available?{query}", headers=UA, timeout=30
    )
    avail.raise_for_status()
    closest = (avail.json().get("archived_snapshots") or {}).get("closest") or {}
    if closest.get("status") != "200":
        raise RuntimeError(f"no Wayback capture for {url}")
    ts = closest["timestamp"]
    # id_ suffix = raw original HTML, no archive toolbar / rewritten links
    raw = httpx.get(
        f"https://web.archive.org/web/{ts}id_/{url}",
        headers=UA,
        timeout=30,
        follow_redirects=True,
    )
    raw.raise_for_status()
    text = _extract(raw.text, url)
    if not text:
        raise RuntimeError(f"Wayback snapshot of {url} yielded no extractable content")
    return Fetched(text=text, source="wayback", note=f"wayback snapshot {ts}")


def _pdf(content: bytes, url: str, save: bool) -> Fetched:
    if save:
        DOWNLOADS.mkdir(parents=True, exist_ok=True)
        path = DOWNLOADS / _pdf_name(url)
        path.write_bytes(content)
        return Fetched(text=doc_mod.parse(path), source="pdf", note=f"saved {path}")
    with tempfile.NamedTemporaryFile(suffix=".pdf") as tmp:
        tmp.write(content)
        tmp.flush()
        return Fetched(text=doc_mod.parse(tmp.name), source="pdf")


def fetch(
    url: str, archive: bool = False, date: str | None = None, save: bool = True
) -> Fetched:
    """One URL → markdown/text, routed by what it is. See module docstring."""
    if arxiv_id := _arxiv_id(url):
        return Fetched(
            text=arxiv_mod.fetch(arxiv_id), source="arxiv", note=f"arxiv {arxiv_id}"
        )
    if archive:
        return _wayback(url, date)

    try:
        resp = httpx.get(url, headers=UA, timeout=30, follow_redirects=True)
        resp.raise_for_status()
    except httpx.HTTPError as e:
        try:
            return _wayback(url, date)  # dead/blocked link → archive fallback
        except (httpx.HTTPError, RuntimeError):
            raise RuntimeError(
                f"{url} is unreachable ({e}) and the Wayback Machine has no usable capture"
            ) from None

    content_type = resp.headers.get("content-type", "")
    if "application/pdf" in content_type or httpx.URL(url).path.lower().endswith(
        ".pdf"
    ):
        return _pdf(resp.content, url, save)

    text = _extract(resp.text, url)
    if not text:
        return _wayback(url, date)  # paywall/empty page → try the archive
    return Fetched(text=text, source="live")
