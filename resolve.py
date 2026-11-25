"""Match a read purchase order to the saved lists, then ask Gemini only about the rest."""

import json
import re

import requests

from config import GEMINI_API_KEY, GEMINI_MODEL
from extract_po import ExtractError, log_gemini_usage
from zoho_customers import _name_score, _words, best_match, rank_customers

ABBREVIATIONS = {
    "BRZ": "BRONZE",
    "BRS": "BRASS",
    "SS": "STAINLESS",
    "CI": "IRON",
    "DI": "DUCTILE",
    "AL": "ALUMINUM",
    "ALU": "ALUMINUM",
    "NBR": "NITRILE",
    "PTFE": "PTFE",
    "EPDM": "EPDM",
}
SKIP_WORDS = {"THE", "AND", "FOR", "WITH", "VALVE", "VLV"}


def resolve_order(rows, catalog, ask=None) -> dict:
    customers = catalog.get("customers") or []
    items = catalog.get("items") or []
    terms = catalog.get("paymentTerms") or []
    methods = catalog.get("deliveryMethods") or []
    figures = catalog.get("figures") or []
    company_text = _first(rows, "Company")
    company_ranked = rank_customers(company_text, customers) if company_text else []
    company = best_match(company_text, customers) if company_text else None
    company_options = []
    company_fallback = None
    if company is None and company_ranked:
        company_options = [item[3] for item in company_ranked[:8]]
        company_fallback = company_options[0]
    elif company is None and company_text and customers:
        company_options = customers
    term, term_options = _named_choice(_first(rows, "Payment Terms"), terms)
    method, method_options = _named_choice(_first(rows, "Delivery Method"), methods)
    figure_matches = {}
    item_matches = {}
    item_fallback = {}
    figure_pending = []
    item_pending = []
    seen = set()
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        figure = str(row.get("FIG#") or "").strip()
        if not figure or figure in seen:
            continue
        seen.add(figure)
        description = _text(row.get("Description"))
        chosen_figure, figure_options = match_figure(figure, figures)
        if chosen_figure:
            figure_matches[figure] = chosen_figure
        elif figure_options:
            figure_pending.append({"source": figure, "options": figure_options})
        chosen_item, item_options = match_item(figure, description, items)
        if chosen_item:
            item_matches[figure] = {"id": chosen_item["id"], "name": chosen_item["name"]}
        elif item_options:
            item_fallback[figure] = item_options[0]
            item_pending.append({
                "source": figure,
                "description": description,
                "options": [[item["id"], item["name"], item.get("sku", "")] for item in item_options],
            })
        elif items:
            item_pending.append({
                "source": figure,
                "description": description,
                "options": [],
            })
    pending = {}
    if company_options:
        pending["company"] = {
            "source": company_text,
            "options": [_customer_option(item) for item in company_options],
        }
    if item_pending:
        pending["items"] = item_pending
        if any(not row["options"] for row in item_pending):
            pending["itemCatalog"] = [
                [item["id"], item.get("name", ""), item.get("sku", "")] for item in items
            ]
    if figure_pending:
        pending["figures"] = figure_pending
    if term is None and term_options:
        pending["paymentTerms"] = {
            "source": _first(rows, "Payment Terms"),
            "options": [[item["id"], item["name"]] for item in term_options],
        }
    if method is None and method_options:
        pending["deliveryMethod"] = {
            "source": _first(rows, "Delivery Method"),
            "options": [[item["id"], item["name"]] for item in method_options],
        }
    if pending and ask is not None:
        try:
            chosen = ask(pending)
        except ExtractError:
            chosen = {}
        _apply_fallback(
            chosen,
            item_pending,
            figure_pending,
            term_options,
            method_options,
            item_matches,
            figure_matches,
            items,
        )
        if company is None:
            company = _by_id(company_options, chosen.get("company"))
        if term is None:
            term = _by_id(term_options, chosen.get("paymentTerms"))
        if method is None:
            method = _by_id(method_options, chosen.get("deliveryMethod"))
    if company is None:
        company = company_fallback
    for figure, item in item_fallback.items():
        if figure not in item_matches:
            item_matches[figure] = {"id": item["id"], "name": item["name"]}
    return {
        "company": {"id": company["id"], "name": company["name"]} if company else None,
        "paymentTerms": _choice(term),
        "deliveryMethod": _choice(method),
        "figures": figure_matches,
        "items": item_matches,
    }


