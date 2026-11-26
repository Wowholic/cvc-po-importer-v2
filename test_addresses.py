import unittest

from addresses import addresses_from_contact, match_address
from test_orders import FIELDS
from zoho_orders import sales_order_payload

# Shapes copied from a real Zoho contact: messy one-line addresses and a clean one.
CONTACT = {
    "billing_address": {
        "address_id": "1",
        "address": "014 HAJOCA CORP, 1240 WELSH ROAD, NORTH WALES, PA - 19454-1820",
        "city": "NORTH WALES",
        "state": "PA",
        "zip": "19454-1820",
    },
    "shipping_address": {
        "address_id": "2",
        "address": "AMWAY ADA, C/O SYNOVOS SUPPLY AND ASSETS, 7575 FULTON RD.EAST BLD43, REC 8, ADA, MI - 49355",
        "city": "ADA",
        "state": "MI",
        "zip": "49355",
    },
    "addresses": [
        {"address_id": "1", "address": "duplicate of billing"},
        {
            "address_id": "3",
            "attention": "KAMPI COMPONENTS",
            "address": "88 CANAL RD",
            "street2": "TAG: CUSTOMER PO# 369707",
            "city": "FAIRLESS HILLS",
            "state": "Pennsylvania",
            "state_code": "PA",
            "zip": "19030-4302",
        },
    ],
}


class AddressListTest(unittest.TestCase):
    def test_default_shipping_first_and_no_duplicates(self):
        rows = addresses_from_contact(CONTACT)
        self.assertEqual([row["id"] for row in rows], ["2", "1", "3"])
        self.assertEqual(rows[0]["tag"], "Default shipping")
        self.assertTrue(rows[0]["default"])
        self.assertEqual(rows[1]["tag"], "Billing")
        self.assertEqual(rows[2]["text"], "KAMPI COMPONENTS, 88 CANAL RD, TAG: CUSTOMER PO# 369707, FAIRLESS HILLS, PA 19030-4302")

    def test_city_is_not_repeated_when_the_street_line_has_it(self):
        rows = addresses_from_contact(CONTACT)
        self.assertEqual(rows[1]["text"], "014 HAJOCA CORP, 1240 WELSH ROAD, NORTH WALES, PA - 19454-1820")


class AddressMatchTest(unittest.TestCase):
    def setUp(self):
        self.rows = addresses_from_contact(CONTACT)

    def test_zip_and_street_number_pick_the_address(self):
        chosen, sure = match_address("Kampi Components, 88 Canal Road, Fairless Hills PA 19030", self.rows)
        self.assertEqual((chosen, sure), ("3", True))

    def test_abbreviations_and_zip_plus_four_still_match(self):
        chosen, sure = match_address("HAJOCA, 1240 Welsh Rd., North Wales, Pennsylvania 19454-1820", self.rows)
        self.assertEqual((chosen, sure), ("1", True))

    def test_no_ship_to_uses_the_default(self):
        self.assertEqual(match_address("", self.rows), ("2", True))

    def test_an_unknown_address_falls_back_to_the_default_and_is_unsure(self):
        self.assertEqual(match_address("500 Main St, Dallas TX 75201", self.rows), ("2", False))

    def test_a_single_address_is_always_chosen(self):
        self.assertEqual(match_address("anything", self.rows[:1]), ("2", True))

    def test_no_addresses(self):
        self.assertEqual(match_address("88 Canal Rd", []), ("", True))


class PayloadTest(unittest.TestCase):
    def test_shipping_address_goes_on_the_sales_order(self):
        order = {
            "customerId": "100", "date": "11-18-2026", "paymentTermsLabel": "Net 30",
            "paymentTermsDays": "30", "deliveryMethod": "UPS", "shippingAddressId": "3",
            "lines": [{"itemId": "77", "quantity": "1", "rate": "5"}],
        }
        self.assertEqual(sales_order_payload(order, FIELDS)["shipping_address_id"], "3")
        order["shippingAddressId"] = ""
        self.assertNotIn("shipping_address_id", sales_order_payload(order, FIELDS))


if __name__ == "__main__":
    unittest.main()
