"""AI/meta/tools cluster CLI commands: llm, models, prompt, agent, mcp, publish, tt."""

from __future__ import annotations

import json
import mimetypes
import os
import re
import sys
from dataclasses import asdict
from pathlib import Path

import typer

from ._cli_shared import (
    _big,
    _clip,
    _die,
    _emit_json,
    _link,
    _no_results,
    _reject_group_flags,
    _table,
    _tsv_cell,
    app,
    lazy,
)

billing_mod = lazy("billing")
brief_mod = lazy("brief")
llm_mod = lazy("llm")
models_mod = lazy("models")
mcp_client_mod = lazy("mcp_client")
prompt_mod = lazy("prompt")
publish_mod = lazy("publish")
ticktick_mod = lazy("ticktick")
agents = lazy("agents")  # pydantic-ai + every toolset: the bulk of the old startup

# --- sub-apps ---

mcp_app = typer.Typer(
    help="Inspect/call MCP servers by name or url (ko is itself an MCP client). "
    "`ko mcp inspect <name|url>` · `call` · `servers`. Names from ~/.config/ko/mcp.json.",
    no_args_is_help=True,
)
app.add_typer(mcp_app, name="mcp")

tt_app = typer.Typer(
    help=(
        "TickTick lists/tasks (TICKTICK_API_KEY). Read: `lists`, `items <list>`, `search`; "
        "write: `add`, `done`. Bare `ko tt` lists your lists. Alias: `ko todo`."
    ),
)
app.add_typer(tt_app, name="tt")
app.add_typer(tt_app, name="todo", hidden=True)  # the name I reach for; `tt` carries the help

publish_app = typer.Typer(
    help=(
        "Publish a folder to Cloudflare; re-publishing overwrites the same URL. "
        "Two steps: scaffold a site, then deploy it.\n\n"
        "Scaffold pathways (`ko publish new <dir> [flag]`) — each drops a CLAUDE.md with how-to:\n\n"
        "• (default) static HTML page — Tailwind + Alpine, no build.\n\n"
        "• --md   markdown doc site — write .md (README is the hub); raw HTML/SVG + Tailwind + "
        "Alpine render inline for custom visuals; TOC, syntax highlighting, print/PDF.\n\n"
        "• --bare  just a CLAUDE.md — the agent builds from scratch.\n\n"
        "• --hono  a Hono worker — API routes, D1/R2, server-side code; add --pin to gate it "
        "behind a 6-digit PIN.\n\n"
        "Preview: `ko publish preview <dir>` → wrangler dev at http://localhost:8787 (real http, "
        "so ES modules + fetch work; view it as you build).\n\n"
        "Deploy: `ko publish <dir>` → <name>.khalido.dev (`--name` to set it, `--force` to take "
        "over an existing name). Needs wrangler (`npm install`) + KO_CLOUDFLARE_API_TOKEN."
    ),
    no_args_is_help=True,
)
app.add_typer(publish_app, name="publish")

agent_app = typer.Typer(help="AI agents powered by pydantic-ai.", no_args_is_help=True)
app.add_typer(agent_app, name="agent")
app.add_typer(agent_app, name="a", hidden=True)  # shortcut: `ko a research`


# --- llm commands ---


def _media_type(inp: str) -> str | None:
    """image/audio/video mime type guessed from the filename/URL, else None (text pipeline).

    PDFs/Office deliberately stay on the text path (doc.parse reads text layers for free, OCRs
    scanned pages, and works with non-vision models); only media with no useful local text form attaches as binary."""
    mt, _ = mimetypes.guess_type(inp.split("?", 1)[0].split("#", 1)[0])
    if mt == "image/svg+xml":
        return None  # SVG is text — _gather reads it directly; vision models mostly reject it
    return mt if mt and mt.startswith(("image/", "audio/", "video/")) else None


def _read_media(inp: str, guessed: str) -> tuple[bytes, str, str]:
    """One media input -> (bytes, media_type, label). Local file or downloaded URL —
    downloading ourselves (vs pydantic-ai's *Url parts, where the provider fetches)
    works uniformly across providers and lets the dispatch line show a real size."""
    p = Path(inp).expanduser()
    if p.is_file():
        return p.read_bytes(), guessed, p.name
    if inp.startswith(("http://", "https://")):
        import httpx2 as httpx

        r = httpx.get(inp, timeout=30, follow_redirects=True)
        r.raise_for_status()
        ct = r.headers.get("content-type", "").split(";")[0].strip()
        if ct.startswith(("image/", "audio/", "video/")):
            mt = ct  # the server knows better than the extension guess
        elif ct in ("", "application/octet-stream", "binary/octet-stream"):
            mt = guessed  # server said nothing useful — the extension guess stands
        else:
            # A .png URL answering text/html is a login wall or error page — attaching it as
            # an "image" sends garbage to the model; fail loud instead.
            raise RuntimeError(f"expected {guessed} but the server sent {ct or 'no content-type'}")
        return r.content, mt, inp.rsplit("/", 1)[-1] or inp
    raise ValueError(f"{inp!r} is neither an existing file nor a URL")


def _first_fence(text: str) -> str:
    """The first fenced code block's body, or the whole text when there's no fence.
    An UNCLOSED fence (truncated reply) yields everything after the opener line — piping
    the ```py opener itself into pbcopy is never what -x meant."""
    m = re.search(r"```[^\n]*\n(.*?)(?:```|\Z)", text, re.DOTALL)
    return m.group(1).rstrip("\n") if m else text


@app.command("llm")
def llm_cmd(
    prompt: str = typer.Argument(
        ..., help="what to do; piped stdin becomes the input text"
    ),
    model: str = typer.Option(
        None,
        "--model",
        "-m",
        help=f"model string, e.g. {models_mod.TIERS["basic"]} (default: KO_DEFAULT_MODEL or that)",
        autocompletion=models_mod.available_models,
    ),
    system: str = typer.Option(
        None, "--system", "-s", help="replace the default system prompt"
    ),
    schema: Path = typer.Option(
        None,
        "--schema",
        exists=True,
        dir_okay=False,
        help="a JSON Schema file (object schema) constraining the output — the reply is JSON "
        "validating against it, compact on stdout, no prose. Works with -m and piped stdin.",
    ),
    files: list[str] = typer.Option(
        None,
        "--file",
        "-f",
        help="attach a file or URL as input (repeatable). Text-extractable inputs route like "
        "`ko sum` (PDF -> text, YouTube -> transcript); images/audio/video attach natively "
        "as multimodal input — the model must support the media type (gemini does; the "
        "basic tier may not). Text goes before the prompt in flag order, piped stdin after.",
    ),
    extract: bool = typer.Option(
        False,
        "--extract",
        "-x",
        help="print only the first fenced code block of the reply (the whole reply if there "
        "is none) — for piping generated code: `ko llm 'jq filter for X' -x | pbcopy`",
    ),
) -> None:
    """One-shot LLM call, no tools: `ko hn item 123 | ko llm "summarize the debate"`.

    `-f report.pdf -f photo.jpg -f <url>` attaches inputs (same convention as opencode and
    simonw's llm: repeatable flag, plain paths — the shell does completion and ~); one flag
    for everything, an image is just a file that happens to need a vision model.
    `--schema extract.json` returns structured JSON instead of prose: the file is a JSON
    Schema for the response (`{"type": "object", ...}`), printed compact to stdout."""
    if llm_mod.is_decision_model(model) and schema is None:
        _die(f"{model} answers typed questions, not prose: use `ko judge` (or give it a --schema)", code="usage")
    schema_obj = None
    if schema is not None:
        try:  # bad JSON / not an object schema is a usage error, before we spend a call
            schema_obj = llm_mod.load_schema(schema)
        except ValueError as e:
            _die(str(e), code="usage")
    stdin, attachments = _inputs(files)
    try:
        out = llm_mod.run(
            prompt,
            stdin=stdin,
            model=model,
            system=system,
            schema=schema_obj,
            attachments=attachments or None,
        )
        typer.echo(_first_fence(out) if extract else out)
        if llm_mod.last_cost:
            typer.echo(llm_mod.last_cost.note, err=True)
    except Exception as e:
        _die(str(e))


