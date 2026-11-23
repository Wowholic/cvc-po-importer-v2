"""Read Zoho Books customers, items, payment terms, and delivery methods.

This module never creates or updates Zoho data.
"""

import json
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import requests

from config import (
    GEMINI_API_KEY,
    GEMINI_MODEL,
    ZOHO_ACCOUNTS_DOMAIN,
    ZOHO_API_DOMAIN,
    ZOHO_CLIENT_ID,
    ZOHO_CLIENT_SECRET,
    ZOHO_ORGANIZATION_ID,
    ZOHO_REFRESH_TOKEN,
)
from extract_po import ExtractError

logger = logging.getLogger("purchase_desk")

CONTACTS_PATH = "/books/v3/contacts"
ITEMS_PATH = "/books/v3/items"
PAYMENT_TERMS_PATH = "/books/v3/settings/paymentterms"
FIELDS_PATH = "/books/v3/settings/fields"
DELIVERY_METHOD_PATHS = (
    "/books/v3/settings/deliverymethods",
    "/books/v3/deliverymethods",
)
READ_PATHS = {CONTACTS_PATH, ITEMS_PATH, PAYMENT_TERMS_PATH, FIELDS_PATH, *DELIVERY_METHOD_PATHS}
LEGAL_WORDS = {
    "inc", "incorporated", "llc", "ltd", "limited", "co", "company", "corp",
    "corporation", "plc", "gmbh", "sa", "nv", "bv", "lp", "llp", "the",
}
MATCH_FLOOR = 0.72
_lock = threading.Lock()
_inflight = {}
_cache = {
    "token": "",
    "token_until": 0.0,
    "api_domain": "",
    "customers": None,
    "customers_until": 0.0,
    "items": None,
    "items_until": 0.0,
    "payment_terms": None,
    "payment_terms_until": 0.0,
    "delivery_methods": None,
    "delivery_methods_until": 0.0,
}


class ZohoError(Exception):
    pass


def configured() -> bool:
    return bool(ZOHO_CLIENT_ID and ZOHO_CLIENT_SECRET and ZOHO_REFRESH_TOKEN and ZOHO_ORGANIZATION_ID)


def customer_choices(company: str) -> dict:
    extracted = " ".join(str(company or "").split())
    if not configured():
        return {"connected": False, "customers": [], "match": None, "extracted": extracted}
    customers = list_customers()
    match = best_match(extracted, customers)
    return {
        "connected": True,
        "customers": customers,
        "match": {"id": match["id"], "name": match["name"]} if match else None,
        "extracted": extracted,
    }


def order_option_choices(payment_terms: str, delivery_method: str) -> dict:
    payment_source = " ".join(str(payment_terms or "").split())
    delivery_source = " ".join(str(delivery_method or "").split())
    empty = {"paymentTerms": None, "deliveryMethod": None}
    if not configured():
        return {
            "connected": False,
            "paymentTerms": [],
            "deliveryMethods": [],
            "matches": empty,
        }
    terms, terms_error = _saved_list("payment_terms", list_payment_terms)
    methods, methods_error = _saved_list("delivery_methods", list_delivery_methods)
    if terms_error and methods_error:
        raise ZohoError(terms_error)
    matches = dict(empty)
    if (payment_source and terms) or (delivery_source and methods):
        try:
            chosen = _ask_option_matches(payment_source, terms, delivery_source, methods)
        except ExtractError:
            logger.exception("order option match failed")
            chosen = {}
        term = next((item for item in terms if item["id"] == chosen.get("paymentTerms")), None)
        method = next((item for item in methods if item["id"] == chosen.get("deliveryMethod")), None)
        if payment_source and term:
            matches["paymentTerms"] = {"id": term["id"], "name": term["name"]}
        if delivery_source and method:
            matches["deliveryMethod"] = {"id": method["id"], "name": method["name"]}
    result = {
        "connected": True,
        "paymentTerms": terms,
        "deliveryMethods": methods,
        "matches": matches,
    }
    if terms_error:
        result["paymentTermsError"] = terms_error
    if methods_error:
        result["deliveryMethodsError"] = methods_error
    return result


def _saved_list(key: str, loader):
    try:
        return loader(), ""
    except ZohoError as error:
        logger.exception("%s list failed", key)
        return [], str(error)


