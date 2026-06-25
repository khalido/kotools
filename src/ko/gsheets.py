"""Google Sheets reader/writer — generic primitives over the Sheets v4 API.

Designed so an agent (or me at a terminal) can understand a sheet's structure and
safely read/write it. No project-specific shortcuts — this module stays generic;
project-specific shortcuts belong in the calling project.

Reads use the read-only scope; writes use the read+write scope (run `ko gsheets auth`
once — it grants write access, which also covers reads). Write safety: the shape-aware
writers (`write_values`, `write_ranges`, `write_row`/`write_column`, `write_df`) refuse
to clobber occupied cells — including formulas that currently display blank — unless
`overwrite=True`, and the error lists exactly which cells are in the way. The low-level
primitives (`write_range`, `batch_update`, `add_tab`, `delete_tab`, `create_spreadsheet`)
have no guard; prefer the shape-aware ones.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from googleapiclient.errors import HttpError

from ko.google_auth import GoogleError, get_sheets_service, id_from_url, raise_for_status


@dataclass
class SheetInfo:
    id: str
    title: str
    tabs: list[str]


class SheetsError(GoogleError):
    pass


class SheetsNotFound(SheetsError):
    pass


class SheetsPermissionDenied(SheetsError):
    pass


class SheetsOverwriteError(SheetsError):
    pass


def _handle(e: HttpError, context: str) -> None:
    raise_for_status(
        e,
        context,
        not_found=SheetsNotFound,
        permission=SheetsPermissionDenied,
        hint="Is the sheet accessible to the signed-in account, and for a write did you `ko gsheets auth`?",
    )


def _svc(write: bool = False):
    """The Sheets service — read-only by default, read+write when write=True."""
    return get_sheets_service(readonly=not write)


# --- helpers ------------------------------------------------------------


def sheet_id(value: str) -> str:
    """Accept a Google Sheets URL or a bare spreadsheet ID; return the ID."""
    return id_from_url(value, "spreadsheets")


def col_letter(n: int) -> str:
    """1-based column number -> spreadsheet letters: 1->A, 27->AA."""
    s = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def col_num(letters: str) -> int:
    """Inverse of col_letter: 'A'->1, 'AA'->27."""
    n = 0
    for ch in letters.upper():
        n = n * 26 + (ord(ch) - 64)
    return n


_ANCHOR_RE = re.compile(r"^(?:(?P<tab>.+)!)?(?P<col>[A-Za-z]+)(?P<row>[0-9]+)$")
_A1_PART_RE = re.compile(r"^\$?(?P<col>[A-Za-z]+)?\$?(?P<row>[0-9]+)?$")
_RANGE_START_RE = re.compile(r"^(?:(?P<tab>.+)!)?(?P<col>[A-Za-z]+)(?P<row>[0-9]+)")


def a1_to_grid(range_name: str, sheet_gid: int) -> dict:
    """A1 range -> API GridRange dict (for batchUpdate requests). Handles
    'Tab!A3:T10', unbounded 'Tab!A3:T', whole columns 'B:D', single cells.
    The tab part is ignored (you supply the numeric gid). Pure — no API call;
    use grid_range() to resolve the gid by tab title."""
    rng = range_name.rsplit("!", 1)[-1].strip()
    parts = rng.split(":")
    if len(parts) > 2:
        raise ValueError(f"not an A1 range: {range_name!r}")
    m1 = _A1_PART_RE.match(parts[0])
    m2 = _A1_PART_RE.match(parts[1]) if len(parts) == 2 else m1
    if not m1 or not m2 or not (m1.group("col") or m1.group("row")):
        raise ValueError(f"not an A1 range: {range_name!r}")
    out: dict = {"sheetId": sheet_gid}
    if m1.group("col"):
        out["startColumnIndex"] = col_num(m1.group("col")) - 1
    if m1.group("row"):
        out["startRowIndex"] = int(m1.group("row")) - 1
    if m2.group("col"):
        out["endColumnIndex"] = col_num(m2.group("col"))
    if m2.group("row"):
        out["endRowIndex"] = int(m2.group("row"))
    return out


def grid_range(spreadsheet_id: str, range_name: str) -> dict:
    """'Tab!A3:T10' -> GridRange with the tab's sheetId resolved by title.
    Raises SheetsNotFound if the tab doesn't exist."""
    tab = range_name.rsplit("!", 1)[0].strip("'") if "!" in range_name else None
    if tab is None:
        raise ValueError(f"range must include a tab: {range_name!r}")
    gid = tab_id(spreadsheet_id, tab)
    if gid is None:
        raise SheetsNotFound(f"no tab named {tab!r} in {spreadsheet_id}")
    return a1_to_grid(range_name, gid)