def _inputs(files: list[str] | None) -> tuple[str | None, list]:
    """`-f` inputs then piped stdin → (text, media attachments). Text-extractable inputs route
    like `ko sum`; images/audio/video come back as pydantic-ai BinaryContent."""
    parts, attachments = [], []
    for f in files or []:
        # An explicitly named input that can't be read is fatal — a prompt silently missing
        # half its context is worse than no answer.
        try:
            mt = _media_type(f)
            if mt:  # image/audio/video: no useful local text form — attach natively
                from pydantic_ai import BinaryContent

                data, mt, name = _read_media(f, mt)
                attachments.append(BinaryContent(data=data, media_type=mt))
                size = f"{len(data) // 1024:,} KB" if len(data) >= 1024 else f"{len(data)} B"
                typer.echo(f"[{mt}:{name} · {size}]", err=True)
                continue
            label, text = _gather(f)
        except UnicodeDecodeError as e:
            _die(f"{f}: {e}")  # a runtime read failure (exit 1), NOT a usage error — it subclasses ValueError
        except ValueError as e:
            _die(str(e), code="usage")
        except Exception as e:
            _die(f"{f}: {e}")
        typer.echo(f"[{label} · ~{len(text) // 4:,} tokens]", err=True)
        parts.append(text)
    if not sys.stdin.isatty():
        parts.append(sys.stdin.read())
    return "\n\n".join(p for p in parts if p.strip()) or None, attachments


@app.command("judge")
def judge_cmd(
    question: str = typer.Argument(..., help="the question; piped stdin or -f is what gets judged"),
    files: list[str] = typer.Option(
        None, "--file", "-f", help="judge a file or URL, read like `ko llm -f` (repeatable)"
    ),
    choice: str = typer.Option(None, "--choice", help="pick one: `a,b,c`, or `a=what a means,b` (no commas inside a meaning)"),
    score: str = typer.Option(None, "--score", help="ordered levels, lowest first: `skip,skim,read=read it properly`"),
    tags: str = typer.Option(None, "--tags", help="yes/no for each tag: `coding,ai,cars=vehicles and EVs`"),
    model: str = typer.Option(
        "jev",
        "--model",
        "-m",
        help="jev (default) gives every answer a probability; any `ko llm` model answers the same question bare",
        autocompletion=models_mod.available_models,
    ),
    as_json: bool = typer.Option(False, "--json", help="emit the full answer: probabilities, confidence, model"),
) -> None:
    """Typed answer to one question, with probabilities: `ko judge "is this a paywall page?" -f page.md`.
    How to word questions: `ko prompt judge`."""
    try:  # bad options are a usage error, before we spend a call
        if sum(bool(x) for x in (choice, score, tags)) > 1:
            raise ValueError("--choice, --score and --tags are different kinds of question; pick one")
        choice_opts = llm_mod.parse_options(choice, kind="choice") if choice else None
        score_opts = llm_mod.parse_options(score, kind="score") if score else None
        tag_opts = llm_mod.parse_options(tags, kind="tags") if tags else None
    except ValueError as e:
        _die(str(e), as_json=as_json, code="usage")
    text, attachments = _inputs(files)
    if attachments:
        _die("judge reads text: images/audio/video can't be the input", as_json=as_json, code="usage")
    if not text:
        _die("nothing to judge: pipe the text in or pass -f", as_json=as_json, code="usage")
    try:
        d = llm_mod.decide(question, text, model=model, choice=choice_opts, score=score_opts, tags=tag_opts)
    except Exception as e:
        _die(str(e), as_json=as_json)
    if as_json:
        typer.echo(json.dumps(asdict(d)))
    else:
        _print_decision(d)
    typer.echo(llm_mod.last_cost.note, err=True)


def _print_decision(d: llm_mod.Decision) -> None:
    """Column 1 of line 1 is always the answer. yes/no → P(yes); choice and tags → option<TAB>
    probability, likeliest first (a language model gives just the pick, or the tags that apply);
    score → level<TAB>position on the scale."""
    if d.kind == "yes_no":
        typer.echo(f"{d.answer:.2f}")
    elif d.kind == "score":
        typer.echo(f"{d.level}\t{d.answer}")
    elif d.probabilities:
        for name, p in sorted(d.probabilities.items(), key=lambda kv: -kv[1]):
            typer.echo(f"{name}\t{p:.2f}")
    else:
        typer.echo("\n".join(d.answer) if isinstance(d.answer, list) else d.answer)


# Scheme-less inputs that are still a URL: hostname labels, then a mandatory path
# (`nyt.com/x`). Requiring the path keeps bare `notes.md` a file-not-found, and the
# hostname shape keeps `./notes.md` and `docs/x.md` from becoming two network round-trips
# (fetch + Wayback) just to report a typo'd local path.
_URLISH_RE = re.compile(r"^[a-z0-9-]+(\.[a-z0-9-]{2,})+(?::\d+)?/", re.IGNORECASE)


def _gather(inp: str) -> tuple[str, str]:
    """One input -> (label, text). A local file reads directly (.md/.txt) or via liteparse
    (PDF/Office/image); anything URL-shaped goes through fetch's deterministic routing (arxiv,
    YouTube transcript, reddit mirror, Wayback...); anything else is an immediate error, no
    network. The label feeds the stderr dispatch line so a wrong routing choice is visible."""
    p = Path(inp).expanduser()
    if p.is_file():
        # .svg is here because it IS text: liteparse returns '' for it, and an empty part is
        # dropped downstream — which would make `-f diagram.svg` a paid call with the input
        # silently missing, the exact failure the fatal-on-unreadable rule exists to prevent.
        if p.suffix.lower() in {".md", ".txt", ".markdown", ".rst", ".svg"}:
            return f"file:{p.name}", p.read_text()
        from ko import doc as doc_mod

        return f"doc:{p.name}", doc_mod.parse(p)
    if inp.startswith(("http://", "https://")) or _URLISH_RE.match(inp):
        from ko import fetch as fetch_mod

        url = inp if inp.startswith(("http://", "https://")) else f"https://{inp}"
        r = fetch_mod.fetch(url, save=False)
        # source alone ("live") makes several URL attachments indistinguishable — add the ref
        ref = re.sub(r"^https?://(www\.)?", "", inp)
        return f"{r.source}:{ref if len(ref) <= 48 else ref[:47] + '…'}", r.text
    raise ValueError(f"{inp!r} is neither an existing file nor a URL")


@app.command("sum")
def sum_cmd(
    inputs: list[str] = typer.Argument(
        None,
        help="URLs and/or files — routed like `ko fetch`/`ko doc` (YouTube -> transcript, "
        "PDF -> text, reddit -> mirror). No inputs = summarize piped stdin.",
    ),
    prompt: str = typer.Option(
        None,
        "--prompt",
        "-p",
        help="replace the default summary prompt: a file path, or a name from `ko prompt` "
        "(default: the packaged `summarize` brief — override it for good by dropping "
        "~/.config/ko/prompts/summarize.md)",
    ),
    model: str = typer.Option(
        None,
        "--model",
        "-m",
        help=f"model (default {llm_mod.SUM_MODEL}; or set `\\[llm] sum`)",
        autocompletion=models_mod.available_models,
    ),
) -> None:
    """Fetch + summarize in one move: `ko sum <url>`, `ko sum report.pdf`, `ko hn item 123 | ko sum`.

    Really a shortcut for `ko fetch ... | ko llm <summarize prompt>`: one command owns the
    prompt/model surface so source commands stay clean primitives. Multiple inputs get one
    summary each (`# <input>` sections). The summary keeps the author's voice, opinions and
    facts — the prompt is `ko prompt summarize`."""
    if prompt:
        pp = Path(prompt).expanduser()
        if pp.is_file():
            sum_prompt = pp.read_text()
        else:
            try:
                sum_prompt = prompt_mod.get_prompt(prompt).body
            except KeyError:
                _die(f"--prompt {prompt!r}: not a file, and no `ko prompt` brief matches", code="usage")
    else:
        sum_prompt = prompt_mod.get_prompt("summarize").body
    use_model = model or llm_mod.sum_model()

    def _one(label: str, text: str) -> str:
        typer.echo(f"[{label} · ~{len(text) // 4:,} tokens → {use_model.split(':')[-1]}]", err=True)
        try:
            out = llm_mod.run(sum_prompt, stdin=text, model=use_model)
        except Exception as e:  # model/key errors fail the run — they'd fail every input
            _die(str(e))
        if llm_mod.last_cost:
            typer.echo(llm_mod.last_cost.note, err=True)
        return out

    if not inputs:
        if sys.stdin.isatty():
            _die("nothing to summarize — pass URLs/files or pipe text in", code="usage")
        text = sys.stdin.read()
        if not text.strip():
            _die("stdin was empty", code="usage")
        typer.echo(_one("stdin", text))
        return
    # Each summary flushes as soon as it's computed — a model error on input 3 must not
    # discard the (already paid-for) summaries of inputs 1 and 2.
    done, failed = 0, []
    for inp in inputs:
        try:
            label, text = _gather(inp)
        except Exception as e:  # a dead URL/file skips, the rest still summarize (fetch's contract)
            typer.echo(f"[failed: {inp} — {e}]", err=True)
            failed.append(inp)
            continue
        summary = _one(label, text)
        if done:
            typer.echo("\n---\n")
        typer.echo(summary if len(inputs) == 1 else f"# {inp}\n\n{summary}")
        done += 1
    if not done:
        _die(f"all {len(failed)} input(s) failed")