def collect_pages(fetch, limit: int) -> list:
    """Fetch list pages together. fetch(page) returns (rows, has_more)."""
    rows, more = fetch(1)
    collected = list(rows)
    page = 2
    while more and page <= limit:
        last = min(page + 7, limit)
        wave = list(range(page, last + 1))
        with ThreadPoolExecutor(max_workers=len(wave)) as pool:
            got = list(pool.map(lambda number: (number, fetch(number)), wave))
        got.sort()
        for _number, (page_rows, page_more) in got:
            collected.extend(page_rows)
            more = page_more
            if not page_more:
                break
        page = last + 1
    return collected


def _remember(key: str, loader):
    now = time.monotonic()
    with _lock:
        cached = _cache[key]
        if cached is not None and now < _cache[f"{key}_until"]:
            return cached
        event = _inflight.get(key)
        if event is None:
            event = threading.Event()
            _inflight[key] = event
            leader = True
        else:
            leader = False
    if not leader:
        event.wait(180)
        with _lock:
            cached = _cache[key]
            fresh = cached is not None and time.monotonic() < _cache[f"{key}_until"]
        if fresh:
            return cached
        return _remember(key, loader)
    try:
        collected = loader()
    except Exception:
        with _lock:
            _inflight.pop(key, None)
        event.set()
        raise
    with _lock:
        _cache[key] = collected
        _cache[f"{key}_until"] = time.monotonic() + 600
        _inflight.pop(key, None)
    event.set()
    return collected


def list_customers() -> list[dict]:
    return _remember("customers", _fetch_customers)


def _fetch_customers() -> list[dict]:
    def fetch(page):
        body = _books_get(CONTACTS_PATH, {
            "organization_id": ZOHO_ORGANIZATION_ID,
            "contact_type": "customer",
            "page": page,
            "per_page": 200,
        })
        rows = []
        for contact in body.get("contacts") or []:
            if not isinstance(contact, dict):
                continue
            name = " ".join(str(contact.get("contact_name") or "").split())
            company_name = " ".join(str(contact.get("company_name") or "").split())
            if not name and not company_name:
                continue
            rows.append({
                "id": str(contact.get("contact_id") or ""),
                "name": name or company_name,
                "company": company_name,
                "status": str(contact.get("status") or "active"),
            })
        context = body.get("page_context") or {}
        return rows, bool(context.get("has_more_page"))

    collected = collect_pages(fetch, 25)
    collected.sort(key=lambda item: (item["status"] != "active", item["name"].lower()))
    return collected


def item_catalog() -> dict:
    if not configured():
        return {"connected": False, "items": []}
    return {"connected": True, "items": list_items()}


def item_choices(sources) -> dict:
    catalog = item_catalog()
    if not catalog["connected"]:
        return {"connected": False, "items": [], "matches": {}}
    items = catalog["items"]
    by_id = {item["id"]: item for item in items if item.get("id")}
    wanted = []
    seen = set()
    for source in sources if isinstance(sources, list) else []:
        text = " ".join(str(source or "").split())
        if not text or text in seen:
            continue
        seen.add(text)
        wanted.append(text)
    chosen = {}
    if wanted and by_id:
        catalog = [[item["id"], item["name"], item.get("sku", "")] for item in items if item.get("id")]
        try:
            chosen = _ask_item_matches(wanted, catalog)
        except ExtractError:
            logger.exception("item match failed")
            for text in wanted:
                fallback = best_item(text, items)
                if fallback:
                    chosen[text] = fallback["id"]
    matches = {}
    for text in wanted:
        item = by_id.get(chosen.get(text, ""))
        if item:
            matches[text] = {"id": item["id"], "name": item["name"]}
    return {"connected": True, "items": items, "matches": matches}