def range_from_anchor(anchor: str, n_rows: int, n_cols: int) -> str:
    """Expand a single top-left anchor cell + a shape into a full A1 range:
    ('Costs!A1', 3, 2) -> 'Costs!A1:B3'. The tab part (quoted or not) is kept
    verbatim. Raises if `anchor` isn't a single cell."""
    m = _ANCHOR_RE.match(anchor.strip())
    if not m:
        raise ValueError(
            f"anchor must be a single top-left cell like 'Tab!A1', got {anchor!r}"
        )
    c0 = col_num(m.group("col"))
    r0 = int(m.group("row"))
    start = f"{col_letter(c0)}{r0}"
    end = f"{col_letter(c0 + n_cols - 1)}{r0 + n_rows - 1}"
    tab = m.group("tab")
    return f"{tab}!{start}:{end}" if tab else f"{start}:{end}"


# --- read primitives ----------------------------------------------------


def get_info(spreadsheet_id: str) -> SheetInfo:
    """Fetch a sheet's title and tab names."""
    try:
        result = (
            _svc()
            .spreadsheets()
            .get(spreadsheetId=spreadsheet_id, fields="properties.title,sheets.properties.title")
            .execute(num_retries=3)
        )
    except HttpError as e:
        _handle(e, spreadsheet_id)
    return SheetInfo(
        id=spreadsheet_id,
        title=result["properties"]["title"],
        tabs=[s["properties"]["title"] for s in result.get("sheets", [])],
    )


def get_meta(spreadsheet_id: str) -> dict:
    """Per-tab structural metadata — no cell data. Each tab carries its grid size,
    frozen panes, hidden flag, tab colour, and the lists of charts / merges /
    protected ranges / bandings / basic filter. Cheap; use it to map a sheet's
    shape before reading values."""
    fields = (
        "properties.title,"
        "sheets.properties,"
        "sheets.charts.chartId,"
        "sheets.merges,"
        "sheets.basicFilter.range,"
        "sheets.protectedRanges.protectedRangeId,"
        "sheets.bandedRanges.bandedRangeId"
    )
    try:
        return _svc().spreadsheets().get(spreadsheetId=spreadsheet_id, fields=fields).execute(num_retries=3)
    except HttpError as e:
        _handle(e, spreadsheet_id)


def get_range(
    spreadsheet_id: str,
    range_name: str,
    value_render: str = "FORMATTED_VALUE",
) -> list[list]:
    """Fetch a single A1 range. value_render: FORMATTED_VALUE | UNFORMATTED_VALUE | FORMULA."""
    try:
        result = (
            _svc()
            .spreadsheets()
            .values()
            .get(spreadsheetId=spreadsheet_id, range=range_name, valueRenderOption=value_render)
            .execute(num_retries=3)
        )
    except HttpError as e:
        _handle(e, f"{spreadsheet_id} {range_name}")
    return result.get("values", [])


def batch_get(
    spreadsheet_id: str,
    ranges: list[str],
    value_render: str = "FORMATTED_VALUE",
) -> list[list[list]]:
    """Fetch multiple ranges in one API call. Returns each range's rows in the same
    order as `ranges`. (Don't key results by range string — the API normalises those,
    so order-by-key is unreliable.)"""
    if not ranges:
        return []
    try:
        result = (
            _svc()
            .spreadsheets()
            .values()
            .batchGet(spreadsheetId=spreadsheet_id, ranges=ranges, valueRenderOption=value_render)
            .execute(num_retries=3)
        )
    except HttpError as e:
        _handle(e, f"{spreadsheet_id} {ranges}")
    return [vr.get("values", []) for vr in result.get("valueRanges", [])]


