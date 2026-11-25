"""Saved Zoho and inventory lists. A refresh replaces the file."""

import json
import os
import threading
import time
from pathlib import Path

from figures import clear_figures, figure_sizes, list_figures
from zoho_customers import (
    clear_cached_lists,
    configured,
    list_customers,
    list_delivery_methods,
    list_items,
    list_payment_terms,
)

_lock = threading.Lock()
_memory = None
_loading = None


def cache_path() -> Path:
    folder = Path(__file__).resolve().parent / "cache"
    try:
        folder.mkdir(exist_ok=True)
        probe = folder / ".write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return folder / "catalog.json"
    except OSError:
        return Path("/tmp/cvc-po-catalog.json")


def load_catalog(refresh: bool = False) -> dict:
    global _memory, _loading
    with _lock:
        if _memory is not None and not refresh:
            return _memory
        if not refresh:
            stored = _read_file()
            if stored is not None:
                _memory = stored
                return stored
        loading = _loading
        if loading is None:
            loading = threading.Event()
            _loading = loading
            leader = True
        else:
            leader = False
    if not leader:
        loading.wait(300)
        with _lock:
            if _memory is not None and not refresh:
                return _memory
        return load_catalog(refresh)
    try:
        built = _fetch()
    except Exception:
        with _lock:
            _loading = None
        loading.set()
        raise
    _write_file(built)
    with _lock:
        _memory = built
        _loading = None
    loading.set()
    return built


def _fetch() -> dict:
    clear_cached_lists()
    clear_figures()
    books = configured()
    customers = list_customers() if books else []
    items = list_items() if books else []
    terms = list_payment_terms() if books else []
    methods = list_delivery_methods() if books else []
    figures = list_figures()
    sizes = figure_sizes()
    return {
        "ready": True,
        "saved": time.time(),
        "connected": books,
        "customers": customers,
        "items": items,
        "paymentTerms": terms,
        "deliveryMethods": methods,
        "figures": figures,
        "figureSizes": sizes,
    }


def _read_file():
    path = cache_path()
    try:
        with path.open(encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    payload["ready"] = True
    payload["connected"] = bool(payload.get("connected"))
    for key in ("customers", "items", "paymentTerms", "deliveryMethods", "figures"):
        if not isinstance(payload.get(key), list):
            payload[key] = []
    if not isinstance(payload.get("figureSizes"), dict):
        payload["figureSizes"] = {}
    return payload


def _write_file(payload: dict):
    path = cache_path()
    path.parent.mkdir(exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle)
    os.replace(temporary, path)