models_app = typer.Typer(
    help="Models usable with -m (bare `ko models` lists them); `info <name>` looks one up.",
    invoke_without_command=True,
)
app.add_typer(models_app, name="models")


@models_app.callback()
def models_cmd(
    ctx: typer.Context,
    refresh: bool = typer.Option(
        False,
        "--refresh",
        help="force re-fetch the model catalogs, ignoring the 24h cache",
    ),
) -> None:
    """List model strings usable with -m, one per line, for providers whose key is set; ko's tested models first, with a note (TSV)."""
    if ctx.invoked_subcommand:
        _reject_group_flags(ctx, refresh="--refresh")
        return
    hit = models_mod.refresh_catalogs(force=refresh)
    if refresh and hit is False:
        # A forced refresh that couldn't reach a catalog used to look identical to a
        # successful one; say so, else the list silently stays stale.
        typer.echo("couldn't reach a model catalog — showing the cached list (network error)", err=True)
    else:
        ages = [
            a
            for p in models_mod.CATALOG_URLS
            if os.environ.get(models_mod.PROVIDER_KEYS[p]) and (a := models_mod.catalog_age(p)) is not None
        ]
        if not refresh and ages and max(ages) > models_mod.CATALOG_STALE:
            typer.echo(
                f"catalog cached {int(max(ages) // 86400)} days ago — ko models --refresh for the latest",
                err=True,
            )
    names = models_mod.available_models()
    tested = [f"vercel:{m.id}" for m in models_mod.MODELS.values()]
    for name in tested:
        if name in names:
            typer.echo(f"{name}\t{models_mod.MODELS[name.removeprefix('vercel:')].note}")
    for name in names:
        if name not in tested:
            typer.echo(name)


@models_app.command("info")
def models_info(
    queries: list[str] = typer.Argument(..., help="model names, fuzzy: sonnet, 'glm flash', kimi; several = a comparison table"),
    years: int = typer.Option(1, "--years", "-y", min=0, help="HN window in years (0 = all time)"),
    as_json: bool = typer.Option(False, "--json", help="one name: an object; several: an array, {query, error, code} for a miss"),
) -> None:
    """The newest gateway model matching a name: price, context, each host's speed, and the top HN threads with their best comment. No LLM, no key."""
    if len(queries) > 1:
        _models_compare(queries, years, as_json)
        return
    try:
        info = models_mod.info(queries[0], years=years)
    except LookupError as e:
        _die(str(e), as_json=as_json, code="not_found")
    except Exception as e:  # gateway or HN down / timeout: one clean line, {error, code} under --json
        _die(f"model lookup failed: {e}", as_json=as_json, code="upstream")
    for part in info.unavailable:
        typer.echo(f"[unavailable: {part}]", err=True)
    if as_json:
        typer.echo(json.dumps(asdict(info), default=str))
        return
    for line in _model_info_lines(info, years, links=sys.stdout.isatty()):
        typer.echo(line)


def _models_compare(queries: list[str], years: int, as_json: bool) -> None:
    """Several models side by side, one HN thread each. A miss is reported, not fatal: exit 1
    only when every name missed."""
    from concurrent.futures import ThreadPoolExecutor

    def one(q: str):
        try:
            return models_mod.info(q, years=years, threads=1)
        except LookupError as e:
            return {"query": q, "error": str(e), "code": "not_found"}
        except TypeError, AttributeError, NameError, ImportError:
            raise  # our bug, not the upstream's: surface it
        except Exception as e:
            return {"query": q, "error": f"model lookup failed: {e}", "code": "upstream"}

    try:
        models_mod._live_catalog()  # warm the per-process cache once, not once per thread
    except Exception as e:
        _die(f"model lookup failed: {e}", as_json=as_json, code="upstream")
    with ThreadPoolExecutor() as pool:
        results = list(pool.map(one, queries))
    found = [r for r in results if not isinstance(r, dict)]
    for r in results:
        if isinstance(r, dict) and not as_json:
            typer.echo(f"[{r['query']}: {r['error']}]", err=True)
        elif not isinstance(r, dict):
            for part in r.unavailable:
                typer.echo(f"[{r.id} unavailable: {part}]", err=True)
    if as_json:
        typer.echo(json.dumps([r if isinstance(r, dict) else asdict(r) for r in results], default=str))
    elif found:  # nothing on stdout when every name missed
        for line in _model_compare_lines(found, years, links=sys.stdout.isatty()):
            typer.echo(line)
    if not found:
        _die(f"none of {len(queries)} names matched a gateway model", code="not_found")


def _num(v, unit: str = "") -> str:
    return "–" if v is None else f"{v:g}{unit}"


def _hn_window(years: int) -> str:
    return "all time" if years == 0 else f"last {years} year" + ("s" if years > 1 else "")


def _model_compare_lines(infos: list, years: int, *, links: bool) -> list[str]:
    """One row per model, then each model's biggest HN thread."""
    rows = [["model", "released", "$in/$out", "context", "max out", "hosts", "fastest host", "quickest 1st token"]]
    for i in infos:
        fast = max(i.hosts, key=lambda h: h.tok_s or 0, default=None)
        quick = min((h for h in i.hosts if h.latency_ms is not None), key=lambda h: h.latency_ms, default=None)
        rows.append([
            i.id,
            i.released,
            f"{_num(i.price_in)}/{_num(i.price_out)}",
            _big(i.context),
            _big(i.max_output),
            str(len(i.hosts)),
            f"{round(fast.tok_s)} tok/s {fast.name}" if fast and fast.tok_s else "–",
            f"{quick.latency_ms / 1000:.1f} s {quick.name}" if quick else "–",
        ])
    out = _table(rows)
    out += ["", "$ per M tokens, the cheapest host's listed price; speeds are the gateway's last hour.", "", f"HN, {_hn_window(years)}:"]
    for i in infos:
        t = i.threads[0] if i.threads else None
        if t is None:
            out.append(f"  {i.id}: no stories found")
            continue
        out.append(f"  {i.id}: {t.story.points}pts {t.story.num_comments}c  {_link(t.story.title, t.url, links)}")
        if t.top:
            out.append(f"      {_link(t.top.author, t.top.url, links)}: {_clip(t.top.text)}")
    return out


def _model_info_lines(i, years: int, *, links: bool) -> list[str]:
    """Human view. On a terminal, titles and comments are clickable (OSC 8); piped, the URL is
    printed instead so an agent can follow it."""

    def link(text: str, url: str) -> str:
        return _link(text, url, links)

    out = [
        f"{i.name}  vercel:{i.id}  · released {i.released}",
        (
            f"${_num(i.price_in)} in · ${_num(i.price_out)} out · ${_num(i.cache_read)} cache read, per M tokens"
            f" · {_big(i.context)} context · {_big(i.max_output)} max out"
        ),
    ]
    if i.tags:
        out.append(", ".join(i.tags))
    if i.note:
        out.append(f"ko: {i.note}")
    out += ["", f"{'host':<18}{'$in/$out':<12}{'1st token':<11}{'tok/s':<7}{'quant':<7}uptime 1d"]
    for h in i.hosts:
        first = "–" if h.latency_ms is None else f"{h.latency_ms / 1000:.1f} s"
        uptime = "–" if h.uptime_1d is None else f"{h.uptime_1d:.2f}%"
        out.append(
            f"{h.name:<18}{_num(h.price_in) + '/' + _num(h.price_out):<12}{first:<11}"
            f"{_num(h.tok_s and round(h.tok_s)):<7}{h.quantization or '–':<7}{uptime}"
        )
    if i.others:
        out.append("also: " + ", ".join(f"{oid} ({d})" for oid, d in i.others))
    window = _hn_window(years)
    out += ["", f"HN, {window}:" if i.threads else f"HN, {window}: no stories found"]
    for t in i.threads:
        st = t.story
        out.append(f"  {st.points:>5}pts {st.num_comments:>5}c  {st.created_at:%Y-%m-%d}  {link(st.title, t.url)}")
        if t.top:
            out.append(f"      {link(t.top.author, t.top.url)}: {_clip(t.top.text)}")
        if t.reply:
            out.append(f"        ↳ {link(t.reply.author, t.reply.url)}: {_clip(t.reply.text, 140)}")
    return out


