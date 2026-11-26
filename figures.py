"""Match a PDF figure number to the inventory list. This only reads that sheet."""

import json
import logging
import re
import threading
import time

import requests

from config import GEMINI_API_KEY, GEMINI_MODEL, GEMINI_THINKING, INVENTORY_TAB, SHEET_ID
from extract_po import ExtractError, log_gemini_usage
from sheet import SheetError, credentials

logger = logging.getLogger("purchase_desk")
_lock = threading.Lock()
_cache = {"figures": None, "sizes": None, "until": 0.0}


def clear_figures():
    with _lock:
        _cache["figures"] = None
        _cache["sizes"] = None
        _cache["until"] = 0.0


def list_figures() -> list[str]:
    return _load()[0]


def figure_sizes() -> dict[str, str]:
    """Column B SIZE for each column C figure, keyed without spaces, dashes, or case."""
    return _load()[1]


def _load() -> tuple[list[str], dict[str, str]]:
    now = time.monotonic()
    with _lock:
        if _cache["figures"] is not None and now < _cache["until"]:
            return _cache["figures"], _cache["sizes"]
    figures, sizes = _read_figures()
    with _lock:
        _cache["figures"] = figures
        _cache["sizes"] = sizes
        _cache["until"] = time.monotonic() + 600
    return figures, sizes


def figure_choices(sources) -> dict:
    catalog = list_figures()
    allowed = {_figure_key(figure): figure for figure in catalog}
    matches = {}
    pending = []
    for source in sources if isinstance(sources, list) else []:
        text = " ".join(str(source or "").split())
        if not text or text in matches or text in pending:
            continue
        known = allowed.get(_figure_key(text))
        if known:
            matches[text] = known
        else:
            pending.append(text)
    if pending:
        try:
            for source, match in _ask_gemini(pending, catalog, allowed).items():
                chosen = allowed.get(_figure_key(match))
                if source in pending and chosen:
                    matches[source] = chosen
        except ExtractError:
            logger.exception("figure match failed")
    return {"figures": catalog, "matches": matches}


def _read_figures() -> tuple[list[str], dict[str, str]]:
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError

    title = INVENTORY_TAB.replace("'", "''")
    try:
        sheets = build("sheets", "v4", credentials=credentials(), cache_discovery=False)
        result = sheets.spreadsheets().values().get(
            spreadsheetId=SHEET_ID,
            range=f"'{title}'!B:C",
        ).execute()
    except SheetError as error:
        raise error
    except HttpError as error:
        raise SheetError("The inventory spreadsheet could not be read.") from error
    figures = []
    seen = set()
    sizes = {}
    conflicting = set()
    for index, row in enumerate(result.get("values") or []):
        if len(row) < 2:
            continue
        size = " ".join(str(row[0]).split())
        figure = " ".join(str(row[1]).split())
        if not figure:
            continue
        if index == 0 and figure.casefold() == "fig. no. qb":
            continue
        key = _figure_key(figure)
        if not key:
            continue
        if size:
            if sizes.get(key, size) != size:
                conflicting.add(key)
            sizes[key] = size
        if key in seen:
            continue
        seen.add(key)
        figures.append(figure)
    for key in conflicting:
        sizes.pop(key, None)
    return figures, sizes


def _figure_key(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def _ask_gemini(sources: list[str], catalog: list[str], allowed: dict[str, str]) -> dict[str, str]:
    if not GEMINI_API_KEY:
        raise ExtractError("Set GEMINI_API_KEY before matching figure numbers.")
    prompt = f"""Match each purchase-order figure number to the closest inventory figure.

Inventory figures:
{json.dumps(catalog)}

Source figures:
{json.dumps(sources)}

Return a JSON array of objects with keys "source" and "match".
Copy "source" exactly from the source figures.
"match" must be copied exactly from the inventory figures, or be an empty string when none is the same part.
Treat spacing, dashes, and letter case as the same number. Do not invent a figure.
"""
    response = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent",
        headers={"x-goog-api-key": GEMINI_API_KEY},
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json", "thinkingConfig": GEMINI_THINKING},
        },
        timeout=60,
    )
    if not response.ok:
        raise ExtractError("The figure list could not be matched.")
    try:
        body = response.json()
        log_gemini_usage("match figures", body)
        text = body["candidates"][0]["content"]["parts"][0]["text"]
        parsed = json.loads(text)
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as error:
        raise ExtractError("The figure list could not be matched.") from error
    if not isinstance(parsed, list):
        raise ExtractError("The figure list could not be matched.")
    matches = {}
    for item in parsed:
        if not isinstance(item, dict):
            continue
        original = next((source for source in sources if _figure_key(source) == _figure_key(item.get("source"))), "")
        match = allowed.get(_figure_key(item.get("match")))
        if original and match:
            matches[original] = match
    return matches