def get_ranges(
    spreadsheet_id: str,
    ranges: list[str],
    value_render: str = "FORMATTED_VALUE",
) -> dict[str, list[list]]:
    """Like batch_get but keyed by the API-normalised range string. Kept for the
    `ko gsheets` CLI; prefer batch_get when order matters."""
    try:
        result = (
            _svc()
            .spreadsheets()
            .values()
            .batchGet(spreadsheetId=spreadsheet_id, ranges=ranges, valueRenderOption=value_render)
            .execute(num_retries=3)
        )
    except HttpError as e:
        _handle(e, f"{spreadsheet_id} {ranges}")
    return {vr["range"]: vr.get("values", []) for vr in result.get("valueRanges", [])}


# --- search -------------------------------------------------------------


def scan_rows(rows: list[list], text: str) -> list[tuple[str, str]]:
    """Case-insensitive substring scan of a 2D block read from A1. Returns
    [(A1 ref, cell as str), ...]. Pure — no API call."""
    needle = text.lower()
    out: list[tuple[str, str]] = []
    for ri, row in enumerate(rows):
        for ci, cell in enumerate(row):
            s = "" if cell is None else str(cell)
            if needle in s.lower():
                out.append((f"{col_letter(ci + 1)}{ri + 1}", s))
    return out


def find_cells(
    spreadsheet_id: str,
    text: str,
    formula: bool = False,
    tabs: list[str] | None = None,
) -> list[tuple[str, str, str]]:
    """Search every cell of every tab (or just `tabs`) for `text`, case-insensitive
    substring. Returns [(tab, A1 ref, cell), ...] in sheet order. formula=True searches
    formulas. One batchGet for the whole sheet."""
    titles = get_info(spreadsheet_id).tabs
    if tabs:
        missing = [t for t in tabs if t not in titles]
        if missing:
            raise SheetsNotFound(f"No such tab(s): {missing}. Tabs: {titles}")
        titles = [t for t in titles if t in tabs]
    render = "FORMULA" if formula else "FORMATTED_VALUE"
    quoted = ["'" + t.replace("'", "''") + "'" for t in titles]
    blocks = batch_get(spreadsheet_id, quoted, value_render=render)
    return [
        (title, ref, cell)
        for title, rows in zip(titles, blocks)
        for ref, cell in scan_rows(rows, text)
    ]


# --- tab / sheet ops ----------------------------------------------------


def create_spreadsheet(title: str) -> str:
    """Create a brand-new spreadsheet and return its ID. It lands in My Drive root —
    move it into a folder by hand if you like (the API addresses sheets by ID)."""
    try:
        result = (
            _svc(write=True)
            .spreadsheets()
            .create(body={"properties": {"title": title}}, fields="spreadsheetId")
            .execute(num_retries=3)
        )
    except HttpError as e:
        _handle(e, f"create spreadsheet '{title}'")
    return result["spreadsheetId"]


def add_tab(spreadsheet_id: str, title: str) -> int:
    """Create a new tab. Returns its sheetId. Errors if a tab with that title already
    exists — check `get_info(...).tabs` first."""
    try:
        result = (
            _svc(write=True)
            .spreadsheets()
            .batchUpdate(
                spreadsheetId=spreadsheet_id,
                body={"requests": [{"addSheet": {"properties": {"title": title}}}]},
            )
            .execute(num_retries=3)
        )
    except HttpError as e:
        _handle(e, f"{spreadsheet_id} addSheet '{title}'")
    return result["replies"][0]["addSheet"]["properties"]["sheetId"]


def tab_id(spreadsheet_id: str, title: str) -> int | None:
    """The sheetId (gid) of a tab by title, or None if there's no such tab."""
    try:
        meta = (
            _svc()
            .spreadsheets()
            .get(spreadsheetId=spreadsheet_id, fields="sheets.properties(sheetId,title)")
            .execute(num_retries=3)
        )
    except HttpError as e:
        _handle(e, spreadsheet_id)
    return next(
        (s["properties"]["sheetId"] for s in meta.get("sheets", []) if s["properties"]["title"] == title),
        None,
    )


def delete_tab(spreadsheet_id: str, title: str) -> bool:
    """Delete a tab by title. Returns True if it existed and was deleted."""
    sid = tab_id(spreadsheet_id, title)
    if sid is None:
        return False
    batch_update(spreadsheet_id, [{"deleteSheet": {"sheetId": sid}}])
    return True