@app.command("ai")
def ai_cmd(
    prompt: str = typer.Argument(
        None, help="what you want done/answered; omit to enter interactive mode"
    ),
    model: str = typer.Option(
        None,
        "--model",
        "-m",
        help="model override (default: the basic tier)",
        autocompletion=models_mod.available_models,
    ),
    resume: str = typer.Option(
        None, "--resume", "-r", help="resume a saved session id"
    ),
) -> None:
    """ko's default agent — all toolsets (web, papers, HN, movies, ~/code files read-only)
    + its own memory. One-shot or REPL; sessions saved; capped at 30 model requests/run."""
    try:
        if prompt:
            agents.ai_run(prompt, model=model, resume=resume)
        else:
            agents.ai_repl(model=model, resume=resume)
    except FileNotFoundError:
        _die(f"no saved session {resume!r} — see `ko agent sessions`")
    except typer.Exit:
        raise
    except Exception as e:
        _die(str(e))


# --- agent commands ---


@agent_app.command("research")
def agent_research(
    prompt: str = typer.Argument(
        None, help="research prompt; omit to enter interactive mode"
    ),
    model: str = typer.Option(
        None,
        "--model",
        "-m",
        help="model override, e.g. vercel:zai/glm-5.3-flash",
    ),
    resume: str = typer.Option(
        None,
        "--resume",
        "-r",
        help="resume a saved session id (enters interactive mode)",
    ),
) -> None:
    """Research agent across web (exa), papers (arxiv, hf), and HN. No prompt = interactive REPL."""
    try:
        if prompt:
            agents.research_run(
                prompt, model=model, resume=resume
            )  # prints itself; resume continues a session
        else:
            agents.research_repl(model=model, resume=resume)
    except FileNotFoundError:
        _die(f"no saved session {resume!r} — see `ko agent sessions`")
    except typer.Exit:
        raise
    except Exception as e:
        _die(str(e))


@agent_app.command("repo")
def agent_repo(
    prompt: str = typer.Argument(
        None,
        help="what to find/explain, naming the repo(s) if you can — e.g. "
        "'how does refs/llm register plugins?'; omit for interactive mode",
    ),
    model: str = typer.Option(
        None, "--model", "-m", help="model override (default: basic tier)"
    ),
    resume: str = typer.Option(
        None,
        "--resume",
        "-r",
        help="resume a saved session id (enters interactive mode)",
    ),
) -> None:
    """Repo-explorer: answers 'how does repo X do Y' over ~/code (read-only; own repos +
    refs/ reference clones; knows the refs/CLAUDE.md map). Cheap model, cites file:line."""
    try:
        if prompt:
            agents.repo_run(prompt, model=model, resume=resume)
        else:
            agents.repo_repl(model=model, resume=resume)
    except FileNotFoundError:
        _die(f"no saved session {resume!r} — see `ko agent sessions`")
    except typer.Exit:
        raise
    except Exception as e:
        _die(str(e))


@agent_app.command("tv")
def agent_tv(
    prompt: str = typer.Argument(
        None, help="what you're in the mood for; omit to enter interactive mode"
    ),
    model: str = typer.Option(None, "--model", "-m", help="model override"),
    resume: str = typer.Option(
        None,
        "--resume",
        "-r",
        help="resume a saved session id (enters interactive mode)",
    ),
) -> None:
    """TV/movie agent — what to watch in Australia, tuned to Ko. No prompt = interactive REPL."""
    try:
        if prompt:
            agents.tv_run(prompt, model=model, resume=resume)
        else:
            agents.tv_repl(model=model, resume=resume)
    except FileNotFoundError:
        _die(f"no saved session {resume!r} — see `ko agent sessions`")
    except typer.Exit:
        raise
    except Exception as e:
        _die(str(e))


sessions_app = typer.Typer(
    help="Saved agent sessions: bare = list; `summarize` = build the SQLite index.",
    invoke_without_command=True,
)
agent_app.add_typer(sessions_app, name="sessions")


@sessions_app.callback()
def agent_sessions(
    ctx: typer.Context,
    as_json: bool = typer.Option(False, "--json", help="emit JSON instead of TSV"),
    tag: str = typer.Option(
        None, "--tag", help="filter to sessions tagged with this word"
    ),
    search: str = typer.Option(
        None, "--search", help="case-insensitive substring filter over title + summary"
    ),
) -> None:
    """List saved agent sessions, newest first (TSV: id, agent, model, title, summary, tags).

    Title and tags come from the DB index when summarized (run `ko agent sessions summarize`);
    unsummarized sessions show the raw first-prompt title with no summary.
    Use --tag or --search to filter after summarizing.
    """
    if ctx.invoked_subcommand:
        _reject_group_flags(ctx, as_json="--json", tag="--tag", search="--search")
        return
    from ko import sessions

    rows = sessions.listing(tag=tag, search=search)
    if not rows:
        # "no sessions yet" would mislead when a filter excluded everything
        if tag or search:
            what = f"tag '{tag}'" if tag else f"search '{search}'"
            _no_results(f"no sessions match {what}", as_json)
        _no_results("no sessions yet", as_json)
    if as_json:
        typer.echo(json.dumps(rows, default=str))
        return
    for s in rows:
        tags_str = ",".join(s.get("tags") or [])
        summary = _tsv_cell(
            s.get("summary") or ""
        )  # LLM-written free text — escape for TSV
        title = _tsv_cell(s.get("title") or "")
        typer.echo(
            f"{s['id']}\t{s['agent']}\t{s['model']}\t{title}\t{summary}\t{tags_str}"
        )


@sessions_app.command("summarize")
def agent_sessions_summarize(
    n: int = typer.Option(
        None, "--n", "-n", help="max sessions to summarize this run (default: all pending)"
    ),
    model: str = typer.Option(
        None,
        "--model",
        "-m",
        help="model for summarization (default: KO_DEFAULT_MODEL)",
    ),
) -> None:
    """Summarize unsummarized (or stale) agent sessions into the SQLite index.

    For each session file that has no DB row or whose file mtime is newer than
    the last summarized_at timestamp, makes ONE cheap model call (structured output)
    to extract a title (≤8 words), a one-sentence summary, and 3-5 topic tags.
    Writes results to ~/.local/state/ko/ko.db.

    Output: one TSV line per newly summarized session (id, title, tags) on stdout.
    Progress/counts on stderr. Exit 0 on any success; exit 1 if all attempted calls failed.
    """
    from datetime import datetime

    from pydantic import BaseModel
    from pydantic_ai import Agent

    from ko import sessions
    from ko.llm import default_model

    class SessionSummary(BaseModel):
        title: str
        summary: str
        tags: list[str]

    mdl = (
        model or default_model()
    )  # default_model already resolves env → config → basic tier

    _summarizer = Agent(
        instructions=(
            "You summarize agent conversation sessions. "
            "Given a digest of a user–AI session, return:\n"
            "- title: ≤8 words capturing the core topic (not a generic label)\n"
            "- summary: ONE sentence with the useful takeaway (what was found/decided, not just the topic)\n"
            "- tags: 3-5 lowercase single words for topic filtering\n"
            "Be specific. 'Researched pydantic-ai agents' is bad; "
            "'How pydantic-ai FunctionToolsets compose with streaming' is good."
        ),
        output_type=SessionSummary,
    )

    sdir = sessions.sessions_dir()
    all_files = sorted(sdir.glob("*.json"), reverse=True)

    pending: list[tuple[Path, dict]] = []
    already_current = 0

    for f in all_files:
        try:
            data = json.loads(f.read_text())
        except OSError, ValueError:
            continue
        sid = data.get("id", f.stem)
        row = sessions.get_session_row(sid)
        if row:
            # stale if the file was modified after the last summarize
            # Use float timestamps for sub-second accuracy (isoformat at seconds
            # precision can round a 10ms touch into the same second as summarized_at)
            file_mtime_f = f.stat().st_mtime
            summarized_at_str = row.get("summarized_at") or ""
            summarized_at_f: float = 0.0
            if summarized_at_str:
                try:
                    summarized_at_f = datetime.fromisoformat(
                        summarized_at_str
                    ).timestamp()
                except ValueError:
                    summarized_at_f = 0.0
            if file_mtime_f <= summarized_at_f:
                already_current += 1
                continue
        pending.append((f, data))

    if n is not None:
        pending = pending[:n]

    if not pending:
        typer.echo(
            f"0 sessions summarized ({already_current} already current)", err=True
        )
        return

    succeeded = 0
    failed = 0
    skipped = 0  # no usable content — reported separately, NOT "already current"
    spent = 0.0  # summed actual/estimated USD across the model calls

    for f, data in pending:
        sid = data.get("id", f.stem)
        msgs = data.get("messages", [])
        digest = sessions._digest(msgs)
        if not digest.strip():
            typer.echo(f"skip {sid}: no usable content", err=True)
            skipped += 1
            continue
        try:
            result = _summarizer.run_sync(
                f"Summarize this session:\n\n{digest}", model=models_mod.resolve(mdl)
            )
            s: SessionSummary = result.output
            sessions.upsert_session_summary(
                id=sid,
                agent=data.get("agent", "unknown"),
                model=data.get("model", "unknown"),
                title=s.title,
                summary=s.summary,
                tags=s.tags,
                created_at=data.get("created_at", ""),
            )
            rc = llm_mod.run_cost(result.all_messages())
            spent += rc.usd or 0.0
            tags_str = ",".join(s.tags)
            typer.echo(f"{sid}\t{_tsv_cell(s.title)}\t{tags_str}")
            succeeded += 1
        except Exception as e:
            typer.echo(f"error summarizing {sid}: {e}", err=True)
            failed += 1

    skipped_note = f", {skipped} skipped (no content)" if skipped else ""
    cost_note = f" · ${spent:.4f}" if spent else ""
    typer.echo(
        f"{succeeded} session{'s' if succeeded != 1 else ''} summarized "
        f"({already_current} already current{skipped_note}){cost_note}",
        err=True,
    )
    if failed and succeeded == 0:
        raise typer.Exit(1)


