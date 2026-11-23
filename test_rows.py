import unittest
from io import BytesIO

from app import app
from extract_po import parse_rows
from rows import build_sheet_values, normalize_row, partition_new, validate_rows


class RowLayoutTest(unittest.TestCase):
    def test_sheet_columns_match_the_mailbox_importer(self):
        rows = validate_rows([
            {
                "Company": "Acme",
                "CUSTOMER PO #": "PO-9",
                "SO No.": "SO-1",
                "PO DATE": "18-11-2026",
                "FIG#": "V-100",
                "Qty": "2",
                "Unit Price": "12.50",
            },
            {
                "Company": "=cmd",
                "CUSTOMER PO #": "",
                "SO No.": "",
                "PO DATE": "",
                "FIG#": "",
                "Qty": 3,
                "Unit Price": "$1,000.00",
            },
        ])
        values = build_sheet_values(rows, 5)
        self.assertEqual(values[0], [
            "Acme", "PO-9", "", "", "18-11-2026", "", "", "V-100", 2, 12.5, "=J5*I5",
        ])
        filled = validate_rows([{
            "Company": "Acme",
            "CUSTOMER PO #": "PO-2",
            "VENDOR PO #": "V-9",
            "PO DATE": "18-11-2026",
            "VENDOR ESTIMATED SHIPMENT DATE": "01-12-2026",
            "CUSTOMER SHIPMENT DATE": "05-12-2026",
            "Qty": "1",
            "Unit Price": "4",
        }])
        placed = build_sheet_values(filled, 8)[0]
        self.assertEqual(placed[2], "V-9")
        self.assertEqual(placed[3], "")
        self.assertEqual(placed[5], "")
        self.assertEqual(placed[6], "")
        self.assertEqual(placed[10], "=J8*I8")
        self.assertEqual(values[1][0], "'=cmd")
        self.assertEqual(values[1][2], "")
        self.assertEqual(values[1][5], "")
        self.assertEqual(values[1][6], "")
        self.assertEqual(values[1][8], 3)
        self.assertEqual(values[1][9], 1000)
        self.assertEqual(values[1][10], "=J6*I6")

    def test_order_options_stay_off_the_spreadsheet(self):
        rows = validate_rows([{
            "Company": "Acme",
            "CUSTOMER PO #": "PO-3",
            "PO DATE": "18-11-2026",
            "FIG#": "V-100",
            "Qty": "1",
            "Unit Price": "2",
            "Payment Terms": "Net 30",
            "Delivery Method": "UPS",
            "Other": "account 123",
        }])
        self.assertEqual(rows[0]["Payment Terms"], "Net 30")
        self.assertEqual(rows[0]["Delivery Method"], "UPS")
        self.assertNotIn("Other", rows[0])
        self.assertNotIn("Net 30", build_sheet_values(rows, 2)[0])
        self.assertNotIn("UPS", build_sheet_values(rows, 2)[0])

    def test_existing_po_numbers_are_left_out(self):
        rows = [normalize_row({"Company": "Acme", "CUSTOMER PO #": "PO-1", "Qty": "1"})]
        fresh, skipped = partition_new(rows, {"PO-1"})
        self.assertEqual(fresh, [])
        self.assertEqual(skipped[0]["po"], "PO-1")

    def test_parser_accepts_a_rows_object(self):
        rows = parse_rows('{"rows":[{"Company":"Acme","CUSTOMER PO #":"10","Qty":"2.0"}]}')
        self.assertEqual(rows[0]["Company"], "Acme")
        self.assertEqual(rows[0]["Qty"], "2")
        self.assertEqual(rows[0]["FIG#"], "")


class AppTest(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    def test_home_links_to_the_shared_spreadsheet(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"1Pkvjtlfba51DLhMJeJA_e9rDmwMC6YUhWUkXZ3Lit-k", response.data)
        self.assertIn(b"Review the lines", response.data)

    def test_rejects_a_file_that_is_not_a_pdf(self):
        response = self.client.post(
            "/api/extract",
            data={"pdf": (BytesIO(b"hello"), "note.txt")},
        )
        self.assertEqual(response.status_code, 400)

    def test_zoho_alone_does_not_write_the_sheet(self):
        response = self.client.post("/api/submit", json={
            "zoho": True,
            "spreadsheet": False,
            "rows": [{
                "Company": "Acme",
                "CUSTOMER PO #": "PO-1",
                "PO DATE": "18-11-2026",
                "Qty": "2",
                "Unit Price": "5",
            }],
        })
        body = response.get_json()
        self.assertIn(response.status_code, (400, 502))
        self.assertIn("error", body)
        self.assertNotIn("zoho", body)

    def test_bad_date_is_rejected_before_the_sheet(self):
        response = self.client.post("/api/submit", json={
            "spreadsheet": True,
            "rows": [{"Company": "Acme", "PO DATE": "2026-11-18"}],
        })
        self.assertEqual(response.status_code, 400)
        self.assertIn("DD-MM-YYYY", response.get_json()["error"])


if __name__ == "__main__":
    unittest.main()
