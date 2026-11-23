#!/usr/bin/env python3
"""Manual purchase-order desk: read a PDF, review the lines, send them on."""

import json
import logging

from flask import Flask, jsonify, render_template, request

from config import MAX_PDF_BYTES, SHEET_URL
from extract_po import ExtractError, extract_bytes
from rows import RowError
from figures import figure_choices
from sheet import SheetError, append_purchase_orders
from zoho_customers import ZohoError, customer_choices, item_choices, order_option_choices
from zoho_orders import OrderInputError, create_draft_sales_order, prepare_draft_sales_order

logging.basicConfig(level=logging.INFO)
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_PDF_BYTES
app.config["TEMPLATES_AUTO_RELOAD"] = True


def fail(message: str, status: int):
    return jsonify(error=message), status


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
    body = request.get_json(silent=True) or {}
    sources = body.get("figures")
    if not isinstance(sources, list):
        return fail("Send the figure numbers to match.", 400)
    try:
        return jsonify(item_choices(sources[:200]))
    except ZohoError as error:
        return fail(str(error), 502)
    except Exception:
        app.logger.exception("item list failed")
        return fail("Zoho Books could not load items.", 502)


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

    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