def clear_range(spreadsheet_id: str, range_name: str) -> str:
    """Clear the VALUES in a range — formatting, notes, and validation survive (like
    Apps Script's clearContents()). Returns the cleared range as reported by the API."""
    try:
        result = (
            _svc(write=True)
            .spreadsheets()
            .values()
            .clear(spreadsheetId=spreadsheet_id, range=range_name, body={})
            .execute(num_retries=3)
        )
    except HttpError as e:
        _handle(e, f"{spreadsheet_id} clear {range_name}")
    return result.get("clearedRange", "")


def batch_update(spreadsheet_id: str, requests: list[dict]) -> dict:
    """Apply a list of Sheets batchUpdate requests (formatting, freezing, charts, …).
    Returns the API reply."""
    try:
        return (
            _svc(write=True)
            .spreadsheets()
            .batchUpdate(spreadsheetId=spreadsheet_id, body={"requests": requests})
            .execute(num_retries=3)
        )
    except HttpError as e:
        _handle(e, f"{spreadsheet_id} batchUpdate")


# --- write primitives ---------------------------------------------------


def write_range(
    spreadsheet_id: str,
    range_name: str,
    values: list[list],
    raw: bool = True,
) -> int:
    """Write a 2D array to a range. Returns the number of cells updated. No overwrite
    guard — prefer write_values. raw=True writes literally (RAW); raw=False parses like
    a user typing (a leading '=' becomes a formula, '1/7/25' a date)."""
    try:
        result = (
            _svc(write=True)
            .spreadsheets()
            .values()
            .update(
                spreadsheetId=spreadsheet_id,
                range=range_name,
                valueInputOption="RAW" if raw else "USER_ENTERED",
                body={"values": values},
            )
            .execute(num_retries=3)
        )
    except HttpError as e:
        _handle(e, f"{spreadsheet_id} {range_name}")
    return result.get("updatedCells", 0)


def occupied_cells(range_name: str, existing: list[list]) -> list[str]:
    """List the non-empty cells in `existing` (rows read from `range_name`) as
    "Tab!B5: value" strings. Pure — no API call; used to build overwrite errors."""
    m = _RANGE_START_RE.match(range_name.strip())
    if not m:
        raise ValueError(f"can't parse range start from {range_name!r}")
    prefix = f"{m.group('tab')}!" if m.group("tab") else ""
    c0, r0 = col_num(m.group("col")), int(m.group("row"))
    return [
        f"{prefix}{col_letter(c0 + ci)}{r0 + ri}: {cell!r}"
        for ri, row in enumerate(existing)
        for ci, cell in enumerate(row)
        if cell not in (None, "")
    ]


def _guard_overwrite(spreadsheet_id: str, range_names: list[str]) -> None:
    """Raise SheetsOverwriteError listing the occupied cells (first 10) if any target
    range already has content. Reads with FORMULA render so a formula that currently
    *displays* blank still counts as occupied — and the error shows it."""
    existing = batch_get(spreadsheet_id, range_names, value_render="FORMULA")
    occupied = [
        line for rng, rows in zip(range_names, existing) for line in occupied_cells(rng, rows)
    ]
    if not occupied:
        return
    shown = "\n  ".join(occupied[:10])
    more = len(occupied) - 10
    raise SheetsOverwriteError(
        f"Refusing to overwrite — {len(occupied)} target cell(s) already have content:\n"
        f"  {shown}"
        + (f"\n  … and {more} more" if more > 0 else "")
        + "\nPass overwrite=True (CLI: --overwrite) to replace."
    )


def _shape_range(anchor: str, values: list[list]) -> str:
    """Anchor + 2D data -> the full A1 range it occupies. Validates the data."""
    if not values or not any(values):
        raise ValueError("values must be a non-empty 2D list")
    return range_from_anchor(anchor, len(values), max(len(r) for r in values))


def write_values(
    spreadsheet_id: str,
    anchor: str,
    values: list[list],
    raw: bool = True,
    overwrite: bool = False,
) -> int:
    """Write a 2D array starting at top-left `anchor` (e.g. 'Costs!A1') — the target
    range is computed from the data shape, no manual A1 math. Returns cells updated.

    overwrite=False (default) refuses to clobber: it reads the target first and raises
    SheetsOverwriteError listing the occupied cells. raw as in write_range."""
    range_name = _shape_range(anchor, values)
    if not overwrite:
        _guard_overwrite(spreadsheet_id, [range_name])
    return write_range(spreadsheet_id, range_name, values, raw=raw)


