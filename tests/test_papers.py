"""Offline tests for ko.papers — pure logic only, no network."""

import pytest

from ko import fetch, papers


# --- id resolution ---


@pytest.mark.parametrize(
    "ref,expected",
    [
        ("10.1002/jemt.20118", "doi:10.1002/jemt.20118"),
        ("doi:10.1002/jemt.20118", "doi:10.1002/jemt.20118"),
        ("https://doi.org/10.1002/jemt.20118", "doi:10.1002/jemt.20118"),
        ("2401.12345", "doi:10.48550/arxiv.2401.12345"),
        ("2401.12345v2", "doi:10.48550/arxiv.2401.12345"),
        ("https://arxiv.org/abs/2401.12345", "doi:10.48550/arxiv.2401.12345"),
        ("https://arxiv.org/pdf/2401.12345v1", "doi:10.48550/arxiv.2401.12345"),
        ("W2036113194", "W2036113194"),
        ("https://openalex.org/W2036113194", "W2036113194"),
    ],
)
def test_resolve_id(ref, expected):
    assert papers._resolve_id(ref) == expected


def test_resolve_id_rejects_garbage():
    with pytest.raises(ValueError):
        papers._resolve_id("not a paper")


@pytest.mark.parametrize(
    "ref,expected",
    [
        ("2401.12345", "arXiv:2401.12345"),
        ("https://arxiv.org/abs/2401.12345", "arXiv:2401.12345"),
        ("10.1002/jemt.20118", "DOI:10.1002/jemt.20118"),
    ],
)
def test_s2_id(ref, expected):
    assert papers._s2_id(ref) == expected


# --- abstract reconstruction ---


def test_reconstruct_abstract():
    inv = {"studied.": [3], "Graphene": [0], "is": [1], "widely": [2]}
    assert papers._reconstruct_abstract(inv) == "Graphene is widely studied."


def test_reconstruct_abstract_repeated_word():
    inv = {"the": [0, 2], "more": [1], "merrier": [3]}
    assert papers._reconstruct_abstract(inv) == "the more the merrier"


def test_reconstruct_abstract_empty():
    assert papers._reconstruct_abstract(None) == ""
    assert papers._reconstruct_abstract({}) == ""


# --- work parsing ---


def test_work_minimal():
    w = papers._work({"id": "https://openalex.org/W123", "title": "T\nT2"})
    assert w.openalex_id == "W123"
    assert w.title == "T T2"  # newlines flattened
    assert w.doi == ""
    assert w.oa_status == "closed"
    assert w.authors == []
    assert w.doi_url == ""


def test_work_full():
    w = papers._work(
        {
            "id": "https://openalex.org/W2036113194",
            "doi": "https://doi.org/10.1002/jemt.20118",
            "title": "eXfoliator",
            "publication_year": 2004,
            "cited_by_count": 743,
            "open_access": {"is_oa": True, "oa_status": "green", "oa_url": "https://x/pdf"},
            "primary_location": {"source": {"display_name": "Microscopy Res."}},
            "authorships": [
                {"author": {"display_name": "A One"}},
                {"author": {}},  # missing display_name is skipped
            ],
            "referenced_works": ["https://openalex.org/W1", "https://openalex.org/W2"],
        }
    )
    assert w.doi == "10.1002/jemt.20118"  # prefix stripped
    assert w.doi_url == "https://doi.org/10.1002/jemt.20118"
    assert w.oa_status == "green"
    assert w.journal == "Microscopy Res."
    assert w.authors == ["A One"]
    assert w.referenced_works == ["W1", "W2"]


def test_work_null_locations():
    # OpenAlex ships explicit nulls for missing locations/oa
    w = papers._work({"id": "x", "primary_location": None, "open_access": None})
    assert w.journal == ""
    assert w.oa_url == ""
    assert w.oa_pdf_urls == []
    assert w.full_text_urls == []


def test_oa_pdf_urls_dedup_and_order():
    d = {
        "best_oa_location": {"is_oa": True, "pdf_url": "https://repo/best.pdf"},
        "locations": [
            {"is_oa": True, "pdf_url": "https://repo/best.pdf"},  # dupe of best → dropped
            {"is_oa": True, "pdf_url": "https://mirror/other.pdf"},
            {"is_oa": False, "pdf_url": "https://paywalled/x.pdf"},  # not OA → skipped
            {"is_oa": True, "pdf_url": None},  # no pdf → skipped
        ],
    }
    assert papers._oa_pdf_urls(d) == [
        "https://repo/best.pdf",
        "https://mirror/other.pdf",
    ]


def test_full_text_urls_appends_landing_page():
    w = papers._work(
        {
            "id": "x",
            "open_access": {"oa_url": "https://publisher/landing"},
            "best_oa_location": {"is_oa": True, "pdf_url": "https://repo/paper.pdf"},
        }
    )
    # direct PDF first, landing page last
    assert w.full_text_urls == ["https://repo/paper.pdf", "https://publisher/landing"]


def test_full_text_urls_no_dupe_when_oa_url_is_the_pdf():
    w = papers._work(
        {
            "id": "x",
            "open_access": {"oa_url": "https://repo/paper.pdf"},
            "best_oa_location": {"is_oa": True, "pdf_url": "https://repo/paper.pdf"},
        }
    )
    assert w.full_text_urls == ["https://repo/paper.pdf"]


# --- fetch DOI routing regex ---


@pytest.mark.parametrize(
    "url,doi",
    [
        ("https://doi.org/10.1002/jemt.20118", "10.1002/jemt.20118"),
        ("http://dx.doi.org/10.3390/nano12071098", "10.3390/nano12071098"),
        ("doi:10.1002/jemt.20118", "10.1002/jemt.20118"),
        ("10.1002/jemt.20118", "10.1002/jemt.20118"),
    ],
)
def test_fetch_doi_re_matches(url, doi):
    m = fetch._DOI_RE.search(url)
    assert m and m.group(1) == doi


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/article/12345",
        "https://arxiv.org/abs/2401.12345",
        "https://example.com/price-is-10.99/deal",  # 10.99 has no /suffix
    ],
)
def test_fetch_doi_re_ignores(url):
    assert fetch._DOI_RE.search(url) is None


# --- cites W-id existence check ---


def test_cites_wid_fetches_work_to_validate(monkeypatch):
    """cites() must hit /works/W<id> to validate existence before filtering, so a bogus
    W-id raises a RuntimeError rather than silently returning an empty list."""
    fetched = []

    def fake_get(path, params=None):
        fetched.append(path)
        if "/works/W99999999" in path and (params is None or "filter" not in params):
            raise RuntimeError("OpenAlex has no record for W99999999")
        return {"results": []}

    monkeypatch.setattr(papers, "_get", fake_get)
    with pytest.raises(RuntimeError, match="W99999999"):
        papers.cites("W99999999")
    # The existence-check fetch must have been attempted
    assert any("W99999999" in p for p in fetched)
