"""Runtime settings. The spreadsheet defaults match the mailbox importer."""

import os

from dotenv import load_dotenv

load_dotenv()

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-flash-latest").strip()

GOOGLE_SERVICE_ACCOUNT_JSON = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
GOOGLE_IMPERSONATE = os.environ.get("GOOGLE_IMPERSONATE", "thenning@lunkenheimercvc.com").strip()

SHEET_ID = os.environ.get("SHEET_ID", "1clSeKesFcx5F8ceOVZEX6EoUNoe3EzKXBWbDGpFxhVE").strip()
SHEET_TAB = os.environ.get("SHEET_TAB", "PO/SO/INV TRACKING 2026").strip() or "PO/SO/INV TRACKING 2026"
SHEET_URL = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit"
# The INVENTORY tab is always read from the same spreadsheet as SHEET_ID.
INVENTORY_TAB = os.environ.get("INVENTORY_TAB", "INVENTORY").strip() or "INVENTORY"

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
MAX_PDF_BYTES = 32 * 1024 * 1024

# Zoho Books reads customers, items, payment terms, and delivery methods.
# Creating a sales order also needs ZohoBooks.salesorders.CREATE.
ZOHO_CLIENT_ID = os.environ.get("ZOHO_CLIENT_ID", "").strip()
ZOHO_CLIENT_SECRET = os.environ.get("ZOHO_CLIENT_SECRET", "").strip()
ZOHO_REFRESH_TOKEN = os.environ.get("ZOHO_REFRESH_TOKEN", "").strip()
ZOHO_ORGANIZATION_ID = os.environ.get("ZOHO_ORGANIZATION_ID", "").strip()
ZOHO_ACCOUNTS_DOMAIN = os.environ.get("ZOHO_ACCOUNTS_DOMAIN", "accounts.zoho.com").strip() or "accounts.zoho.com"
ZOHO_API_DOMAIN = os.environ.get("ZOHO_API_DOMAIN", "www.zohoapis.com").strip() or "www.zohoapis.com"

# Extraction and matching need little reasoning; "low" keeps Gemini from overthinking a messy PDF.
GEMINI_THINKING = {"thinkingLevel": "low"}
