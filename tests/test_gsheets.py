"""Smoke tests for ko.gsheets. Live tests skip unless a cached OAuth token exists."""

from __future__ import annotations

import pytest

from ko import google_auth
from ko import gsheets


def test_error_types_exist():
    # these are imported/referenced by callers; verify the public surface
    assert issubclass(gsheets.SheetsNotFound, gsheets.SheetsError)
    assert issubclass(gsheets.SheetsPermissionDenied, gsheets.SheetsError)
    assert issubclass(gsheets.SheetsOverwriteError, gsheets.SheetsError)


# --- request builders (network monkeypatched away) -----------------------


class _FakeService:
    """Just enough of the googleapiclient chain for a metadata get()."""

    def __init__(self, meta: dict):
        self._meta = meta

    def spreadsheets(self):
        return self

    def get(self, **kwargs):
        return self

    def execute(self, num_retries=0):
        return self._meta


_META = {
    "sheets": [
        {"properties": {"sheetId": 11, "title": "Summary", "index": 0}},
        {"properties": {"sheetId": 22, "title": "kodev-007", "index": 1}},
    ]
}


def test_duplicate_tab_builds_request(monkeypatch):
    sent = {}

    def fake_batch_update(sid, requests):
        sent["requests"] = requests
        return {"replies": [{"duplicateSheet": {"properties": {"sheetId": 33}}}]}

    monkeypatch.setattr(gsheets, "_svc", lambda write=False: _FakeService(_META))
    monkeypatch.setattr(gsheets, "batch_update", fake_batch_update)

    gid = gsheets.duplicate_tab("sheet1", "kodev-007", "kodev-008")
    assert gid == 33
    req = sent["requests"][0]["duplicateSheet"]
    assert req["sourceSheetId"] == 22
    assert req["insertSheetIndex"] == 2  # right after the source
    assert req["newSheetName"] == "kodev-008"

    gsheets.duplicate_tab("sheet1", "Summary")  # no name -> API's "Copy of" default
    assert "newSheetName" not in sent["requests"][0]["duplicateSheet"]


def test_duplicate_tab_missing_tab_lists_tabs(monkeypatch):
    monkeypatch.setattr(gsheets, "_svc", lambda write=False: _FakeService(_META))
    with pytest.raises(gsheets.SheetsNotFound, match="kodev-007"):
        gsheets.duplicate_tab("sheet1", "nope")


def test_export_pdf_builds_request(monkeypatch):
    sent = {}

    class _FakeCreds:
        valid = True
        token = "tok"

    class _FakeResp:
        status_code = 200
        content = b"%PDF-1.4 fake"

    def fake_get(url, params=None, headers=None, **kw):
        sent.update(url=url, params=params, headers=headers)
        return _FakeResp()

    monkeypatch.setattr(gsheets, "get_credentials", lambda readonly=True: _FakeCreds())
    monkeypatch.setattr(gsheets.httpx, "get", fake_get)
    monkeypatch.setattr(gsheets, "tab_id", lambda sid, title: 22 if title == "kodev-007" else None)

    pdf = gsheets.export_pdf("sheet1", "kodev-007")
    assert pdf.startswith(b"%PDF")
    assert sent["url"].endswith("/sheet1/export")
    assert sent["headers"]["Authorization"] == "Bearer tok"
    p = sent["params"]
    assert p["gid"] == "22" and p["size"] == "A4" and p["portrait"] == "true"
    assert p["fitw"] == "true" and p["gridlines"] == "false"

    gsheets.export_pdf("sheet1", landscape=True)  # whole spreadsheet, landscape
    assert "gid" not in sent["params"] and sent["params"]["portrait"] == "false"

    with pytest.raises(gsheets.SheetsNotFound):
        gsheets.export_pdf("sheet1", "nope")

    _FakeResp.content = b"<html>login</html>"  # auth redirect page, not a PDF
    with pytest.raises(gsheets.SheetsError, match="non-PDF"):
        gsheets.export_pdf("sheet1", "kodev-007")


