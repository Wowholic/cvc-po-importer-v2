import unittest

from test_auth import signed_in_client
from zoho_customers import (
    best_item,
    best_match,
    collect_pages,
    configured,
    delivery_methods_from,
    payment_terms_from,
)


class PageFetchTest(unittest.TestCase):
    def test_pages_stop_when_the_list_ends(self):
        def fetch(page):
            return [page], page < 3

        self.assertEqual(collect_pages(fetch, 10), [1, 2, 3])


class CustomerMatchTest(unittest.TestCase):
    def test_legal_suffixes_still_match(self):
        customers = [
            {"id": "1", "name": "Acme Valve Co.", "company": "Acme Valve Company", "status": "active"},
            {"id": "2", "name": "Other Works", "company": "", "status": "active"},
        ]
        match = best_match("Acme Valve Company", customers)
        self.assertEqual(match["id"], "1")

    def test_a_weak_or_tied_name_is_not_chosen(self):
        customers = [
            {"id": "1", "name": "Acme Valve", "company": "", "status": "active"},
            {"id": "2", "name": "Other Works", "company": "", "status": "active"},
        ]
        self.assertIsNone(best_match("Acme", customers))
        self.assertIsNone(best_match("North Pump", customers))
        tied = [
            {"id": "1", "name": "Acme Valve North Works", "company": "", "status": "active"},
            {"id": "2", "name": "Acme Valve North Supply", "company": "", "status": "active"},
        ]
        self.assertIsNone(best_match("Acme Valve North", tied))

    def test_duplicate_records_of_the_same_customer_still_match(self):
        customers = [
            {"id": "1", "name": "SUNBELT SUPPLY", "company": "SUNBELT SUPPLY", "status": "active"},
            {"id": "2", "name": "SUNBELT SUPPLY", "company": "SUNBELT SUPPLY", "status": "active"},
        ]
        match = best_match("Sunbelt Supply Co.", customers)
        self.assertEqual(match["name"], "SUNBELT SUPPLY")


class ItemMatchTest(unittest.TestCase):
    def test_a_figure_inside_a_longer_item_name_is_proposed(self):
        items = [
            {"id": "1", "name": "123010 Bronze Gate Valve", "sku": "CVC-1", "status": "active"},
            {"id": "2", "name": "999999 Other", "sku": "", "status": "active"},
        ]
        self.assertEqual(best_item("123010", items)["id"], "1")

    def test_a_shared_figure_code_is_proposed(self):
        items = [
            {"id": "1", "name": "BRONZE - 906BS001", "sku": "", "status": "active"},
            {"id": "2", "name": "BRONZE - 111AA001", "sku": "", "status": "active"},
        ]
        self.assertEqual(best_item("1LUNKENHEIMER906BS", items)["id"], "1")

    def test_an_exact_code_beats_a_longer_name(self):
        items = [
            {"id": "long", "name": "123010 Bronze Gate Valve", "sku": "", "status": "active"},
            {"id": "exact", "name": "Adapter", "sku": "123-010", "status": "active"},
        ]
        self.assertEqual(best_item("123 010", items)["id"], "exact")

    def test_two_close_items_are_not_chosen(self):
        items = [
            {"id": "1", "name": "123010A", "sku": "", "status": "active"},
            {"id": "2", "name": "123010B", "sku": "", "status": "active"},
        ]
        self.assertIn(best_item("123010", items)["id"], {"1", "2"})


class OrderOptionParseTest(unittest.TestCase):
    def test_payment_terms_keep_the_zoho_label(self):
        body = {"data": {"payment_terms": [
            {"payment_terms_id": "10", "payment_terms": 30, "payment_terms_label": "Net 30"},
            {"payment_terms_id": "", "payment_terms": 0, "payment_terms_label": "Due on Receipt"},
        ]}}
        terms = payment_terms_from(body)
        self.assertEqual(terms[0], {"id": "Due on Receipt", "name": "Due on Receipt", "days": 0})
        self.assertEqual(terms[1], {"id": "10", "name": "Net 30", "days": 30})

    def test_delivery_methods_accept_either_response_shape(self):
        nested = {"data": {"delivery_methods": [
            {"delivery_method_id": "1", "delivery_method": "UPS Ground"},
        ]}}
        flat = {"delivery_methods": ["FedEx", "UPS Ground"]}
        self.assertEqual(delivery_methods_from(nested)[0]["name"], "UPS Ground")
        self.assertEqual([item["name"] for item in delivery_methods_from(flat)], ["FedEx", "UPS Ground"])


class CustomerApiTest(unittest.TestCase):
    def setUp(self):
        self.client = signed_in_client()

    def test_unconfigured_books_is_left_unread(self):
        if configured():
            self.skipTest("Zoho credentials are present")
        response = self.client.get("/api/customers?company=Acme")
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertFalse(body["connected"])
        self.assertEqual(body["customers"], [])
        self.assertIsNone(body["match"])
