"""Which model, and how it's reached: ko's tested models, the price tiers, resolving a model
string into what pydantic-ai runs, and the cached gateway catalogs (-m completion, prices).

ko's twin of everx's `ai-models.ts`. Every LLM call goes through `resolve()`; nothing else
builds a model. `llm.py` runs prompts and prices them; this module decides what they run on.
"""

from __future__ import annotations

import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

from ko import config

if TYPE_CHECKING:
    from pydantic_ai.models import Model

    from ko.hn import Comment, Story


@dataclass(frozen=True)
class ModelSpec:
    """A model we've tested, and how ko calls it on Vercel AI Gateway."""

    id: str  # gateway id, e.g. "zai/glm-5.3-flash"
    note: str  # why we'd pick it and what we measured; `ko models` prints it
    # Hosts to try first, in order. After them the gateway routes as it likes, minus hosts
    # that report fp4 weights. Empty = gateway defaults (single-maker models: OpenAI, Google).
    hosts: tuple[str, ...] = ()
    # pydantic-ai ModelSettings merged into every call (reasoning effort, thinking, max_tokens…).
    # Add one at a time, with a before/after on cost and answers.
    settings: dict = field(default_factory=dict)


# Hosts measured 2026-10-27: each pinned in turn, a two-city tool call x2, every host correct;
# seconds to the answer. WORKLOG 2026-10-27 has the table. Prices $/M in+out, checked the same
# day — re-check when you touch this, they drift fast.
MODELS = {
    m.id: m
    for m in [
        ModelSpec(
            "deepseek/deepseek-v4.1-flash-1017",
            "basic default (ko llm, ko ai), $0.15/$0.60 (Together 2x). Together 0.9 s, Parasail 1.2 s, maker 2.2 s. "
            "Dated id: plain v4.1-flash serves the September checkpoint",
            hosts=("togetherai", "parasail", "deepseek"),
        ),
        ModelSpec(
            "zai/glm-5.3-flash",
            "basic peer, $0.15/$0.50. Baseten 1.4 s, Friendli 2.1 s, Together 1.6-6 s; the maker Z.ai 12 s",
            hosts=("baseten", "friendli", "togetherai"),
        ),
        ModelSpec("openai/gpt-6-luna", "basic peer, $0.10/$0.50; OpenAI ~0.8 s"),
        ModelSpec("openai/gpt-6.1-terra", "medium, $1/$5. Cleanest tool use in the 2026-10-27 ko ai test, 5x deepseek's cost"),
        ModelSpec("moonshotai/kimi-k3.1-pro", "smart (research), $2/$10; slow to first token (~5 s)"),
        ModelSpec("google/gemini-4-argon", "smart peer, $2/$10"),
        ModelSpec("google/gemini-3.5-flash-lite", "ko sum: fast, long context, $0.30/$2.50"),
    ]
}

# Tiers by price. Override per tier in config.toml `[llm]`; `-m` per run.
TIERS = {
    "basic": "vercel:deepseek/deepseek-v4.1-flash-1017",
    "medium": "vercel:openai/gpt-6.1-terra",
    "smart": "vercel:moonshotai/kimi-k3.1-pro",
}


def model_for(tier: str) -> str:
    """The model for a tier ('basic'/'medium'/'smart'): config.toml `[llm] <tier>` → baked default."""
    return config.get("llm", tier) or TIERS[tier]


# `-m jev` is TypeSafe's Jev through OpenRouter, on the key ko already has; TYPESAFE_API_KEY,
# when set, goes direct instead.
_OPENROUTER_SYSTEMONE = "https://openrouter.ai/api"  # OpenRouter's /v1/systemone, TypeSafe's API shape


def resolve(model: str | Model) -> str | Model:
    """A model string → what Agent.run takes. `vercel:` gets its ModelSpec (host order,
    settings) and the key check; `jev` / `typesafe:` get their provider; anything else (or a
    Model) passes through."""
    if not isinstance(model, str):
        return model
    if model.startswith("vercel:"):
        return _gateway_model(model.removeprefix("vercel:"))
    if model != "jev" and not model.startswith("typesafe:"):
        return model
    from pydantic_ai.models.typesafe import TypeSafeModel
    from pydantic_ai.providers.typesafe import TypeSafeProvider

    name = "jev-latest" if model == "jev" else model.removeprefix("typesafe:")
    if os.environ.get("TYPESAFE_API_KEY"):
        return TypeSafeModel(name)
    key = config.require_key("OPENROUTER_API_KEY", "Jev runs through OpenRouter: https://openrouter.ai/keys")
    return TypeSafeModel(name, provider=TypeSafeProvider(api_key=key, base_url=_OPENROUTER_SYSTEMONE))