# --- ticktick commands ---


@tt_app.command("lists")
def tt_lists(
    json_out: bool = typer.Option(False, "--json", help="JSON instead of TSV"),
) -> None:
    """List your TickTick lists (TSV: id, name)."""
    try:
        projects = ticktick_mod.list_projects()
    except Exception as e:
        _die(str(e), as_json=json_out)
    if not projects:
        _no_results("no lists", json_out)
    if json_out:
        _emit_json(projects)
        return
    for p in projects:
        typer.echo(f"{p.id}\t{p.name}")


@tt_app.command("items")
def tt_items(
    list_name: str = typer.Argument(
        ..., help="list name (substring/emoji ok) or id; `inbox` works"
    ),
    json_out: bool = typer.Option(False, "--json", help="JSON instead of TSV"),
) -> None:
    """Open tasks in a list (TSV: priority, due, title). `ko tt <list>` is a shortcut.

    Open (undone) tasks only — completed ones are reachable via `ko tt search`. Due dates
    render in the ko cal timezone: 'YYYY-MM-DD' all-day, 'YYYY-MM-DD HH:MM' timed."""
    try:
        proj = ticktick_mod.resolve_list(list_name)
        if not proj:
            _die(f"no list matching {list_name!r}", as_json=json_out, code="not_found")
        tasks = ticktick_mod.get_tasks(proj.id)
    except typer.Exit:
        raise
    except Exception as e:
        _die(str(e), as_json=json_out)
    if not tasks:
        _no_results(f"{proj.name}: no open tasks", json_out)
    if json_out:
        _emit_json(tasks)
        return
    for t in tasks:
        typer.echo(f"{t.priority}\t{t.due or '—'}\t{_tsv_cell(t.title)}")


@tt_app.command("add")
def tt_add(
    title: str = typer.Argument(..., help="task title"),
    list_name: str = typer.Argument(
        None, metavar="[LIST]", help="list name (substring/emoji ok) or id; default: your Inbox"
    ),
    note: str = typer.Option(
        None, "--note", help="markdown body (the task's `content`) — renders in the app"
    ),
    due: str = typer.Option(
        None,
        "--due",
        help="today | tomorrow | YYYY-MM-DD (all-day) | YYYY-MM-DDTHH:MM (timed), in the "
        "ko cal timezone",
    ),
    priority: str = typer.Option(
        None, "--priority", "-p", help="none|low|med|high (default: none)"
    ),
    tag: list[str] = typer.Option(None, "--tag", help="tag to attach (repeatable)"),
    json_out: bool = typer.Option(False, "--json", help="JSON instead of TSV"),
) -> None:
    """Create a task (TSV: id, list, title). No LIST → your Inbox.

    --note is markdown, written verbatim into `content` and rendered by TickTick. Quirks:
    single `~text~` is UNDERLINE (strikethrough is `~~text~~`), `::text::` is highlight,
    headings stop at 3 levels. Quote it and use real newlines: --note $'# heading\\n- item'.
    """
    level = 0
    if priority is not None:
        if priority.lower() not in ticktick_mod.PRIORITIES:
            _die(
                f"bad --priority {priority!r}: pick one of "
                f"{', '.join(sorted(set(ticktick_mod.PRIORITIES)))}",
                as_json=json_out,
                code="usage",
            )
        level = ticktick_mod.PRIORITIES[priority.lower()]
    if due:
        try:  # fail as a usage error here, before we create anything
            ticktick_mod.parse_due(due)
        except ValueError:
            _die(
                f"bad --due {due!r}: use today | tomorrow | YYYY-MM-DD | YYYY-MM-DDTHH:MM",
                as_json=json_out,
                code="usage",
            )
    try:
        proj = None
        if list_name:
            proj = ticktick_mod.resolve_list(list_name)
            if not proj:
                _die(f"no list matching {list_name!r}", as_json=json_out, code="not_found")
        task = ticktick_mod.create_task(
            title,
            projectId=proj.id if proj else None,
            content=note,
            due=due,
            priority=level,
            tags=list(tag) if tag else None,
        )
    except typer.Exit:
        raise
    except Exception as e:
        _die(str(e), as_json=json_out)
    if json_out:
        _emit_json([task])
        return
    typer.echo(f"{task.id}\t{proj.name if proj else 'Inbox'}\t{_tsv_cell(task.title)}")


@tt_app.command("done")
def tt_done(
    query: str = typer.Argument(..., help="task id, or a substring of its title"),
    list_name: str = typer.Option(
        None, "--list", "-l", help="only look in this list (name substring/emoji ok, or id)"
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="skip the TTY confirmation"),
    json_out: bool = typer.Option(False, "--json", help="JSON instead of TSV"),
) -> None:
    """Complete a task, matched by id or title substring (TSV: id, list, title).

    Searches your OPEN tasks (all lists, Inbox included, unless --list). Several matches =
    exit 1 with the candidates listed, never a guess — narrow the query or pass an id.
    Confirms on a TTY (`--yes` skips; pipes/agents are never prompted). Not reversible
    from here — un-complete in TickTick.
    """
    try:
        projects = ticktick_mod.list_projects()
        scope = None
        if list_name:
            proj = ticktick_mod.resolve_list(list_name, projects)
            if not proj:
                _die(f"no list matching {list_name!r}", as_json=json_out, code="not_found")
            scope = [proj.id]
        matches = ticktick_mod.find_tasks(query, ticktick_mod.open_tasks(scope))
    except typer.Exit:
        raise
    except Exception as e:
        _die(str(e), as_json=json_out)
    names = {p.id: p.name for p in projects}

    def _list_of(task) -> str:
        return names.get(task.project_id, "Inbox" if task.project_id.startswith("inbox") else "?")

    if not matches:
        where = f" in {list_name!r}" if list_name else ""
        _die(f"no open task matching {query!r}{where}", as_json=json_out, code="not_found")
    if len(matches) > 1:
        note = f"{len(matches)} open tasks match {query!r} — narrow it or pass an id"
        if json_out:
            # the {error, code} contract, plus the candidates so an agent can pick an id
            payload = {"error": note, "code": "ambiguous", "matches": [asdict(m) for m in matches]}
            typer.echo(json.dumps(payload), err=True)
        else:
            typer.echo(f"{note}:", err=True)
            for m in matches:
                typer.echo(f"  {m.id}\t{_list_of(m)}\t{_tsv_cell(m.title)}", err=True)
        raise typer.Exit(1)
    task = matches[0]
    if not yes and sys.stdin.isatty():
        typer.confirm(f"complete {task.title!r} in {_list_of(task)}?", abort=True)
    try:
        ticktick_mod.complete_task(task.project_id, task.id)
    except Exception as e:
        _die(str(e), as_json=json_out)
    if json_out:
        _emit_json([task])
        return
    typer.echo(f"{task.id}\t{_list_of(task)}\t{_tsv_cell(task.title)}")


