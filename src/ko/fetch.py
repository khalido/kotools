"""URL → clean markdown. The universal "thing → text" command, fully local/free.

Deterministic routing, no LLM:
- arxiv.org/abs|pdf links → arxiv2md (LaTeX-source markdown beats any PDF parse — measured)
- reddit thread → the same path on a public mirror, trying a ladder in order
  (safereddit.com → reddit.synth.download); direct reddit fetching fails every
  method we tried (see the field-verified list in the reddit branch) and mirrors
  churn, so the ladder is config-swappable (`[fetch] reddit_mirrors`); if every
  mirror is dead/empty we recover via Wayback of the ORIGINAL url
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
- when the free live fetch fails, the rescuers run IN PARALLEL and the most-preferred
  one that succeeds wins (`_rescue`):
  - refused (401/403/429), a bot challenge, or 200-but-empty (paywall) → a real browser
    (agent-browser, if installed — clears JS challenges, renders SPAs), Exa contents (paid,
    if EXA_API_KEY is set), archive.today (JS-rendered capture, good on hard paywalls),
    Wayback — in that order of preference
  - dead link (404/5xx/timeout) → Wayback (the page is gone, archive it), then Exa
- results cache locally for `[fetch] cache_hours` (default 1); `fresh=True` skips it

The live fetch sends the header bundle a browser sends on a top-level navigation
(Sec-Fetch-*, et al) — a Cloudflare-class wall 403s on their absence, not on the
User-Agent, so ours stays our own. See BROWSER for what that does and doesn't claim.

Force a source: `archive=True` (Wayback), `archive_is=True` (archive.today).
Wayback notes: availability API picks the snapshot; the `id_` URL suffix is
mandatory (raw HTML without archive toolbar/rewritten links). ~60 req/min limit.
archive.today gates datacenter IPs behind a CAPTCHA — best-effort, residential-only.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import queue
import re
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import unquote

import httpx2 as httpx
import trafilatura

from . import arxiv as arxiv_mod
from . import config
from . import doc as doc_mod
from . import yt as yt_mod
from .dirs import cache_dir

DOWNLOADS = Path.home() / "Downloads"
UA = {"User-Agent": "kotools/0.1 (+https://github.com/khalido/kotools)"}
# Bot walls (Cloudflare and friends) don't ask "are you Chrome" — they ask whether the
# request carries the header bundle a click-initiated top-level GET sends, chiefly the
# Sec-Fetch-* family. Measured on drive.com.au 2026-08-19: our own UA + these = 200; that
# same UA without them = 403; a spoofed Chrome UA *without* them still 403 (claiming Chrome
# while sending nothing Chrome sends looks worse, not better); and dropping Sec-Fetch-User
# alone re-breaks it, three paired rounds out of three. So we keep our real UA and send the
# bundle. `Sec-Fetch-User: ?1` does assert a click nobody made — the one false byte here,
# and the load-bearing one. `Sec-Fetch-Site: none` likewise goes stale across redirects (a
# browser flips it to same-origin/cross-site; httpx replays ours verbatim) — harmless so
# far. When walls start pairing this with UA or TLS fingerprints the bundle stops working,
# and the successor is the 403 → archive.today rung below, not a better disguise.
BROWSER = UA | {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-AU,en;q=0.9",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}
# Gone, not refused: the page isn't there, so only an archive can have it.
_GONE_STATUS = frozenset({404, 410})
# Archives are fallbacks, run in parallel with the other rescuers: one slow archive mirror
# must not hold a fetch for 30 s (it did — stackoverflow took 60 s+ to fail).
_ARCHIVE_TIMEOUT = 10
_RESCUE_DEADLINE = 25  # seconds for the whole parallel rescue; the best answer so far wins

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
# The mirror ladder, in try-order. A snapshot, not a promise — probed against a live
# thread 2026-08-31: safereddit.com (a public redlib instance — reddit re-served as
# plain readable HTML, extracts cleanly; the ONLY one alive of 9 probed: catsarch 403s
# us, perennialte is shut down, bloat.cat serves a challenge page, privacydev times
# out) → reddit.synth.download (the spine until that day; 502 then, worked 2026-08 —
# second for when it's back). Instance churn IS the recurring failure mode, so the
# ladder is swappable without a release: `[fetch] reddit_mirrors = "h1,h2"` in
# config.toml or KO_REDDIT_MIRRORS (comma-separated hosts, first wins).
_BAKED_REDDIT_MIRRORS = ("safereddit.com", "reddit.synth.download")


# A "page" that is really a bot check. Only short pages count: an article can mention captchas.
_WALL_PHRASES = (
    "not a robot", "are you a robot", "verify you are human", "verify you are a human",
    "checking your browser", "just a moment", "enable javascript and cookies", "unusual traffic",
    "press & hold", "press and hold", "attention required", "access denied", "captcha",
)
_WALL_MAX_CHARS = 1500


def _walled(text: str) -> bool:
    low = text.lower()
    return len(text) < _WALL_MAX_CHARS and any(p in low for p in _WALL_PHRASES)


def _reddit_check(html: str, url: str) -> None:
    """A thread URL must come back as that thread. A mirror shown an id that lives in another
    subreddit serves that thread instead (r/LocalLLaMA/comments/1ft0000 gave r/Line6Helix), so
    match `/r/<sub>/comments/<id>` in the page; raise naming the real subreddit when it differs."""
    m = re.search(r"/r/([^/]+)/comments/([a-z0-9]+)", httpx.URL(url).path, re.IGNORECASE)
    if not m:
        return  # a subreddit or user page, not a thread
    sub, post = m.groups()
    found = re.findall(rf"/r/([A-Za-z0-9_]+)/comments/{post}\b", html, re.IGNORECASE)
    if any(f.lower() == sub.lower() for f in found):
        return
    if found:
        raise LookupError(f"reddit thread {post} is in r/{found[0]}, not r/{sub} — check the URL")
    raise LookupError(f"the mirror page doesn't show thread {post}")


def _reddit_mirror_hosts() -> tuple[str, ...]:
    raw = config.setting("KO_REDDIT_MIRRORS", "fetch", "reddit_mirrors", "")
    hosts = tuple(h.strip().lower() for h in raw.split(",") if h.strip())
    return hosts or _BAKED_REDDIT_MIRRORS


def _reddit_mirrors(url: str) -> list[str] | None:
    """A reddit thread URL → the same path/query on each mirror host, ladder order,
    else None (any non-reddit url is returned unchanged by the caller). Straight host
    swap per mirror. Skips redd.it short links and /s/ share links, which need reddit's
    own redirect to resolve — the very thing that fails — so they take the normal path."""
    try:
        u = httpx.URL(url)
    except httpx.InvalidURL:
        return None
    if u.host.lower() not in _REDDIT_HOSTS:
        return None
    if "s" in u.path.strip("/").split("/"):  # /r/<sub>/s/<code> share link
        return None
    return [str(u.copy_with(host=h)) for h in _reddit_mirror_hosts()]


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
    source: str  # "live" | "browser" | "exa" | "archive.is" | "wayback" | "pdf" | "arxiv" | …
    note: str = ""  # human context for stderr: saved path, wayback timestamp


def _arxiv_id(url: str) -> str | None:
    m = _ARXIV_RE.search(url)
    return m.group(1) if m else None


# The common ENGLISH namespaces only — "X talk:" variants and non-English prefixes


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
        f"https://archive.org/wayback/available?{query}", headers=UA, timeout=_ARCHIVE_TIMEOUT
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
        timeout=_ARCHIVE_TIMEOUT,
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
        timeout=_ARCHIVE_TIMEOUT,
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


# --- rescuers: what runs when the free, deterministic live fetch didn't get the page ---

_BROWSER_SESSION = "ko-fetch"  # ko's own agent-browser session: warm between fetches, never
# the shared default one (another agent's page), and the place logins will live later
_READY_JS = (
    "document.readyState === 'complete' && "
    "!/just a moment|attention required|checking your browser|verify you are human/i.test(document.title)"
)


def _ab(*args: str, timeout: float) -> dict:
    """One agent-browser call in ko's session → its `data`, or RuntimeError with its error."""
    env = {**os.environ, "AGENT_BROWSER_SESSION": _BROWSER_SESSION}
    r = subprocess.run(
        ["agent-browser", "--json", *args], env=env, capture_output=True, text=True, timeout=timeout,
        check=False,  # failures come back as {"success": false} on stdout
    )
    try:
        out = json.loads(r.stdout)
    except ValueError:
        raise RuntimeError(f"agent-browser {args[0]}: {(r.stderr or r.stdout).strip()[:200]}") from None
    if not out.get("success"):
        raise RuntimeError(f"agent-browser {args[0]}: {out.get('error')}")
    return out.get("data") or {}


