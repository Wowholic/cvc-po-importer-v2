#!/usr/bin/env python3
"""Manual purchase-order desk: read a PDF, review the lines, send them on."""

import base64
import json
import logging
import math
import threading
import time

from flask import Flask, Response, jsonify, redirect, render_template, request, url_for

from auth import (
    SESSION_COOKIE,
    SESSION_MAX_AGE,
    auth_settings,
    check_credentials,
    clear_failures,
    client_ip,
    create_session,
    locked_for,
    record_failure,
    verify_session,
)
from config import MAX_PDF_BYTES
from extract_po import ExtractError, extract_bytes, gemini_usage, reset_gemini_usage
from rows import RowError, validate_rows
from figures import figure_choices
from sheet import SheetError, append_purchase_orders
from catalog import load_catalog
from highlight import highlight_pdf, merge_pdfs
from resolve import ask_leftovers, catalog_hints, resolve_order
from zoho_customers import ZohoError, customer_choices, item_catalog, item_choices, order_option_choices
from addresses import address_choices
from zoho_orders import OrderInputError, create_draft_sales_order, prepare_draft_sales_order, sales_order_pdf

logging.basicConfig(level=logging.INFO)
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_PDF_BYTES
app.config["TEMPLATES_AUTO_RELOAD"] = True


def fail(message: str, status: int, detail: str = ""):
    # detail is technical: the browser only prints it in the console, to see where an error came from.
    return (jsonify(error=message, detail=detail) if detail else jsonify(error=message)), status


def _signed_in() -> bool:
    return verify_session(request.cookies.get(SESSION_COOKIE))


def _remember(response):
    response.set_cookie(
        SESSION_COOKIE,
        create_session(),
        max_age=SESSION_MAX_AGE,
        httponly=True,
        secure=request.is_secure or request.headers.get("X-Forwarded-Proto") == "https",
        samesite="Strict",
        path="/",
    )
    return response


_warm_started = False


def _warm_zoho():
    try:
        load_catalog()
    except Exception:
        app.logger.exception("catalog warm failed")


def _start_warm():
    global _warm_started
    if _warm_started or app.config.get("TESTING"):
        return
    _warm_started = True
    threading.Thread(target=_warm_zoho, daemon=True).start()


@app.before_request
def track_gemini_usage():
    reset_gemini_usage()


def respond(payload):
    usage = gemini_usage()
    if usage and isinstance(payload, dict):
        payload = {**payload, "usage": usage}
    return jsonify(payload)


@app.before_request
def warm_zoho_lists():
    _start_warm()


@app.before_request
def require_login():
    if request.endpoint in {"login", "logout", "static"}:
        return None
    if _signed_in():
        return None
    if request.path.startswith("/api/"):
        return fail("Not logged in.", 401)
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET" and _signed_in():
        return redirect(url_for("index"))
    settings, setup_error = auth_settings()
    error = "" if settings else setup_error
    if request.method == "POST" and settings:
        address = client_ip(request.headers, request.remote_addr)
        wait = locked_for(address)
        if wait:
            error = f"Too many failed attempts. Try again in {math.ceil(wait / 60)} minutes."
        elif not check_credentials(request.form.get("user", ""), request.form.get("password", "")):
            record_failure(address)
            if not app.config.get("TESTING"):
                time.sleep(0.8)
            error = "Wrong user or password."
        else:
            clear_failures(address)
            return _remember(redirect(url_for("index")))
    return render_template("login.html", error=error, ready=settings is not None)


@app.post("/logout")
def logout():
    response = redirect(url_for("login"))
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


@app.get("/")
def index():
    return render_template("index.html")


