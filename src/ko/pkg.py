"""`ko pkg info`: one npm or PyPI package, looked up deterministically.

deps.dev (Google, public, no key) gives the facts: every version with its date, licence,
advisories, provenance, the GitHub project's stars and OpenSSF Scorecard. The registry itself
(PyPI JSON, npm) is the authority on the latest version: deps.dev's "default" can be a
pre-release (httpx 1.0.0.dev6 while PyPI says 0.28.1). GitHub adds the last push and the
release notes. The lockfile in the current repo says which version you're on.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tomllib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote

import httpx2 as httpx
from packaging.version import InvalidVersion, Version

DEPS = "https://api.deps.dev/v3"
SYSTEMS = ("npm", "pypi")
FLOOR_DAYS = 3  # matches `exclude-newer = "3 days"` (pyproject) and `min-release-age=3` (.npmrc)
NOTES_LINES = 6


@dataclass
class Advisory:
    id: str
    title: str
    cvss: float | None
    url: str


@dataclass
class PkgInfo:
    system: str  # npm | pypi
    name: str
    latest: str  # the registry's latest stable
    released: str  # YYYY-MM-DD, "" if deps.dev hasn't indexed it yet
    floor_pick: str  # what uv/npm pick under the release-age floor, when latest is too new; else ""
    locked: str  # the version in this repo's lockfile, else ""
    locked_advisories: list[Advisory]
    advisories: list[Advisory]  # on latest
    description: str
    licenses: list[str]
    deprecated: str  # the reason, when deprecated
    provenance: bool  # latest has a verified build attestation from its repo
    releases_1y: int  # stable releases in the last year
    recent: list[tuple[str, str]]  # (version, date) of the stable releases before latest, newest first
    downloads_week: int | None
    repo: str  # https://github.com/owner/name, "" when not on GitHub
    stars: int | None
    open_issues: int | None
    scorecard: float | None
    pushed: str  # YYYY-MM-DD of the repo's last push
    homepage: str
    notes_url: str  # release notes for latest, or the changelog
    notes: list[str] = field(default_factory=list)  # their first lines


def _get(url: str, **kw) -> httpx.Response:
    return httpx.get(url, timeout=15, follow_redirects=True, **kw)


def _date(ts: str) -> str:
    return ts[:10] if ts else ""


# --- which package -----------------------------------------------------------------------


def _deps_package(system: str, name: str) -> dict | None:
    r = _get(f"{DEPS}/systems/{system}/packages/{quote(name, safe='')}")
    if r.status_code == 404:
        return None
    return r.raise_for_status().json()


def systems_for(query: str) -> list[tuple[str, str]]:
    """(system, name) pairs to look up: `npm:x`/`pypi:x` pin one; `@scope/x` is npm; a name in
    this repo's lockfile goes to that ecosystem; else both are tried."""
    for s in SYSTEMS:
        if query.startswith(f"{s}:"):
            return [(s, query.removeprefix(f"{s}:"))]
    if query.startswith("@"):
        return [("npm", query)]
    here = [s for s in SYSTEMS if locked_version(s, query)]
    return [(s, query) for s in (here or SYSTEMS)]


# --- versions ----------------------------------------------------------------------------


def _stable(system: str, v: str) -> bool:
    if system == "npm":
        return "-" not in v
    try:
        return not Version(v).is_prerelease
    except InvalidVersion:
        return False


def _vkey(system: str, v: str):
    if system == "pypi":
        return Version(v)
    return tuple(int(p) for p in re.findall(r"\d+", v.split("-")[0]))


def _registry_latest(system: str, name: str) -> tuple[str, dict]:
    """The registry's own latest version, plus PyPI project_urls (npm: repository directory)."""
    if system == "pypi":
        info = _get(f"https://pypi.org/pypi/{name}/json").raise_for_status().json()["info"]
        return info["version"], info.get("project_urls") or {}
    d = _get(f"https://registry.npmjs.org/{name.replace('/', '%2F')}/latest").raise_for_status().json()
    repo = d.get("repository") or {}
    return d["version"], {"directory": repo.get("directory", "")} if isinstance(repo, dict) else {}


def _downloads(system: str, name: str) -> int | None:
    try:
        if system == "npm":
            return _get(f"https://api.npmjs.org/downloads/point/last-week/{name}").json()["downloads"]
        return _get(f"https://pypistats.org/api/packages/{name.lower()}/recent").json()["data"]["last_week"]
    except Exception:
        return None  # a nice-to-have; pypistats rate-limits