def match_item(figure: str, description: str, items: list[dict]):
    codes = loose_codes(figure) | loose_codes(description)
    hints = description_words(description)
    ranked = []
    for item in items:
        found = _overlap(codes, loose_codes(f"{item.get('name', '')} {item.get('sku', '')}"))
        if not found:
            continue
        extra = min(pair[0] for pair in found)
        names = set(re.findall(r"[A-Z]{3,}", f"{item.get('name', '')} {item.get('sku', '')}".upper()))
        overlap = len(hints & names)
        score = 100 - (extra * 5) + (overlap * 20)
        if item.get("status") == "active":
            score += 1
        ranked.append((score, extra, overlap, item))
    ranked.sort(key=lambda row: (-row[0], row[1], row[3].get("name", "")))
    if not ranked:
        return _match_item_words(figure, description, items)
    if len(ranked) == 1:
        if ranked[0][1] <= 6:
            return ranked[0][3], []
        return None, [ranked[0][3]]
    if ranked[0][0] >= ranked[1][0] + 15:
        return ranked[0][3], []
    return None, [row[3] for row in ranked[:8]]


def _match_item_words(figure: str, description: str, items: list[dict]):
    query = _words(f"{figure} {description}")
    if not query:
        return None, []
    ranked = []
    for item in items:
        score = _name_score(query, _words(f"{item.get('name', '')} {item.get('sku', '')}"))
        if score < 0.45:
            continue
        ranked.append((score, item))
    ranked.sort(key=lambda row: (-row[0], row[1].get("name", "")))
    if not ranked:
        return None, []
    if len(ranked) == 1 or (ranked[0][0] >= 0.9 and ranked[0][0] >= ranked[1][0] + 0.08):
        return ranked[0][1], []
    return None, [row[1] for row in ranked[:8]]


def match_figure(figure: str, figures: list[str]):
    key = _code(figure)
    exact = [item for item in figures if _code(item) == key]
    if len(exact) == 1:
        return exact[0], []
    codes = loose_codes(figure)
    hits = []
    for item in figures:
        if _overlap(codes, loose_codes(item)):
            hits.append(item)
    if len(hits) == 1:
        return hits[0], []
    return None, hits[:12]


def loose_codes(value: str) -> set[str]:
    cleaned = re.sub(r"[#/.,_-]+", " ", str(value or "").upper())
    tokens = re.findall(r"[A-Z0-9]+", cleaned)
    found = set()
    for token in tokens:
        if _useful(token):
            found.add(token)
    for left, right in zip(tokens, tokens[1:]):
        if any(character.isdigit() for character in left) and right.isalpha() and len(right) <= 4:
            joined = left + right
            if _useful(joined):
                found.add(joined)
    compact = _code(value)
    tail = re.search(r"[A-Z]{3,}(\d[A-Z0-9]{2,})$", compact)
    if tail and _useful(tail.group(1)):
        found.add(tail.group(1))
    return found


def description_words(value: str) -> set[str]:
    words = set()
    for token in re.findall(r"[A-Z]+", str(value or "").upper()):
        word = ABBREVIATIONS.get(token, token)
        if len(word) >= 3 and word not in SKIP_WORDS:
            words.add(word)
    return words


