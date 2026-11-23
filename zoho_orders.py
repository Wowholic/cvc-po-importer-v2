"""Save a Zoho Books sales order as a draft.

The only writes are the draft itself and its PDF attachment. This module
does not email the customer, open the order, or create customers or items.
"""

import logging
import re
import threading
import time
from pathlib import Path

import requests

from config import ZOHO_API_DOMAIN, ZOHO_ORGANIZATION_ID
from zoho_customers import FIELDS_PATH, ZohoError, _access_token, _books_get, configured


class OrderInputError(ZohoError):
    """The form is not ready to save. Nothing was sent to Zoho Books."""

logger = logging.getLogger("purchase_desk")

SALES_ORDER_PATH = "/books/v3/salesorders"
SHIPMENT_API = "cf_shipment_status"
SHIPMENT_VALUE = "Not Shipped"
OTHER_API = "cf_other"
DATE_RE = re.compile(r"^(0[1-9]|[12][0-9]|3[01])-(0[1-9]|1[0-2])-([0-9]{4})$")
NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")
_lock = threading.Lock()
_fields = {"until": 0.0, "values": None}


def sales_order_payload(order: dict, fields: list[dict]) -> dict:
    if not isinstance(order, dict):
        raise OrderInputError("Choose a Zoho Books customer before creating a sales order.")
    customer_id = _record_id(order.get("customerId"), "Choose a Zoho Books customer before creating a sales order.")
    lines = _line_items(order.get("lines"))
    payload = {
        "customer_id": customer_id,
        "date": _iso_date(order.get("date")),
        "delivery_method": _text(order.get("deliveryMethod")),
        "line_items": lines,
        "notes": "",
        "terms": "",
        "is_update_customer": False,
        "contact_persons_associated": [],
        "custom_fields": _custom_fields(order, fields),
    }
    payload["payment_terms"] = _days(order.get("paymentTermsDays"))
    payload["payment_terms_label"] = _text(order.get("paymentTermsLabel"))
    if not payload["payment_terms_label"]:
        raise OrderInputError("Choose a Zoho Books payment term before creating a sales order.")
    if not payload["delivery_method"]:
        raise OrderInputError("Choose a Zoho Books delivery method before creating a sales order.")
    reference = _text(order.get("reference"))
    if reference:
        payload["reference_number"] = reference
    method_id = str(order.get("deliveryMethodId") or "").strip()
    if method_id.isdigit():
        payload["delivery_method_id"] = method_id
    return payload


def prepare_draft_sales_order(order: dict, pdf_bytes: bytes) -> dict:
    if not configured():
        raise ZohoError("Zoho Books is not connected. No order was created.")
    if not pdf_bytes.startswith(b"%PDF"):
        raise OrderInputError("Attach the purchase order PDF before creating the sales order.")
    return sales_order_payload(order, sales_order_fields())


def create_draft_sales_order(payload: dict, pdf_bytes: bytes, filename: str) -> dict:
    body = _post_json(SALES_ORDER_PATH, payload)
    salesorder = body.get("salesorder")
    if isinstance(salesorder, list):
        salesorder = salesorder[0] if salesorder else {}
    if not isinstance(salesorder, dict):
        salesorder = {}
    salesorder_id = str(salesorder.get("salesorder_id") or "")
    number = str(salesorder.get("salesorder_number") or "").strip() or "the sales order"
    status = str(salesorder.get("status") or "").strip().lower()
    if not salesorder_id.isdigit():
        raise ZohoError("Zoho Books did not return the draft sales order.")
    attached = _attach_pdf(salesorder_id, pdf_bytes, filename)
    if status == "draft":
        message = f"Draft {number} was saved in Zoho Books."
    else:
        shown = status or "an unexpected status"
        message = f"Zoho Books saved {number} as {shown}."
    if not attached:
        message += " The PDF was not attached."
    return {
        "created": True,
        "id": salesorder_id,
        "number": number,
        "status": status,
        "attachment": attached,
        "url": _sales_order_url(salesorder_id),
        "message": message,
    }


def _sales_order_url(salesorder_id: str) -> str:
    host = ZOHO_API_DOMAIN.replace("www.zohoapis", "books.zoho")
    return f"https://{host}/app/{ZOHO_ORGANIZATION_ID}#/salesorders/{salesorder_id}"


def sales_order_fields() -> list[dict]:
    now = time.monotonic()
    with _lock:
        if _fields["values"] is not None and now < _fields["until"]:
            return _fields["values"]
    body = _books_get(FIELDS_PATH, {
        "organization_id": ZOHO_ORGANIZATION_ID,
        "entity": "salesorder",
    })
    collected = []
    for field in body.get("fields") or []:
        if not isinstance(field, dict) or not field.get("is_active", True):
            continue
        if str(field.get("status") or "Active").lower() == "inactive":
            continue
        collected.append(field)
    with _lock:
        _fields["values"] = collected
        _fields["until"] = time.monotonic() + 600
    return collected