def _merge(base: dict, over: dict) -> dict:
    """Nested dicts merge key by key; anything else in `over` wins."""
    out = dict(base)
    for k, v in over.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


@lru_cache
def _gateway_model(name: str) -> Model:
    """A Vercel AI Gateway model with its MODELS spec applied: host order as providerOptions,
    then the spec's own settings. Unlisted models get the gateway's defaults."""
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.vercel import VercelProvider

    key = config.require_key(
        "VERCEL_AI_GATEWAY_API_KEY", "mint one: `vercel ai-gateway api-keys create --name ko-<machine>`"
    )
    refresh_catalog("vercel")  # prices for the cost note; a no-op within the 24h TTL
    spec = MODELS.get(name)
    settings: dict = {}
    if spec and spec.hosts:
        gateway = {"order": list(spec.hosts), "has": ["!quantization:fp4"]}
        settings["extra_body"] = {"providerOptions": {"gateway": gateway}}
    if spec:
        settings = _merge(settings, spec.settings)  # a spec's own providerOptions must not drop the host order
    return OpenAIChatModel(name, provider=VercelProvider(api_key=key), settings=settings or None)


# provider prefix -> env var that makes it usable; drives -m autocomplete.
# Only providers whose SDK we actually install (slim extras: google, openrouter
# -> google-genai + openai). Everything else is reachable via `vercel:<id>` (the
# default route) or `openrouter:<slug>` (kept for Jev), one key each.
PROVIDER_KEYS = {
    "google": "GEMINI_API_KEY",
    "openai": "OPENAI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "vercel": "VERCEL_AI_GATEWAY_API_KEY",
}


# Gateway catalogs are open-ended, so pydantic-ai leaves them out of known_model_names().
# We fetch each public /models list (no key needed) and cache it so `-m vercel:<TAB>` can
# complete; the gateway's copy also carries prices, which the cost note estimates from.
CATALOG_URLS = {
    "vercel": "https://ai-gateway.vercel.sh/v1/models",
    "openrouter": "https://openrouter.ai/api/v1/models",
}
_CATALOG_TTL = 24 * 3600  # re-fetch a catalog at most once a day
CATALOG_STALE = 7 * 24 * 3600  # past this, `ko models` hints to run --refresh (public: cli_ai reads it)

# Did the most recent refresh_catalog() reach the network? True = fetched fresh, False =
# network failed and we fell back to the existing cache, None = never attempted (no key, or
# a within-TTL cache served without a fetch). Lets `ko models --refresh` warn when a forced
# refresh silently served the old cache instead of the fresh catalog it promised.
last_refresh_hit_network: bool | None = None


def _catalog_file(provider: str) -> Path:
    from ko.dirs import cache_dir

    return cache_dir() / f"{provider}_models.json"


def catalog_age(provider: str) -> float | None:
    """Age in seconds of a cached catalog, or None if there's no cache file."""
    try:
        return time.time() - _catalog_file(provider).stat().st_mtime
    except OSError:
        return None


def _catalog(provider: str) -> dict:
    """The cached catalog, parsed once per file version (run_cost asks for a price per response)."""
    path = _catalog_file(provider)
    try:
        return _parse_catalog(path, path.stat().st_mtime_ns)
    except OSError:
        return {}


@lru_cache(maxsize=4)
def _parse_catalog(path: Path, _mtime: int) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def cached_models(provider: str) -> list[str]:
    """Cached `<provider>:<id>` names. Read-only, never hits network — completion-safe."""
    return _catalog(provider).get("models", [])


def gateway_price(model_id: str) -> tuple[float, float] | None:
    """The gateway's listed $/token (input, output) for a model, from the cached catalog.
    The listed price is the cheapest host off-peak, so cost from it is an estimate."""
    p = _catalog("vercel").get("prices", {}).get(model_id)
    return (float(p["input"]), float(p["output"])) if p else None