@tt_app.command("search")
def tt_search(
    query: str = typer.Argument(..., help="keyword(s) to match against titles and bodies"),
    n: int = typer.Option(20, "-n", "--n", help="max results (default 20)"),
    json_out: bool = typer.Option(False, "--json", help="JSON instead of TSV"),
) -> None:
    """Keyword search over every task, open and completed (TSV: id, list, title, url).

    Matches the markdown body (`content`) as well as the title. Its index trails writes by
    ~5-10s — a task you just added won't be found yet (`items`/`done` read live).
    """
    try:
        hits = ticktick_mod.search(query)
    except Exception as e:
        _die(str(e), as_json=json_out)
    if not hits:
        _no_results(f"no tasks matching {query!r}", json_out)
    hits = hits[:n]
    if json_out:
        _emit_json(hits)
        return
    for h in hits:
        typer.echo(f"{h.id}\t{h.project}\t{_tsv_cell(h.title)}\t{h.url}")


# --- publish commands ---


@publish_app.command("new")
def publish_new(
    path: str = typer.Argument(..., help="folder to scaffold (created if missing)"),
    title: str = typer.Option(
        None, "--title", "-t", help="page title (default: folder name)"
    ),
    md: bool = typer.Option(
        False, "--md", help="markdown doc site (write .md, README is the hub)"
    ),
    bare: bool = typer.Option(
        False, "--bare", help="just a CLAUDE.md of hints; build from scratch"
    ),
    hono: bool = typer.Option(
        False, "--hono", help="Hono worker site — backend-ready (API routes, D1/R2)"
    ),
    pin: bool = typer.Option(
        False, "--pin", help="PIN-gate it (implies --hono; generates a 6-digit PIN)"
    ),
    name: str = typer.Option(
        None,
        "--name",
        "-n",
        help="worker/subdomain name (default: folder, or parent if folder is generic)",
    ),
) -> None:
    """Scaffold a site to publish. Default: static (Tailwind + Alpine). `--md` / `--bare` / `--hono`."""
    if pin:
        hono = True
    if sum([md, bare, hono]) > 1:
        typer.echo("--md / --bare / --hono are mutually exclusive", err=True)
        raise typer.Exit(2)
    mode = "md" if md else "bare" if bare else "hono" if hono else "static"
    folder = Path(path)
    written = publish_mod.scaffold(folder, title=title, mode=mode, pin=pin, name=name)
    if written:
        for p in written:
            typer.echo(f"created {p}")
    else:
        typer.echo(f"{folder} already scaffolded (nothing overwritten)", err=True)
    edit = {"md": "README.md", "hono": "public/README.md"}.get(mode, "index.html")
    if mode == "bare":
        hint = f"build an index.html in {folder}, then: ko publish {folder}"
    else:
        hint = f"edit {folder}/{edit}, then: ko publish {folder}"
    typer.echo(f"\n{hint}", err=True)


@publish_app.command("preview")
def publish_preview(
    path: str = typer.Argument(".", help="folder to preview (default: current dir)"),
    port: int = typer.Option(
        None, "--port", "-p", help="port (default: wrangler's 8787)"
    ),
) -> None:
    """Preview a folder locally with `wrangler dev` (http://localhost:8787). Serves over http so ES
    modules + fetch work (a `file://` open doesn't); for --hono it runs the real worker. Ctrl-C to stop."""
    try:
        code = publish_mod.preview(Path(path), port=port)
    except RuntimeError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from None
    raise typer.Exit(code)


@publish_app.command("up")
def publish_up(
    path: str = typer.Argument(".", help="folder to publish (default: current dir)"),
    name: str = typer.Option(
        None,
        "--name",
        "-n",
        help="subdomain/name (default: derived from folder, sticky)",
    ),
    force: bool = typer.Option(
        False, "--force", "-f", help="take over an existing subdomain/Worker name"
    ),
    pin: str = typer.Option(
        None,
        "--pin",
        help="set/rotate the gate PIN on a --hono site ('new' = random 6-digit)",
    ),
) -> None:
    """Deploy a folder to Cloudflare. Prints the URL. Re-running overwrites the same URL."""
    try:
        if pin is not None:
            publish_mod.set_pin(
                Path(path), pin
            )  # rotate before deploy so the new PIN ships
        url = publish_mod.deploy(Path(path), name=name, force=force)
    except RuntimeError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from None
    if url:
        typer.echo(url)  # stdout: the URL itself (pipeable)
        gate_pin = publish_mod.config_pin(Path(path))
        code = publish_mod.check_url(url)  # sanity check: is it actually live?
        if gate_pin:
            typer.echo(f"🔒 PIN: {gate_pin}", err=True)
            note = (
                "✓ live (PIN gate active)"
                if code in (200, 401)
                else (
                    f"⚠ deployed but HTTP {code}"
                    if code
                    else "⚠ deployed but not reachable yet"
                )
            )
        elif code == 200:
            note = "✓ live (HTTP 200)"
        elif code:
            note = f"⚠ deployed but HTTP {code} (cert may still be provisioning)"
        else:
            note = "⚠ deployed but not reachable yet (cert may be provisioning)"
        typer.echo(note, err=True)
    else:
        typer.echo(
            "deployed — couldn't parse the URL; run `wrangler deployments list`",
            err=True,
        )


@publish_app.command("list")
def publish_list(
    cf: bool = typer.Option(
        False,
        "--cf",
        help="reconcile against Cloudflare: append account Workers this machine's registry doesn't know",
    ),
    as_json: bool = typer.Option(False, "--json", help="emit JSON instead of TSV"),
) -> None:
    """List everything published (TSV: name, url, folder, last published).

    The registry is machine-local; `--cf` asks the Cloudflare account for the full Worker
    list and appends the ones published elsewhere (their folder/date show as `-`)."""
    rows = publish_mod.published()
    extra: list[dict] = []
    if cf:
        names = publish_mod.account_workers()
        if names is None:
            typer.echo(
                "⚠ can't reach Cloudflare (creds? see `ko doctor`) — local registry only",
                err=True,
            )
        else:
            known = {p.name for p in rows}
            domains = publish_mod.worker_domains()
            extra = [
                {
                    "name": n,
                    "url": f"https://{domains[n]}" if n in domains else "",
                    "folder": "",
                    "updated_at": "",
                }
                for n in names
                if n not in known
            ]
    if not rows and not extra:
        _no_results("nothing published yet", as_json)
    if as_json:
        typer.echo(json.dumps([asdict(p) for p in rows] + extra, default=str))
        return
    for p in rows:
        typer.echo(f"{p.name}\t{p.url}\t{p.folder}\t{p.updated_at[:10]}")
    for e in extra:
        typer.echo(f"{e['name']}\t{e['url'] or '-'}\t-\t-")


@publish_app.command("rm")
def publish_rm(
    target: str = typer.Argument(..., help="Worker name, or a published folder path"),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="skip the confirm (required when not a TTY)"
    ),
) -> None:
    """Delete a published site from Cloudflare and forget it locally.

    Removes the Worker; its custom domain (and that DNS record) detach with it. The folder
    and its files are untouched — `ko publish <dir>` brings the site back any time."""
    p = Path(target)
    # a folder only counts when it's an actual publish project (wrangler config or in the
    # registry) — otherwise `rm site` with a stray ./site dir would resolve, via the
    # generic-folder-name rule, to a DIFFERENT worker than the one named
    is_project = p.is_dir() and (
        (p / publish_mod.WRANGLER_CONFIG).exists()
        or str(p.resolve()) in publish_mod.registry()
    )
    name = publish_mod.resolve_name(p) if is_project else target
    if name != target:
        typer.echo(f"{target} → Worker {name!r}", err=True)
    url = next((r.url for r in publish_mod.published() if r.name == name), None)
    what = f"{name} ({url})" if url else name
    if not yes:
        if sys.stdin.isatty():
            typer.confirm(f"delete Worker {what} from Cloudflare?", abort=True)
        else:
            typer.echo("refusing to delete without a TTY — pass --yes", err=True)
            raise typer.Exit(2)
    try:
        publish_mod.delete_worker(name)
    except RuntimeError as e:
        if isinstance(e, publish_mod.WorkerNotFound) and publish_mod.forget(name):
            typer.echo(
                f"{name}: not on Cloudflare — removed from the local registry", err=True
            )
            return
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from None
    publish_mod.forget(name)
    typer.echo(f"deleted {what}", err=True)


# --- prompt helpers ---


def _prompt_names(incomplete: str) -> list[str]:
    return [n for n in prompt_mod.names() if n.startswith(incomplete)]


