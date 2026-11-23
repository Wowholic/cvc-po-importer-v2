"""Send a purchase-order PDF to Gemini and return spreadsheet rows."""

import base64
import json
import re

import requests

from config import GEMINI_API_KEY, GEMINI_MODEL
from rows import normalize_row

PROMPT = """You read a purchase order PDF and return the rows that belong in a spreadsheet.

Return these fields only:
- Company: the customer company that issued this purchase order. Cincinnati Valve Company and CVC are the supplier when they only appear as the company receiving the order. Do not use them as Company in that case.
- CUSTOMER PO #: the customer's own purchase order number
- PO DATE: the date this purchase order was issued. Ignore ship dates and due dates. The PDF may print that date in any format. Convert it to DD-MM-YYYY.
- Payment Terms: the payment terms printed on the purchase order, such as Net 30 or due on receipt. Copy the wording from the PDF.
- Delivery Method: the carrier, ship method, or delivery method printed on the purchase order. Copy the wording from the PDF.
- FIG#: the part, figure, or manufacturer part identifier for the line
- Qty: the quantity for that line
- Unit Price: the price for one unit on that line

Decide what each value means from the document. Layouts and labels differ between purchase orders.

Rules:
- Return one object per line item. Repeat the purchase-order fields on every line.
- If a field is not clearly present, use an empty string. Never invent a value.
- Dates must be DD-MM-YYYY.
- Qty and Unit Price must be plain numbers, with no currency symbol or thousands separator. Use a dot as the decimal separator.
- Do not return a sales amount, a vendor purchase order number, any shipment date, or a field named Other.
- Return only JSON: an array of objects with exactly these keys and no others.
"""


class ExtractError(Exception):
    pass


def _message(response) -> str:
    try:
        message = response.json()["error"]["message"]
    except (ValueError, KeyError, TypeError):
        message = response.text[:300]
    return " ".join(str(message).split()) or "Gemini could not read that PDF."


def parse_rows(text: str) -> list[dict]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("[")
        end = cleaned.rfind("]")
        if start == -1 or end == -1 or end <= start:
            start = cleaned.find("{")
            end = cleaned.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ExtractError("Gemini did not return purchase-order lines.")
        data = json.loads(cleaned[start:end + 1])
    if isinstance(data, dict):
        data = data.get("rows", data.get("lines"))
    if not isinstance(data, list):
        raise ExtractError("Gemini did not return a list of lines.")
    return [normalize_row(row) for row in data if isinstance(row, dict)]


def rows_from_gemini(body: dict) -> list[dict]:
    error = body.get("error") or {}
    if error and not body.get("candidates"):
        raise ExtractError(error.get("message") or "Gemini could not read that PDF.")
    try:
        text = body["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError):
        raise ExtractError("Gemini did not return any purchase-order lines.")
    return parse_rows(text)


def extract_bytes(pdf_bytes: bytes) -> list[dict]:
    if not GEMINI_API_KEY:
        raise ExtractError("Set GEMINI_API_KEY before reading a purchase order.")
    document = base64.standard_b64encode(pdf_bytes).decode("ascii")
    response = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent",
        headers={"x-goog-api-key": GEMINI_API_KEY},
        json={
            "contents": [
                {
                    "parts": [
                        {"inline_data": {"mime_type": "application/pdf", "data": document}},
                        {"text": PROMPT},
                    ]
                }
            ],
            "generationConfig": {"responseMimeType": "application/json"},
        },
        timeout=120,
    )
    if not response.ok:
        raise ExtractError(_message(response))
    return rows_from_gemini(response.json())
