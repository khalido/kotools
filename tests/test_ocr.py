"""ko.ocr: the liteparse OCR-server contract, engines faked (no Vision, no gateway)."""

from __future__ import annotations

import threading

import httpx2 as httpx
import pytest

from ko import doc, ocr

PNG = bytes.fromhex(  # 1x1 white PNG
    "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753de0000000c4944415408d763f8ffff3f0005fe02fe"
    "a7356a810000000049454e44ae426082"
)


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


class _Parsed:
    def __init__(self, texts):
        from types import SimpleNamespace as NS

        self.pages = [NS(page_num=i, page_label=None, text=t, markdown=t) for i, t in enumerate(texts, 1)]
        self.total_pages = len(texts)


def _fake_local_ocr(monkeypatch, native, ocred, blank=()):
    """liteparse with a local engine: a text-layer read, then an OCR read of the pages asked for."""
    from types import SimpleNamespace as NS

    class FakeLP:
        def __init__(self, ocr_enabled=False, **kw):
            self.ocr = ocr_enabled

        def parse(self, path):
            return _Parsed(ocred if self.ocr else native)

        def screenshot(self, path, page_numbers):
            return [NS(page_num=n, image_bytes=str(n).encode(), is_solid_fill=n in blank) for n in page_numbers]

    monkeypatch.setattr(doc, "LiteParse", FakeLP)
    monkeypatch.setattr(doc, "pages_needing_ocr", lambda p: {1, 2, 3})
    monkeypatch.setattr(ocr, "reader", lambda name: lambda img: [])
    monkeypatch.setattr(ocr, "Model", FakeModel)


def test_an_empty_read_is_noted_and_never_pays(tmp_path, monkeypatch, capsys):
    f = tmp_path / "scan.pdf"
    f.write_bytes(b"%PDF-")
    _fake_local_ocr(monkeypatch, ["", "", ""], ["read", "", ""], blank={3})
    monkeypatch.setattr(ocr, "Model", lambda name: pytest.fail("spent money unasked"))
    d = doc.parse_pages(f, engine="vision")
    assert [p.text for p in d.pages] == ["read", "", ""]
    err = capsys.readouterr().err
    assert "page(s) 2;" in err and "--engine llm --pages 2" in err  # page 3 is blank, not a miss


def test_blank_pages_need_no_key(tmp_path, monkeypatch):
    f = tmp_path / "blank.pdf"
    f.write_bytes(b"%PDF-")
    _fake_doc(monkeypatch, [""], need={1})
    monkeypatch.setattr(doc, "_page_images", lambda p, pages, password: {})
    monkeypatch.setattr(ocr, "Model", lambda name: pytest.fail("built a model for a blank page"))
    assert [p.text for p in doc.parse_pages(f, engine="llm").pages] == [""]


def _scan(tmp_path, monkeypatch, pages: int, price=(0.3e-6, 1.2e-6)):
    from ko import models

    f = tmp_path / "book.pdf"
    f.write_bytes(b"%PDF-")
    _fake_doc(monkeypatch, [""] * pages, need=set(range(1, pages + 1)))
    monkeypatch.setattr(models, "gateway_price", lambda name: price)
    return f


def test_a_big_scan_stops_before_any_call_naming_the_cost(tmp_path, monkeypatch):
    f = _scan(tmp_path, monkeypatch, 51)
    monkeypatch.setattr(ocr, "Model", lambda name: pytest.fail("spent money unasked"))
    with pytest.raises(doc.SpendLimit, match=r"51 pages need OCR, about \$0\.04–0\.16 on deepseek.*KO_OCR_MAX_PAGES.*KO_OCR_ENGINE=vision"):
        doc.parse_pages(f, engine="llm")


def test_confirming_spends_and_a_small_job_never_asks(tmp_path, monkeypatch):
    assert len(doc.parse_pages(_scan(tmp_path, monkeypatch, 51), engine="llm", confirm_spend=True).pages) == 51
    assert len(doc.parse_pages(_scan(tmp_path, monkeypatch, 50), engine="llm").pages) == 50


def test_a_dear_model_stops_on_cost_under_the_page_limit(tmp_path, monkeypatch):
    f = _scan(tmp_path, monkeypatch, 40, price=(5e-6, 25e-6))
    with pytest.raises(doc.SpendLimit, match="40 pages"):
        doc.parse_pages(f, engine="llm")


def test_limits_come_from_config(tmp_path, monkeypatch):
    f = _scan(tmp_path, monkeypatch, 60)
    monkeypatch.setenv("KO_OCR_MAX_PAGES", "100")
    assert len(doc.parse_pages(f, engine="llm").pages) == 60
    monkeypatch.setenv("KO_OCR_MAX_PAGES", "5")
    with pytest.raises(doc.SpendLimit):
        doc.parse_pages(f, engine="llm")


def test_ko_doc_stop_exits_2_and_offers_its_own_flags(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from ko.cli import app

    f = _scan(tmp_path, monkeypatch, 51)
    r = CliRunner().invoke(app, ["doc", str(f), "-e", "llm"])
    assert r.exit_code == 2
    assert "--yes" in r.output and "KO_OCR_MAX_PAGES" not in r.output and "51 pages" in r.output


def test_a_doi_whose_paper_is_over_the_limit_says_so(monkeypatch):
    """Not the publisher's landing page passed off as the paper."""
    from ko import fetch, papers

    def too_big(url, save=True):
        if "oa.example" in url:
            raise doc.SpendLimit("60 pages need OCR.")
        pytest.fail("fell through to the landing page")

    monkeypatch.setattr(papers, "oa_url", lambda doi: "https://oa.example/p.pdf")
    real = fetch.fetch
    monkeypatch.setattr(fetch, "fetch", lambda url, save=True, **k: too_big(url, save) if "oa.example" in url else real(url, save=save, **k))
    with pytest.raises(doc.SpendLimit):
        real("https://doi.org/10.1000/xyz", save=False)


def test_the_agent_tool_reports_the_stop(monkeypatch):
    from ko import fetch
    from ko.agents import _toolsets

    def stop(url, save=True):
        raise doc.SpendLimit("60 pages need OCR, about $0.05–0.17 on deepseek.")

    monkeypatch.setattr(fetch, "fetch", stop)
    out = _toolsets.fetch_url("https://x.example/p.pdf")
    assert out.startswith("Not read: 60 pages") and "KO's OK" in out and "KO_OCR" not in out


def test_parse_keeps_the_text_layers_over_the_limit_and_says_so(tmp_path, monkeypatch):
    f = tmp_path / "mixed.pdf"
    f.write_bytes(b"%PDF-")
    _fake_doc(monkeypatch, ["page one"] * 60, need=set(range(1, 52)))
    monkeypatch.setattr(ocr, "Model", lambda name: pytest.fail("spent money unasked"))
    monkeypatch.setenv("KO_OCR_ENGINE", "llm")
    text = doc.parse(f)
    assert text.startswith("[ko: text layers only") and "51 pages need OCR" in text and "page one" in text


def test_parse_of_a_pure_scan_over_the_limit_still_fails(tmp_path, monkeypatch):
    f = _scan(tmp_path, monkeypatch, 51)
    monkeypatch.setenv("KO_OCR_ENGINE", "llm")
    with pytest.raises(doc.SpendLimit):
        doc.parse(f)