def _custom_fields(order: dict, fields: list[dict]) -> list[dict]:
    by_api = {str(field.get("api_name") or ""): field for field in fields}
    shipment = by_api.get(SHIPMENT_API)
    if shipment is None or not _dropdown_has(shipment, SHIPMENT_VALUE):
        raise OrderInputError("Zoho Books has no Shipment Status choice named Not Shipped.")
    custom = [{"customfield_id": str(shipment.get("field_id") or ""), "value": SHIPMENT_VALUE}]
    other = _text(order.get("other"))
    if other:
        field = by_api.get(OTHER_API)
        if field is None:
            raise OrderInputError("Zoho Books has no custom field named Other on sales orders.")
        custom.append({"customfield_id": str(field.get("field_id") or ""), "value": other[:500]})
    return custom


def _dropdown_has(field: dict, value: str) -> bool:
    for option in field.get("values") or []:
        if isinstance(option, dict) and option.get("name") == value and option.get("is_active", True):
            return True
    return False


def _line_items(lines) -> list[dict]:
    if not isinstance(lines, list) or not lines:
        raise OrderInputError("Add at least one Zoho Books item before creating a sales order.")
    items = []
    for index, line in enumerate(lines, start=1):
        if not isinstance(line, dict):
            raise OrderInputError(f"Line {index}: choose a Zoho Books item.")
        item_id = _record_id(line.get("itemId"), f"Line {index}: choose a Zoho Books item.")
        items.append({
            "item_id": item_id,
            "quantity": _number(line.get("quantity"), f"Line {index}: Qty needs to be a plain number."),
            "rate": _number(line.get("rate"), f"Line {index}: Unit Price needs to be a plain number."),
        })
    return items


def _record_id(value, message: str) -> str:
    text = str(value or "").strip()
    if not text.isdigit():
        raise OrderInputError(message)
    return text


def _text(value) -> str:
    return " ".join(str(value or "").split())


def _days(value) -> int:
    if isinstance(value, bool):
        raise OrderInputError("Choose a Zoho Books payment term before creating a sales order.")
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value if value is not None else "").strip()
    if not re.fullmatch(r"-?\d+", text):
        raise OrderInputError("Choose a Zoho Books payment term before creating a sales order.")
    return int(text)


def _iso_date(value) -> str:
    match = DATE_RE.fullmatch(_text(value))
    if not match:
        raise OrderInputError("PO DATE needs to be DD-MM-YYYY.")
    day, month, year = match.groups()
    return f"{year}-{month}-{day}"


def _number(value, message: str):
    text = str(value or "").strip().replace(",", "").replace("$", "")
    if not NUMBER_RE.fullmatch(text):
        raise OrderInputError(message)
    number = float(text)
    if number.is_integer():
        return int(number)
    return number


def _post_json(path: str, payload: dict) -> dict:
    if path != SALES_ORDER_PATH:
        raise ZohoError("This app only creates a draft sales order.")
    token, domain = _access_token()
    response = requests.post(
        f"{domain}{path}",
        params={"organization_id": ZOHO_ORGANIZATION_ID},
        headers={
            "Authorization": f"Zoho-oauthtoken {token}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=60,
    )
    return _response_body(response)


def _attach_pdf(salesorder_id: str, pdf_bytes: bytes, filename: str) -> bool:
    safe_name = Path(str(filename or "")).name or "purchase-order.pdf"
    if not safe_name.lower().endswith(".pdf"):
        safe_name += ".pdf"
    token, domain = _access_token()
    try:
        response = requests.post(
            f"{domain}{SALES_ORDER_PATH}/{salesorder_id}/attachment",
            params={"organization_id": ZOHO_ORGANIZATION_ID, "can_send_in_mail": "false"},
            headers={"Authorization": f"Zoho-oauthtoken {token}"},
            files={"attachment": (safe_name, pdf_bytes, "application/pdf")},
            data={"can_send_in_mail": "false"},
            timeout=60,
        )
        _response_body(response)
    except (ZohoError, requests.RequestException):
        logger.exception("sales order attachment failed")
        return False
    return True


def _response_body(response) -> dict:
    try:
        body = response.json()
    except ValueError:
        body = {}
    if response.status_code >= 400 or not isinstance(body, dict) or body.get("code") not in (0, None):
        message = body.get("message") if isinstance(body, dict) else ""
        raise ZohoError(message or "Zoho Books could not save the draft sales order.")
    return body