def test_insert_dimension_builds_request(monkeypatch):
    sent = {}

    def fake_batch_update(sid, requests):
        sent["requests"] = requests
        return {"replies": [{}]}

    monkeypatch.setattr(gsheets, "batch_update", fake_batch_update)
    monkeypatch.setattr(gsheets, "tab_id", lambda sid, title: 22 if title == "kodev-007" else None)

    # mid-table: inherit from the row above by default
    gsheets.insert_dimension("sheet1", "kodev-007", start_index=4, n=3)
    req = sent["requests"][0]["insertDimension"]
    assert req["range"] == {"sheetId": 22, "dimension": "ROWS", "startIndex": 4, "endIndex": 7}
    assert req["inheritFromBefore"] is True

    # index 0: nothing before to inherit from (the API rejects it) -> auto False
    gsheets.insert_dimension("sheet1", "kodev-007", dimension="COLUMNS", start_index=0)
    req = sent["requests"][0]["insertDimension"]
    assert req["range"]["dimension"] == "COLUMNS" and req["range"]["endIndex"] == 1
    assert req["inheritFromBefore"] is False

    with pytest.raises(gsheets.SheetsNotFound):
        gsheets.insert_dimension("sheet1", "nope")


def test_find_replace_builds_request(monkeypatch):
    sent = {}

    def fake_batch_update(sid, requests):
        sent["requests"] = requests
        return {"replies": [{"findReplace": {"occurrencesChanged": 4}}]}

    monkeypatch.setattr(gsheets, "batch_update", fake_batch_update)
    monkeypatch.setattr(gsheets, "tab_id", lambda sid, title: 22 if title == "kodev-008" else None)

    n = gsheets.find_replace("sheet1", "007", "008", tab="kodev-008")
    assert n == 4
    req = sent["requests"][0]["findReplace"]
    assert req["sheetId"] == 22 and "allSheets" not in req
    assert req["matchCase"] is False and req["includeFormulas"] is False

    gsheets.find_replace("sheet1", "007", "008")  # no tab -> whole spreadsheet
    assert sent["requests"][0]["findReplace"]["allSheets"] is True

    with pytest.raises(gsheets.SheetsNotFound):
        gsheets.find_replace("sheet1", "007", "008", tab="nope")


# --- pure helpers (no network) ------------------------------------------


def test_col_letter_num_roundtrip():
    assert gsheets.col_letter(1) == "A"
    assert gsheets.col_letter(26) == "Z"
    assert gsheets.col_letter(27) == "AA"
    assert gsheets.col_letter(703) == "AAA"
    for n in (1, 26, 27, 52, 703, 1000):
        assert gsheets.col_num(gsheets.col_letter(n)) == n


def test_sheet_id_accepts_url_or_bare():
    url = "https://docs.google.com/spreadsheets/d/1AbC_def-123/edit#gid=0"
    assert gsheets.sheet_id(url) == "1AbC_def-123"
    assert gsheets.sheet_id("  1AbC_def-123  ") == "1AbC_def-123"


def test_a1_to_grid():
    assert gsheets.a1_to_grid("Tab!A1:B3", 5) == {
        "sheetId": 5,
        "startColumnIndex": 0,
        "startRowIndex": 0,
        "endColumnIndex": 2,
        "endRowIndex": 3,
    }
    g = gsheets.a1_to_grid("Tab!A3:T", 0)  # unbounded end row
    assert g["startRowIndex"] == 2 and "endRowIndex" not in g
    assert gsheets.a1_to_grid("B:D", 1) == {  # whole columns
        "sheetId": 1,
        "startColumnIndex": 1,
        "endColumnIndex": 4,
    }


def test_a1_to_grid_rejects_garbage():
    with pytest.raises(ValueError):
        gsheets.a1_to_grid("not a range", 0)


def test_range_from_anchor():
    assert gsheets.range_from_anchor("Costs!A1", 3, 2) == "Costs!A1:B3"
    assert gsheets.range_from_anchor("B2", 1, 1) == "B2:B2"
    with pytest.raises(ValueError):
        gsheets.range_from_anchor("A1:B2", 1, 1)  # not a single cell


def test_occupied_cells():
    rows = [["x", ""], ["", "y"]]
    assert gsheets.occupied_cells("Sheet1!B5", rows) == ["Sheet1!B5: 'x'", "Sheet1!C6: 'y'"]


