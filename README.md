# Purchase desk

Purchase desk turns a customer purchase-order PDF into a reviewed order. Drop in the PDF, check the customer, dates, and lines, then send it on.

It can append the lines to the **PO/SO/INV TRACKING 2026** tab and save a draft sales order in Zoho Books, with the PDF attached. That tab and the inventory figures live in the same spreadsheet. The Zoho order stays a draft. The app does not email the customer, open the order, or create customers or items.

## What a person does

1. Sign in at `/login`. One shared username and password covers the whole desk. The session cookie lasts 12 hours.
2. Drop a PDF on the homepage, or choose one. Only a PDF is accepted, and it must be under 32 MB.
3. Press **Read purchase order**. Gemini reads the PDF. The app then matches the customer, figure numbers, items, payment terms, and delivery method against the saved lists.
4. Review the form. The PDF stays on the left. Correct anything that was read or matched wrong. Spreadsheet figure numbers and Zoho items are separate fields, because the inventory sheet and Zoho Books do not always use the same name.
5. Choose **Spreadsheet**, **Zoho Books**, or both, then press **Submit**.
6. A successful submit returns to the homepage. A green banner under the drop area says what was added and links to the spreadsheet and the sales order. Choosing the next PDF clears that banner.
7. A failed submit stays on the review form with the error, and the lines remain filled in.

**Sync Zoho Books Data** replaces the saved customer, item, payment-term, delivery-method, and inventory-figure lists. Use it after those lists change in Zoho Books or on the inventory sheet. The first request after a cold start builds the same file if `cache/catalog.json` is missing.

## How a PDF becomes an order

```
PDF
  -> Gemini reads the lines          POST /api/extract
  -> local match against the catalog POST /api/resolve
  -> Gemini only for what is left
  -> person reviews the form
  -> spreadsheet, Zoho Books, or both POST /api/submit
```

### What Gemini is asked to read

The first call sends the PDF and asks for JSON lines with these fields:

| Field | Meaning |
| --- | --- |
| Company | The customer that issued the purchase order. Cincinnati Valve and CVC are the supplier when they only appear as the company receiving the order, so they are not used as Company in that case. |
| CUSTOMER PO # | The customer's own purchase order number. |
| PO DATE | The date the purchase order was issued, converted to MM-DD-YYYY. Ship dates and due dates are ignored. |
| Payment Terms | The wording printed on the PDF, such as Net 30. |
| Delivery Method | The carrier or ship method printed on the PDF. |
| Other | The customer's shipping account number, when the PDF prints one. Often beside the carrier or ship method. Empty when there is none. It is not written to the spreadsheet. |
| FIG# | The part, figure, or manufacturer part identifier. |
| Description | The words on that line that describe the part. Used for matching. It is not a spreadsheet column. |
| Qty | Quantity, as a plain number. |
| Unit Price | Price for one unit, as a plain number. |

A missing value stays an empty string. The model is `GEMINI_MODEL` in `.env`. The default, `gemini-flash-latest`, follows Google's current Flash model, so the underlying model can change.

### How matching works

The catalog is the saved copy of Zoho customers, Zoho items, Zoho payment terms, Zoho delivery methods, and inventory figure numbers. Matching tries the catalog locally first. Gemini is called again only for the fields that are still ambiguous. That second call may be skipped entirely when every field already has one clear match.

A customer matches on the company name. One strong candidate is selected immediately. Several close candidates, or no local candidate, go to Gemini. If Gemini returns nothing usable, the closest local customer is still proposed.

A Zoho item matches when the figure code or the description shares a distinctive code with the item name or SKU. One clear winner is selected. Close ties go to Gemini, which must choose an id from the offered items. A figure number for the spreadsheet is matched the same way against column C of the inventory tab (`Fig. No. QB`). Spacing, dashes, and letter case are ignored for that comparison.

Payment terms and delivery methods match when the PDF wording equals a Zoho name, contains it, or shares most of its words. Anything less definite goes to Gemini, which must copy an id from the offered choices.

The person can override every match before submitting.

### Spreadsheet

Checked by default. Lines are appended to `SHEET_TAB` in `SHEET_ID`. Both default to the **PO/SO/INV TRACKING 2026** tab of `1col-aNAhDKJyqTg6Bai3MSIwPPS_-ELby_F-FKxCi2s`. The **INVENTORY** tab in that same file is only read, for figure numbers in column C.

New lines start on the first row that is empty, or whose only value is a `$0.00` sales amount from the prefilled `=J*I` formula. Every cell the app writes is centered. If column B already contains that customer PO number, those lines are left out and the banner says so. Qty and Unit Price are numbers. Unit Price and the sales amount are currency. Text that starts with `=`, `+`, or `@` is prefixed with a quote so it cannot become a formula.