def ask_leftovers(pending: dict) -> dict:
    if not GEMINI_API_KEY:
        raise ExtractError("Set GEMINI_API_KEY before matching the remaining fields.")
    prompt = f"""Match the purchase-order text to the offered choices only.

{json.dumps(pending)}

Return a JSON object with the same keys that were given.
company is an id copied from company.options. Always choose the closest customer when company is given.
items is an array of {{"source", "id"}} when items was given. Copy source exactly. Always choose one id. Use that source's options when the list is not empty. When options is empty, choose the id from itemCatalog.
figures is an array of {{"source", "match"}} when figures was given. Copy match from that source's options, or use an empty string.
paymentTerms and deliveryMethod are an id copied from their options, or an empty string.
The figure code is often only part of a longer name. A material in the description can agree with the item name. A name can be one or two letters off. Do not invent an id or a figure.
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
        raise ExtractError("The remaining fields could not be matched.")
    try:
        body = response.json()
        log_gemini_usage("match leftovers", body)
        text = body["candidates"][0]["content"]["parts"][0]["text"]
        parsed = json.loads(text)
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as error:
        raise ExtractError("The remaining fields could not be matched.") from error
    if not isinstance(parsed, dict):
        raise ExtractError("The remaining fields could not be matched.")
    return parsed


def _apply_fallback(chosen, item_pending, figure_pending, terms, methods, item_matches, figure_matches, items):
    catalog_names = {item["id"]: item.get("name", "") for item in items}
    allowed_items = {}
    names = {}
    for row in item_pending:
        offered = {option[0] for option in row["options"]}
        allowed_items[row["source"]] = offered or set(catalog_names)
        for option in row["options"]:
            names[(row["source"], option[0])] = option[1]
    for entry in chosen.get("items") or []:
        if not isinstance(entry, dict):
            continue
        source = _text(entry.get("source"))
        item_id = str(entry.get("id") or "")
        if item_id and item_id in allowed_items.get(source, ()):
            item_matches[source] = {
                "id": item_id,
                "name": names.get((source, item_id)) or catalog_names.get(item_id, ""),
            }
    allowed_figures = {row["source"]: set(row["options"]) for row in figure_pending}
    for entry in chosen.get("figures") or []:
        if not isinstance(entry, dict):
            continue
        source = _text(entry.get("source"))
        match = _text(entry.get("match"))
        if match and match in allowed_figures.get(source, ()):
            figure_matches[source] = match


def _named_choice(source: str, options: list[dict]):
    text = _text(source).casefold()
    if not text or not options:
        return None, []
    exact = [item for item in options if item.get("name", "").casefold() == text]
    if len(exact) == 1:
        return exact[0], []
    contained = [
        item for item in options
        if text in item.get("name", "").casefold() or item.get("name", "").casefold() in text
    ]
    if len(contained) == 1:
        return contained[0], []
    scored = []
    query = set(re.findall(r"[a-z0-9]+", text))
    for item in options:
        name = set(re.findall(r"[a-z0-9]+", item.get("name", "").casefold()))
        if not query or not name:
            continue
        score = len(query & name) / max(len(query), len(name))
        if score:
            scored.append((score, item))
    scored.sort(key=lambda row: row[0], reverse=True)
    if scored and (len(scored) == 1 or scored[0][0] >= scored[1][0] + 0.25) and scored[0][0] >= 0.6:
        return scored[0][1], []
    return None, [item for _score, item in scored[:8]] or options[:8]


def _overlap(left: set[str], right: set[str]):
    found = []
    for pdf_code in left:
        if not _useful(pdf_code):
            continue
        for item_code in right:
            if not _useful(item_code):
                continue
            if pdf_code in item_code or item_code in pdf_code:
                found.append((abs(len(item_code) - len(pdf_code)), pdf_code, item_code))
    return found


def _useful(code: str) -> bool:
    return (
        len(code) >= 4
        and any(character.isdigit() for character in code)
        and any(character.isalpha() for character in code)
    )


def _code(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def _text(value) -> str:
    return " ".join(str(value or "").split())


def _first(rows, field: str) -> str:
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, dict):
            text = _text(row.get(field))
            if text:
                return text
    return ""


def _customer_option(customer: dict) -> list:
    name = customer.get("name") or ""
    company = customer.get("company") or ""
    label = name if not company or company == name else f"{name} / {company}"
    return [customer.get("id"), label]


def _choice(item):
    if not item:
        return None
    return {"id": item["id"], "name": item["name"]}


def _by_id(options, item_id):
    item_id = str(item_id or "")
    for item in options:
        if item.get("id") == item_id:
            return item
    return None