@app.post("/api/extract")
def extract():
    upload = request.files.get("pdf")
    if upload is None or not upload.filename:
        return fail("Choose a PDF first.", 400)
    pdf_bytes = upload.read()
    if not pdf_bytes:
        return fail("That PDF is empty.", 400)
    if not pdf_bytes.startswith(b"%PDF"):
        return fail("That file is not a PDF.", 400)
    try:
        hints = catalog_hints(load_catalog())
    except Exception:
        app.logger.exception("inventory lists for the PDF read failed")
        hints = None
    try:
        rows, sources = extract_bytes(pdf_bytes, hints)
    except ExtractError as error:
        app.logger.warning("purchase order read failed: %s (%s)", error, error.detail)
        return fail(str(error), 502, error.detail or f"Gemini: {error}")
    except Exception as error:
        app.logger.exception("purchase order read failed")
        return fail("The purchase order could not be read.", 502, f"{type(error).__name__}: {error}")
    marked = highlight_pdf(pdf_bytes, sources)
    return respond({
        "rows": rows,
        # The PDF with the text Gemini used highlighted, for the preview and printing.
        "highlighted": base64.standard_b64encode(marked).decode("ascii") if marked else None,
    })


@app.get("/api/catalog")
def catalog():
    try:
        return jsonify(load_catalog())
    except (ZohoError, SheetError) as error:
        return fail(str(error), 502)
    except Exception:
        app.logger.exception("catalog load failed")
        return fail("The saved lists could not be loaded.", 502)


@app.post("/api/catalog/refresh")
def catalog_refresh():
    try:
        return jsonify(load_catalog(refresh=True))
    except (ZohoError, SheetError) as error:
        return fail(str(error), 502)
    except Exception:
        app.logger.exception("catalog refresh failed")
        return fail("The lists could not be refreshed.", 502)


@app.post("/api/resolve")
def resolve():
    body = request.get_json(silent=True) or {}
    rows = body.get("rows")
    if not isinstance(rows, list):
        return fail("Send the purchase order lines.", 400)
    try:
        return respond(resolve_order(rows[:200], load_catalog(), ask_leftovers))
    except (ZohoError, SheetError) as error:
        return fail(str(error), 502)
    except Exception:
        app.logger.exception("purchase order match failed")
        return fail("The purchase order could not be matched.", 502)


@app.get("/api/customers")
def customers():
    try:
        return jsonify(customer_choices(request.args.get("company", "")))
    except ZohoError as error:
        return fail(str(error), 502)
    except Exception:
        app.logger.exception("customer list failed")
        return fail("Zoho Books could not load customers.", 502)


@app.post("/api/items")
def items():
    try:
        return jsonify(item_catalog())
    except ZohoError as error:
        return fail(str(error), 502)
    except Exception:
        app.logger.exception("item list failed")
        return fail("Zoho Books could not load items.", 502)


@app.post("/api/item-matches")
def item_matches():
    body = request.get_json(silent=True) or {}
    sources = body.get("figures")
    if not isinstance(sources, list):
        return fail("Send the figure numbers to match.", 400)
    try:
        result = item_choices(sources[:200])
    except ZohoError as error:
        return fail(str(error), 502)
    except Exception:
        app.logger.exception("item match failed")
        return fail("Zoho Books could not match items.", 502)
    result.pop("items", None)
    return respond(result)


@app.post("/api/order-options")
def order_options():
    body = request.get_json(silent=True) or {}
    try:
        return respond(order_option_choices(
            body.get("paymentTerms", ""),
            body.get("deliveryMethod", ""),
        ))
    except ZohoError as error:
        return fail(str(error), 502)
    except Exception:
        app.logger.exception("order options failed")
        return fail("Zoho Books could not load payment terms.", 502)


@app.post("/api/addresses")
def addresses():
    body = request.get_json(silent=True) or {}
    try:
        return respond(address_choices(body.get("customerId", ""), str(body.get("shipTo") or "")))
    except ZohoError as error:
        return fail(str(error), 502)
    except Exception:
        app.logger.exception("addresses failed")
        return fail("Zoho Books could not load the customer's addresses.", 502)


@app.post("/api/figures")
def figures():
    body = request.get_json(silent=True) or {}
    sources = body.get("figures")
    if not isinstance(sources, list):
        return fail("Send the figure numbers to match.", 400)
    try:
        return respond(figure_choices(sources[:200]))
    except SheetError as error:
        return fail(str(error), 502)
    except Exception:
        app.logger.exception("figure list failed")
        return fail("The inventory figures could not be loaded.", 502)


