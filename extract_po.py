"""Send a purchase-order PDF to Gemini and return spreadsheet rows."""

import base64
import contextvars
import json
import re
from datetime import date

import requests

from config import GEMINI_API_KEY, GEMINI_MODEL, GEMINI_THINKING
from rows import COLUMNS, HINT_FIELDS, ORDER_FIELDS, normalize_row

# Paid-tier USD per 1,000,000 tokens. Output includes thinking tokens.
# A date is the last day of that rate. https://ai.google.dev/gemini-api/docs/pricing
_RATES = {
    "gemini-3.8-flash": ((date(2026, 12, 31), 0.75, 3.75), (None, 1.50, 7.50)),
    "gemini-3.6-flash": ((date(2026, 12, 31), 0.75, 3.75), (None, 1.50, 7.50)),
    "gemini-flash-latest": ((date(2026, 12, 31), 0.75, 3.75), (None, 1.50, 7.50)),
    "gemini-3.5-flash-lite": ((None, 0.30, 2.50),),
    "gemini-2.5-flash": ((None, 0.30, 2.50),),
    "gemini-3.1-flash-lite": ((None, 0.25, 1.50),),
    "gemini-3-flash-preview": ((None, 0.50, 3.00),),
    "gemini-4-hydrogen": ((None, 1.50, 7.50),),
    "gemini-4-neon": ((None, 3.00, 15.00),),
    "gemini-4-carbon": ((date(2026, 11, 30), 2.00, 10.00), (None, 4.00, 20.00)),
}
_calls = contextvars.ContextVar("gemini_calls", default=None)

PROMPT = """You read a purchase order PDF and return the rows that belong in a spreadsheet.

Return these fields only:
- Company: the customer company that issued this purchase order. Cincinnati Valve Company and CVC are the supplier when they only appear as the company receiving the order. Do not use them as Company in that case.
- CUSTOMER PO #: the customer's own purchase order number
- PO DATE: the date this purchase order was issued. Ignore ship dates and due dates. The PDF may print that date in any format. Convert it to MM-DD-YYYY.
- Payment Terms: the payment terms printed on the purchase order, such as Net 30 or due on receipt. Copy the wording from the PDF.
- Delivery Method: the carrier, ship method, or delivery method printed on the purchase order. Copy the wording from the PDF.
- Other: the customer's shipping account number, when the PDF gives one. This is the account the shipment should bill, such as a UPS, FedEx, or other carrier account, a collect account, or a third-party billing account. It is often printed beside the delivery method, ship via, freight, or carrier. Copy the account number, and keep a short carrier label when the PDF prints one with it. Use an empty string when no shipping account number is printed. Do not put the delivery method, a street address, a phone number, or the payment terms here.
- Ship To: the address the order should be delivered to, often labeled Ship To, Deliver To, or Delivery Address. Copy it on one line: name, street, city, state, and ZIP. Never use Cincinnati Valve's own address, and never use the bill-to address unless the PDF says to ship there. Use an empty string when no delivery address is printed.
- FIG#: the part or figure number for the line, copied exactly as printed, with every prefix, suffix, and size it includes (for example MSP_18374X1/4" stays MSP_18374X1/4"). Never shorten it. Many purchase orders also print the customer's own internal number in a column such as SKU#, Item #, Stock #, or Material #. That is not the FIG#. When the line has no other part number and the description starts with one, such as 1837 in "1837 - 1/2 BRZ QO VALVE", use that.
- Description: the words printed on that line that describe the part, such as a material, size, or valve type. Use an empty string when that line has no description.
- Qty: the quantity for that line
- Unit Price: the price for one unit on that line
- Sources: the text on the PDF you used to fill this line's fields, so a person can find it quickly. Copy each piece exactly as printed, character for character, with one entry per printed line of text: an address printed on four lines is four entries. Copy the values only, not their labels, and do not convert dates or numbers here. On the first line include the purchase-order fields (company, PO number, date, terms, delivery method, account, ship-to). On every line include that line's part number, description, quantity and unit price.

Decide what each value means from the document. Layouts and labels differ between purchase orders.

Rules:
- Return one object per line item. Repeat the purchase-order fields on every line.
- If a field is not clearly present, use an empty string. Never invent a value.
- Dates must be MM-DD-YYYY.
- Qty and Unit Price must be plain numbers, with no currency symbol or thousands separator. Use a dot as the decimal separator.
- Do not return a sales amount, a vendor purchase order number, or any shipment date.
- Return only JSON: an array of objects with exactly these keys and no others.
"""