def _ask_item_matches(sources: list[str], catalog: list[list[str]]) -> dict[str, str]:
    if not GEMINI_API_KEY:
        raise ExtractError("Set GEMINI_API_KEY before matching items.")
    allowed = {row[0] for row in catalog}
    prompt = f"""Match each purchase-order figure number to the most likely Zoho Books item.

Zoho Books items. Each entry is [id, name, sku]:
{json.dumps(catalog)}

Figure numbers from the PDF:
{json.dumps(sources)}

Return a JSON array of objects with keys "source" and "id".
Copy "source" exactly from the figure numbers.
Copy "id" exactly from the Zoho Books items. For every figure, choose the single most likely item.
The figure code is often only part of a longer name or sku. A brand, material, size, or extra characters may sit beside it. Match on the shared figure code, even when the two strings are not identical.
Leave "id" empty only when no item shares any of that figure code. Do not invent an id.
"""
    response = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent",
        headers={"x-goog-api-key": GEMINI_API_KEY},
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json"},
        },
        timeout=120,
    )
    if not response.ok:
        raise ExtractError("The item list could not be matched.")
    try:
        text = response.json()["candidates"][0]["content"]["parts"][0]["text"]
        parsed = json.loads(text)
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as error:
        raise ExtractError("The item list could not be matched.") from error
    if not isinstance(parsed, list):
        raise ExtractError("The item list could not be matched.")
    matches = {}
    for item in parsed:
        if not isinstance(item, dict):
            continue
        source = " ".join(str(item.get("source") or "").split())
        item_id = str(item.get("id") or "")
        if source in sources and item_id in allowed:
            matches[source] = item_id
    return matches


def _ask_option_matches(payment_source: str, terms: list[dict], delivery_source: str, methods: list[dict]) -> dict:
    if not GEMINI_API_KEY:
        raise ExtractError("Set GEMINI_API_KEY before matching payment terms.")
    payload = {}
    allowed = {}
    if payment_source and terms:
        payload["paymentTerms"] = {
            "source": payment_source,
            "options": [[item["id"], item["name"]] for item in terms],
        }
        allowed["paymentTerms"] = {item["id"] for item in terms}
    if delivery_source and methods:
        payload["deliveryMethod"] = {
            "source": delivery_source,
            "options": [[item["id"], item["name"]] for item in methods],
        }
        allowed["deliveryMethod"] = {item["id"] for item in methods}
    if not payload:
        return {}
    prompt = f"""Match the purchase order to the most likely Zoho Books payment term and delivery method.

{json.dumps(payload)}

Each present key has "source" (the text from the PDF) and "options" (every Zoho Books choice, as [id, name]).
Return a JSON object with those same keys. Each value is an id copied from that key's options, or an empty string.
Choose the single most likely option. The PDF wording often differs from the Zoho name. Numbers, "net", "days", "due", a carrier, and extra words can still be the same choice.
Leave the id empty only when no option is that payment term or delivery method. Do not invent an id.
"""
    response = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent",
        headers={"x-goog-api-key": GEMINI_API_KEY},
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json"},
        },
        timeout=60,
    )
    if not response.ok:
        raise ExtractError("The payment terms could not be matched.")
    try:
        text = response.json()["candidates"][0]["content"]["parts"][0]["text"]
        parsed = json.loads(text)
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as error:
        raise ExtractError("The payment terms could not be matched.") from error
    if not isinstance(parsed, dict):
        raise ExtractError("The payment terms could not be matched.")
    chosen = {}
    for key, ids in allowed.items():
        item_id = str(parsed.get(key) or "")
        if item_id in ids:
            chosen[key] = item_id
    return chosen


def list_payment_terms() -> list[dict]:
    return _cached_records("payment_terms", _fetch_payment_terms)


def list_delivery_methods() -> list[dict]:
    return _cached_records("delivery_methods", _fetch_delivery_methods)


def _cached_records(key: str, loader) -> list[dict]:
    now = time.monotonic()
    with _lock:
        cached = _cache[key]
        if cached is not None and now < _cache[f"{key}_until"]:
            return cached
    collected = loader()
    with _lock:
        _cache[key] = collected
        _cache[f"{key}_until"] = time.monotonic() + 600
    return collected


def _fetch_payment_terms() -> list[dict]:
    body = _books_get(PAYMENT_TERMS_PATH, {"organization_id": ZOHO_ORGANIZATION_ID})
    return payment_terms_from(body)


def _fetch_delivery_methods() -> list[dict]:
    last_error = None
    saw_success = False
    for path in DELIVERY_METHOD_PATHS:
        try:
            body = _books_get(path, {"organization_id": ZOHO_ORGANIZATION_ID})
        except ZohoError as error:
            last_error = error
            continue
        saw_success = True
        found = delivery_methods_from(body)
        if found:
            return found
    if saw_success:
        return []
    raise last_error or ZohoError("Zoho Books could not load delivery methods.")