| Column | What is written |
| --- | --- |
| A | Company. The header cell has no title. |
| B | CUSTOMER PO # |
| C | VENDOR PO #. Typed on the form. Gemini does not invent it. |
| D | SO No. The Zoho number without the `SO-` prefix, when this submit created one. `SO-1223066` is written as `1223066`. Left blank when only the spreadsheet is written. |
| E | PO DATE, as `m/d/yyyy`. `11-18-2026` on the form becomes `11/18/2026` on the sheet. |
| F | VENDOR ESTIMATED SHIPMENT DATE. Left blank. |
| G | CUSTOMER SHIPMENT DATE. Left blank. |
| H | FIG# for the spreadsheet |
| I | Qty |
| J | Unit Price |
| K | Sales Amount, as the formula `=J*I` |
| L–N, P–Q | Left blank. |
| O | PAYMENT STATUS. The existing dropdown choice `Not Paid`. |
| R | SHIPMENT STATUS. The existing dropdown choice `NOT SHIIPED`, spelled the way the sheet already has it. |

Columns S through AA, the invoice, payment, and shipping notes, are left untouched. Payment Terms, Delivery Method, Other, Customer Notes, and Description are not spreadsheet columns.

The Google connection is a service account. `GOOGLE_SERVICE_ACCOUNT_JSON` is the account JSON on one line, not a path to a file. When `GOOGLE_IMPERSONATE` is set, the account acts as that user through domain-wide delegation. The Sheets scope is `https://www.googleapis.com/auth/spreadsheets`.

### Zoho Books

Optional. Submit creates one draft sales order and attaches the original PDF.

The order needs a Zoho customer, a payment term, a delivery method, a PO date, and one Zoho item with a quantity and rate on every non-blank line. The customer PO number is sent as the reference number when it is filled in. The shipment-status custom field is set to **Not Shipped**. The app does not update the customer record.

| Form field | Where it comes from | Where it goes |
| --- | --- | --- |
| Other | The shipping account number read from the PDF, when one is printed. It can be edited. | Zoho custom field `cf_other`. Not the spreadsheet. |
| Customer Notes | Empty. Typed by hand. | The sales order's Customer Notes. Not the spreadsheet. |

Zoho must already contain an active sales-order custom field `cf_shipment_status` whose choices include `Not Shipped`. Sending **Other** also requires `cf_other`.

The OAuth scopes on the refresh token are `ZohoBooks.contacts.READ`, `ZohoBooks.settings.READ`, and `ZohoBooks.salesorders.CREATE`. The app reads customers, items, payment terms, delivery methods, and sales-order fields. The only writes are the draft and its PDF attachment.

Use `accounts.zoho.eu` and `www.zohoapis.eu` when the Books organization is on the EU domain. The sales-order link in the banner is `https://books.zoho.com/app/<organization id>#/salesorders/<id>`, with the host changed to match `ZOHO_API_DOMAIN`.

If both destinations are selected, Zoho Books is saved first. Zoho's sales order number, without the `SO-` prefix, is then written in column D of every line added to the spreadsheet. A Zoho failure stops the submit before the spreadsheet is touched, and the review form stays open. If Zoho succeeds and the spreadsheet write then fails, the banner shows the sales order link and the spreadsheet error.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
```

Fill `.env` before reading a PDF or sending an order. `.env` stays out of git. `.env.example` lists every variable and the empty defaults.

| Variable | Required for | What it is |
| --- | --- | --- |
| `GEMINI_API_KEY` | Reading a PDF and the leftover match | Gemini Developer API key. |
| `GEMINI_MODEL` | Those same calls | Model id. Default `gemini-flash-latest`. |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | The spreadsheet and the inventory figures | Service account JSON, on one line. |
| `GOOGLE_IMPERSONATE` | Sheets, when the service account uses delegation | The Google user the service account acts as. |
| `SHEET_ID` | Writing orders | Spreadsheet id. Default `1col-aNAhDKJyqTg6Bai3MSIwPPS_-ELby_F-FKxCi2s`. |
| `SHEET_TAB` | Writing orders | Tab name. Default `PO/SO/INV TRACKING 2026`. |
| `INVENTORY_SHEET_ID` | Figure matching | Same spreadsheet by default. |
| `INVENTORY_TAB` | Figure matching | Tab name. Default `INVENTORY`. Figure numbers are read from column C. |
| `ZOHO_CLIENT_ID` | Zoho reads and the draft | OAuth client. |
| `ZOHO_CLIENT_SECRET` | Zoho | OAuth secret. |
| `ZOHO_REFRESH_TOKEN` | Zoho | Refresh token with the three scopes above. |
| `ZOHO_ORGANIZATION_ID` | Zoho | Books organization id. |
| `ZOHO_ACCOUNTS_DOMAIN` | Zoho | Default `accounts.zoho.com`. |
| `ZOHO_API_DOMAIN` | Zoho | Default `www.zohoapis.com`. |
| `PORT` | Local `python app.py` | Default `8080`. |
| `AUTH_USER` | Sign-in | Shared username. |
| `AUTH_PASSWORD` | Sign-in | At least 12 characters. |
| `AUTH_SECRET` | Sign-in | Random signing key, at least 32 characters. Not the password. `openssl rand -hex 32` |

Five failed sign-ins from the same address within 15 minutes lock that address for 15 minutes.

## Run

```bash
.venv/bin/gunicorn --bind 127.0.0.1:8080 --timeout 180 app:app
```

Open `http://127.0.0.1:8080`.