def refresh_catalog(provider: str, ttl: int = _CATALOG_TTL, *, force: bool = False) -> list[str]:
    """Refresh one cached catalog if stale, and return it.

    No-op unless the provider's key is set (we only surface models a user can call). The
    /models endpoints are public; network errors fall back to the existing cache, so this is
    safe to call from any command. `force=True` ignores the TTL, for a just-released model.
    """
    global last_refresh_hit_network
    last_refresh_hit_network = None
    if not os.environ.get(PROVIDER_KEYS[provider]):
        return cached_models(provider)
    path = _catalog_file(provider)
    if not force:
        try:
            if path.stat().st_mtime > time.time() - ttl:
                return cached_models(provider)
        except OSError:
            pass
    try:
        import httpx2 as httpx

        data = httpx.get(CATALOG_URLS[provider], timeout=3).json()["data"]  # prices only feed an estimate: never stall a call
    except Exception:
        # Keep the old cache (and DON'T touch its mtime, so the next call retries), but
        # record the miss so a forced --refresh can say so instead of looking like it worked.
        last_refresh_hit_network = False
        return cached_models(provider)
    out: dict = {"models": sorted(f"{provider}:{m['id']}" for m in data)}
    if provider == "vercel":
        out["prices"] = {
            m["id"]: {"input": m["pricing"]["input"], "output": m["pricing"]["output"]}
            for m in data
            if {"input", "output"} <= (m.get("pricing") or {}).keys()  # language models; image/video price differently
        }
    tmp = path.with_suffix(".tmp")  # atomic: a crash mid-write must not corrupt the cache
    tmp.write_text(json.dumps(out))
    os.replace(tmp, path)
    last_refresh_hit_network = True
    return out["models"]


def refresh_catalogs(*, force: bool = False) -> bool | None:
    """Refresh every catalog whose key is set. Returns False if any fetch missed the network."""
    hits = []
    for provider in CATALOG_URLS:
        refresh_catalog(provider, force=force)
        hits.append(last_refresh_hit_network)
    return False if False in hits else (True if True in hits else None)


def available_models(prefix: str = "") -> list[str]:
    """Model names filtered to providers whose env key is set. Feeds -m tab-completion.

    pydantic-ai's static list, plus each cached gateway catalog whose key is set
    (populated by refresh_catalog). Reads cache only — no network, so completion
    stays instant.
    """
    from pydantic_ai.models import known_model_names

    names = [
        n
        for n in known_model_names()
        if ":" in n and os.environ.get(PROVIDER_KEYS.get(n.split(":")[0], "")) is not None
    ]
    for provider in CATALOG_URLS:
        if os.environ.get(PROVIDER_KEYS[provider]) is not None:
            names += cached_models(provider)
    return sorted({n for n in names if n.startswith(prefix)})


# --- `ko models info`: one model, looked up deterministically --------------------------------

GATEWAY_API = "https://ai-gateway.vercel.sh/v1"


@dataclass
class Host:
    """One provider serving a model on the gateway, with the gateway's last-hour stats."""

    name: str
    price_in: float | None  # $/M tokens
    price_out: float | None
    latency_ms: float | None  # p50 time to first token
    tok_s: float | None  # p50 output tokens a second
    quantization: str | None
    uptime_1d: float | None  # %


@dataclass
class Thread:
    """An HN story about the model, with its top-ranked comment and reply."""

    story: Story
    url: str  # the HN thread
    top: Comment | None
    reply: Comment | None


@dataclass
class ModelInfo:
    id: str
    name: str
    released: str  # YYYY-MM-DD
    context: int | None
    max_output: int | None
    price_in: float | None  # $/M, the gateway's listed (cheapest-host) price
    price_out: float | None
    cache_read: float | None
    tags: list[str]
    description: str
    note: str  # ko's own note when it's a tested ModelSpec, else ""
    hosts: list[Host]
    others: list[tuple[str, str]]  # other matches: (id, released), newest first
    threads: list[Thread]
    unavailable: list[str] = field(default_factory=list)  # parts that failed (HN, host stats), with why


def _norm(s: str) -> str:
    """'Sonnet 5.5', 'sonnet-5-5' and 'claude-sonnet-5.5' all compare alike."""
    return " ".join(re.sub(r"[^a-z0-9]+", " ", s.lower()).split())


def _per_m(v) -> float | None:
    return round(float(v) * 1e6, 4) if v not in (None, "") else None


def _released(m: dict) -> str:
    ts = m.get("released") or m.get("created")
    return datetime.fromtimestamp(ts, tz=UTC).date().isoformat() if ts else ""


def _version_runs(words: list[str]) -> list[list[str]]:
    """Consecutive number words: 'sonnet 5 5 fast' -> [['5', '5']]."""
    runs: list[list[str]] = []
    for prev, w in zip([""] + words, words, strict=False):
        if w.isdigit():
            if prev.isdigit():
                runs[-1].append(w)
            else:
                runs.append([w])
    return runs


def _matches(words: list[str], candidate: list[str]) -> bool:
    """Every word present, and every version exact: '5' must not match '5.5', nor '5.5' '5'."""
    if not all(w in candidate for w in words if not w.isdigit()):
        return False
    cand_runs = _version_runs(candidate)
    return all(run in cand_runs for run in _version_runs(words))


