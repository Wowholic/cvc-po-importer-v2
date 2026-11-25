"""Turn reviewed purchase-order lines into POs-tab rows.

Columns A–K stay in the mailbox layout. The vendor estimated shipment
date and the customer shipment date are left blank. SO No. is the Zoho
sales order number when this submit just created one.
"""

import re

COLUMNS = [
    "Company",
    "CUSTOMER PO #",
    "VENDOR PO #",
    "PO DATE",
    "FIG#",
    "Qty",
    "Unit Price",
]

NUMBER_COLUMNS = {"Qty", "Unit Price"}
ORDER_FIELDS = ("Payment Terms", "Delivery Method", "Other", "Description")
DATE_RE = re.compile(r"^(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])-[0-9]{4}$")
# Existing choices on PO/SO/INV TRACKING 2026. The shipment choice is spelled this way on the sheet.
PAYMENT_STATUS = "Not Paid"
SHIPMENT_STATUS = "NOT SHIIPED"


class RowError(ValueError):
    pass


def clean_text(value) -> str:
    text = " ".join(str(value or "").split())
    if text[:1] in {"=", "+", "@"}:
        text = "'" + text
    return text[:500]


def normalize_number(value) -> str:
    if value is None or value is False or value == "":
        return ""
    if isinstance(value, bool):
        return ""
    if isinstance(value, (int, float)):
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return format(value, "f").rstrip("0").rstrip(".") if isinstance(value, float) else str(value)
    text = str(value).strip()
    text = text.replace(",", "").replace("$", "").replace("€", "").replace(" ", "")
    if not text:
        return ""
    if not re.fullmatch(r"-?\d+(\.\d+)?", text):
        return text
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def normalize_row(row: dict) -> dict:
    cleaned = {}
    for column in COLUMNS:
        value = "" if not isinstance(row, dict) else row.get(column, "")
        if column in NUMBER_COLUMNS:
            cleaned[column] = normalize_number(value)
        elif column == "PO DATE":
            cleaned[column] = " ".join(str(value or "").split())
        else:
            cleaned[column] = clean_text(value)
    for column in ORDER_FIELDS:
        cleaned[column] = clean_text(row.get(column, "") if isinstance(row, dict) else "")
    return cleaned


def is_blank(row: dict) -> bool:
    return all(not str(row.get(column, "")).strip() for column in COLUMNS)


def number_cell(value: str):
    if value == "":
        return ""
    number = float(value)
    if number.is_integer():
        return int(number)
    return number


def validate_rows(rows) -> list[dict]:
    if not isinstance(rows, list):
        raise RowError("Submit a list of lines.")
    if len(rows) > 200:
        raise RowError("Too many lines to submit at once.")
    checked = []
    for index, raw in enumerate(rows, start=1):
        row = normalize_row(raw if isinstance(raw, dict) else {})
        if is_blank(row):
            continue
        date = row["PO DATE"]
        if date and not DATE_RE.fullmatch(date):
            raise RowError(f"Line {index}: PO DATE needs to be MM-DD-YYYY.")
        for column in NUMBER_COLUMNS:
            if row[column] and not re.fullmatch(r"-?\d+(\.\d+)?", row[column]):
                raise RowError(f"Line {index}: {column} needs to be a plain number.")
        checked.append(row)
    if not checked:
        raise RowError("Add at least one line before submitting.")
    return checked


def sheet_date(value: str) -> str:
    text = str(value or "").strip()
    if not DATE_RE.fullmatch(text):
        return ""
    month, day, year = text.split("-")
    return f"{int(month)}/{int(day)}/{year}"


def build_sheet_values(rows: list[dict], start: int, sales_order_number: str = "") -> list[list]:
    so_number = clean_text(sales_order_number)
    values = []
    for offset, row in enumerate(rows):
        line = start + offset
        values.append([
            row.get("Company", ""),
            row.get("CUSTOMER PO #", ""),
            row.get("VENDOR PO #", ""),
            so_number,
            sheet_date(row.get("PO DATE", "")),
            "",
            "",
            row.get("FIG#", ""),
            number_cell(row.get("Qty", "")),
            number_cell(row.get("Unit Price", "")),
            f"=J{line}*I{line}",
            "",
            "",
            "",
            PAYMENT_STATUS,
            "",
            "",
            SHIPMENT_STATUS,
        ])
    return values


def partition_new(rows: list[dict], known: set[str]) -> tuple[list[dict], list[dict]]:
    fresh = []
    skipped = []
    for row in rows:
        number = str(row.get("CUSTOMER PO #", "")).strip()
        if number and number in known:
            skipped.append({
                "po": number,
                "reason": "This customer PO number is already on the spreadsheet.",
            })
            continue
        fresh.append(row)
    return fresh, skipped