# --- the lockfile ------------------------------------------------------------------------


def _find_up(filename: str) -> Path | None:
    for d in [Path.cwd(), *Path.cwd().parents]:
        if (d / filename).is_file():
            return d / filename
    return None


def _norm_py(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def locked_version(system: str, name: str) -> str:
    """The version this repo (cwd or a parent) locks, from uv.lock or package-lock.json."""
    if system == "pypi":
        lock = _find_up("uv.lock")
        if lock:
            for p in tomllib.loads(lock.read_text()).get("package", []):
                if _norm_py(p["name"]) == _norm_py(name):
                    return p.get("version", "")
        return ""
    lock = _find_up("package-lock.json")
    if lock:
        return (json.loads(lock.read_text()).get("packages", {}).get(f"node_modules/{name}") or {}).get("version", "")
    return ""


# --- deps.dev version detail -------------------------------------------------------------


def _version(system: str, name: str, v: str) -> dict:
    r = _get(f"{DEPS}/systems/{system}/packages/{quote(name, safe='')}/versions/{quote(v, safe='')}")
    return {} if r.status_code == 404 else r.raise_for_status().json()


def _advisories(keys: list[dict]) -> list[Advisory]:
    def one(k):
        a = _get(f"{DEPS}/advisories/{k['id']}").raise_for_status().json()
        return Advisory(k["id"], a.get("title", ""), a.get("cvss3Score"), a.get("url", ""))

    with ThreadPoolExecutor() as pool:
        found = list(pool.map(one, keys))
    return sorted(found, key=lambda a: -(a.cvss or 0))


# --- GitHub ------------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _gh_headers() -> dict:
    """Use a token when there is one: unauthenticated GitHub allows 60 calls an hour."""
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token and shutil.which("gh"):
        out = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, check=False)
        token = out.stdout.strip()
    return {"Authorization": f"Bearer {token}"} if token else {}


def _repo(links: list[dict], related: list[dict]) -> str:
    for p in related:
        pid = p["projectKey"]["id"]
        if pid.startswith("github.com/"):
            return f"https://{pid}"
    for link in links:
        if m := re.search(r"github\.com/([\w.-]+)/([\w.-]+?)(?:\.git)?(?:[/#]|$)", link["url"]):
            return f"https://github.com/{m[1]}/{m[2]}"
    return ""


def _head(text: str, n: int = NOTES_LINES) -> list[str]:
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)  # generator stamps
    return [line.rstrip() for line in text.splitlines() if line.strip()][:n]


def _release_notes(repo: str, name: str, version: str) -> tuple[str, list[str]]:
    """The GitHub release for `version` (tags v1.2, 1.2, name@1.2, name-v1.2): link + first lines."""
    slug = repo.removeprefix("https://github.com/")
    for tag in [
        f"v{version}",
        version,
        f"{name}@{version}",
        f"{name}@v{version}",
        f"{name}-v{version}",
    ]:
        r = _get(
            f"https://api.github.com/repos/{slug}/releases/tags/{quote(tag, safe='')}",
            headers=_gh_headers(),
        )
        if r.status_code == 200:
            rel = r.json()
            return rel["html_url"], _head(rel.get("body") or "")
    return "", []


def _changelog(repo: str, directory: str, version: str) -> tuple[str, list[str]]:
    """CHANGELOG.md in the package's folder or the repo root: link + the lines under `version`."""
    slug = repo.removeprefix("https://github.com/")
    for path in [
        f"{directory}/CHANGELOG.md" if directory else "",
        "CHANGELOG.md",
        "CHANGES.md",
        "HISTORY.md",
    ]:
        if not path:
            continue
        r = _get(f"https://raw.githubusercontent.com/{slug}/HEAD/{path}")
        if r.status_code != 200:
            continue
        url = f"{repo}/blob/HEAD/{path}"
        lines = r.text.splitlines()
        heading = re.compile(rf"(?<![\d.]){re.escape(version)}(?!\.?\d)")  # 2.0.0 must not match 12.0.0
        at = next((i for i, line in enumerate(lines) if line.startswith("#") and heading.search(line)), None)
        if at is None:
            return url, []
        section = []
        for line in lines[at + 1 :]:
            if line.startswith("#") and re.match(r"#+\s*\[?v?\d", line):
                break
            section.append(line)
        return url, _head("\n".join(section))
    return "", []