def match(query: str, catalog: list[dict]) -> list[dict]:
    """Language models whose id or name has every word of `query` (versions exact), newest first."""
    words = _norm(query).split()
    hits = [
        m
        for m in catalog
        if m.get("type", "language") == "language"
        and any(_matches(words, _norm(field).split()) for field in (m["id"], m.get("name", "")))
    ]
    return sorted(hits, key=lambda m: m.get("released") or m.get("created") or 0, reverse=True)


@lru_cache(maxsize=1)
def _live_catalog() -> list[dict]:
    """The gateway's full catalog, fetched once per process (a compare looks up several models)."""
    import httpx2 as httpx

    return httpx.get(f"{GATEWAY_API}/models", timeout=15).raise_for_status().json()["data"]


def info(query: str, years: int = 1, threads: int = 3) -> ModelInfo:
    """The newest gateway model matching `query`: specs, prices, every host's speed, and the
    biggest HN threads about it (top comment + top reply). No LLM; public endpoints, no key.
    Raises LookupError when nothing matches."""
    import httpx2 as httpx

    from ko import hn

    hits = match(query, _live_catalog())
    if not hits:
        raise LookupError(f"no gateway model matches {query!r} — `ko models | grep -i <word>` lists them all")
    m = hits[0]
    # The catalog facts are the answer; HN threads and host stats are extras, so a failure there
    # is reported in `unavailable`, not fatal.
    unavailable: list[str] = []
    with ThreadPoolExecutor() as pool:
        eps = pool.submit(lambda: httpx.get(f"{GATEWAY_API}/models/{m['id']}/endpoints", timeout=15).raise_for_status())
        stories = pool.submit(_hn_stories, m, years, threads)
        try:
            found = stories.result()
            tops = list(pool.map(lambda s: hn.top_comment(s.id) if s.num_comments else (None, None), found))
        except (httpx.HTTPError, KeyError, ValueError) as e:
            found, tops = [], []
            unavailable.append(f"HN: {e}")
        try:
            endpoints = eps.result().json()["data"]["endpoints"]
        except (httpx.HTTPError, KeyError, ValueError) as e:
            endpoints = []
            unavailable.append(f"host stats: {e}")
    p = m.get("pricing") or {}
    spec = MODELS.get(m["id"])
    return ModelInfo(
        id=m["id"],
        name=m.get("name") or m["id"],
        released=_released(m),
        context=m.get("context_window"),
        max_output=m.get("max_tokens"),
        price_in=_per_m(p.get("input")),
        price_out=_per_m(p.get("output")),
        cache_read=_per_m(p.get("input_cache_read")),
        tags=m.get("tags") or [],
        description=m.get("description") or "",
        note=spec.note if spec else "",
        hosts=sorted(
            (
                Host(
                    e["provider_name"],
                    _per_m(e["pricing"].get("prompt")),
                    _per_m(e["pricing"].get("completion")),
                    (e.get("latency_last_1h") or {}).get("p50"),
                    (e.get("throughput_last_1h") or {}).get("p50"),
                    e.get("quantization"),
                    e.get("uptime_last_1d"),
                )
                for e in endpoints
            ),
            key=lambda h: -(h.tok_s or 0),
        ),
        others=[(o["id"], _released(o)) for o in hits[1:6]],
        threads=[
            Thread(s, f"https://news.ycombinator.com/item?id={s.id}", t, r)
            for s, (t, r) in zip(found, tops, strict=True)
        ],
        unavailable=unavailable,
    )


def _hn_stories(m: dict, years: int, n: int) -> list:
    """The biggest HN stories about the model. Titles use the full name ('Claude Sonnet 5.5') or
    drop the maker ('Sonnet 5.5', the 884-point launch thread), so search both, merge by points.
    The short form needs a digit, else 'GPT-6 Luna' -> 'Luna' finds moon stories."""
    from ko import hn

    name = m.get("name") or m["id"].split("/")[-1]
    short = " ".join(name.split()[1:])  # only while it keeps a version: "GPT-6 Luna" must not search "Luna"
    queries = [q for q in dict.fromkeys([name, short]) if q and (q == name or any(c.isdigit() for c in q))]
    with ThreadPoolExecutor() as pool:
        found = [s for batch in pool.map(lambda q: hn.search(q, n=n, years=years), queries) for s in batch]
    unique = {s.id: s for s in found}.values()
    # A story naming the model in its title beats a bigger one that only mentions it in the text.
    titled = [_norm(q).split() for q in queries]
    return sorted(unique, key=lambda s: (not any(_matches(w, _norm(s.title).split()) for w in titled), -s.points))[:n]
