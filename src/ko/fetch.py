"""URL → clean markdown. The universal "thing → text" command, fully local/free.

Deterministic routing, no LLM:
- arxiv.org/abs|pdf links → arxiv2md (LaTeX-source markdown beats any PDF parse — measured)
- reddit thread → the same path on reddit.synth.download, a third-party public mirror
  (direct reddit fetching fails every method we tried; the mirror extracts cleanly via
  trafilatura); if the mirror is dead/empty we recover via Wayback of the ORIGINAL url
- wikipedia.org/wiki/<Title> → MediaWiki's own plaintext extract API, not trafilatura:
  the generic HTML path round-trips every wikilink as a full markdown link, ~4x the
  tokens for the same read (measured: BYD Auto, 81k vs 24k); the extract API returns
  clean prose with `== Section ==` headers but NO links at all (confirmed empty on
  Cobra) and no infobox table — a real trade for anything that follows links or wants
  the infobox's structured facts; the old link-preserving markdown is still one call
  away, unrouted (`_extract(httpx.get(url).text, url)`), just not the wiki default
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
from urllib.parse import unquote

import httpx
import trafilatura

from . import arxiv as arxiv_mod
from . import doc as doc_mod
from . import yt as yt_mod

DOWNLOADS = Path.home() / "Downloads"
UA = {"User-Agent": "kotools/0.1 (+https://github.com/khalido/kotools)"}

# /abs and /html → arxiv2md (LaTeX/HTML markdown beats a PDF parse — when it renders).
# /pdf is deliberately excluded: those fall through to the PDF download path (full text),
# because arxiv2md only scrapes the abstract page and returns a near-empty scaffold for
# papers with no HTML render.
_ARXIV_RE = re.compile(r"arxiv\.org/(?:abs|html)/(\d{4}\.\d{4,5})(?:v\d+)?")
# Below this, arxiv2md's output is just a scaffold (no HTML render) → fall back to the PDF.
_ARXIV_MIN_CHARS = 2000
# DOI links (doi.org URLs, doi: prefix, or a bare DOI) — publisher landing pages 403
# or extract to nothing, so resolve the open-access copy via OpenAlex first.
# [^\s?#] stops before query strings/fragments (?utm=… isn't part of the DOI).
_DOI_RE = re.compile(r"(?:doi\.org/|doi:|^)(10\.\d{4,}/[^\s?#]+)", re.IGNORECASE)
# YouTube URLs — routed to the transcript path. We check the domain early and deterministically.
# Deliberately fails loud (no trafilatura fallback) when no transcript exists — the description
# page is less useful than an honest error.
_YT_RE = re.compile(
    r"(?:https?://)?(?:www\.)?"
    r"(?:youtube\.com/(?:watch|shorts|live|embed|v/)|youtu\.be/)"
)
# Reddit web hosts we rewrite to the mirror. NOT redd.it short links or /s/ share links:
# both need a redirect resolve against reddit (which is exactly what fails), so they fall
# through to the normal path instead.
_REDDIT_HOSTS = frozenset(
    {"reddit.com", "www.reddit.com", "old.reddit.com", "np.reddit.com", "m.reddit.com"}
)
_REDDIT_MIRROR = "reddit.synth.download"
# The common ENGLISH namespaces only — "X talk:" variants and non-English prefixes
# (Discussion:, Diskussion:) slip through to the extract API, where extracts still work,
# just with the links-stripped trade. An exhaustive guard needs a per-language table.
_WIKIPEDIA_NAMESPACES = (
    "File:", "Category:", "Talk:", "User:", "Wikipedia:",
    "Template:", "Portal:", "Help:", "Draft:", "Module:", "Special:",
)
# Language subdomains run 2-12 chars with hyphens: en, simple, nds-nl, zh-min-nan.
_WIKIPEDIA_LANG_RE = re.compile(r"[a-z][a-z-]{0,10}[a-z]")


def _wikipedia_ref(url: str) -> tuple[str, str] | None:
    """(lang, raw_title) when the URL's HOST is a wikipedia article host — `<lang>.wikipedia.org`
    or mobile `<lang>.m.wikipedia.org` — and the path is /wiki/<Title>; else None. Host-parsed,
    not regexed over the whole URL: the old `[a-z]{2,3}` regex captured the TAIL of longer
    subdomains (nds-nl → the Dutch article, silently wrong) and, unanchored, hijacked any URL
    merely *containing* a wikipedia URL (example.com/r?u=en.wikipedia.org/wiki/Dog)."""
    try:
        u = httpx.URL(url if "://" in url else f"https://{url}")
    except httpx.InvalidURL:
        return None
    labels = u.host.lower().split(".")
    if labels[-2:] != ["wikipedia", "org"]:
        return None
    labels = labels[:-2]
    if labels and labels[-1] == "m":  # mobile share URLs: en.m.wikipedia.org
        labels = labels[:-1]
    if len(labels) != 1 or labels[0] == "www" or not _WIKIPEDIA_LANG_RE.fullmatch(labels[0]):
        return None
    if not u.path.startswith("/wiki/"):
        return None
    title = u.path[len("/wiki/") :]
    return (labels[0], title) if title else None


@dataclass
class Fetched:
    text: str
    source: str  # "live" | "wayback" | "pdf" | "arxiv"
    note: str = ""  # human context for stderr: saved path, wayback timestamp


def _arxiv_id(url: str) -> str | None:
    m = _ARXIV_RE.search(url)
    return m.group(1) if m else None


def _reddit_mirror(url: str) -> str | None:
    """A reddit thread URL → the same path/query on the public mirror reddit.synth.download,
    else None (any non-reddit url is returned unchanged by the caller). Straight host swap.
    Skips redd.it short links (host doesn't match) and /s/ share links, which need reddit's
    own redirect to resolve — the very thing that fails — so they take the normal path."""
    try:
        u = httpx.URL(url)
    except httpx.InvalidURL:
        return None
    if u.host.lower() not in _REDDIT_HOSTS:
        return None
    if "s" in u.path.strip("/").split("/"):  # /r/<sub>/s/<code> share link
        return None
    return str(u.copy_with(host=_REDDIT_MIRROR))


def _wikipedia_extract(lang: str, title: str) -> str | None:
    """MediaWiki's plaintext extract for one article title, or None on a missing/empty page
    (redirects resolve server-side via `redirects=1`) — either way the caller falls through
    to the generic path rather than erroring, since the URL was a real webpage regardless."""
    try:
        resp = httpx.get(
            f"https://{lang}.wikipedia.org/w/api.php",
            params={
                "action": "query",
                "prop": "extracts",
                "explaintext": 1,
                "redirects": 1,
                "titles": title,
                "format": "json",
            },
            headers=UA,
            timeout=30,
        )
        resp.raise_for_status()
        page = next(iter(resp.json()["query"]["pages"].values()))
    except (httpx.HTTPError, KeyError, ValueError, StopIteration):
        return None
    return page.get("extract") or None


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
    # YouTube URLs → transcript. Must be early and deterministic: no fallback to trafilatura
    # (the description page is worse than an honest error when captions are disabled).
    if _YT_RE.match(url):
        try:
            vid = yt_mod.video_id(url)
            text = yt_mod.transcript(vid)
        except (yt_mod.YtError, ValueError) as e:  # ValueError: yt-looking URL, no id in it
            raise RuntimeError(str(e)) from None
        # Token count is left to the CLI banner (_fetch_banner) so every source reports it
        # the same way — don't duplicate it in the note.
        return Fetched(text=text, source="yt", note=f"youtube transcript {vid}")
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
    if doi_m := _DOI_RE.search(url):
        # try the OA copy (OpenAlex = Unpaywall's data) before the publisher's landing page
        doi = doi_m.group(1)
        from . import papers as papers_mod

        try:
            oa = papers_mod.oa_url(doi)
        except Exception:
            oa = ""
        if oa and oa != url:
            try:
                r = fetch(oa, save=save)
                r.note = f"doi:{doi} → {oa}" + (f"; {r.note}" if r.note else "")
                return r
            except Exception:
                pass  # OA copy bot-blocked or dead → fall through to the landing page

    if mirror := _reddit_mirror(url):
        # Why a mirror (field-verified 2026-08, don't re-test): direct fetch → blocked, .json
        # API → HTML interstitial, old.reddit + browser UA → "Welcome to Reddit", headless
        # browser → JS network-security challenge, Exa contents → empty. Exa doesn't index
        # reddit.com at all (--domains reddit.com finds nothing) — an unfiltered Exa search is
        # how the mirror surfaced. Durable path if the mirror dies for good: Reddit's OAuth API.
        try:
            resp = httpx.get(mirror, headers=UA, timeout=30, follow_redirects=True)
            resp.raise_for_status()
            text = _extract(resp.text, mirror)
        except httpx.HTTPError:
            text = None
        if text:
            return Fetched(text=text, source="reddit-mirror", note=f"reddit → {mirror}")
        # Mirror dead/empty — someone else's infrastructure, so say so LOUDLY (a silent
        # zero-word result reads as an empty page) and recover via Wayback of the ORIGINAL url.
        try:
            r = _wayback(url, date)
            r.note = f"reddit mirror returned no content → {r.note or 'wayback'}"
            return r
        except Exception as e:
            raise RuntimeError(
                f"reddit mirror ({_REDDIT_MIRROR}) returned no content, and Wayback has no "
                f"capture of {url} — try Reddit's official OAuth API"
            ) from e

    if wiki_ref := _wikipedia_ref(url):
        lang, raw_title = wiki_ref
        title = unquote(raw_title).replace("_", " ")
        if not title.startswith(_WIKIPEDIA_NAMESPACES):
            text = _wikipedia_extract(lang, title)
            if text:
                return Fetched(text=text, source="live", note=f"wikipedia extract ({lang})")
            # missing/empty extract (rare) — fall through, the generic path still works

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
