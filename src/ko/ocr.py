"""OCR server for liteparse: liteparse renders the pages that need OCR, POSTs each image to
`/ocr`, and places the text we return by its box. Contract:
https://developers.llamaindex.ai/liteparse/guides/ocr/#custom-ocr-servers — multipart `file`
(PNG/JPG) + `language`, reply `{"results": [{"text", "bbox": [x1, y1, x2, y2], "confidence"}]}`,
pixels, origin top-left, reading order.

Engines (`--engine`), default the DeepSeek model:
- a vision model on the AI Gateway, `llm` = DeepSeek v4.1 Flash with thinking off: best on
  tables and equations in the sfl-mes bake-off (20 hard book pages, 2026-11-04: $0.0010 and
  4.1 s a page; thinking on took 3.6 min and $0.06 on one page). One result per image, covering
  the whole image: a model gives no boxes.
- `vision`: Apple Vision, free and on-device (~0.15 s a page). Reads scanned text well; returns
  garbage on equations and no table structure. Through liteparse, whose layout keeps columns
  side by side (a direct read interleaves them, tested 2026-11-06).
  Watch https://github.com/run-llama/liteparse/pull/521 (native `ocr_backend="macos-vision"`,
  open as of 2026-11-06): once it ships, drop vision-ocr.swift and this engine's server.
- `tesseract`: liteparse's built-in OCR, no server.

`ko doc` sends a model the page images itself (`transcribe`) and uses the transcript as the
page text: liteparse drops OCR that overlaps a text layer, which a whole-image box always does.
For Vision it uses `running()`: a server on a thread for the one parse (liteparse releases the
GIL), never a `ko ocr serve` already up, whose failures it couldn't see.
"""

from __future__ import annotations

import email
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import threading
from contextlib import contextmanager
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path

from ko import config

DEFAULT_PORT = 8828
DEFAULT_MODEL = "vercel:deepseek/deepseek-v4.1-flash-1017"
# Per image; Vision hung on a few pages in sfl-mes's test run. Under liteparse's own 60 s client
# timeout, so a slow image fails here rather than being retried on top of a call still running.
TIMEOUT_S = 50
LOCAL = ("vision", "tesseract")
VISION_BIN = "ko-vision-ocr"

# Thinking off, via the gateway's own `reasoning` field: DeepSeek thinks by default (~4,000
# reasoning tokens a page), and `providerOptions.deepseek.thinking` is ignored on this API (tested
# 2026-11-04). Gemini 3 can't go to zero, so low.
_NO_THINKING = {"google/": {"reasoning": {"effort": "low"}}}
_NO_THINKING_DEFAULT = {"reasoning": {"enabled": False}}

PROMPT = """Transcribe the text in this image into Markdown, exactly as printed.
- Keep the wording, numbers and units exactly. Do not correct, summarise, translate or add anything.
- Join the lines of a paragraph into one line; one blank line between paragraphs. Join words hyphenated at a line end.
- Tables: a Markdown pipe table, one row per printed row, with the caption in bold above it.
- Equations: LaTeX, inline $...$ or display $$...$$, with the equation number as printed.
- Figures and diagrams: the labels, axis titles and values printed inside them, as text. Labels only: no description of the figure.
- Leave out the running header, running footer and page number.
- If something cannot be read, write [illegible]. Never guess.
- If the image has no text, output nothing.
Output only the Markdown."""


def engine(name: str | None = None) -> str:
    """The engine to use: `name`, else $KO_OCR_ENGINE / `[ocr] engine`, else the DeepSeek model.
    `llm` means that model; a bare gateway id (`google/gemini-3.8-flash`) gets `vercel:`."""
    name = name or config.setting("KO_OCR_ENGINE", "ocr", "engine", DEFAULT_MODEL)
    if name in LOCAL:
        return name
    if name == "llm":
        return DEFAULT_MODEL
    return name if ":" in name else f"vercel:{name}"


def spend_estimate(name: str, pages: int) -> tuple[float, float] | None:
    """(low, high) USD for a model to read `pages` pages; None when its price isn't known. Tokens
    a page, measured 2026-11-06: ~1,200 in; ~400 out on prose, ~2,300 on a dense table."""
    from ko import models

    price = models.gateway_price(name.removeprefix("vercel:"))
    if not price:
        return None
    return tuple(pages * (1200 * price[0] + out * price[1]) for out in (400, 2300))


def workers(name: str) -> int:
    """Pages liteparse sends at once: Vision is CPU-bound, a gateway model is network-bound."""
    return (os.cpu_count() or 4) if name == "vision" else 8


# --- engines: image bytes -> liteparse results ---