The timeout is 180 seconds because a PDF read, a catalog refresh, and a Zoho save can each take a while. For a local process without gunicorn:

```bash
PORT=8080 .venv/bin/python app.py
```

That command uses Flask's development server. Restart it after code changes. Debug reload is off.

On startup the app warms the catalog in the background, unless it is running the tests.

## Pages and API

Every route except login, logout, and static files requires the session cookie. An API call without it returns `401`.

| Method and path | What it does |
| --- | --- |
| `GET /login` | Sign-in page. |
| `POST /login` | Checks the shared username and password and sets the `po_session` cookie. |
| `POST /logout` | Clears the cookie. |
| `GET /` | The desk: drop area, review form, and the spreadsheet link. |
| `POST /api/extract` | Multipart field `pdf`. Returns `{rows}` from Gemini. |
| `GET /api/catalog` | Returns the saved lists. Builds them on first use. |
| `POST /api/catalog/refresh` | Fetches Zoho and the inventory figures again and replaces `cache/catalog.json`. |
| `POST /api/resolve` | JSON `{rows}`. Returns the customer, payment term, delivery method, figure, and item matches. |
| `GET /api/customers?company=` | Customer search. Used when the saved catalog is not ready. |
| `POST /api/items` | Item list. Used when the saved catalog is not ready. |
| `POST /api/item-matches` | JSON `{figures}`. Matches figure numbers to Zoho items. |
| `POST /api/order-options` | JSON `{paymentTerms, deliveryMethod}`. Loads and matches those two lists. |
| `POST /api/figures` | JSON `{figures}`. Matches figure numbers to the inventory sheet. |
| `POST /api/submit` | Multipart or JSON. Writes the selected destinations. |

`/api/submit` fields:

| Field | Meaning |
| --- | --- |
| `spreadsheet` | `"true"` or `"false"`. |
| `zoho` | `"true"` or `"false"`. |
| `rows` | JSON array of spreadsheet lines. |
| `order` | JSON object for the Zoho draft. Sent only when Zoho is checked. |
| `pdf` | The original PDF. Sent only when Zoho is checked. |

A normal read uses `/api/extract` and `/api/resolve`. The other list routes are the fallback when the catalog has not been saved yet.

## Costs

Each Gemini call prints one line in the terminal that is running the app, and the same line is written to the browser console. Refresh the page after a restart so the browser has the script that prints it.

```text
Gemini read PDF (gemini-3.8-flash): 18,420 input tokens, 312 output tokens, $0.0150
```

A second line appears when leftovers are sent back to Gemini. Its label is `match leftovers`, `match figures`, `match items`, or `match options`. Input tokens are what was sent, including the PDF. Output tokens include thinking tokens. The dollar amount is the paid-tier list price for the model named in the response.

The rates live in `_RATES` in `extract_po.py` and follow [Gemini Developer API pricing](https://ai.google.dev/gemini-api/docs/pricing). Flash is $0.75 per million input tokens and $3.75 per million output tokens through December 31, 2026, then $1.50 and $7.50. `gemini-flash-latest` uses that Flash price when the response does not name a more specific model. An unknown model still prints the token counts and says `price unknown`. A key that is still on the free tier is not actually charged.

That line is an estimate for the call that just finished. The bill for the API key is in Google AI Studio and Google Cloud:

- Pricing table: https://ai.google.dev/gemini-api/docs/pricing
- How billing tiers work: https://ai.google.dev/gemini-api/docs/billing
- The project's charges: https://console.cloud.google.com/billing

Zoho Books and Google Sheets calls are not included in that estimate.

## Tests

```bash
.venv/bin/python -m unittest
```

The tests do not call Gemini, Zoho, or Google Sheets.

## Files

| File | Role |
| --- | --- |
| `app.py` | Pages, sign-in, and the API. |
| `extract_po.py` | The PDF prompt, the Gemini call, and the token price line. |
| `resolve.py` | Local matching, then one Gemini call for whatever is left. |
| `catalog.py` | Saves the Zoho and inventory lists in `cache/catalog.json`. |
| `figures.py` | Reads inventory figure numbers from column C. |
| `rows.py` | Cleans lines and maps them onto the tracking-tab columns. |
| `sheet.py` | Appends rows to the tracking tab, reusing a `$0.00` sales-amount row when that is all the row contains. |
| `zoho_customers.py` | Reads Zoho customers, items, payment terms, and delivery methods. |
| `zoho_orders.py` | Creates the draft sales order and attaches the PDF. |
| `auth.py` | The shared login, the session cookie, and the lockout. |
| `config.py` | Reads `.env`. |
| `static/app.js` | The desk in the browser. |
| `templates/index.html` | The drop area and the review form. |
