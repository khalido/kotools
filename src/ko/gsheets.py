"""Google Sheets reader — read-only primitives over the Sheets v4 API.

Designed so Claude Code (or me at a terminal) can quickly understand a sheet's
structure before writing Apps Script or formulas against it. No UDH-specific
shortcuts here — this module stays generic; project-specific shortcuts belong
in the calling project.
"""

from __future__ import annotations

from dataclasses import dataclass

from googleapiclient.errors import HttpError

from ko.google_auth import get_sheets_service


@dataclass
class SheetInfo:
    id: str
    title: str
    tabs: list[str]


class SheetsError(RuntimeError):
    pass


class SheetsNotFound(SheetsError):
    pass


class SheetsPermissionDenied(SheetsError):
    pass


def _handle(e: HttpError, context: str) -> None:
    if e.resp.status == 403:
        raise SheetsPermissionDenied(
            f"Permission denied for {context}. "
            f"Is the sheet accessible to the signed-in Google account?"
        ) from e
    if e.resp.status == 404:
        raise SheetsNotFound(f"Not found: {context}") from e
    raise e


def get_info(spreadsheet_id: str) -> SheetInfo:
    """Fetch a sheet's title and tab names."""
    svc = get_sheets_service()
    try:
        result = (
            svc.spreadsheets()
            .get(
                spreadsheetId=spreadsheet_id,
                fields="properties.title,sheets.properties.title",
            )
            .execute()
        )
    except HttpError as e:
        _handle(e, spreadsheet_id)
    return SheetInfo(
        id=spreadsheet_id,
        title=result["properties"]["title"],
        tabs=[s["properties"]["title"] for s in result.get("sheets", [])],
    )


def get_range(
    spreadsheet_id: str,
    range_name: str,
    value_render: str = "FORMATTED_VALUE",
) -> list[list]:
    """Fetch a single A1 range. value_render: FORMATTED_VALUE | UNFORMATTED_VALUE | FORMULA."""
    svc = get_sheets_service()
    try:
        result = (
            svc.spreadsheets()
            .values()
            .get(
                spreadsheetId=spreadsheet_id,
                range=range_name,
                valueRenderOption=value_render,
            )
            .execute()
        )
    except HttpError as e:
        _handle(e, f"{spreadsheet_id} {range_name}")
    return result.get("values", [])


def get_ranges(
    spreadsheet_id: str,
    ranges: list[str],
    value_render: str = "FORMATTED_VALUE",
) -> dict[str, list[list]]:
    """Fetch multiple ranges in one API call. Returns {range: rows}."""
    svc = get_sheets_service()
    try:
        result = (
            svc.spreadsheets()
            .values()
            .batchGet(
                spreadsheetId=spreadsheet_id,
                ranges=ranges,
                valueRenderOption=value_render,
            )
            .execute()
        )
    except HttpError as e:
        _handle(e, f"{spreadsheet_id} {ranges}")
    return {vr["range"]: vr.get("values", []) for vr in result.get("valueRanges", [])}