def _copy_clipboard(text: str) -> None:
    import subprocess

    try:
        subprocess.run(["pbcopy"], input=text.encode(), check=True)
    except (OSError, subprocess.CalledProcessError) as e:
        _die(f"couldn't copy to clipboard (pbcopy): {e}")


# --- prompt command ---


@app.command("prompt")
def prompt_cmd(
    name: str = typer.Argument(
        None, help="brief to print; omit to list all", autocompletion=_prompt_names
    ),
    as_json: bool = typer.Option(False, "--json", help="emit JSON instead of text"),
    copy: bool = typer.Option(
        False, "--copy", help="copy the brief to the clipboard (pbcopy)"
    ),
) -> None:
    """Kickoff briefs — my opinionated 'how I build X' notes to load into an agent.

    Bare `ko prompt` lists them (a table on a TTY, TSV when piped); `ko prompt <name>` prints one.
    Add your own in ~/.config/ko/prompts/*.md — a file there overrides a packaged brief of
    the same name. `--copy` puts the brief on the clipboard; `--json` for structured output.
    """
    if name is None:
        prompts = prompt_mod.list_prompts()
        if not prompts:
            _no_results("no briefs found", as_json)
        if as_json:
            typer.echo(
                json.dumps(
                    [
                        {
                            "name": p.name,
                            "description": p.description,
                            "source": p.source,
                        }
                        for p in prompts
                    ]
                )
            )
            return
        if not sys.stdout.isatty():  # piped: keep the TSV contract
            for p in prompts:
                typer.echo(f"{p.name}\t{p.description}")
            return
        from rich.console import Console
        from rich.table import Table
        from rich.text import Text

        table = Table(box=None, show_header=False, pad_edge=False, padding=(0, 2, 0, 0))
        table.add_column(style="bold cyan", no_wrap=True)
        table.add_column(overflow="fold")
        for p in prompts:
            # Text(), not a markup string: descriptions are prose (and user briefs in
            # ~/.config/ko/prompts/ are arbitrary), so `[llm] model` would be parsed as a
            # style tag and silently render as "  model".
            cell = Text(p.description)
            if p.source == "user":
                cell.append(" (user)", style="dim")
            table.add_row(p.name, cell)
        console = Console(highlight=False)
        console.print(
            "[bold]Briefs to paste into a coding agent[/bold] "
            "[dim]— my stack, my defaults, the parts that bite.[/dim]\n"
        )
        console.print(table)
        console.print(
            "[dim]ko prompt <name> to print one · --copy puts it on the clipboard[/dim]"
        )
        return
    try:
        p = prompt_mod.get_prompt(name)
    except KeyError:
        avail = ", ".join(prompt_mod.names()) or "(none)"
        _die(
            f"no brief named {name!r}. Available: {avail}",
            as_json=as_json,
            code="not_found",
        )
    if copy:
        _copy_clipboard(p.body)
        typer.echo(
            f"Copied '{p.name}' to the clipboard ({len(p.body):,} chars).", err=True
        )
        return
    if as_json:
        typer.echo(json.dumps(asdict(p)))
        return
    typer.echo(p.body)


# --- mcp commands ---


def _mcp_args(arg: list[str] | None, as_json: bool) -> dict:
    args: dict = {}
    for a in arg or []:
        k, sep, v = a.partition("=")
        if not sep:
            _die(f"bad --arg {a!r}; expected k=value", as_json=as_json, code="usage")
        args[k.strip()] = v.strip()
    return args


@mcp_app.command("inspect")
def mcp_inspect(
    server: str = typer.Argument(
        ...,
        help="server name (from ~/.config/ko/mcp.json) or a URL, e.g. http://localhost:5180/mcp",
    ),
    header: list[str] = typer.Option(
        None,
        "--header",
        "-H",
        help="extra header 'Key: Value' (overrides config; e.g. 'Authorization: Bearer ...'); repeatable",
    ),
    tool: str = typer.Option(
        None, "--tool", help="print one tool's full JSON input schema and exit"
    ),
    as_json: bool = typer.Option(False, "--json", help="emit JSON"),
) -> None:
    """Investigate an MCP server: its tools, resources, and prompts (+ capabilities and any server
    instructions). Target a configured name or a raw URL. `--tool <name>` dumps that tool's full
    input schema. On failure it prints the server's real HTTP status + body — so a 503 'not
    configured' is clearly a server issue, not your client. Listings to stdout; banner to stderr.
    """
    try:
        headers = mcp_client_mod.parse_headers(header)
    except mcp_client_mod.MCPTestError as e:
        _die(str(e), as_json=as_json, code="usage")
    try:
        spec = mcp_client_mod.resolve(server, headers)
        info = mcp_client_mod.inspect(spec)
    except mcp_client_mod.MCPTestError as e:
        _die(str(e), as_json=as_json, code="connect")
    if tool:
        match = next((t for t in info.tools if t.name == tool), None)
        if match is None:
            avail = ", ".join(t.name for t in info.tools) or "(none)"
            _die(
                f"no tool named {tool!r}. Tools: {avail}",
                as_json=as_json,
                code="not_found",
            )
        typer.echo(json.dumps(match.schema, indent=2, default=str))
        return
    if as_json:
        typer.echo(json.dumps(asdict(info), default=str))
        return
    typer.echo(f"✓ {info.name} v{info.version}  (protocol {info.protocol})", err=True)
    typer.echo(f"  capabilities: {', '.join(info.capabilities) or '(none)'}", err=True)
    if info.instructions:
        typer.echo(f"  instructions: {info.instructions[:200]}", err=True)

    def _section(title: str, items: list, render) -> None:
        if items:
            typer.echo(f"\n{title} ({len(items)}):")
            for it in items:
                typer.echo("  " + render(it))

    _section(
        "TOOLS",
        info.tools,
        lambda t: (
            f"{t.name}({', '.join(t.required)})"
            + (f"  —  {t.description}" if t.description else "")
        ),
    )
    _section(
        "RESOURCES",
        info.resources,
        lambda r: (
            f"{r.uri}"
            + (" [template]" if r.template else "")
            + (f"  —  {r.description}" if r.description else "")
        ),
    )
    _section(
        "PROMPTS",
        info.prompts,
        lambda p: (
            f"{p.name}({', '.join(p.arguments)})"
            + (f"  —  {p.description}" if p.description else "")
        ),
    )
    if not (info.tools or info.resources or info.prompts):
        typer.echo("(server exposes no tools, resources, or prompts)", err=True)


@mcp_app.command("call")
def mcp_call(
    server: str = typer.Argument(..., help="server name (from mcp.json) or URL"),
    tool: str = typer.Argument(..., help="tool name to call (see `ko mcp inspect`)"),
    arg: list[str] = typer.Option(None, "--arg", help="k=value argument; repeatable"),
    header: list[str] = typer.Option(
        None,
        "--header",
        "-H",
        help="extra header 'Key: Value' (overrides config); repeatable",
    ),
    as_json: bool = typer.Option(False, "--json", help="emit JSON"),
) -> None:
    """Call one tool on an MCP server and print its result to stdout."""
    try:
        headers = mcp_client_mod.parse_headers(header)
    except mcp_client_mod.MCPTestError as e:
        _die(str(e), as_json=as_json, code="usage")
    args = _mcp_args(arg, as_json)
    try:
        spec = mcp_client_mod.resolve(server, headers)
        out = mcp_client_mod.call(spec, tool, args)
    except mcp_client_mod.MCPTestError as e:
        _die(str(e), as_json=as_json, code="call")
    typer.echo(json.dumps({"tool": tool, "result": out}) if as_json else out)


@mcp_app.command("overview")
def mcp_overview(
    server: str = typer.Argument(..., help="server name (from mcp.json) or URL"),
    header: list[str] = typer.Option(
        None,
        "--header",
        "-H",
        help="extra header 'Key: Value' (overrides config); repeatable",
    ),
    model: str = typer.Option(
        None,
        "--model",
        "-m",
        help="agent model (default: KO_AGENT_MODEL, \\[agents] model, else the medium tier)",
    ),
) -> None:
    """An LLM agent connects to an MCP server, explores it, and summarizes what it's for and what's
    most useful — great for sizing up a server (incl. your own dev server). Makes a (cheap) model call.
    """
    from . import config

    try:
        headers = mcp_client_mod.parse_headers(header)
    except mcp_client_mod.MCPTestError as e:
        _die(str(e), code="usage")
    # via the tier table + the documented env → config.toml chain; a hardcoded model
    # string here silently drifted from TIERS once already.
    mdl = model or config.setting(
        "KO_AGENT_MODEL", "agents", "model", models_mod.model_for("medium")
    )
    try:
        spec = mcp_client_mod.resolve(server, headers)
        out = mcp_client_mod.overview(spec, models_mod.resolve(mdl))
    except mcp_client_mod.MCPTestError as e:
        _die(str(e), code="overview")
    typer.echo(out)


