import unittest

from zoho_orders import OrderInputError, sales_order_payload


FIELDS = [
    {
        "api_name": "cf_shipment_status",
        "field_id": "1",
        "is_active": True,
        "values": [
            {"name": "Shipped", "is_active": True},
            {"name": "Not Shipped", "is_active": True},
        ],
    },
    {"api_name": "cf_other", "field_id": "2", "is_active": True},
]


def sample_order():
    return {
        "customerId": "100",
        "reference": "PO-9",
        "date": "11-18-2026",
        "paymentTermsLabel": "Net 30",
        "paymentTermsDays": "30",
        "deliveryMethod": "BEST WAY",
        "deliveryMethodId": "55",
        "other": "acct 4451",
        "lines": [{"itemId": "77", "quantity": "2", "rate": "12.50"}],
    }


class SalesOrderPayloadTest(unittest.TestCase):
    def test_the_selected_zoho_records_are_sent(self):
        payload = sales_order_payload(sample_order(), FIELDS)
        self.assertEqual(payload["customer_id"], "100")
        self.assertNotIn("salesorder_number", payload)
        self.assertNotIn("shipment_date", payload)
        self.assertNotIn("salesperson_id", payload)
        self.assertNotIn("shipping_charge", payload)
        self.assertNotIn("adjustment", payload)
        self.assertEqual(payload["reference_number"], "PO-9")
        self.assertEqual(payload["date"], "2026-11-18")
        self.assertEqual(payload["payment_terms"], 30)
        self.assertEqual(payload["payment_terms_label"], "Net 30")
        self.assertEqual(payload["delivery_method"], "BEST WAY")
        self.assertEqual(payload["delivery_method_id"], "55")
        self.assertEqual(payload["line_items"], [{"item_id": "77", "quantity": 2, "rate": 12.5}])
        self.assertEqual(payload["notes"], "")
        self.assertEqual(payload["terms"], "")
        self.assertEqual(payload["contact_persons_associated"], [])
        self.assertFalse(payload["is_update_customer"])
        self.assertEqual(
            {item["value"] for item in payload["custom_fields"]},
            {"Not Shipped", "acct 4451"},
        )

    def test_customer_notes_are_sent_to_zoho(self):
        raw = sample_order()
        raw["customerNotes"] = "Leave at the dock.\nCall first."
        self.assertEqual(
            sales_order_payload(raw, FIELDS)["notes"],
            "Leave at the dock.\nCall first.",
        )

    def test_a_blank_other_field_is_left_off(self):
        raw = sample_order()
        raw["other"] = ""
        payload = sales_order_payload(raw, FIELDS)
        self.assertEqual(payload["custom_fields"], [{"customfield_id": "1", "value": "Not Shipped"}])

    def test_due_on_receipt_keeps_zero_days(self):
        raw = sample_order()
        raw["paymentTermsDays"] = 0
        raw["paymentTermsLabel"] = "Due on Receipt"
        self.assertEqual(sales_order_payload(raw, FIELDS)["payment_terms"], 0)

    def test_a_customer_name_is_not_accepted(self):
        raw = sample_order()
        raw["customerId"] = "ACME"
        with self.assertRaises(OrderInputError):
            sales_order_payload(raw, FIELDS)