def write_ranges(
    spreadsheet_id: str,
    data: list[tuple[str, list[list]]],
    raw: bool = True,
    overwrite: bool = False,
) -> int:
    """Write many (anchor, 2D values) blocks in ONE values.batchUpdate call. Each anchor
    is a single top-left cell; the range comes from each block's shape. Same overwrite
    guard as write_values, checked across all targets in one batchGet. Returns total
    cells updated."""
    targets = [(_shape_range(anchor, values), values) for anchor, values in data]
    if not targets:
        raise ValueError("data must be a non-empty list of (anchor, values)")
    if not overwrite:
        _guard_overwrite(spreadsheet_id, [rng for rng, _ in targets])
    body = {
        "valueInputOption": "RAW" if raw else "USER_ENTERED",
        "data": [{"range": rng, "values": values} for rng, values in targets],
    }
    try:
        result = (
            _svc(write=True)
            .spreadsheets()
            .values()
            .batchUpdate(spreadsheetId=spreadsheet_id, body=body)
            .execute(num_retries=3)
        )
    except HttpError as e:
        _handle(e, f"{spreadsheet_id} batch write of {len(targets)} range(s)")
    return result.get("totalUpdatedCells", 0)


def write_column(
    spreadsheet_id: str,
    anchor: str,
    items: list,
    raw: bool = True,
    overwrite: bool = False,
) -> int:
    """Write a 1D list down a column from `anchor`. See write_values for the guard."""
    return write_values(spreadsheet_id, anchor, [[x] for x in items], raw=raw, overwrite=overwrite)


def write_row(
    spreadsheet_id: str,
    anchor: str,
    items: list,
    raw: bool = True,
    overwrite: bool = False,
) -> int:
    """Write a 1D list across a row from `anchor`. See write_values for the guard."""
    return write_values(spreadsheet_id, anchor, [list(items)], raw=raw, overwrite=overwrite)


def _cell(v):
    """Coerce a value to something the Sheets API accepts: None/NaN -> '', primitives
    pass through, everything else (dates, Decimals, …) -> str."""
    if v is None:
        return ""
    if isinstance(v, float) and v != v:  # NaN — json.dumps would emit invalid JSON
        return ""
    if isinstance(v, (str, int, float, bool)):
        return v
    return str(v)


def df_to_values(df, header: bool = True) -> list[list]:
    """polars OR pandas DataFrame -> 2D list (column names first unless header=False).
    Duck-typed so neither library is imported here — bring your own DataFrame."""
    cols = list(df.columns)
    if hasattr(df, "iter_rows"):  # polars
        rows = [list(r) for r in df.iter_rows()]
    elif hasattr(df, "itertuples"):  # pandas
        rows = [list(r) for r in df.itertuples(index=False, name=None)]
    else:
        raise TypeError("write_df expects a polars or pandas DataFrame")
    data = ([cols] if header else []) + rows
    return [[_cell(v) for v in row] for row in data]


def write_df(
    spreadsheet_id: str,
    anchor: str,
    df,
    header: bool = True,
    raw: bool = True,
    overwrite: bool = False,
) -> int:
    """Write a polars/pandas DataFrame to a sheet from `anchor` — header row of column
    names (unless header=False) then the data. Same shape inference + overwrite guard as
    write_values. For dates/formulas to parse, pass raw=False (USER_ENTERED)."""
    return write_values(
        spreadsheet_id, anchor, df_to_values(df, header=header), raw=raw, overwrite=overwrite
    )


# --- formatting ---------------------------------------------------------

DEFAULT_HEADER_BG = {"red": 0.89, "green": 0.93, "blue": 0.98}  # soft blue