def payment_terms_from(body: dict) -> list[dict]:
    data = body.get("data") if isinstance(body.get("data"), dict) else {}
    rows = data.get("payment_terms") or body.get("payment_terms") or []
    terms = _named_rows(rows, "payment_terms_id", "payment_terms_label", "payment_terms")
    days_by_id = {}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        label = " ".join(str(row.get("payment_terms_label") or "").split())
        item_id = " ".join(str(row.get("payment_terms_id") or "").split()) or label
        days = row.get("payment_terms")
        if item_id and isinstance(days, (int, float)) and not isinstance(days, bool):
            days_by_id[item_id] = int(days)
    for term in terms:
        if term["id"] in days_by_id:
            term["days"] = days_by_id[term["id"]]
    return terms


def delivery_methods_from(body: dict) -> list[dict]:
    data = body.get("data") if isinstance(body.get("data"), dict) else {}
    rows = (
        data.get("delivery_methods")
        or body.get("delivery_methods")
        or data.get("delivery_method")
        or []
    )
    if isinstance(rows, dict):
        rows = [rows]
    return _named_rows(rows, "delivery_method_id", "delivery_method", "name")


def _named_rows(rows, id_key: str, name_key: str, extra_key: str) -> list[dict]:
    if not isinstance(rows, list):
        return []
    collected = []
    seen = set()
    for row in rows:
        if isinstance(row, str):
            name = " ".join(row.split())
            item_id = name
        elif isinstance(row, dict):
            name = " ".join(str(row.get(name_key) or row.get(extra_key) or "").split())
            item_id = " ".join(str(row.get(id_key) or "").split()) or name
        else:
            continue
        if not name or item_id in seen:
            continue
        seen.add(item_id)
        collected.append({"id": item_id, "name": name})
    collected.sort(key=lambda item: item["name"].lower())
    return collected


def list_items() -> list[dict]:
    return _remember("items", _fetch_items)


def _fetch_items() -> list[dict]:
    def fetch(page):
        body = _books_get(ITEMS_PATH, {
            "organization_id": ZOHO_ORGANIZATION_ID,
            "page": page,
            "per_page": 200,
        })
        rows = []
        for item in body.get("items") or []:
            if not isinstance(item, dict):
                continue
            name = " ".join(str(item.get("name") or "").split())
            sku = " ".join(str(item.get("sku") or "").split())
            if not name and not sku:
                continue
            rows.append({
                "id": str(item.get("item_id") or ""),
                "name": name or sku,
                "sku": sku,
                "status": str(item.get("status") or "active"),
            })
        context = body.get("page_context") or {}
        return rows, bool(context.get("has_more_page"))

    collected = collect_pages(fetch, 50)
    collected.sort(key=lambda item: (item["status"] != "active", item["name"].lower()))
    return collected


def best_item(figure: str, items: list[dict]) -> dict | None:
    ranked = []
    for item in items:
        score = _item_score(figure, item)
        if score < 0.8:
            continue
        ranked.append((
            -score,
            _code_distance(figure, item),
            item.get("status") != "active",
            item.get("name", "").lower(),
            item,
        ))
    if not ranked:
        return None
    ranked.sort()
    return ranked[0][-1]


