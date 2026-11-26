# Order Entry

Order Entry turns a customer's purchase-order PDF into an order. Gemini reads the PDF and the app fills a form. A person checks the form, then sends the order to:

- the **PO/SO/INV TRACKING 2026** tab of the [Sales Order Tracker](https://docs.google.com/spreadsheets/d/1clSeKesFcx5F8ceOVZEX6EoUNoe3EzKXBWbDGpFxhVE/edit) spreadsheet, and/or
- **Zoho Books**, as a draft sales order with the PDF attached.

The app never emails the customer, confirms the Zoho order, or creates customers or items.

## Tools it uses

| Tool | What for |
| --- | --- |
| **Gemini** (Google AI) | Reads the PDF. Helps match a field only when the app can't decide on its own. |
| **Google Sheets** | Writes order lines to the tracking tab. Reads figure numbers and sizes from the INVENTORY tab. |
| **Zoho Books** | Reads customers, items, payment terms and delivery methods. Creates the draft sales order. |
| **Flask** (Python) | Runs the web app. |

## How to use it

1. Sign in. One shared username and password covers the whole team. The session lasts 12 hours.
2. Drop a PDF on the page (under 32 MB) and press **Read purchase order**.
3. Check the form. The PDF shows on the left. Correct anything that was read or matched wrong.
4. Tick **Spreadsheet**, **Zoho Books**, or both, and press **Submit**.
5. A green banner confirms what was added, with links to the spreadsheet and the sales order. If something fails, the form stays open with the error.
6. **Print Zoho SO** prints the sales order PDF that Zoho builds from its template. **Print client PO** prints the PDF you dropped. Both stay available until you drop the next PDF.

**Sync Zoho Books Data** (top right) reloads the saved customers, items, payment terms, delivery methods and inventory figures, and forgets the saved customer addresses. Press it after any of those change in Zoho or in the INVENTORY tab. The time of the last sync is shown next to the button, in Cincinnati time.

## Fields

What Gemini reads from the PDF:

| Field | Meaning |
| --- | --- |
| Company | The customer who sent the order. Never Cincinnati Valve or CVC. |
| CUSTOMER PO # | The customer's purchase order number. |
| PO DATE | The date the order was issued (MM-DD-YYYY). |
| Payment Terms | As printed, e.g. Net 30. |
| Delivery Method | The carrier or ship method. |
| Other | The customer's shipping account number, if printed. |
| Ship To | The delivery address. Used only to match the shipping address. |
| FIG# | The part or figure number. |
| Description | The line's description. Used only for matching. |
| Qty | Quantity. |
| Unit Price | Price per unit. |

Fields the person fills in by hand:

| Field | Meaning |
| --- | --- |
| VENDOR PO # | Written to the spreadsheet. |
| Customer Notes | Sent to Zoho. |

Two fields are matched for each line:

| Field | Meaning |
| --- | --- |
| Fig# for Spreadsheet | The figure number from column C of the INVENTORY tab. |
| Fig# for Zoho Books | The Zoho item. |

One field is matched for the order:

| Field | Meaning |
| --- | --- |
| Shipping Address | One of the customer's Zoho addresses. Sent to Zoho only. |

## How matching works

The app first compares what Gemini read with the saved lists, without calling Gemini again. The customer, payment terms and delivery method are matched by name.

A line's Zoho item and inventory figure are matched in this order:

1. **Family + size.** An inventory figure is a family plus a 3-digit size code: `1837005` = family `1837` + size `005`. If the FIG# contains a family and the PDF gives a size, the app finds the inventory row whose column B (**SIZE**) matches and the Zoho item with the same name (`BRONZE - 1837005`). If several Zoho items share that figure (`180020` and `BRONZE - 180020`), the material named in the description (`BRZ`) decides; if none is named, only those items are offered. The size comes from the description (`1"`, `1-1/2 IN`, or a leading `1/2`, ignoring the FIG# if the description repeats it) or from the FIG# (the `1/4` in `MSP_18374X1/4`).
2. **Exact name.** A Zoho item whose whole name or SKU is the FIG# (`1837`, `T-811X`) is chosen, unless the description names a material (`BRZ`/`BZ` = BRONZE, `STL` = STEEL, `SS`, `CI`…) that other items with that part number carry before " - " (`BRONZE - 1837005`). Then those items compete in step 3.
3. **Part number.** A code from the FIG# matches an item name or SKU, including all-digit codes like `1837`. If it clearly wins, it's chosen. A code found only in the description, such as `CR13`, counts for less. If several items are close (e.g. every size of `906BS`), the size from the PDF picks the one whose SIZE matches.
4. **Gemini.** Anything still undecided goes to Gemini in one call. Gemini can only choose from the options the app offers.

**Shipping address.** Once the company is chosen, the app fetches that customer's addresses from Zoho in one request and saves them in `cache/addresses.json` until the next Sync. It compares each one with the PDF's Ship To, without Gemini: same ZIP code, same street number, shared street words (`Road` = `Rd`, `Suite` = `Ste`, and so on), same city. A clear winner is chosen. Otherwise the customer's default shipping address is chosen and the field says to check it.

The person can change every match before submitting.

## Spreadsheet

- **File:** [Sales Order Tracker](https://docs.google.com/spreadsheets/d/1clSeKesFcx5F8ceOVZEX6EoUNoe3EzKXBWbDGpFxhVE/edit) (`1clSeKesFcx5F8ceOVZEX6EoUNoe3EzKXBWbDGpFxhVE`)
- **Writes to:** the **PO/SO/INV TRACKING 2026** tab
- **Reads from:** the **INVENTORY** tab, column B (SIZE) and column C (Fig. No. QB)

New lines go on the first free row. A row is free if it's empty, or if its only value is `$0.00` in column M from the prefilled formula. If the customer PO number is already in column B, the line is skipped and the banner says so.

| Column | What is written |
| --- | --- |
| A | Company |
| B | CUSTOMER PO # |
| C | VENDOR PO # |
| D | SO No. from Zoho, without `SO-` (only when Zoho was ticked too) |
| E | PO DATE, as `m/d/yyyy` |
| H | FIG# for Spreadsheet |
| I | Quantity Ordered |
| L | Unit Price |
| M | Sales Amount, formula `=L*I` |
| Q | PAYMENT STATUS: `Not Paid` |
| T | SHIPMENT STATUS: `NOT SHIIPED` (spelled as in the sheet's dropdown) |

All other columns are left empty or untouched. That includes J–K (partial and balance quantities) and everything from U onwards.

## Zoho Books

Creates one **draft** sales order and attaches the PDF. It needs a customer, payment term, delivery method, PO date, and a Zoho item, quantity and price on every line.

- The customer PO number goes in **Reference #**.
- The chosen **Shipping Address** goes on the order. The customer's own record in Zoho is not changed.
- **Other** goes in the custom field `cf_other`, and **Customer Notes** in Customer Notes.
- The custom field `cf_shipment_status` is set to **Not Shipped**. Both custom fields must exist in Zoho.

If both destinations are ticked, Zoho goes first so its SO number can be written in column D. If Zoho fails, nothing is written to the spreadsheet.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env      # then fill in the values below
```

`.env` holds the passwords and keys and is never committed.

| Variable | What it is |
| --- | --- |
| `GEMINI_API_KEY` | Gemini API key. |
| `GEMINI_MODEL` | Gemini model. Default `gemini-flash-latest`. |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Google service account JSON, on one line. |
| `GOOGLE_IMPERSONATE` | The Google user the service account acts as. That user needs edit access to the spreadsheet. |
| `SHEET_ID` / `SHEET_TAB` | Spreadsheet and tab to write to. Defaults: the Sales Order Tracker, `PO/SO/INV TRACKING 2026`. |
| `INVENTORY_TAB` | The inventory tab, always in the same spreadsheet as `SHEET_ID`. Default `INVENTORY`. |
| `ZOHO_CLIENT_ID`, `ZOHO_CLIENT_SECRET`, `ZOHO_REFRESH_TOKEN` | Zoho API access. Token scopes: `ZohoBooks.contacts.READ`, `ZohoBooks.settings.READ`, `ZohoBooks.salesorders.CREATE`, `ZohoBooks.salesorders.READ` (for Print Zoho SO). |
| `ZOHO_ORGANIZATION_ID` | Zoho Books organization id. |
| `ZOHO_ACCOUNTS_DOMAIN` / `ZOHO_API_DOMAIN` | Default `accounts.zoho.com` / `www.zohoapis.com`. Use the `.eu` versions for an EU account. |
| `AUTH_USER` / `AUTH_PASSWORD` | Shared sign-in. Password: at least 12 characters. |
| `AUTH_SECRET` | Random key for the session cookie, at least 32 characters (`openssl rand -hex 32`). |
| `PORT` | Local port. Default `8080`. |

## Run

Locally:

```bash
PORT=8080 .venv/bin/python app.py
```

Open http://127.0.0.1:8080. Restart after code changes; it does not reload by itself.

On a server:

```bash
.venv/bin/gunicorn --bind 127.0.0.1:8080 --timeout 180 app:app
```

The 180-second timeout leaves room for slow PDF reads, syncs and Zoho saves.

## Pages and API

Everything except the sign-in page needs a signed-in session. Without one, the API answers `401`. Five wrong passwords in 15 minutes lock that address out for 15 minutes.

| Method and path | What it does |
| --- | --- |
| `GET /login`, `POST /login` | Sign-in page and sign-in. |
| `POST /logout` | Sign out. |
| `GET /` | The main page. |
| `POST /api/extract` | Upload a PDF (`pdf`). Gemini reads it and returns the lines. |
| `POST /api/resolve` | Send the lines. Returns the customer, terms, delivery method, figure and item matches. |
| `POST /api/submit` | Send the order. Fields: `spreadsheet` and `zoho` (`"true"`/`"false"`), `rows` (JSON lines), `order` (Zoho details), `pdf`. |
| `GET /api/salesorders/<id>/pdf` | The sales order PDF from Zoho (Print Zoho SO). |
| `POST /api/addresses` | Send `customerId` and `shipTo`. Returns the customer's addresses and the best match. |
| `GET /api/catalog` | The saved Zoho and inventory lists. |
| `POST /api/catalog/refresh` | Reload those lists (the Sync button). |
| `GET /api/customers?company=`, `POST /api/items`, `POST /api/item-matches`, `POST /api/order-options`, `POST /api/figures` | Fallbacks used only when the saved lists aren't ready yet. |

## Costs

Only Gemini is charged per use. Google Sheets and Zoho Books calls have no per-call cost beyond the existing subscriptions.

Every Gemini call prints its cost in the terminal running the app and in the browser console:

```text
Gemini read PDF (gemini-3.8-flash): 4,769 input tokens, 1,339 output tokens including 1,169 thinking, $0.0086
Gemini match leftovers (gemini-3.8-flash): 996 input tokens, 6,937 output tokens including 6,850 thinking, $0.0268
```

A typical PDF costs around one to four cents. These are estimates from the list prices in `_RATES` in `extract_po.py`. Flash costs $0.75 per million input tokens and $3.75 per million output tokens until December 31, 2026, then $1.50 and $7.50.

The real bill:

- Charges for the API key: https://console.cloud.google.com/billing
- Gemini prices: https://ai.google.dev/gemini-api/docs/pricing
- Billing tiers (free vs paid): https://ai.google.dev/gemini-api/docs/billing

## Tests

```bash
.venv/bin/python -m unittest
```

The tests don't call Gemini, Zoho or Google Sheets. `test_real_orders.py` runs lines from real purchase orders against the saved catalog (it's skipped if `cache/catalog.json` is missing). When a PDF is matched wrong, add its line there with the right answer.

## Files

| File | Role |
| --- | --- |
| `app.py` | Pages, sign-in and API routes. |
| `extract_po.py` | Reads the PDF with Gemini and prints the cost. |
| `resolve.py` | Matching: part number, size, family + size, then Gemini. |
| `addresses.py` | Fetches and saves a customer's addresses, and matches the Ship To. |
| `catalog.py` | Saves the Zoho and inventory lists in `cache/catalog.json`. |
| `figures.py` | Reads figure numbers and sizes from the INVENTORY tab. |
| `rows.py` | Puts each line into the tracking-tab columns. |
| `sheet.py` | Finds the first free row and writes to the spreadsheet. |
| `zoho_customers.py` | Reads customers, items, payment terms and delivery methods from Zoho. |
| `zoho_orders.py` | Creates the Zoho draft, attaches the PDF, and downloads the sales order PDF. |
| `auth.py` | Shared sign-in, session cookie and lockout. |
| `config.py` | Reads `.env`. |
| `templates/`, `static/` | The web page. |
