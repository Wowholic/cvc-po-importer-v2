import unittest
from io import BytesIO
from unittest import mock

from sheet import first_writable_row
from test_auth import signed_in_client
from extract_po import parse_rows
from rows import build_sheet_values, normalize_row, partition_new, validate_rows


class RowLayoutTest(unittest.TestCase):
    def test_sheet_columns_match_the_mailbox_importer(self):
        rows = validate_rows([
            {
                "Company": "Acme",
                "CUSTOMER PO #": "PO-9",
                "SO No.": "SO-1",
                "PO DATE": "11-18-2026",
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
            "Acme", "PO-9", "", "", "11/18/2026", "", "", "V-100", 2, 12.5, "=J5*I5",
            "", "", "", "Not Paid", "", "", "NOT SHIIPED",
        ])
        filled = validate_rows([{
            "Company": "Acme",
            "CUSTOMER PO #": "PO-2",
            "VENDOR PO #": "V-9",
            "PO DATE": "11-18-2026",
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
        self.assertEqual(placed[4], "11/18/2026")
        self.assertEqual(placed[10], "=J8*I8")
        self.assertEqual(placed[14], "Not Paid")
        self.assertEqual(placed[17], "NOT SHIIPED")
        self.assertEqual(values[1][0], "'=cmd")
        self.assertEqual(values[1][2], "")
        self.assertEqual(values[1][5], "")
        self.assertEqual(values[1][6], "")
        self.assertEqual(values[1][8], 3)
        self.assertEqual(values[1][9], 1000)
        self.assertEqual(values[1][10], "=J6*I6")

    def test_the_zoho_number_is_written_in_column_d(self):
        rows = validate_rows([{
            "Company": "Acme",
            "CUSTOMER PO #": "PO-9",
            "PO DATE": "11-18-2026",
            "Qty": "1",
            "Unit Price": "2",
            "SO No.": "typed-on-the-form",
        }, {
            "Company": "Acme",
            "CUSTOMER PO #": "PO-9",
            "Qty": "3",
            "Unit Price": "4",
        }])
        values = build_sheet_values(rows, 5, "SO-42")
        self.assertEqual(values[0][3], "SO-42")
        self.assertEqual(values[1][3], "SO-42")
        self.assertEqual(build_sheet_values(rows, 5)[0][3], "")

    def test_order_options_stay_off_the_spreadsheet(self):
        rows = validate_rows([{
            "Company": "Acme",
            "CUSTOMER PO #": "PO-3",
            "PO DATE": "11-18-2026",
            "FIG#": "V-100",
            "Qty": "1",
            "Unit Price": "2",
            "Payment Terms": "Net 30",
            "Delivery Method": "UPS",
            "Other": "account 123",
        }])
        self.assertEqual(rows[0]["Payment Terms"], "Net 30")
        self.assertEqual(rows[0]["Delivery Method"], "UPS")
        self.assertEqual(rows[0]["Other"], "account 123")
        sheet = build_sheet_values(rows, 2)[0]
        self.assertNotIn("Net 30", sheet)
        self.assertNotIn("UPS", sheet)
        self.assertNotIn("account 123", sheet)

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
        self.assertEqual(rows[0]["Other"], "")

    def test_a_shipping_account_is_kept_for_zoho(self):
        rows = parse_rows('[{"Company":"Acme","Other":"UPS 1X234"}]')
        self.assertEqual(rows[0]["Other"], "UPS 1X234")


class OpenRowTest(unittest.TestCase):
    def test_a_zero_sales_amount_is_reusable(self):
        rows = [
            ["Company"],
            ["Acme", "PO-1", "", "", "", "", "", "", "2", "5", "$10.00"],
            [""] * 10 + ["$0.00"],
            [""] * 10 + ["$0.00"],
        ]
        self.assertEqual(first_writable_row(rows, 2), 3)

    def test_a_zero_sales_amount_with_anything_else_is_kept(self):
        rows = [
            ["Company"],
            ["Acme"] + [""] * 9 + ["$0.00"],
            [],
        ]
        self.assertEqual(first_writable_row(rows, 1), 3)

    def test_the_run_must_be_long_enough(self):
        rows = [
            ["Company"],
            [""] * 10 + ["$0.00"],
            ["Acme"],
            [],
        ]
        self.assertEqual(first_writable_row(rows, 2), 4)


class AppTest(unittest.TestCase):
    def setUp(self):
        self.client = signed_in_client()

    def test_home_is_order_entry(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"<title>CVC - Order Entry</title>", response.data)
        self.assertIn(b"Order Entry", response.data)
        self.assertNotIn(b"docs.google.com", response.data)

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
                "PO DATE": "11-18-2026",
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
        self.assertIn("MM-DD-YYYY", response.get_json()["error"])

    def test_zoho_is_saved_before_the_sheet_gets_its_number(self):
        order = []

        def create(_draft, _pdf, _name):
            order.append("zoho")
            return {
                "created": True,
                "number": "SO-1223066",
                "attachment": True,
                "url": "https://books.example/so",
                "message": "Draft SO-1223066 was saved in Zoho Books.",
            }

        def append(_rows, sales_order_number=""):
            order.append(("sheet", sales_order_number))
            return {"written": 1, "skipped": [], "url": "https://sheet.example"}

        with mock.patch("app.prepare_draft_sales_order", return_value={"ready": True}), \
             mock.patch("app.create_draft_sales_order", side_effect=create), \
             mock.patch("app.append_purchase_orders", side_effect=append):
            response = self.client.post("/api/submit", json={
                "zoho": True,
                "spreadsheet": True,
                "rows": [{
                    "Company": "Acme",
                    "CUSTOMER PO #": "PO-1",
                    "PO DATE": "11-18-2026",
                    "Qty": "2",
                    "Unit Price": "5",
                }],
                "order": {"customerId": "100"},
            })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(order, ["zoho", ("sheet", "1223066")])

    def test_a_bad_date_does_not_create_the_sales_order(self):
        with mock.patch("app.create_draft_sales_order") as create:
            response = self.client.post("/api/submit", json={
                "zoho": True,
                "spreadsheet": True,
                "rows": [{"Company": "Acme", "PO DATE": "2026-11-18", "Qty": "1", "Unit Price": "1"}],
                "order": {"customerId": "100"},
            })
        self.assertEqual(response.status_code, 400)
        create.assert_not_called()


if __name__ == "__main__":
    unittest.main()