def submission():
    if request.is_json:
        body = request.get_json(silent=True) or {}
        return body, b"", ""
    try:
        rows = json.loads(request.form.get("rows") or "[]")
        order_text = request.form.get("order")
        order = json.loads(order_text) if order_text else None
    except json.JSONDecodeError as error:
        raise RowError("The order could not be read.") from error
    upload = request.files.get("pdf")
    pdf_bytes = upload.read() if upload is not None else b""
    filename = upload.filename if upload is not None else ""
    return {
        "spreadsheet": request.form.get("spreadsheet") == "true",
        "zoho": request.form.get("zoho") == "true",
        "rows": rows,
        "order": order,
    }, pdf_bytes, filename


@app.get("/api/salesorders/<salesorder_id>/pdf")
def salesorder_pdf(salesorder_id):
    try:
        pdf_bytes = sales_order_pdf(salesorder_id)
    except ZohoError as error:
        return fail(str(error), 502)
    except Exception:
        app.logger.exception("sales order pdf failed")
        return fail("Zoho Books could not send the sales order PDF.", 502)
    return Response(pdf_bytes, mimetype="application/pdf", headers={"Cache-Control": "no-store"})


@app.post("/api/pdf/merge")
def pdf_merge():
    """One PDF from several, in the order sent, so they print in one go."""
    documents = [upload.read() for upload in request.files.getlist("pdf")]
    if len(documents) < 2 or not all(document.startswith(b"%PDF") for document in documents):
        return fail("Send the PDFs to put together.", 400)
    try:
        merged = merge_pdfs(documents)
    except Exception:
        app.logger.exception("pdf merge failed")
        return fail("The PDFs could not be put together.", 502)
    return Response(merged, mimetype="application/pdf", headers={"Cache-Control": "no-store"})


@app.post("/api/submit")
def submit():
    try:
        body, pdf_bytes, filename = submission()
    except RowError as error:
        return fail(str(error), 400)
    to_sheet = bool(body.get("spreadsheet"))
    to_zoho = bool(body.get("zoho"))
    if not to_sheet and not to_zoho:
        return fail("Choose the spreadsheet, Zoho Books, or both.", 400)
    result = {"spreadsheet": None, "zoho": None}
    if to_sheet:
        try:
            validate_rows(body.get("rows"))
        except RowError as error:
            return fail(str(error), 400)
    draft = None
    if to_zoho:
        try:
            draft = prepare_draft_sales_order(body.get("order"), pdf_bytes)
        except OrderInputError as error:
            return fail(str(error), 400)
        except ZohoError as error:
            return fail(str(error), 502)
        except Exception:
            app.logger.exception("sales order check failed")
            return fail("Zoho Books could not save the draft sales order.", 502)
        try:
            result["zoho"] = create_draft_sales_order(draft, pdf_bytes, filename)
        except ZohoError as error:
            return fail(str(error), 502)
        except Exception:
            app.logger.exception("sales order create failed")
            return fail("Zoho Books could not save the draft sales order.", 502)
    if to_sheet:
        sales_order_number = _sales_order_number(result["zoho"])
        try:
            result["spreadsheet"] = append_purchase_orders(body.get("rows"), sales_order_number)
        except RowError as error:
            if result["zoho"] is None:
                return fail(str(error), 400)
            result["spreadsheet"] = _sheet_failure(str(error))
        except SheetError as error:
            if result["zoho"] is None:
                return fail(str(error), 502)
            result["spreadsheet"] = _sheet_failure(str(error))
        except Exception:
            app.logger.exception("spreadsheet write failed")
            message = "The lines could not be added to the spreadsheet."
            if result["zoho"] is None:
                return fail(message, 502)
            result["spreadsheet"] = _sheet_failure(message)
    return jsonify(result)


def _sales_order_number(zoho) -> str:
    if not isinstance(zoho, dict):
        return ""
    number = str(zoho.get("number") or "").strip()
    if number.casefold() == "the sales order":
        return ""
    if number[:3].casefold() == "so-":
        number = number[3:].strip()
    return number


def _sheet_failure(message: str) -> dict:
    return {"written": 0, "skipped": [], "message": message}


@app.errorhandler(413)
def too_large(_error):
    return fail("That PDF is larger than 32 MB.", 413)


if __name__ == "__main__":
    import os

    _start_warm()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")), threaded=True)