def _browser(url: str) -> Fetched:
    """Render the page in a real Chrome (agent-browser) and extract it like a live fetch. Clears
    JS bot challenges ("Just a moment…") and client-rendered pages. One shared tab, so ko
    processes take turns (a file lock) rather than navigating each other's page."""
    lock = cache_dir() / "fetch-browser.lock"
    with open(lock, "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        _ab("open", url, timeout=20)
        _ab("wait", "--fn", _READY_JS, "--timeout", "12000", timeout=15)
        html = _ab("eval", "document.documentElement.outerHTML", timeout=10).get("result") or ""
    text = _extract(html, url)
    if not text:
        raise RuntimeError("rendered page had no extractable content")
    if _walled(text):
        raise RuntimeError("rendered page is a bot check")
    return Fetched(text=text, source="browser", note="rendered in agent-browser")


def _exa(url: str) -> Fetched:
    """Exa's crawler, from its own infrastructure (paid, ~$1 per 1,000 pages)."""
    from . import exa as exa_mod

    text = next(iter(exa_mod.get_contents([url]).texts.values()), "")
    if not text.strip():
        raise RuntimeError("Exa returned no text")
    return Fetched(text=text, source="exa", note="exa contents (paid)")


def _rescuers(url: str, date: str | None, dead: bool) -> list[tuple[str, object]]:
    """(name, fn) in order of preference. A dead page's truth is its archive; a walled page's
    is the live page, so a real browser comes first there."""
    wayback = ("wayback", lambda: _wayback(url, date))
    exa = [("exa", lambda: _exa(url))] if os.environ.get("EXA_API_KEY") else []
    if dead:
        return [wayback, *exa]
    browser = [("browser", lambda: _browser(url))] if shutil.which("agent-browser") else []
    return [*browser, *exa, ("archive.today", lambda: _archive_is(url)), wayback]


def _rescue(url: str, date: str | None, *, dead: bool, why: str) -> Fetched:
    """Run every rescuer at once; return the most-preferred success as soon as everything
    preferred over it has failed, or the best by the deadline. Daemon threads: a hung rescuer
    is abandoned, never waited on at exit."""
    rungs = _rescuers(url, date, dead)
    results: queue.Queue = queue.Queue()
    for name, fn in rungs:
        def run(name=name, fn=fn):
            try:
                results.put((name, fn()))
            except Exception as e:  # each rung's failure is data for the final message
                results.put((name, e))

        threading.Thread(target=run, daemon=True).start()
    done: dict[str, Fetched | Exception] = {}
    deadline = time.monotonic() + _RESCUE_DEADLINE
    while True:
        for name, _ in rungs:
            if name not in done:
                break  # a more-preferred rung is still running — wait for it
            if isinstance(done[name], Fetched):
                return done[name]
        else:
            break  # every rung finished and failed
        left = deadline - time.monotonic()
        if left <= 0:
            break
        try:
            name, value = results.get(timeout=left)
        except queue.Empty:
            break
        done[name] = value
    for name, _ in rungs:  # deadline: the best that did come back
        if isinstance(done.get(name), Fetched):
            return done[name]
    tried = "; ".join(
        f"{name}: {done.get(name, 'timed out')}" for name, _ in rungs
    )
    raise RuntimeError(f"{url}: {why}, and no fallback got it ({tried})")


def _cache_path(url: str, archive: bool, archive_is: bool, date: str | None) -> Path:
    key = hashlib.sha256(json.dumps([url, archive, archive_is, date]).encode()).hexdigest()[:32]
    return cache_dir() / "fetch" / f"{key}.json"


def fetch(
    url: str,
    archive: bool = False,
    archive_is: bool = False,
    date: str | None = None,
    save: bool = True,
    fresh: bool = False,
) -> Fetched:
    """One URL → markdown/text, routed by what it is, from the local cache when a fetch of it is
    younger than `[fetch] cache_hours` (default 1). See module docstring."""
    path = _cache_path(url, archive, archive_is, date)
    ttl = float(config.get("fetch", "cache_hours", 1)) * 3600
    if not fresh:
        try:
            age = time.time() - path.stat().st_mtime
            if age < ttl:
                hit = Fetched(**json.loads(path.read_text()))
                ago = f"cached {int(age // 60)}m ago"
                hit.note = f"{ago} · {hit.note}" if hit.note else ago
                return hit
        except (OSError, ValueError, TypeError):
            pass  # no entry, or an unreadable one: fetch fresh and overwrite it
    result = _fetch(url, archive=archive, archive_is=archive_is, date=date, save=save)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(result)))
    return result