@lru_cache
def _vision_bin() -> Path:
    """The Vision helper, compiled on first use into the cache (a folder per source hash).

    One name across rebuilds, since macOS keeps a model cache per binary name in
    ~/Library/Caches/<name>. That cache can go bad: a helper lost 4 of 40 lines on a clear page,
    exit 0, every run, until the folder was deleted (2026-11-06, trigger unknown)."""
    from ko import dirs

    src = files("ko").joinpath("vision-ocr.swift").read_bytes()
    final = dirs.cache_dir() / "bin" / hashlib.sha256(src).hexdigest()[:8]
    if (final / VISION_BIN).exists():
        return final / VISION_BIN
    if sys.platform != "darwin":
        raise RuntimeError("--engine vision needs macOS (Apple Vision); use the default model or tesseract")
    print("[ocr: building the Vision helper, once, ~30 s]", file=sys.stderr)
    # Built and warmed in a folder of its own, renamed when ready: a run that's interrupted, or a
    # second one at the same moment, never finds a half-built or unwarmed helper.
    tmp = final.with_name(f"{final.name}.{os.getpid()}")
    tmp.mkdir(parents=True, exist_ok=True)
    swift = tmp / f"{VISION_BIN}.swift"
    swift.write_bytes(src)
    try:
        r = subprocess.run(["swiftc", "-O", str(swift), "-o", str(tmp / VISION_BIN)], check=False, capture_output=True, text=True)
    except FileNotFoundError:
        raise RuntimeError("the Vision helper needs swiftc: xcode-select --install") from None
    if r.returncode:
        raise RuntimeError(f"building the Vision helper failed (needs Xcode command line tools): {r.stderr[:300]}")
    # macOS loads the recognizer on a binary's first read (12-58 s). Once here, unhurried, before
    # the pages arrive in parallel under their own timeout.
    r = subprocess.run([str(tmp / VISION_BIN), "--warm"], check=False, capture_output=True, text=True, timeout=300)
    if r.returncode:
        raise RuntimeError(f"the Vision helper failed its first read: {r.stderr[:300]}")
    try:
        tmp.rename(final)
    except OSError:  # another run got there first
        shutil.rmtree(tmp, ignore_errors=True)
    return final / VISION_BIN


def _vision(img: bytes) -> list[dict]:
    try:
        r = subprocess.run([str(_vision_bin()), "-"], check=False, input=img, capture_output=True, timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired:
        raise TimeoutError(f"Vision took over {TIMEOUT_S} s on an image") from None
    if r.returncode:
        raise RuntimeError(r.stderr.decode(errors="replace").strip() or f"vision-ocr exited {r.returncode}")
    return json.loads(r.stdout)["results"]


def image_size(img: bytes) -> tuple[int, int]:
    """(width, height) of a PNG or JPEG, read from its header."""
    if img[:8] == b"\x89PNG\r\n\x1a\n":
        return struct.unpack(">II", img[16:24])
    if img[:2] == b"\xff\xd8":
        i = 2
        while i + 9 < len(img):
            marker, length = img[i + 1], struct.unpack(">H", img[i + 2 : i + 4])[0]
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):  # a start-of-frame
                h, w = struct.unpack(">HH", img[i + 5 : i + 9])
                return w, h
            i += 2 + length
    raise ValueError("not a PNG or JPEG")


@lru_cache
def _loop():
    """The process's one event loop for model calls, running on its own thread."""
    import asyncio

    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True, name="ocr-model").start()
    return loop


class Model:
    """A gateway vision model, thinking off. `text(img)` is the transcript; calling it gives
    the server's one result covering the whole image. Costs add up across threads.

    Every call runs on one event loop for the process (`_loop`), on its own HTTP client
    (`resolve(fresh=True)`), since a client is tied to the loop that first used it: `run_sync`
    from several threads (a loop each) failed ~1 call in 10 at once with "bound to a different event loop", and so did
    a second Model on a loop of its own (8 threads, 2026-11-04)."""

    def __init__(self, name: str):
        from ko import models

        self.loop = _loop()
        self.name = name
        self.model = models.resolve(name, fresh=True)  # fails here, not per page, when the key is missing
        gid = name.removeprefix("vercel:")
        off = next((v for k, v in _NO_THINKING.items() if gid.startswith(k)), _NO_THINKING_DEFAULT)
        base = getattr(self.model, "settings", None) or {}
        self.settings = models._merge(dict(base), {"timeout": TIMEOUT_S, "extra_body": off})
        self.lock = threading.Lock()
        self.images = 0
        self.usd: float | None = 0.0  # None once any call has no known price
        self.tokens = [0, 0]

    def text(self, img: bytes) -> str:
        """Safe from any thread: the request runs on the Model's loop."""
        import asyncio

        return asyncio.run_coroutine_threadsafe(self._text(img), self.loop).result()

    async def _text(self, img: bytes) -> str:
        from pydantic_ai import Agent, BinaryContent

        from ko import llm

        media = "image/png" if img[:4] == b"\x89PNG" else "image/jpeg"
        r = await Agent(model=self.model, model_settings=self.settings).run([PROMPT, BinaryContent(img, media_type=media)])
        llm.raise_if_cut_short(r.all_messages(), self.name)
        with self.lock:  # run_cost adds to the command's log event: one thread at a time
            c = llm.run_cost(r.new_messages())
            self.images += 1
            self.usd = None if c.usd is None or self.usd is None else self.usd + c.usd
            self.tokens[0] += c.input_tokens
            self.tokens[1] += c.output_tokens
        return str(r.output).strip()

    def __call__(self, img: bytes) -> list[dict]:
        text = self.text(img)
        if not text:
            return []
        w, h = image_size(img)
        return [{"text": text, "bbox": [0, 0, w, h], "confidence": 1.0}]

    def note(self) -> str | None:
        """The stderr cost line, or None when nothing was sent."""
        if not self.images:
            return None
        from ko import llm

        label = f"ocr: {self.name.removeprefix('vercel:')} · {self.images} image(s)"
        return llm.RunCost(label, self.tokens[0], self.tokens[1], self.usd, "estimate").note


