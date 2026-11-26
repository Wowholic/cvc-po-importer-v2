"""Match a read purchase order to the saved lists, then ask Gemini only about the rest."""

import json
import re
from fractions import Fraction

import requests

from config import GEMINI_API_KEY, GEMINI_MODEL, GEMINI_THINKING
from extract_po import ExtractError, log_gemini_usage
from zoho_customers import _name_score, _words, best_match, rank_customers

ABBREVIATIONS = {
    "BRZ": "BRONZE",
    "BZ": "BRONZE",
    "STL": "STEEL",
    "FRG": "FORGED",
    "FORG": "FORGED",
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
# Words on almost every line or item ("NON LUNK DESIGN - ..."), so they say nothing about the material.
BRAND_WORDS = {"LUNK", "LUNKENHEIMER", "NON", "DESIGN"}
UNICODE_FRACTIONS = {"½": "1/2", "¼": "1/4", "¾": "3/4", "⅛": "1/8", "⅜": "3/8", "⅝": "5/8", "⅞": "7/8"}
SIZE_NUMBER = r"\d+(?:\s*-\s*|\s+)\d+/\d+|\d+/\d+|\d*\.\d+|\d+"


def resolve_order(rows, catalog, ask=None) -> dict:
    customers = catalog.get("customers") or []
    items = catalog.get("items") or []
    terms = catalog.get("paymentTerms") or []
    methods = catalog.get("deliveryMethods") or []
    figures = catalog.get("figures") or []
    sizes = catalog.get("figureSizes") or {}
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
        if not chosen_figure:
            chosen_figure = _sheet_spelling(family_figure(figure, description, sizes), figures)
        if chosen_figure:
            figure_matches[figure] = chosen_figure
        elif figure_options:
            figure_pending.append({"source": figure, "options": figure_options})
        chosen_item, item_options = match_item(figure, description, items, sizes)
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


def match_item(figure: str, description: str, items: list[dict], sizes: dict | None = None):
    # Family + size from the inventory tab (1837 + 1/2 -> 1837005) is the most precise
    # answer, so it wins over a looser part-number hit such as 1837-40-005.
    family = _items_for_figure(family_figure(figure, description, sizes), items)
    if family:
        chosen = settle_tie(family, description)
        # Several Zoho items share this inventory figure and nothing tells them apart:
        # offer only those, never a looser match.
        return (chosen, []) if chosen else (None, family[:8])
    return _match_item_codes(figure, description, items, sizes)


def _match_item_codes(figure: str, description: str, items: list[dict], sizes: dict | None = None):
    exact = _exact_item(figure, items)
    if exact and not _other_material(figure, description, items, exact):
        return exact, []
    figure_codes = loose_codes(figure, digits=True)
    codes = figure_codes | loose_codes(description)
    hints = description_words(description)
    ranked = []
    for item in items:
        found = _overlap(codes, loose_codes(f"{item.get('name', '')} {item.get('sku', '')}", digits=True))
        if not found:
            continue
        extra = min(pair[0] for pair in found)
        names = set(re.findall(r"[A-Z]{3,}", f"{item.get('name', '')} {item.get('sku', '')}".upper()))
        overlap = len(hints & names)
        score = 100 - (extra * 5) + (overlap * 20)
        # The FIG# is the part number; a code found only in the description,
        # such as a CR13 trim, is a weaker hint and must not tie with it.
        if any(pair[1] in figure_codes for pair in found):
            score += 30
        if item.get("status") == "active":
            score += 1
        ranked.append((score, extra, overlap, item))
    ranked.sort(key=lambda row: (-row[0], row[1], row[3].get("name", "")))
    if not ranked:
        return _match_item_words(figure, description, items, sizes)
    if len(ranked) == 1:
        if ranked[0][1] <= 6:
            return ranked[0][3], []
        return None, [ranked[0][3]]
    if ranked[0][0] >= ranked[1][0] + 15:
        return ranked[0][3], []
    close = [row[3] for row in ranked if row[0] > ranked[0][0] - 15]
    sized = pick_by_size(close, figure, description, sizes)
    if sized:
        return sized, []
    settled = pick_by_material(close, description)
    if settled:
        return settled, []
    return None, [row[3] for row in ranked[:8]]


def _exact_item(figure: str, items: list[dict]):
    """An item whose whole name or SKU is the FIG#, such as 1837 or T-811X."""
    key = _code(figure)
    if len(key) < 3:
        return None
    hits = [item for item in items if key in (_code(item.get("name")), _code(item.get("sku")))]
    active = [item for item in hits if item.get("status") == "active"]
    for group in (hits, active):
        if len(group) == 1:
            return group[0]
    return None


def _other_material(figure: str, description: str, items: list[dict], exact: dict) -> bool:
    """True when the description names a material, such as BRZ, that another item
    with the same part number carries before " - " (BRONZE - 1837005) and the exact item lacks."""
    hints = description_words(description) - BRAND_WORDS
    if not hints or _material_words(exact) & hints:
        return False
    codes = loose_codes(figure, digits=True)
    for item in items:
        if item is exact or not _material_words(item) & hints:
            continue
        if _overlap(codes, loose_codes(f"{item.get('name', '')} {item.get('sku', '')}", digits=True)):
            return True
    return False


def _material_words(item: dict) -> set[str]:
    name = str(item.get("name") or "")
    prefix = name.split(" - ", 1)[0] if " - " in name else ""
    return set(re.findall(r"[A-Z]{3,}", prefix.upper())) - BRAND_WORDS


def _match_item_words(figure: str, description: str, items: list[dict], sizes: dict | None = None):
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
    close = [row[1] for row in ranked if row[0] > ranked[0][0] - 0.08]
    sized = pick_by_size(close, figure, description, sizes)
    if sized:
        return sized, []
    return None, [row[1] for row in ranked[:8]]


def pick_by_size(candidates: list[dict], figure: str, description: str, sizes: dict | None):
    """Break a tie between close items with the SIZE column of the inventory tab.

    A size printed in the PDF must equal the inventory size of exactly one
    candidate. Otherwise nothing is picked and Gemini decides.
    """
    if not sizes or len(candidates) < 2:
        return None
    wanted = pdf_sizes(figure, description)
    if not wanted:
        return None
    hits = [item for item in candidates if size_value(item_size(item, sizes)) in wanted]
    return hits[0] if len(hits) == 1 else None


def family_figure(figure: str, description: str, sizes: dict | None) -> str:
    """Find the inventory figure from its family and size, such as MSP_18374X1/4 -> 1837002.

    An inventory figure is its family followed by a three-digit size code
    (1837 + 002). The family must appear in the PDF figure, not right after
    another digit, and its SIZE must be a size printed in the PDF. Exactly one
    inventory figure must qualify; the longest family wins over a shorter one
    that is part of it.
    """
    if not sizes:
        return ""
    wanted = pdf_sizes(figure, description)
    text = _code(figure)
    if not wanted or not text:
        return ""
    hits = []
    for key, size in sizes.items():
        family = key[:-3]
        if not re.search(r"\d{3}$", key) or len(family) < 3 or not re.search(r"\d", family):
            continue
        if size_value(size) not in wanted:
            continue
        if any(start == 0 or not text[start - 1].isdigit() for start in _starts(text, family)):
            hits.append((len(family), key))
    if not hits:
        return ""
    longest = max(length for length, _key in hits)
    best = {key for length, key in hits if length == longest}
    return best.pop() if len(best) == 1 else ""


def _starts(text: str, part: str):
    start = text.find(part)
    while start != -1:
        yield start
        start = text.find(part, start + 1)


def _items_for_figure(key: str, items: list[dict]) -> list[dict]:
    """The Zoho items named after this inventory figure, such as 180020 and BRONZE - 180020."""
    if not key:
        return []
    found = []
    for item in items:
        name = item.get("name", "")
        tail = name.split(" - ", 1)[1] if " - " in name else ""
        if key in (_code(tail), _code(name), _code(item.get("sku", ""))):
            found.append(item)
    return found


def settle_tie(candidates: list[dict], description: str):
    """Pick one of several equally good items, or None.

    One candidate wins. Otherwise a single active one wins, then a single one whose
    material (the part before " - ", such as BRONZE) is named in the description (BRZ).
    """
    if len(candidates) == 1:
        return candidates[0]
    active = [item for item in candidates if item.get("status") == "active"] or candidates
    if len(active) == 1:
        return active[0]
    return pick_by_material(active, description)


def pick_by_material(candidates: list[dict], description: str):
    """The single candidate whose material (BRONZE in BRONZE - 180020) the description names (BRZ)."""
    hints = description_words(description) - BRAND_WORDS
    if not hints:
        return None
    named = [item for item in candidates if _material_words(item) & hints]
    return named[0] if len(named) == 1 else None


def _sheet_spelling(key: str, figures: list[str]) -> str:
    if not key:
        return ""
    return next((figure for figure in figures if _code(figure) == key), "")


def pdf_sizes(figure: str, description: str) -> set:
    """Sizes printed in the PDF: the first one in the description, and any in the figure.

    In the figure only a fraction (X1/4, 1-1/2) or a number with an inch mark counts,
    because bare digits there are usually part of the part number.
    """
    found = set()
    first = pdf_size(_without_figure(figure, description))
    if first is not None:
        found.add(first)
    # A part code in the description, such as MSP_18374X1/4", carries its size like the FIG# does.
    codes = [token for token in str(description or "").split() if re.search(r"[A-Za-z]", token) and re.search(r"\d/\d", token)]
    text = _plain_size_text(" ".join([figure, *codes]))
    for match in re.finditer(r"(?<![\d/.])(\d+-\d+/\d+|\d+/\d+)(?![\d/])", text):
        value = size_value(match.group(1))
        if value is not None:
            found.add(value)
    for match in re.finditer(r"(?<![\d/.])(\d+(?:\.\d+)?)\s*(?:\"|''|IN\b|INCH)", text):
        value = size_value(match.group(1))
        if value is not None:
            found.add(value)
    return found


def _without_figure(figure: str, description: str) -> str:
    """Drop the FIG# from the description, so "1837 - 1/2 BRZ" reads as 1/2 and not 1837 1/2."""
    figure = _text(figure)
    text = _text(description)
    if not figure:
        return text
    text = re.sub(rf"(?<![A-Z0-9]){re.escape(figure)}(?![A-Z0-9])", " ", text, flags=re.IGNORECASE)
    return text.strip().lstrip("-:,;").strip()


def item_size(item: dict, sizes: dict) -> str:
    name = item.get("name", "")
    tail = name.split(" - ", 1)[1] if " - " in name else ""
    for key in (_code(tail), _code(name), _code(item.get("sku", ""))):
        if key and key in sizes:
            return sizes[key]
    return ""


def pdf_size(description: str):
    """The first size in a description: one with an inch mark, else a size it starts with."""
    text = _plain_size_text(description)
    marked = re.search(rf"(?<![\w/.-])({SIZE_NUMBER})\s*(?:\"|''|IN\b|INCH)", text)
    if marked:
        return size_value(marked.group(1))
    leading = re.match(rf"\s*({SIZE_NUMBER})(?=\s|$)", text)
    if leading:
        return size_value(leading.group(1))
    return None


def size_value(text: str):
    """1-1/2", 1 1/2, 1.5 IN and 1½ all become Fraction(3, 2)."""
    cleaned = _plain_size_text(text)
    cleaned = re.sub(r"INCH(?:ES)?|\bIN\b|[\"']", " ", cleaned)
    cleaned = " ".join(cleaned.split())
    try:
        mixed = re.fullmatch(r"(\d+)(?:\s*-\s*|\s+)(\d+)/(\d+)", cleaned)
        if mixed:
            value = int(mixed.group(1)) + Fraction(int(mixed.group(2)), int(mixed.group(3)))
        elif re.fullmatch(r"\d+/\d+|\d*\.\d+|\d+", cleaned):
            value = Fraction(cleaned)
        else:
            return None
    except (ValueError, ZeroDivisionError):
        return None
    return value if value > 0 else None


def _plain_size_text(value) -> str:
    text = str(value or "").upper().replace("”", '"').replace("″", '"').replace("“", '"')
    for symbol, fraction in UNICODE_FRACTIONS.items():
        text = text.replace(symbol, f" {fraction}")
    return text


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


def loose_codes(value: str, digits: bool = False) -> set[str]:
    """Part-number-like codes. With digits=True, all-digit codes such as 1837 count too."""
    cleaned = re.sub(r"[#/.,_-]+", " ", str(value or "").upper())
    tokens = re.findall(r"[A-Z0-9]+", cleaned)
    found = set()
    for token in tokens:
        if _useful(token) or (digits and len(token) >= 4 and token.isdigit()):
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
            "generationConfig": {"responseMimeType": "application/json", "thinkingConfig": GEMINI_THINKING},
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
        if not _codeish(pdf_code):
            continue
        for item_code in right:
            if not _codeish(item_code):
                continue
            if pdf_code in item_code or item_code in pdf_code:
                found.append((abs(len(item_code) - len(pdf_code)), pdf_code, item_code))
    return found


def _codeish(code: str) -> bool:
    return len(code) >= 4 and any(character.isdigit() for character in code)


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
