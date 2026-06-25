"""URL → clean markdown. The universal "thing → text" command, fully local/free.

Deterministic routing, no LLM:
- arxiv.org/abs|pdf links → arxiv2md (LaTeX-source markdown beats any PDF parse — measured)
- PDF (by extension or content-type) → download to ~/Downloads (where I'd look
  for it), parse via liteparse; `save=False` parses from temp and discards
- everything else → trafilatura extraction (best-in-benchmark boilerplate removal)
- dead link (HTTP error) → Wayback Machine (the page is gone, archive it)
- 200 but empty extraction (paywall) → archive.today first (JS-rendered capture
  beats Wayback on hard paywalls), then Wayback

Force a source: `archive=True` (Wayback), `archive_is=True` (archive.today).
Wayback notes: availability API picks the snapshot; the `id_` URL suffix is
mandatory (raw HTML without archive toolbar/rewritten links). ~60 req/min limit.
archive.today gates datacenter IPs behind a CAPTCHA — best-effort, residential-only.
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

# /abs and /html → arxiv2md (LaTeX/HTML markdown beats a PDF parse — when it renders).
# /pdf is deliberately excluded: those fall through to the PDF download path (full text),
# because arxiv2md only scrapes the abstract page and returns a near-empty scaffold for
# papers with no HTML render.
_ARXIV_RE = re.compile(r"arxiv\.org/(?:abs|html)/(\d{4}\.\d{4,5})(?:v\d+)?")
# Below this, arxiv2md's output is just a scaffold (no HTML render) → fall back to the PDF.
_ARXIV_MIN_CHARS = 2000


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


def _archive_is(url: str) -> Fetched:
    """Latest archive.today snapshot → extracted text. Best-effort.

    archive.is renders JavaScript at capture time, so it catches hard paywalls
    (SMH, NYT, FT) that the Wayback Machine misses. The catch: it gates
    automated clients behind a CAPTCHA/429 from datacenter IPs — works from a
    normal residential connection, may fail from a VM/server. We fail loud so
    the caller can fall back to Wayback.
    """
    # /newest/ redirects through the mirror chain to the most recent capture
    resp = httpx.get(
        f"https://archive.ph/newest/{url}",
        headers=UA,
        timeout=30,
        follow_redirects=True,
    )
    if resp.status_code == 429 or "captcha" in resp.text[:2000].lower():
        raise RuntimeError(
            "archive.today blocked this request (CAPTCHA/rate-limit) — "
            "common from datacenter IPs; try from a residential connection"
        )
    resp.raise_for_status()
    text = _extract(resp.text, url)
    if not text:
        raise RuntimeError(f"no archive.today capture for {url}")
    return Fetched(
        text=text, source="archive.is", note=f"archive.today snapshot ({resp.url})"
    )


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


def _paywall_fallback(url: str, date: str | None) -> Fetched:
    """A 200 page that extracted to nothing is usually a paywall. archive.today
    (JS-rendered capture) beats Wayback here, so try it first, then Wayback."""
    try:
        return _archive_is(url)
    except (httpx.HTTPError, RuntimeError):
        return _wayback(url, date)


def fetch(
    url: str,
    archive: bool = False,
    archive_is: bool = False,
    date: str | None = None,
    save: bool = True,
) -> Fetched:
    """One URL → markdown/text, routed by what it is. See module docstring."""
    if arxiv_id := _arxiv_id(url):
        md = arxiv_mod.fetch(arxiv_id)
        if len(md) >= _ARXIV_MIN_CHARS:  # arxiv2md got a real render
            return Fetched(text=md, source="arxiv", note=f"arxiv {arxiv_id}")
        # only a scaffold came back (this paper has no HTML render) → parse the PDF instead
        try:
            resp = httpx.get(
                f"https://arxiv.org/pdf/{arxiv_id}", headers=UA, timeout=30, follow_redirects=True
            )
            resp.raise_for_status()
            return _pdf(resp.content, f"https://arxiv.org/pdf/{arxiv_id}", save)
        except httpx.HTTPError:
            return Fetched(text=md, source="arxiv", note=f"arxiv {arxiv_id} (scaffold; PDF unavailable)")
    if archive_is:
        return _archive_is(url)
    if archive:
        return _wayback(url, date)

    try:
        # ask for markdown — agent-optimized servers (e.g. Cloudflare's markdown-for-agents)
        # do content negotiation and hand back clean markdown; others ignore it and send HTML.
        resp = httpx.get(
            url,
            headers={**UA, "Accept": "text/markdown, text/html;q=0.9"},
            timeout=30,
            follow_redirects=True,
        )
        resp.raise_for_status()
    except httpx.HTTPError as e:
        # dead link → page is gone → Wayback is the historical archive (not paywall)
        try:
            return _wayback(url, date)
        except (httpx.HTTPError, RuntimeError):
            raise RuntimeError(
                f"{url} is unreachable ({e}) and the Wayback Machine has no usable capture"
            ) from None

    content_type = resp.headers.get("content-type", "")
    if "text/markdown" in content_type:  # the server already gave us clean markdown — use it
        tokens = resp.headers.get("x-markdown-tokens")
        note = f"text/markdown ({tokens} tokens)" if tokens else "text/markdown"
        return Fetched(text=resp.text, source="live", note=note)
    if "application/pdf" in content_type or httpx.URL(url).path.lower().endswith(
        ".pdf"
    ):
        return _pdf(resp.content, url, save)

    text = _extract(resp.text, url)
    if not text:
        return _paywall_fallback(url, date)  # 200 but empty → likely paywall
    return Fetched(text=text, source="live")