def transcribe(name: str, images: dict[int, bytes]) -> dict[int, str]:
    """Page number -> the model's transcript, pages in parallel. Raises naming every page that
    failed (after the rest finish, so their cost is noted)."""
    from concurrent.futures import ThreadPoolExecutor

    model = Model(name)
    with ThreadPoolExecutor(workers(name)) as pool:
        futures = {n: pool.submit(model.text, img) for n, img in images.items()}
    if note := model.note():
        print(note, file=sys.stderr)
    failed = {n: f.exception() for n, f in futures.items() if f.exception()}
    if failed:
        first = next(iter(failed.values()))
        raise RuntimeError(f"OCR failed on page(s) {', '.join(map(str, sorted(failed)))}: {type(first).__name__}: {first}")
    return {n: f.result() for n, f in futures.items()}


def reader(name: str):
    """The callable for an engine: image bytes -> results."""
    if name == "vision":
        _vision_bin()  # build (or fail) before the first page arrives
        return _vision
    if name == "tesseract":
        raise ValueError("tesseract runs inside liteparse; it has no server")
    return Model(name)


# --- the server ---


def _form(content_type: str, body: bytes) -> dict[str, bytes]:
    msg = email.message_from_bytes(b"Content-Type: " + content_type.encode() + b"\r\n\r\n" + body)
    if not msg.is_multipart():
        raise ValueError("expected multipart/form-data")
    return {p.get_param("name", header="content-disposition"): p.get_payload(decode=True) for p in msg.get_payload()}


def server(name: str, port: int = DEFAULT_PORT, read=None, host: str = "127.0.0.1") -> ThreadingHTTPServer:
    """A server for engine `name` (not started; call serve_forever). Port 0 = any free port.
    `srv.failed` holds the hashes of images whose last attempt got a 500: liteparse retries
    those, then keeps going without them, and a retry that works clears the image. A model engine's one whole-image box overlaps any text layer, and
    liteparse drops overlapping OCR, so it suits pages with no text at all; `ko doc` sends a
    model the page images directly instead."""
    read = read or reader(name)
    gate = threading.BoundedSemaphore(workers(name))

    class Handler(BaseHTTPRequestHandler):
        def _reply(self, code: int, obj: dict) -> None:
            out = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def do_GET(self):
            if self.path == "/health":
                return self._reply(200, {"engine": name})
            return self._reply(404, {"error": "POST /ocr, GET /health"})

        def do_POST(self):
            if self.headers.get("Origin"):  # a web page posting cross-site; liteparse sends none
                return self._reply(403, {"error": "browser requests refused"})
            if self.path != "/ocr":
                return self._reply(404, {"error": "POST /ocr"})
            key = None
            try:
                img = _form(self.headers.get("Content-Type", ""), self.rfile.read(int(self.headers["Content-Length"])))["file"]
                key = hashlib.sha256(img).digest()
                with gate:
                    results = read(img)
                srv.failed.discard(key)
                return self._reply(200, {"results": results})
            except (BrokenPipeError, ConnectionResetError):
                return None  # the client gave up on this image (its own timeout): nobody to answer
            except Exception as e:
                srv.failed.add(key)  # None: a request too broken to read
                print(f"[ocr: {type(e).__name__}: {str(e)[:200]}]", file=sys.stderr)
                return self._reply(500, {"error": str(e)[:500]})

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer((host, port), Handler)
    srv.daemon_threads = True
    srv.reader = read
    srv.failed = set()
    return srv


@contextmanager
def running(name: str):
    """(url, workers) for liteparse while the block runs; (None, None) for tesseract. Serves on
    a thread and stops it after, raising if any image still failed, since liteparse carries on
    without it and doesn't say so. Its own server every time: only this one's failures are visible."""
    if name == "tesseract":
        yield None, None
        return
    srv = server(name, port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{srv.server_port}/ocr", workers(name)
    finally:
        srv.shutdown()
        srv.server_close()
    if srv.failed:
        raise RuntimeError(f"OCR failed on {len(srv.failed)} image(s) after liteparse's retries; see the notes above")
