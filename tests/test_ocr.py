"""ko.ocr: the liteparse OCR-server contract, engines faked (no Vision, no gateway)."""

from __future__ import annotations

import threading

import httpx2 as httpx
import pytest

from ko import doc, ocr

PNG = ocr._BLANK_PNG


def test_engine_names_resolve(monkeypatch):
    monkeypatch.delenv("KO_OCR_ENGINE")
    assert ocr.engine() == ocr.DEFAULT_MODEL
    assert ocr.engine("llm") == ocr.DEFAULT_MODEL
    assert ocr.engine("vision") == "vision"
    assert ocr.engine("google/gemini-3.8-flash") == "vercel:google/gemini-3.8-flash"
    assert ocr.engine("openrouter:x/y") == "openrouter:x/y"


def test_image_size_reads_png_and_jpeg_headers():
    assert ocr.image_size(PNG) == (1, 1)
    sof = b"\xff\xd8" + b"\xff\xe0\x00\x04ab" + b"\xff\xc0\x00\x11\x08" + (300).to_bytes(2) + (200).to_bytes(2)
    assert ocr.image_size(sof + b"\x00" * 16) == (200, 300)
    with pytest.raises(ValueError):
        ocr.image_size(b"GIF89a")


@pytest.fixture
def served(monkeypatch):
    """A real server on a free port, its engine faked."""
    seen = []

    def fake(img):
        seen.append(img)
        return [{"text": "hello", "bbox": [0, 0, 1, 1], "confidence": 0.9}]

    monkeypatch.setattr(ocr, "reader", lambda name: fake)
    srv = ocr.server("vision", port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}", seen
    srv.shutdown()
    srv.server_close()


def test_server_speaks_liteparses_contract(served):
    base, seen = served
    r = httpx.post(f"{base}/ocr", files={"file": ("p.png", PNG, "image/png")}, data={"language": "eng"})
    assert r.json() == {"results": [{"text": "hello", "bbox": [0, 0, 1, 1], "confidence": 0.9}]}
    assert seen == [PNG]
    assert httpx.get(f"{base}/health").json() == {"engine": "vision"}
    assert httpx.post(f"{base}/nope").status_code == 404