def _code(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def _shared_code_length(left: str, right: str) -> int:
    if not left or not right:
        return 0
    shorter, longer = (left, right) if len(left) <= len(right) else (right, left)
    for size in range(len(shorter), 3, -1):
        for start in range(0, len(shorter) - size + 1):
            chunk = shorter[start:start + size]
            if any(character.isdigit() for character in chunk) and chunk in longer:
                return size
    return 0


def _code_distance(figure: str, item: dict) -> int:
    query = _code(figure)
    keys = [key for key in (_code(item.get("name")), _code(item.get("sku"))) if key]
    if not query or not keys:
        return 999
    return min(abs(len(key) - len(query)) for key in keys)


def _item_score(figure: str, item: dict) -> float:
    query = _code(figure)
    if not query:
        return 0
    best = 0.0
    for raw in (item.get("name", ""), item.get("sku", "")):
        key = _code(raw)
        if not key:
            continue
        if query == key:
            return 1
        parts = re.findall(r"[A-Z0-9]+", str(raw).upper())
        if query in parts:
            best = max(best, 0.97)
        shared = _shared_code_length(query, key)
        if shared >= 4:
            best = max(best, 0.7 + (min(shared, 12) / 40))
        if len(query) >= 4 and (query in key or key in query):
            extra = abs(len(key) - len(query))
            if extra <= 3:
                best = max(best, 0.9 - (extra * 0.02))
        best = max(best, _score(_tokens(figure), _tokens(raw)))
    return best


def best_match(company: str, customers: list[dict]) -> dict | None:
    query = _tokens(company)
    if not query:
        return None
    ranked = []
    for customer in customers:
        names = [customer.get("name", ""), customer.get("company", "")]
        score = max((_score(query, _tokens(name)) for name in names), default=0)
        if score >= MATCH_FLOOR:
            ranked.append((score, customer))
    if not ranked:
        return None
    ranked.sort(key=lambda item: item[0], reverse=True)
    if len(ranked) > 1 and ranked[0][0] - ranked[1][0] < 0.05:
        leading = [item[1] for item in ranked if ranked[0][0] - item[0] < 0.05]
        names = {_tokens(item.get("name", "")) for item in leading}
        if len(names) != 1:
            return None
    return ranked[0][1]


def _tokens(value: str) -> tuple[str, ...]:
    words = re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).split()
    return tuple(word for word in words if word not in LEGAL_WORDS)


def _score(query: tuple[str, ...], candidate: tuple[str, ...]) -> float:
    if not query or not candidate:
        return 0
    if query == candidate:
        return 1
    left, right = set(query), set(candidate)
    if left == right:
        return 0.98
    overlap = len(left & right)
    if not overlap:
        return 0
    return overlap / max(len(left), len(right))


def _access_token() -> tuple[str, str]:
    now = time.monotonic()
    with _lock:
        if _cache["token"] and now < _cache["token_until"]:
            return _cache["token"], _cache["api_domain"] or ZOHO_API_DOMAIN
    response = requests.post(
        f"https://{ZOHO_ACCOUNTS_DOMAIN}/oauth/v2/token",
        params={
            "refresh_token": ZOHO_REFRESH_TOKEN,
            "client_id": ZOHO_CLIENT_ID,
            "client_secret": ZOHO_CLIENT_SECRET,
            "grant_type": "refresh_token",
        },
        timeout=30,
    )
    try:
        body = response.json()
    except ValueError:
        body = {}
    token = body.get("access_token") if isinstance(body, dict) else None
    if response.status_code >= 400 or not token:
        message = body.get("error") if isinstance(body, dict) else ""
        raise ZohoError(_token_message(str(message or "")))
    domain = str(body.get("api_domain") or f"https://{ZOHO_API_DOMAIN}").rstrip("/")
    expires = int(body.get("expires_in") or 3600)
    with _lock:
        _cache["token"] = token
        _cache["api_domain"] = domain
        _cache["token_until"] = time.monotonic() + max(expires - 60, 30)
    return token, domain


def _books_get(path: str, params: dict) -> dict:
    if path not in READ_PATHS:
        raise ZohoError("This app only reads Zoho Books.")
    token, domain = _access_token()
    response = requests.get(
        f"{domain}{path}",
        params=params,
        headers={"Authorization": f"Zoho-oauthtoken {token}"},
        timeout=30,
    )
    try:
        body = response.json()
    except ValueError:
        body = {}
    if response.status_code >= 400 or not isinstance(body, dict) or body.get("code") not in (0, None):
        message = body.get("message") if isinstance(body, dict) else ""
        if path == ITEMS_PATH and _unauthorized(response.status_code, message):
            raise ZohoError(
                "Zoho Books refused the item list. Add the ZohoBooks.settings.READ scope and create a new refresh token."
            )
        raise ZohoError(message or "Zoho Books could not be read.")
    return body


def _unauthorized(status: int, message: str) -> bool:
    text = message.casefold()
    return status in {401, 403} or "author" in text or "scope" in text


def _token_message(code: str) -> str:
    if code in {"invalid_code", "invalid_client", "invalid_client_secret", "access_denied"}:
        return "Zoho Books refused the connection. Check the client, the refresh token, and the data center."
    return "Zoho Books could not be reached."
