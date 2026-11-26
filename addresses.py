"""A Zoho customer's addresses, and which one a purchase order ships to.

Addresses are fetched once per customer, when that customer is chosen, and
saved in cache/addresses.json until the next Sync. Matching uses the ZIP code,
street numbers, street words and city. It never calls Gemini.
"""

import json
import os
import re
import threading
from pathlib import Path

import requests

from config import ZOHO_ORGANIZATION_ID
from zoho_customers import ZohoError, _access_token, configured

CONTACTS_PATH = "/books/v3/contacts"
SURE_SCORE = 50
SURE_LEAD = 15
ABBREVIATIONS = {
    "STREET": "ST", "ROAD": "RD", "AVENUE": "AVE", "AV": "AVE", "DRIVE": "DR",
    "BOULEVARD": "BLVD", "SUITE": "STE", "BUILDING": "BLDG", "BLD": "BLDG",
    "HIGHWAY": "HWY", "LANE": "LN", "PARKWAY": "PKWY", "COURT": "CT",
    "PLACE": "PL", "CIRCLE": "CIR", "NORTH": "N", "SOUTH": "S", "EAST": "E",
    "WEST": "W", "FLOOR": "FL", "DEPARTMENT": "DEPT", "MOUNT": "MT", "SAINT": "ST",
}
# Words too common to tell two addresses apart.
COMMON_WORDS = {
    "ST", "RD", "AVE", "DR", "BLVD", "STE", "BLDG", "HWY", "LN", "PKWY", "CT", "PL",
    "CIR", "N", "S", "E", "W", "FL", "DEPT", "C", "O", "THE", "AND", "OF", "USA", "US",
    "INC", "LLC", "CORP", "CO", "ATTN", "SHIP", "TO",
}

_lock = threading.Lock()
_memory = None


def address_choices(contact_id: str, ship_to: str) -> dict:
    """The customer's addresses and the one that best fits the PDF's ship-to."""
    addresses = customer_addresses(contact_id)
    chosen, sure = match_address(ship_to, addresses)
    return {"addresses": addresses, "match": chosen, "sure": sure}


def customer_addresses(contact_id: str) -> list[dict]:
    contact_id = str(contact_id or "").strip()
    if not contact_id.isdigit():
        raise ZohoError("Choose a Zoho Books customer.")
    saved = _saved().get(contact_id)
    if isinstance(saved, list):
        return saved
    addresses = _fetch(contact_id)
    with _lock:
        stored = _saved()
        stored[contact_id] = addresses
        _write(stored)
    return addresses


def clear_addresses() -> None:
    global _memory
    with _lock:
        _memory = {}
        try:
            os.remove(_path())
        except OSError:
            pass


def match_address(ship_to: str, addresses: list[dict]) -> tuple[str, bool]:
    """Return (address id, sure). Falls back to the default shipping address."""
    if not addresses:
        return "", True
    default = next((row["id"] for row in addresses if row.get("default")), addresses[0]["id"])
    if len(addresses) == 1:
        return addresses[0]["id"], True
    words = _words(ship_to)
    if not words:
        return default, True
    scored = sorted(
        ((_score(words, row), row["id"]) for row in addresses),
        reverse=True,
    )
    best, best_id = scored[0]
    runner_up = scored[1][0]
    if best >= SURE_SCORE and best - runner_up >= SURE_LEAD:
        return best_id, True
    return default, False


def _score(pdf_words: list[str], address: dict) -> float:
    pdf_set = set(pdf_words)
    pdf_numbers = {word for word in pdf_words if word.isdigit()}
    score = 0.0
    zip_code = address.get("zip5", "")
    if zip_code and zip_code in pdf_numbers:
        score += 40
    # Some Zoho addresses repeat the ZIP+4 inside the street line; it is not a street number.
    street = _words(re.sub(r"\b\d{5}-\d{4}\b", " ", address.get("street", "")))
    numbers = {word for word in street if word.isdigit() and len(word) <= 6 and word != zip_code}
    if numbers & pdf_numbers:
        score += 30
    names = {word for word in street if not word.isdigit() and word not in COMMON_WORDS and len(word) > 1}
    if names:
        score += 20 * len(names & pdf_set) / len(names)
    city = [word for word in _words(address.get("city", "")) if word not in COMMON_WORDS]
    if city and all(word in pdf_set for word in city):
        score += 10
    return score


