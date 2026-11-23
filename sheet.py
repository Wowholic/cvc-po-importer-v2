"""Append reviewed lines to the shared POs spreadsheet."""

import json
import logging

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from config import (
    GOOGLE_IMPERSONATE,
    GOOGLE_SERVICE_ACCOUNT_JSON,
    SCOPES,
    SHEET_ID,
    SHEET_TAB,
    SHEET_URL,
)
from rows import build_sheet_values, partition_new, validate_rows

logger = logging.getLogger("purchase_desk")


class SheetError(Exception):
    pass


def credentials():
    if not GOOGLE_SERVICE_ACCOUNT_JSON:
        raise SheetError("Set GOOGLE_SERVICE_ACCOUNT_JSON to the service account JSON.")
    try:
        info = json.loads(GOOGLE_SERVICE_ACCOUNT_JSON)
    except json.JSONDecodeError as error:
        raise SheetError("GOOGLE_SERVICE_ACCOUNT_JSON is not valid JSON.") from error
    creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    if GOOGLE_IMPERSONATE:
        creds = creds.with_subject(GOOGLE_IMPERSONATE)
    return creds


def tab_range(cells: str) -> str:
    title = SHEET_TAB.replace("'", "''")
    return f"'{title}'!{cells}"


def existing_po_numbers(sheets) -> set[str]:
    result = sheets.spreadsheets().values().get(spreadsheetId=SHEET_ID, range=tab_range("B:B")).execute()
    numbers = set()
    for row in result.get("values") or []:
        if not row:
            continue
        number = str(row[0]).strip()
        if number and number != "CUSTOMER PO #":
            numbers.add(number)
    return numbers


def pos_sheet_id(sheets) -> int:
    meta = sheets.spreadsheets().get(spreadsheetId=SHEET_ID, fields="sheets.properties").execute()
    for sheet in meta["sheets"]:
        props = sheet["properties"]
        if props["title"] == SHEET_TAB:
            return props["sheetId"]
    raise SheetError(f"The spreadsheet has no tab named {SHEET_TAB}.")


def plain_format(sheets, start: int, end: int) -> None:
    sheet_id = pos_sheet_id(sheets)
    solid_border = {"style": "SOLID", "width": 1, "color": {"red": 0, "green": 0, "blue": 0}}

    def cell_range(first_column: int, last_column: int) -> dict:
        return {
            "sheetId": sheet_id,
            "startRowIndex": start - 1,
            "endRowIndex": end,
            "startColumnIndex": first_column,
            "endColumnIndex": last_column,
        }

    sheets.spreadsheets().batchUpdate(
        spreadsheetId=SHEET_ID,
        body={"requests": [
            {
                "repeatCell": {
                    "range": cell_range(0, 11),
                    "cell": {
                        "userEnteredFormat": {
                            "backgroundColor": {"red": 1, "green": 1, "blue": 1},
                            "horizontalAlignment": "LEFT",
                            "wrapStrategy": "OVERFLOW_CELL",
                            "textFormat": {
                                "foregroundColor": {"red": 0, "green": 0, "blue": 0},
                                "fontFamily": "Arial",
                                "fontSize": 10,
                                "bold": False,
                                "italic": False,
                                "underline": False,
                                "strikethrough": False,
                            },
                            "borders": {
                                "top": solid_border,
                                "bottom": solid_border,
                                "left": solid_border,
                                "right": solid_border,
                            },
                        }
                    },
                    "fields": ",".join([
                        "userEnteredFormat.backgroundColor",
                        "userEnteredFormat.horizontalAlignment",
                        "userEnteredFormat.wrapStrategy",
                        "userEnteredFormat.textFormat",
                        "userEnteredFormat.borders",
                    ]),
                }
            },
            {
                "repeatCell": {
                    "range": cell_range(8, 9),
                    "cell": {"userEnteredFormat": {"numberFormat": {"type": "NUMBER", "pattern": "General"}}},
                    "fields": "userEnteredFormat.numberFormat",
                }
            },
            {
                "repeatCell": {
                    "range": cell_range(9, 11),
                    "cell": {"userEnteredFormat": {"numberFormat": {"type": "CURRENCY", "pattern": '"$"#,##0.00'}}},
                    "fields": "userEnteredFormat.numberFormat",
                }
            },
        ]},
    ).execute()


def append_purchase_orders(raw_rows) -> dict:
    rows = validate_rows(raw_rows)
    try:
        sheets = build("sheets", "v4", credentials=credentials(), cache_discovery=False)
        known = existing_po_numbers(sheets)
        fresh, skipped = partition_new(rows, known)
        if fresh:
            existing = sheets.spreadsheets().values().get(
                spreadsheetId=SHEET_ID,
                range=tab_range("A:K"),
            ).execute()
            start = len(existing.get("values") or []) + 1
            values = build_sheet_values(fresh, start)
            end = start + len(values) - 1
            sheets.spreadsheets().values().update(
                spreadsheetId=SHEET_ID,
                range=tab_range(f"A{start}:K{end}"),
                valueInputOption="USER_ENTERED",
                body={"values": values},
            ).execute()
            try:
                plain_format(sheets, start, end)
            except (HttpError, SheetError):
                logger.exception("row formatting failed after the lines were written")
    except SheetError:
        raise
    except HttpError as error:
        status = getattr(error.resp, "status", "")
        raise SheetError(f"The spreadsheet rejected the update ({status}).") from error
    except (json.JSONDecodeError, OSError, ValueError) as error:
        raise SheetError(str(error)) from error
    return {"written": len(fresh), "skipped": skipped, "url": SHEET_URL}