def test_server_reports_an_engine_failure_as_500(monkeypatch):
    def boom(img):
        raise RuntimeError("vision broke")

    monkeypatch.setattr(ocr, "reader", lambda name: boom)
    srv = ocr.server("vision", port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        r = httpx.post(f"http://127.0.0.1:{srv.server_port}/ocr", files={"file": ("p.png", PNG, "image/png")})
        assert (r.status_code, r.json()["error"]) == (500, "vision broke")
    finally:
        srv.shutdown()
        srv.server_close()


def test_running_starts_a_server_for_the_parse_and_stops_it(monkeypatch):
    monkeypatch.setattr(ocr, "reader", lambda name: lambda img: [])
    with ocr.running("vision") as (url, n):
        assert url.endswith("/ocr") and ":8828/" not in url and n >= 1
        assert httpx.post(url, files={"file": ("p.png", PNG)}).json() == {"results": []}
    with pytest.raises(httpx.TransportError):
        httpx.post(url, files={"file": ("p.png", PNG)}, timeout=1)


def test_running_tesseract_has_no_server():
    with ocr.running("tesseract") as (url, n):
        assert (url, n) == (None, None)


def test_parse_pages_points_liteparse_at_the_server(tmp_path, monkeypatch):
    from types import SimpleNamespace as NS

    f = tmp_path / "scan.png"
    f.write_bytes(PNG)
    got = {}

    class FakeLP:
        def __init__(self, **kw):
            got.update(kw)

        def parse(self, path):
            return NS(pages=[NS(page_num=1, page_label=None, text="t", markdown="t")], total_pages=1)

    monkeypatch.setattr(doc, "LiteParse", FakeLP)
    monkeypatch.setattr(ocr, "reader", lambda name: lambda img: [])
    doc.parse_pages(f, engine="vision")
    assert got["ocr_enabled"] and got["ocr_server_url"].startswith("http://127.0.0.1:") and got["num_workers"]
    got.clear()
    doc.parse_pages(f)  # KO_OCR_ENGINE=tesseract (conftest): liteparse's own OCR, no server
    assert got["ocr_enabled"] and "ocr_server_url" not in got


def test_server_refuses_browser_posts(served):
    base, seen = served
    r = httpx.post(f"{base}/ocr", files={"file": ("p.png", PNG)}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403 and seen == []


def test_running_raises_when_an_image_failed(monkeypatch):
    def boom(img):
        raise TimeoutError("Vision took over 60 s")

    monkeypatch.setattr(ocr, "reader", lambda name: boom)
    with pytest.raises(RuntimeError, match="1 image"), ocr.running("vision") as (url, _):
        httpx.post(url, files={"file": ("p.png", PNG)})


def test_running_forgives_an_image_that_a_retry_read(monkeypatch):
    calls = []

    def flaky(img):
        calls.append(img)
        if len(calls) == 1:
            raise TimeoutError("slow")
        return []

    monkeypatch.setattr(ocr, "reader", lambda name: flaky)
    with ocr.running("vision") as (url, _):
        assert httpx.post(url, files={"file": ("p.png", PNG)}).status_code == 500
        assert httpx.post(url, files={"file": ("p.png", PNG)}).status_code == 200


def test_running_counts_images_not_attempts(monkeypatch):
    def boom(img):
        raise TimeoutError("slow")

    monkeypatch.setattr(ocr, "reader", lambda name: boom)
    with pytest.raises(RuntimeError, match="on 1 image"), ocr.running("vision") as (url, _):
        for _ in range(3):
            httpx.post(url, files={"file": ("p.png", PNG)})


class FakeModel:
    def __init__(self, name, fail=()):
        self.fail = fail
        self.images = 0

    def text(self, img):
        n = int(img)
        if n in self.fail:
            raise TimeoutError("slow")
        self.images += 1
        return f"| page {n} |"

    def note(self):
        return f"[ocr: fake · {self.images} image(s)]"


def test_transcribe_names_every_failed_page(monkeypatch, capsys):
    monkeypatch.setattr(ocr, "Model", lambda name: FakeModel(name, fail={2, 5}))
    with pytest.raises(RuntimeError, match=r"page\(s\) 2, 5: TimeoutError"):
        ocr.transcribe("vercel:x/y", {n: str(n).encode() for n in range(1, 7)})
    assert "4 image(s)" in capsys.readouterr().err  # the pages that ran are still noted


def _fake_doc(monkeypatch, texts, need):
    """liteparse faked: a text-layer read of `texts`, pages_needing_ocr = `need`, screenshots
    whose bytes are the page number."""
    from types import SimpleNamespace as NS

    class FakeLP:
        def __init__(self, ocr_enabled=False, **kw):
            assert not ocr_enabled, "a model engine must not go through liteparse's OCR merge"

        def parse(self, path):
            return NS(pages=[NS(page_num=i, page_label=None, text=t, markdown=t) for i, t in enumerate(texts, 1)], total_pages=len(texts))

        def screenshot(self, path, page_numbers):
            return [NS(page_num=n, image_bytes=str(n).encode(), is_solid_fill=False) for n in page_numbers]

    monkeypatch.setattr(doc, "LiteParse", FakeLP)
    monkeypatch.setattr(doc, "pages_needing_ocr", lambda p: need)
    monkeypatch.setattr(ocr, "Model", FakeModel)


def test_a_model_transcript_replaces_the_page_text_even_over_a_text_layer(tmp_path, monkeypatch):
    f = tmp_path / "bates.pdf"
    f.write_bytes(b"%PDF-")
    _fake_doc(monkeypatch, ["native one", "ABC000123", "native three"], need={2})  # page 2: a scan with a stamp
    d = doc.parse_pages(f, engine="llm")
    assert [p.text for p in d.pages] == ["native one", "| page 2 |", "native three"]
    out = doc.marked(d)
    assert "| page 2 |\n" in out and "```text\nnative one" in out  # the transcript isn't fenced


def test_office_files_ocr_only_pages_without_a_text_layer(tmp_path, monkeypatch):
    f = tmp_path / "deck.pptx"
    f.write_bytes(b"PK")
    _fake_doc(monkeypatch, ["slide one", "slide two"], need=set())
    assert [p.text for p in doc.parse_pages(f, engine="llm").pages] == ["slide one", "slide two"]


def test_an_image_is_ocrd_whole(tmp_path, monkeypatch):
    f = tmp_path / "scan.png"
    f.write_bytes(PNG)
    _fake_doc(monkeypatch, [""], need=set())  # an image has no text layer to ask about
    assert [p.text for p in doc.parse_pages(f, engine="llm").pages] == ["| page 1 |"]