def _fetch(
    url: str,
    archive: bool = False,
    archive_is: bool = False,
    date: str | None = None,
    save: bool = True,
) -> Fetched:
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

    if mirrors := _reddit_mirrors(url):
        # Why mirrors (field-verified 2026-08-31, don't re-test): direct fetch → blocked,
        # .json on www/api.reddit → 403 block page with AND without the BROWSER bundle +
        # a spoofed Chrome UA (this wall is endpoint/IP-shaped, not header-shaped),
        # old.reddit → 302 login wall (reason=lor2 — a logged-out rate limit), headless
        # browser → JS network-security challenge, Exa contents → empty (and Exa doesn't
        # index reddit.com at all — an unfiltered Exa search is how mirrors surfaced),
        # pullpush.io → 200 but zero data, r.jina.ai free → empty shell. Durable path if
        # the ladder ever dies for good: Reddit's OAuth API — the app "kotools" is
        # registered (developers.reddit.com/apps/kotools, 2026-08-31).
        for mirror in mirrors:
            try:
                resp = httpx.get(mirror, headers=BROWSER, timeout=30, follow_redirects=True)
                resp.raise_for_status()
                text = _extract(resp.text, mirror)
            except httpx.HTTPError:
                text = None
            if text:
                try:
                    _reddit_check(resp.text, url)
                except LookupError as e:
                    if " is in r/" in str(e):
                        raise RuntimeError(f"{url}: {e}") from None  # a wrong URL, not a dead mirror
                    continue
                return Fetched(text=text, source="reddit-mirror", note=f"reddit → {mirror}")
        # Whole ladder dead/empty — someone else's infrastructure, so say so LOUDLY (a
        # silent zero-word result reads as an empty page) and recover via Wayback of the
        # ORIGINAL url.
        try:
            r = _wayback(url, date)
            r.note = f"all reddit mirrors empty → {r.note or 'wayback'}"
            return r
        except Exception as e:
            raise RuntimeError(
                f"all reddit mirrors returned no content ({', '.join(_reddit_mirror_hosts())}), "
                f"and Wayback has no capture of {url} — try Reddit's official OAuth API"
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
        # No `Accept: text/markdown` — we asked servers to content-negotiate markdown and
        # measured the result (2026-08-19): only 2 of 6 sites answer, and when they do it's
        # a whole-DOM dump, not an extraction (drive.com.au: 31k tokens of nav and icons vs
        # trafilatura's 4.7k of just the review). Worth re-testing as the practice matures —
        # more sites will serve genuinely useful markdown — but today trafilatura wins.
        resp = httpx.get(
            url,
            headers=BROWSER,
            timeout=30,
            follow_redirects=True,
        )
        resp.raise_for_status()
    except httpx.HTTPError as e:
        # 401/403/429 = refused, not gone. The page is there and a wall said no, so the
        # JS-rendered archive.today capture is the better first try — the same rung the
        # paywall path already uses (and it falls through to Wayback anyway, so this is a
        # strict superset). Everything else — 404, 5xx, timeouts — really is a dead link,
        # and Wayback is the historical archive for it.
        # Gone (404/410, or no answer at all) → its archive is the truth. Any other status is
        # a server that answered and said no — a wall (Cloudflare challenges come as 403 AND
        # 503), a rate limit, a paywall — so the page exists and the browser goes first.
        status = e.response.status_code if isinstance(e, httpx.HTTPStatusError) else None
        dead = status is None or status in _GONE_STATUS
        why = f"HTTP {status}" if status else f"unreachable ({type(e).__name__})"
        return _rescue(url, date, dead=dead, why=why)

    content_type = resp.headers.get("content-type", "")
    if "text/markdown" in content_type:
        # Unsolicited — we no longer ask (see above) — but a server that volunteers markdown
        # has already done the extraction, and trafilatura on a markdown body is
        # length-dependent: it returns None on short docs (measured on llmstxt.org), which
        # would send a perfectly good page down the paywall path.
        tokens = resp.headers.get("x-markdown-tokens")
        note = f"text/markdown ({tokens} tokens)" if tokens else "text/markdown"
        return Fetched(text=resp.text, source="live", note=note)
    if "application/pdf" in content_type or httpx.URL(url).path.lower().endswith(
        ".pdf"
    ):
        return _pdf(resp.content, url, save)

    text = _extract(resp.text, url)
    if not text:  # 200 but empty → a paywall, or a page that renders client-side
        return _rescue(url, date, dead=False, why="the live page had no extractable content")
    if _walled(text):  # 200 with a captcha (Bloomberg: "let us know you're not a robot")
        return _rescue(url, date, dead=False, why="the live page is a bot check")
    return Fetched(text=text, source="live")