# Added when the inventory lists are saved. Gemini picks from them, so the code
# never has to understand how each customer writes a size or a figure.
HINTS_PROMPT = """
Also return these fields on every line, each chosen from its list below:
- Family: the Lunkenheimer figure this line is for, from FAMILIES. It may be printed on its own (Fig. 3072), inside a longer part number (MSP_18374X1/4" is 1837, LV-180-2 is 180), or at the start of the description. Use the value exactly as it appears in the list. Use NONE when no family in the list fits.
- Size: the size of this line in inches, from SIZES, without an inch mark. Sizes are written many ways (3-Inch, 3 in, 3 inch mark, 1-1/2 inch, and a size can be part of the part number). Use NONE when no size is printed for this line.
- Material: what the line says the part is made of, or that it is a spare part, from MATERIALS (BRZ is BRONZE, cast carbon steel is STEEL). Use the main body material. Use NONE when the line does not say.

FAMILIES: {families}
SIZES: {sizes}
MATERIALS: {materials}
"""


NO_PICK = "NONE"


class ExtractError(Exception):
    def __init__(self, message: str, detail: str = ""):
        super().__init__(message)
        # Where the error came from, for the browser console.
        self.detail = detail


def _message(response) -> str:
    try:
        message = response.json()["error"]["message"]
    except (ValueError, KeyError, TypeError):
        message = response.text[:300]
    return " ".join(str(message).split()) or "Gemini could not read that PDF."


def parse_rows(text: str) -> list[dict]:
    return parse_reply(text)[0]


