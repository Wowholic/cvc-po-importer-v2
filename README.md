# Purchase desk

Purchase desk turns a purchase-order PDF into a reviewed order. Drop in the PDF, check the customer, dates, and lines, then send it on.

It can add the lines to the purchase-order spreadsheet and save a draft sales order in Zoho Books, with the PDF attached.

## Run

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
.venv/bin/gunicorn --bind 127.0.0.1:8080 --timeout 180 app:app
```

Open `http://127.0.0.1:8080`. Fill in `.env` from `.env.example` before reading a PDF or sending an order.