def test_scan_rows():
    rows = [["Hello", "World"], ["foo", "WORLDLY"]]
    assert gsheets.scan_rows(rows, "world") == [("B1", "World"), ("B2", "WORLDLY")]


def test_shape_range_and_cell():
    assert gsheets._shape_range("A1", [[1, 2], [3, 4]]) == "A1:B2"
    with pytest.raises(ValueError):
        gsheets._shape_range("A1", [])
    assert gsheets._cell(None) == ""
    assert gsheets._cell(float("nan")) == ""
    assert gsheets._cell(3) == 3


def test_df_to_values_duck_types_polars_and_pandas():
    class FakePolars:
        columns = ["a", "b"]

        def iter_rows(self):
            return iter([(1, 2), (3, 4)])

    class FakePandas:
        columns = ["a", "b"]

        def itertuples(self, index=False, name=None):
            return iter([(1, 2), (3, 4)])

    expected = [["a", "b"], [1, 2], [3, 4]]
    assert gsheets.df_to_values(FakePolars()) == expected
    assert gsheets.df_to_values(FakePandas()) == expected
    assert gsheets.df_to_values(FakePolars(), header=False) == [[1, 2], [3, 4]]


def test_df_to_values_rejects_unknown():
    class NoRows:
        columns = ["a"]

    with pytest.raises(TypeError):
        gsheets.df_to_values(NoRows())


def test_cli_parse_cells_and_norm_block():
    from ko.cli import _norm_block, _parse_cells

    assert _parse_cells("hello") == [["hello"]]
    assert _parse_cells("=SUM(A1:A2)") == [["=SUM(A1:A2)"]]  # formula stays a scalar cell
    assert _parse_cells("a\tb\nc\td") == [["a", "b"], ["c", "d"]]
    assert _parse_cells("a\tb\n") == [["a", "b"]]  # trailing newline dropped
    assert _parse_cells("[[1, 2], [3, 4]]") == [[1, 2], [3, 4]]
    assert _parse_cells('["x", "y"]') == [["x", "y"]]  # 1D -> a single row
    assert _parse_cells("") == [[""]]  # empty input must not crash
    assert _norm_block("n") == [["n"]]
    assert _norm_block([1, 2]) == [[1, 2]]  # row
    assert _norm_block([[1], [2]]) == [[1], [2]]  # 2D as-is


def test_multi_account_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(google_auth.config, "get", lambda *a, **k: None)
    monkeypatch.setattr(google_auth, "config_dir", lambda: tmp_path)
    monkeypatch.delenv("KO_GOOGLE_CLIENT_FILE", raising=False)
    monkeypatch.delenv("KO_GOOGLE_ACCOUNT", raising=False)

    # default account → legacy filenames (back-compat)
    assert google_auth.active_account() == "default"
    assert google_auth.token_file().name == "google_token.json"
    assert google_auth.client_file().name == "google_client.json"

    # a named account → suffixed token; client falls back to the shared one until a
    # per-account client file exists
    monkeypatch.setenv("KO_GOOGLE_ACCOUNT", "work")
    assert google_auth.active_account() == "work"
    assert google_auth.token_file().name == "google_token_work.json"
    assert google_auth.client_file().name == "google_client.json"
    (tmp_path / "google_client_work.json").write_text("{}")
    assert google_auth.client_file().name == "google_client_work.json"

    # env beats config; KO_GOOGLE_CLIENT_FILE beats everything
    monkeypatch.setenv("KO_GOOGLE_CLIENT_FILE", str(tmp_path / "custom.json"))
    assert google_auth.client_file().name == "custom.json"


@pytest.mark.skipif(
    not google_auth.token_file().exists(),
    reason="no cached google token; run `ko gsheets auth` first for live tests",
)
def test_get_info_on_public_sheet():
    # Google's public sample sheet — the SDK uses this in its tutorials
    SAMPLE_ID = "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms"
    info = gsheets.get_info(SAMPLE_ID)
    assert info.id == SAMPLE_ID
    assert info.title
    assert isinstance(info.tabs, list)