def format_header(
    spreadsheet_id: str,
    range_name: str,
    bold: bool = True,
    bg: dict | None = DEFAULT_HEADER_BG,
    freeze: bool = False,
) -> None:
    """Mark a range as a header: bold + background fill, optionally freezing every row up
    to and including it. bg=None for no fill."""
    g = grid_range(spreadsheet_id, range_name)
    cell: dict = {"textFormat": {"bold": bold}}
    fields = ["textFormat.bold"]
    if bg is not None:
        cell["backgroundColor"] = bg
        fields.append("backgroundColor")
    requests: list[dict] = [
        {
            "repeatCell": {
                "range": g,
                "cell": {"userEnteredFormat": cell},
                "fields": f"userEnteredFormat({','.join(fields)})",
            }
        }
    ]
    if freeze:
        if "endRowIndex" not in g:
            raise ValueError(
                f"freeze needs a bounded header range (e.g. 'Tab!A3:T3'), got {range_name!r} — "
                "an open-ended range would freeze only row 1."
            )
        requests.append(
            {
                "updateSheetProperties": {
                    "properties": {
                        "sheetId": g["sheetId"],
                        "gridProperties": {"frozenRowCount": g["endRowIndex"]},
                    },
                    "fields": "gridProperties.frozenRowCount",
                }
            }
        )
    batch_update(spreadsheet_id, requests)


def add_chart(
    spreadsheet_id: str,
    anchor: str,
    title: str,
    domain: str,
    series: list[str],
    chart_type: str = "COLUMN",
    legend: str = "BOTTOM_LEGEND",
) -> int:
    """Add a basic chart. domain = A1 range of the x-axis labels, series = list of A1
    ranges (one per plotted series; include the header cell so the legend gets names),
    anchor = top-left cell the chart floats over. chart_type: COLUMN | BAR | LINE | AREA |
    SCATTER | STEPPED_AREA. Returns the chartId."""
    anchor_g = grid_range(spreadsheet_id, anchor)
    spec = {
        "title": title,
        "basicChart": {
            "chartType": chart_type,
            "legendPosition": legend,
            "headerCount": 1,
            "domains": [
                {"domain": {"sourceRange": {"sources": [grid_range(spreadsheet_id, domain)]}}}
            ],
            "series": [
                {
                    "series": {"sourceRange": {"sources": [grid_range(spreadsheet_id, s)]}},
                    "targetAxis": "LEFT_AXIS",
                }
                for s in series
            ],
        },
    }
    result = batch_update(
        spreadsheet_id,
        [
            {
                "addChart": {
                    "chart": {
                        "spec": spec,
                        "position": {
                            "overlayPosition": {
                                "anchorCell": {
                                    "sheetId": anchor_g["sheetId"],
                                    "rowIndex": anchor_g.get("startRowIndex", 0),
                                    "columnIndex": anchor_g.get("startColumnIndex", 0),
                                }
                            }
                        },
                    }
                }
            }
        ],
    )
    return result["replies"][0]["addChart"]["chart"]["chartId"]


def set_conditional_rules(spreadsheet_id: str, tab: str, rules: list[dict]) -> None:
    """REPLACE all conditional-format rules on `tab` with `rules` — idempotent by design
    (addConditionalFormatRule alone appends duplicates on every re-run). Each rule:
    {'range': 'Tab!D17:D25', 'booleanRule': {...}} or {'range': ..., 'gradientRule': {...}}."""
    gid = tab_id(spreadsheet_id, tab)
    if gid is None:
        raise SheetsNotFound(f"no tab named {tab!r} in {spreadsheet_id}")
    try:
        meta = (
            _svc()
            .spreadsheets()
            .get(spreadsheetId=spreadsheet_id, fields="sheets(properties.sheetId,conditionalFormats)")
            .execute(num_retries=3)
        )
    except HttpError as e:
        _handle(e, spreadsheet_id)
    existing = next(
        (len(s.get("conditionalFormats", [])) for s in meta["sheets"] if s["properties"]["sheetId"] == gid),
        0,
    )
    requests: list[dict] = [
        {"deleteConditionalFormatRule": {"sheetId": gid, "index": 0}} for _ in range(existing)
    ]
    for i, rule in enumerate(rules):
        body = {k: v for k, v in rule.items() if k != "range"}
        body["ranges"] = [grid_range(spreadsheet_id, rule["range"])]
        requests.append({"addConditionalFormatRule": {"rule": body, "index": i}})
    if requests:
        batch_update(spreadsheet_id, requests)