def parse_reply(text: str) -> tuple[list[dict], list[str]]:
    """The rows, and the text Gemini says it used (its Sources), for highlighting."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as first_error:
        start = cleaned.find("[")
        end = cleaned.rfind("]")
        if start == -1 or end == -1 or end <= start:
            start = cleaned.find("{")
            end = cleaned.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ExtractError("Gemini did not return purchase-order lines.", _bad_reply(first_error, cleaned))
        try:
            data = json.loads(cleaned[start:end + 1])
        except json.JSONDecodeError as error:
            raise ExtractError("The purchase order could not be read.", _bad_reply(error, cleaned)) from error
    if isinstance(data, dict):
        data = data.get("rows", data.get("lines"))
    if not isinstance(data, list):
        raise ExtractError("Gemini did not return a list of lines.")
    data = [row for row in data if isinstance(row, dict)]
    rows = [normalize_row(row) for row in data]
    for row in rows:
        for field in HINT_FIELDS:
            if row[field].upper() == NO_PICK:
                row[field] = ""
    sources = []
    for row in data:
        found = row.get("Sources")
        for source in found if isinstance(found, list) else []:
            text = " ".join(str(source or "").split())
            if text and text not in sources:
                sources.append(text)
    return rows, sources


def _bad_reply(error: json.JSONDecodeError, text: str) -> str:
    near = text[max(0, error.pos - 80):error.pos + 80]
    return f"Gemini's reply is not valid JSON ({error.msg} at character {error.pos}). Near there: {near}"


def reset_gemini_usage() -> None:
    _calls.set([])


def gemini_usage() -> list[dict]:
    return list(_calls.get() or [])


def _count(usage: dict, key: str) -> int:
    value = usage.get(key) or 0
    return value if isinstance(value, int) and value > 0 else 0


def _model_name(body: dict) -> str:
    reported = str(body.get("modelVersion") or GEMINI_MODEL or "").strip()
    if reported.startswith("models/"):
        reported = reported[len("models/"):]
    return reported


def _token_rates(model: str, today: date):
    steps = _RATES.get(model)
    if steps is None:
        steps = next((rates for name, rates in _RATES.items() if model.startswith(name + "-")), None)
    if steps is None:
        return None
    for until, incoming, outgoing in steps:
        if until is None or today <= until:
            return incoming, outgoing
    return None


def _dollars(amount: float) -> str:
    if amount >= 0.0001:
        return f"${amount:.4f}"
    return f"${amount:.6f}"


def usage_record(label: str, body: dict, today: date | None = None) -> dict:
    today = today or date.today()
    usage = body.get("usageMetadata") if isinstance(body, dict) else None
    model = _model_name(body if isinstance(body, dict) else {})
    if not isinstance(usage, dict):
        return {
            "label": label,
            "model": model,
            "line": f"Gemini {label}: token usage was not returned",
            "usd": None,
        }
    incoming = _count(usage, "promptTokenCount")
    outgoing = _count(usage, "candidatesTokenCount") + _count(usage, "thoughtsTokenCount")
    rates = _token_rates(model, today)
    usd = None
    if rates is not None:
        usd = incoming / 1_000_000 * rates[0] + outgoing / 1_000_000 * rates[1]
    thinking = _count(usage, "thoughtsTokenCount")
    thought = f" including {thinking:,} thinking" if thinking else ""
    price = _dollars(usd) if usd is not None else "price unknown"
    return {
        "label": label,
        "model": model,
        "input": incoming,
        "output": outgoing,
        "usd": None if usd is None else round(usd, 6),
        "line": (
            f"Gemini {label} ({model}): {incoming:,} input tokens, "
            f"{outgoing:,} output tokens{thought}, {price}"
        ),
    }


def log_gemini_usage(label: str, body: dict) -> None:
    record = usage_record(label, body)
    print(record["line"], flush=True)
    calls = _calls.get()
    if calls is not None:
        calls.append(record)


def rows_from_gemini(body: dict) -> tuple[list[dict], list[str]]:
    error = body.get("error") or {}
    if error and not body.get("candidates"):
        raise ExtractError(error.get("message") or "Gemini could not read that PDF.")
    try:
        text = body["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError):
        raise ExtractError("Gemini did not return any purchase-order lines.")
    return parse_reply(text)


def build_prompt(hints: dict | None = None) -> str:
    if not _has_hints(hints):
        return PROMPT
    return PROMPT + HINTS_PROMPT.format(
        families=", ".join(hints["families"]),
        sizes=", ".join(hints["sizes"]),
        materials=", ".join(hints.get("materials") or []),
    )


def response_schema(hints: dict | None = None) -> dict:
    """The JSON shape Gemini must return: valid JSON, exactly these keys, and the
    Family, Size and Material values limited to the inventory lists."""
    keys = list(COLUMNS) + [field for field in ORDER_FIELDS if field not in HINT_FIELDS]
    properties = {key: {"type": "STRING"} for key in keys}
    if _has_hints(hints):
        lists = {"Family": hints["families"], "Size": hints["sizes"], "Material": hints.get("materials") or []}
        for field, values in lists.items():
            # Gemini does not accept an empty choice, so NONE stands for "not printed".
            properties[field] = {"type": "STRING", "enum": [NO_PICK, *values]}
            keys.append(field)
    properties["Sources"] = {"type": "ARRAY", "items": {"type": "STRING"}}
    keys.append("Sources")
    return {
        "type": "ARRAY",
        "items": {"type": "OBJECT", "properties": properties, "required": keys, "propertyOrdering": keys},
    }


def _has_hints(hints: dict | None) -> bool:
    return bool(hints and hints.get("families") and hints.get("sizes"))


def extract_bytes(pdf_bytes: bytes, hints: dict | None = None) -> tuple[list[dict], list[str]]:
    """The purchase-order rows, and the printed text Gemini used for them."""
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
                        {"text": build_prompt(hints)},
                    ]
                }
            ],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": response_schema(hints),
                "thinkingConfig": GEMINI_THINKING,
            },
        },
        timeout=120,
    )
    if not response.ok:
        raise ExtractError(_message(response), f"Gemini API answered HTTP {response.status_code}: {_message(response)}")
    body = response.json()
    log_gemini_usage("read PDF", body)
    return rows_from_gemini(body)