@app.command("billing")
def billing(
    as_json: bool = typer.Option(False, "--json", help="emit JSON"),
) -> None:
    """Balance + recent usage across paid services (read-only): AI Gateway, OpenRouter."""
    balances = [fn() for fn in billing_mod.PROVIDERS]
    if as_json:
        # key_capped is a property, so asdict() drops it — but it's the field an agent
        # actually wants ("why did that call 403?"), so put it in explicitly.
        payload = [{**asdict(b), "key_capped": b.key_capped} for b in balances]
        typer.echo(json.dumps(payload, default=str))
        return
    for b in balances:
        if b.error:
            typer.echo(f"{b.provider:12} {b.error}", err=True)
            continue
        left = f"${b.remaining:.2f} left" if b.remaining is not None else "—"
        of = f"  (of ${b.total:.0f}; ${b.used:.2f} used)" if b.total is not None else ""
        trend = f"  · {b.trend}" if b.trend else ""
        acct = f"  [{b.account}]" if b.account else ""
        typer.echo(f"{b.provider:12}{acct}  {left}{of}{trend}")
        if b.key_limit is not None:
            # The key's own cap. Printed second because it's the one that actually stops
            # calls — an exhausted key 403s while the account line above still looks fine.
            state = "CAP REACHED — calls will fail" if b.key_capped else f"${b.key_left:.2f} left"
            expires = f"; expires {b.key_expires[:10]}" if b.key_expires else ""
            typer.echo(
                f"{'':12}  key cap ${b.key_limit:.0f}: ${b.key_used:.2f} used, {state}{expires}",
                err=b.key_capped,
            )


@mcp_app.command("auth-info")
def mcp_auth_info(
    server: str = typer.Argument(..., help="server name (from mcp.json) or URL"),
    header: list[str] = typer.Option(
        None, "--header", "-H", help="extra header 'Key: Value'; repeatable"
    ),
    as_json: bool = typer.Option(False, "--json", help="emit JSON"),
) -> None:
    """Validate a remote server's OAuth discovery surface BEFORE connecting — the public `.well-known`
    metadata (protected-resource + auth-server), scopes, endpoints, DCR — and flag anything malformed
    (e.g. HTML where JSON should be). Spec-aware (RFC 9728 + RFC 8414). Exit 1 if problems are found.
    """
    try:
        headers = mcp_client_mod.parse_headers(header)
        spec = mcp_client_mod.resolve(server, headers)
    except mcp_client_mod.MCPTestError as e:
        _die(str(e), as_json=as_json, code="usage")
    if spec.get("transport") != "http":
        _die("auth-info is for remote (http) servers", as_json=as_json, code="usage")
    info = mcp_client_mod.auth_info(spec["url"], spec.get("headers"))
    if as_json:
        typer.echo(json.dumps(info, default=str))
        raise typer.Exit(1 if info.get("problems") else 0)
    typer.echo(
        f"MCP endpoint: HTTP {info.get('mcp_status', '?')}  (auth required: {info.get('requires_auth')})"
    )
    if info.get("www_authenticate"):
        typer.echo(f"  WWW-Authenticate: {info['www_authenticate']}")
    pr = info.get("protected_resource")
    if pr:
        typer.echo(f"\nProtected resource  [{info.get('protected_resource_url')}]")
        typer.echo(f"  resource:     {pr.get('resource')}")
        typer.echo(
            f"  scopes:       {', '.join(pr.get('scopes_supported', [])) or '(none)'}"
        )
        typer.echo(f"  auth servers: {', '.join(pr.get('authorization_servers', []))}")
    for asm in info.get("auth_servers", []):
        typer.echo(f"\nAuth server  [{asm.get('_discovered_at')}]")
        typer.echo(f"  issuer:       {asm.get('issuer')}")
        typer.echo(f"  authorize:    {asm.get('authorization_endpoint')}")
        typer.echo(f"  token:        {asm.get('token_endpoint')}")
        typer.echo(
            f"  registration: {asm.get('registration_endpoint') or '— (no DCR)'}"
        )
        typer.echo(
            f"  scopes:       {', '.join(asm.get('scopes_supported', [])) or '(none)'}"
        )
        typer.echo(
            f"  PKCE S256:    {'S256' in (asm.get('code_challenge_methods_supported') or [])}"
        )
    if info.get("problems"):
        typer.echo("\nPROBLEMS:", err=True)
        for prob in info["problems"]:
            typer.echo(f"  ✗ {prob}", err=True)
        raise typer.Exit(1)
    typer.echo("\n✓ discovery surface looks well-formed", err=True)


def _redact_server(cfg: dict) -> dict:
    """Copy a server config with secret-bearing header/env values masked. (A URL with inline
    userinfo/`?api_key=` isn't touched — don't put secrets in mcp.json URLs; use headers.)
    `ko mcp inspect -H` is how you pass a real token deliberately; `servers --json` never leaks one."""
    SECRET_KEYS = (
        "authorization",
        "token",
        "api-key",
        "apikey",
        "key",
        "secret",
        "password",
    )
    out = dict(cfg)
    for field in ("headers", "env"):
        d = cfg.get(field)
        if isinstance(d, dict):
            out[field] = {
                k: ("***" if any(s in k.lower() for s in SECRET_KEYS) else v)
                for k, v in d.items()
            }
    return out


@mcp_app.command("servers")
def mcp_servers(
    as_json: bool = typer.Option(False, "--json", help="emit JSON"),
) -> None:
    """List MCP servers configured in ~/.config/ko/mcp.json (standard `mcpServers` shape).
    TSV: name, transport, target. Use a name with `ko mcp inspect <name>` / `call <name> <tool>`."""
    try:
        servers = mcp_client_mod.load_servers()
    except mcp_client_mod.MCPTestError as e:
        _die(str(e), as_json=as_json, code="config")
    if not servers:
        _no_results(
            "no servers in ~/.config/ko/mcp.json (add an `mcpServers` object)", as_json
        )
    if as_json:
        # load_servers() expands ${ENV} placeholders, so headers/env carry live secrets —
        # redact them before printing to stdout (which gets piped/logged).
        typer.echo(json.dumps({n: _redact_server(c) for n, c in servers.items()}))
        return
    for name, cfg in sorted(servers.items()):
        if cfg.get("command"):
            kind, target = "stdio", " ".join([cfg["command"], *cfg.get("args", [])])
        else:
            kind, target = "http", cfg.get("url", "?")
        typer.echo(f"{name}\t{kind}\t{target}")


# --- brief command (root-level) ---


@app.command("brief")
def brief_cmd(
    raw: bool = typer.Option(
        False, "--raw", help="print gathered sections verbatim, skip the LLM entirely"
    ),
    model: str = typer.Option(
        None,
        "--model",
        "-m",
        help=f"model for synthesis (default: KO_DEFAULT_MODEL or {models_mod.TIERS["basic"]})",
        autocompletion=models_mod.available_models,
    ),
) -> None:
    """Morning brief: calendar + unread email + HN top 10 + AI papers → LLM synthesis.
    Prioritizes, not just concatenates — actionable email is triaged out of newsletter noise.

    Sources are best-effort: a misconfigured or unauthenticated source (e.g. no Google
    auth) becomes a one-liner note rather than an error. Use --raw to inspect gathered
    data or skip the LLM.

    Sends calendar and email content to the configured LLM. Use --raw for a local-only view.
    """
    sections = brief_mod.gather()

    if raw:
        typer.echo(brief_mod.render_raw(sections))
        return

    # If every section is a failure note (starts with '('), die cleanly rather than
    # paying for an empty synthesis. A section is a note if its text is a single-line
    # error note matching the _try() pattern.
    meaningful = [
        (t, txt)
        for t, txt in sections
        if not (txt.startswith(f"({t}:") and "\n" not in txt)
    ]
    if not meaningful:
        _die(
            "all sources failed — run `ko brief --raw` to see details, "
            "or check `ko doctor` for auth/key issues"
        )

    content = brief_mod.render_raw(sections)
    typer.echo("[gathering brief…]", err=True)
    try:
        result = llm_mod.run(brief_mod.BRIEF_SYSTEM, stdin=content, model=model)
    except Exception as e:
        _die(str(e))
    typer.echo(result)
    if llm_mod.last_cost:
        typer.echo(llm_mod.last_cost.note, err=True)