def _github(repo: str) -> str:
    """The repo's last push, YYYY-MM-DD."""
    r = _get(
        f"https://api.github.com/repos/{repo.removeprefix('https://github.com/')}",
        headers=_gh_headers(),
    )
    return _date(r.json().get("pushed_at") or "") if r.status_code == 200 else ""


# --- the lookup --------------------------------------------------------------------------


def info(query: str, *, notes: bool = True) -> list[PkgInfo]:
    """Every ecosystem where `query` exists (usually one). Raises LookupError when none."""
    found = [(s, n, p) for s, n in systems_for(query) if (p := _deps_package(s, n))]
    if not found:
        raise LookupError(f"no npm or PyPI package named {query!r}")
    return [_one(s, n, p, notes=notes) for s, n, p in found]


def _one(system: str, name: str, pkg: dict, *, notes: bool) -> PkgInfo:
    versions = {v["versionKey"]["version"]: _date(v.get("publishedAt", "")) for v in pkg["versions"]}
    stable = sorted(
        (v for v in versions if _stable(system, v)),
        key=lambda v: _vkey(system, v),
        reverse=True,
    )
    locked = locked_version(system, name)

    with ThreadPoolExecutor() as pool:
        reg = pool.submit(_registry_latest, system, name)
        dl = pool.submit(_downloads, system, name)
        latest, extra = reg.result()
        cur = pool.submit(_version, system, name, latest)
        old = pool.submit(_version, system, name, locked) if locked and locked != latest else None
        detail = cur.result()
        repo = _repo(detail.get("links", []), detail.get("relatedProjects", []))
        proj = (
            pool.submit(lambda: _get(f"{DEPS}/projects/{quote(repo.removeprefix('https://'), safe='')}").json())
            if repo
            else None
        )
        pushed = pool.submit(_github, repo) if repo else None
        rel = pool.submit(_release_notes, repo, name, latest) if repo and notes else None
        adv = pool.submit(_advisories, detail.get("advisoryKeys", []))
        old_adv = pool.submit(lambda: _advisories(old.result().get("advisoryKeys", []))) if old else None
        project = proj.result() if proj else {}
        notes_url, head = rel.result() if rel else ("", [])
        if repo and notes and not head:
            cl_url, cl_head = _changelog(repo, extra.get("directory", ""), latest)
            notes_url, head = (cl_url, cl_head) if cl_url else (notes_url, head)
        if not notes_url:
            notes_url = next(
                (u for k, u in extra.items() if k.lower() in ("changelog", "changes", "release notes")),
                "",
            )

    # Below the registry's latest only: a stable 5.0 on another dist-tag isn't what npm picks.
    older = [v for v in stable if _vkey(system, v) < _vkey(system, latest)]
    cutoff = (datetime.now(UTC) - timedelta(days=FLOOR_DAYS)).date().isoformat()
    year_ago = (datetime.now(UTC) - timedelta(days=365)).date().isoformat()
    released = versions.get(latest, "")
    floor_pick = ""
    if released and released > cutoff:
        floor_pick = next((v for v in older if versions[v] and versions[v] <= cutoff), "")
    homepage = next(
        (link["url"] for link in detail.get("links", []) if link["label"] == "HOMEPAGE"),
        "",
    )
    return PkgInfo(
        system=system,
        name=name,
        latest=latest,
        released=released,
        floor_pick=floor_pick,
        locked=locked,
        locked_advisories=(old_adv.result() if old_adv else adv.result()) if locked else [],
        advisories=adv.result(),
        description=project.get("description", ""),
        licenses=detail.get("licenses", []),
        deprecated=detail.get("deprecatedReason", "") if detail.get("isDeprecated") else "",
        provenance=any(a.get("verified") for a in detail.get("attestations", [])),
        releases_1y=sum(1 for v in stable if versions[v] >= year_ago),
        recent=[(v, versions[v]) for v in older][:4],
        downloads_week=dl.result(),
        repo=repo,
        stars=project.get("starsCount"),
        open_issues=project.get("openIssuesCount"),
        scorecard=(project.get("scorecard") or {}).get("overallScore"),
        pushed=pushed.result() if pushed else "",
        homepage=homepage,
        notes_url=notes_url,
        notes=head,
    )