def _words(text: str) -> list[str]:
    text = re.sub(r"[^A-Z0-9]+", " ", str(text or "").upper())
    text = re.sub(r"(?<=[A-Z])(?=\d)|(?<=\d)(?=[A-Z])", " ", text)
    return [ABBREVIATIONS.get(word, word) for word in text.split()]


def _fetch(contact_id: str) -> list[dict]:
    if not configured():
        raise ZohoError("Zoho Books is not connected.")
    token, domain = _access_token()
    try:
        response = requests.get(
            f"{domain}{CONTACTS_PATH}/{contact_id}",
            params={"organization_id": ZOHO_ORGANIZATION_ID},
            headers={"Authorization": f"Zoho-oauthtoken {token}"},
            timeout=60,
        )
        body = response.json()
    except (requests.RequestException, ValueError) as error:
        raise ZohoError("Zoho Books could not load the customer's addresses.") from error
    if response.status_code >= 400 or not isinstance(body, dict) or body.get("code") not in (0, None):
        message = body.get("message") if isinstance(body, dict) else ""
        raise ZohoError(message or "Zoho Books could not load the customer's addresses.")
    return addresses_from_contact(body.get("contact") or {})


def addresses_from_contact(contact: dict) -> list[dict]:
    """Default shipping first, then billing, then any other addresses. No duplicates."""
    shipping = contact.get("shipping_address") or {}
    billing = contact.get("billing_address") or {}
    shipping_id = str(shipping.get("address_id") or "")
    billing_id = str(billing.get("address_id") or "")
    raw = [shipping, billing, *(contact.get("addresses") or [])]
    rows = []
    seen = set()
    for address in raw:
        if not isinstance(address, dict):
            continue
        address_id = str(address.get("address_id") or "").strip()
        if not address_id.isdigit() or address_id in seen:
            continue
        text = _one_line(address)
        if not text:
            continue
        seen.add(address_id)
        tags = []
        if address_id == shipping_id:
            tags.append("Default shipping")
        if address_id == billing_id:
            tags.append("Billing")
        rows.append({
            "id": address_id,
            "text": text,
            "tag": ", ".join(tags),
            "default": address_id == shipping_id,
            "street": " ".join(_plain(address.get(key)) for key in ("attention", "address", "street2")),
            "city": _plain(address.get("city")),
            "zip5": _zip5(address),
        })
    return rows


def _one_line(address: dict) -> str:
    parts = [_plain(address.get(key)) for key in ("attention", "address", "street2")]
    parts = [part for part in parts if part]
    city = _plain(address.get("city"))
    state = _plain(address.get("state_code") or address.get("state"))
    zip_code = _plain(address.get("zip"))
    joined = " ".join(parts).upper()
    if city and city.upper() not in joined:
        parts.append(city)
    tail = " ".join(part for part in (state, zip_code) if part and part.upper() not in joined)
    if tail:
        parts.append(tail)
    return ", ".join(parts)


def _plain(value) -> str:
    return " ".join(str(value or "").split())


def _zip5(address: dict) -> str:
    found = re.match(r"\s*(\d{5})", str(address.get("zip") or ""))
    return found.group(1) if found else ""


def _path() -> Path:
    folder = Path(__file__).resolve().parent / "cache"
    if folder.is_dir() and os.access(folder, os.W_OK):
        return folder / "addresses.json"
    return Path("/tmp/cvc-po-addresses.json")


def _saved() -> dict:
    global _memory
    if _memory is None:
        try:
            with _path().open(encoding="utf-8") as handle:
                loaded = json.load(handle)
            _memory = loaded if isinstance(loaded, dict) else {}
        except (OSError, ValueError):
            _memory = {}
    return _memory


def _write(stored: dict) -> None:
    path = _path()
    temporary = path.with_suffix(".json.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(stored, handle)
        os.replace(temporary, path)
    except OSError:
        pass
