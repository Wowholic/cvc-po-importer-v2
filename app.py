#!/usr/bin/env python3
"""Manual purchase-order desk: read a PDF, review the lines, send them on."""

import json
import logging
import math
import threading
import time

from flask import Flask, jsonify, redirect, render_template, request, url_for

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
from config import MAX_PDF_BYTES, SHEET_URL
from extract_po import ExtractError, extract_bytes
from rows import RowError
from figures import figure_choices
from sheet import SheetError, append_purchase_orders
from zoho_customers import ZohoError, customer_choices, item_catalog, item_choices, list_customers, list_items, order_option_choices
from zoho_orders import OrderInputError, create_draft_sales_order, prepare_draft_sales_order

logging.basicConfig(level=logging.INFO)
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_PDF_BYTES
app.config["TEMPLATES_AUTO_RELOAD"] = True


def fail(message: str, status: int):
    return jsonify(error=message), status


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
        from zoho_customers import configured
        if not configured():
            return
        list_customers()
        list_items()
    except Exception:
        app.logger.exception("zoho list warm failed")


def _start_warm():
    global _warm_started
    if _warm_started or app.config.get("TESTING"):
        return
    _warm_started = True
    threading.Thread(target=_warm_zoho, daemon=True).start()


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
    return render_template("index.html", sheet_url=SHEET_URL)


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
        rows = extract_bytes(pdf_bytes)
    except ExtractError as error:
        return fail(str(error), 502)
    except Exception:
        app.logger.exception("purchase order read failed")
        return fail("The purchase order could not be read.", 502)
    return jsonify(rows=rows)


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
    return jsonify(result)


@app.post("/api/order-options")
def order_options():
    body = request.get_json(silent=True) or {}
    try:
        return jsonify(order_option_choices(
            body.get("paymentTerms", ""),
            body.get("deliveryMethod", ""),
        ))
    except ZohoError as error:
        return fail(str(error), 502)
    except Exception:
        app.logger.exception("order options failed")
        return fail("Zoho Books could not load payment terms.", 502)


@app.post("/api/figures")
def figures():
    body = request.get_json(silent=True) or {}
    sources = body.get("figures")
    if not isinstance(sources, list):
        return fail("Send the figure numbers to match.", 400)
    try:
        return jsonify(figure_choices(sources[:200]))
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
    if to_sheet:
        try:
            result["spreadsheet"] = append_purchase_orders(body.get("rows"))
        except RowError as error:
            return fail(str(error), 400)
        except SheetError as error:
            return fail(str(error), 502)
        except Exception:
            app.logger.exception("spreadsheet write failed")
            return fail("The lines could not be added to the spreadsheet.", 502)
    if draft is not None:
        try:
            result["zoho"] = create_draft_sales_order(draft, pdf_bytes, filename)
        except ZohoError as error:
            result["zoho"] = {"created": False, "message": str(error)}
            if result["spreadsheet"] is None:
                return fail(str(error), 502)
        except Exception:
            app.logger.exception("sales order create failed")
            message = "Zoho Books could not save the draft sales order."
            result["zoho"] = {"created": False, "message": message}
            if result["spreadsheet"] is None:
                return fail(message, 502)
    return jsonify(result)


@app.errorhandler(413)
def too_large(_error):
    return fail("That PDF is larger than 32 MB.", 413)


if __name__ == "__main__":
    import os

    _start_warm()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")), threaded=True)
