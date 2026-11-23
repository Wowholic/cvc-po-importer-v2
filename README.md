# Purchase desk

A manual companion to the mailbox importer. Drop a purchase-order PDF, read it with Gemini, correct the lines, then append them to the same `POs` spreadsheet.

Vendor PO and both shipment dates stay blank. Sales Amount is the formula `=J*I`. A customer PO number that is already on the sheet is not added again.

## Run

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
.venv/bin/gunicorn --bind 127.0.0.1:8080 --timeout 180 app:app
```

Open `http://127.0.0.1:8080`.

`GEMINI_API_KEY` is the same key the mailbox app stores as `gemini-api-key`. The model is `gemini-flash-latest`.

`GOOGLE_SERVICE_ACCOUNT_JSON` is the service account JSON on one line. `GOOGLE_IMPERSONATE` stays `thenning@lunkenheimercvc.com` so the write uses the same access to the shared sheet.

Zoho Books is a choice on submit and does not create an order yet.
