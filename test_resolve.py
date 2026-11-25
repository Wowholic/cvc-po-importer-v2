import unittest
from datetime import date

from extract_po import usage_record
from resolve import match_item, resolve_order

BRONZE = {"id": "1", "name": "BRONZE - 906BS001", "sku": "", "status": "active"}
BRASS = {"id": "2", "name": "BRASS GATE 12", "sku": "", "status": "active"}
OTHER = {"id": "3", "name": "BRONZE - 906BS002", "sku": "", "status": "active"}


class ItemMatchTest(unittest.TestCase):
    def test_a_code_inside_a_longer_name_matches_with_the_description(self):
        chosen, pending = match_item(
            "1LUNKENHEIMER906BS",
            "1 400# BRZ THR NEEDLE VL #906 BS",
            [BRONZE, BRASS],
        )
        self.assertEqual(chosen["id"], "1")
        self.assertEqual(pending, [])

    def test_the_same_code_matches_without_a_description(self):
        chosen, pending = match_item("1LUNKENHEIMER906BS", "", [BRONZE, BRASS])
        self.assertEqual(chosen["id"], "1")
        self.assertEqual(pending, [])

    def test_two_close_items_are_left_for_gemini(self):
        chosen, pending = match_item("1LUNKENHEIMER906BS", "", [BRONZE, OTHER])
        self.assertIsNone(chosen)
        self.assertEqual([item["id"] for item in pending], ["1", "3"])


class ResolveTest(unittest.TestCase):
    def test_a_clear_order_does_not_ask_gemini(self):
        catalog = {
            "customers": [{
                "id": "c1",
                "name": "Sunbelt Supply",
                "company": "Sunbelt Supply",
                "status": "active",
            }],
            "items": [BRONZE, BRASS],
            "paymentTerms": [{"id": "t", "name": "Net 30", "days": 30}],
            "deliveryMethods": [{"id": "d", "name": "Best Way"}],
            "figures": ["906BS"],
        }
        rows = [{
            "Company": "Sunbelt Supply",
            "FIG#": "1LUNKENHEIMER906BS",
            "Description": "1 400# BRZ THR NEEDLE VL #906 BS",
            "Payment Terms": "Net 30",
            "Delivery Method": "Best Way",
        }]
        calls = {"count": 0}

        def ask(_pending):
            calls["count"] += 1
            return {}

        result = resolve_order(rows, catalog, ask)
        self.assertEqual(calls["count"], 0)
        self.assertEqual(result["items"]["1LUNKENHEIMER906BS"]["id"], "1")
        self.assertEqual(result["figures"]["1LUNKENHEIMER906BS"], "906BS")
        self.assertEqual(result["company"]["id"], "c1")
        self.assertEqual(result["paymentTerms"]["id"], "t")
        self.assertEqual(result["deliveryMethod"]["id"], "d")

    def test_a_misspelled_company_is_proposed_without_asking(self):
        catalog = {
            "customers": [
                {"id": "c1", "name": "APPLIED IND. CANADA", "company": "APPLIED INDUSTRIAL TECHONOLOGIES, LP"},
                {"id": "c2", "name": "APPLIED INDUSTRIAL TECHONOLOGIES", "company": "APPLIED INDUSTRIAL TECHONOLOGIES"},
            ],
            "items": [BRONZE],
            "paymentTerms": [],
            "deliveryMethods": [],
            "figures": [],
        }
        rows = [{"Company": "Applied Industrial Technologies", "FIG#": "1LUNKENHEIMER906BS"}]

        def ask(_pending):
            raise AssertionError("a clear company should not be sent to Gemini")

        result = resolve_order(rows, catalog, ask)
        self.assertEqual(result["company"]["id"], "c2")
        self.assertEqual(result["items"]["1LUNKENHEIMER906BS"]["id"], "1")

    def test_a_close_item_still_gets_a_proposal_when_gemini_returns_nothing(self):
        catalog = {
            "customers": [{"id": "c1", "name": "Sunbelt Supply", "company": "Sunbelt Supply"}],
            "items": [BRONZE, OTHER],
            "paymentTerms": [],
            "deliveryMethods": [],
            "figures": [],
        }
        rows = [{"Company": "Sunbelt Supply", "FIG#": "1LUNKENHEIMER906BS"}]
        result = resolve_order(rows, catalog, lambda _pending: {})
        self.assertEqual(result["items"]["1LUNKENHEIMER906BS"]["id"], "1")

    def test_no_local_company_is_sent_to_gemini_and_then_used(self):
        catalog = {
            "customers": [
                {"id": "c1", "name": "North Pump", "company": "North Pump"},
                {"id": "c2", "name": "Other Works", "company": "Other Works"},
            ],
            "items": [],
            "paymentTerms": [],
            "deliveryMethods": [],
            "figures": [],
        }
        rows = [{"Company": "Totally Different"}]
        seen = {}

        def ask(pending):
            seen["company"] = pending["company"]["source"]
            return {"company": "c2"}

        result = resolve_order(rows, catalog, ask)
        self.assertEqual(seen["company"], "Totally Different")
        self.assertEqual(result["company"]["id"], "c2")


class GeminiPriceTest(unittest.TestCase):
    def test_flash_tokens_use_the_current_paid_rate(self):
        record = usage_record("read PDF", {
            "modelVersion": "gemini-3.8-flash",
            "usageMetadata": {
                "promptTokenCount": 1_000_000,
                "candidatesTokenCount": 1_000,
                "thoughtsTokenCount": 1_000,
            },
        }, date(2026, 11, 24))
        self.assertEqual(record["input"], 1_000_000)
        self.assertEqual(record["output"], 2_000)
        self.assertEqual(record["usd"], 0.7575)
        self.assertIn("$0.7575", record["line"])
        self.assertIn("1,000 thinking", record["line"])

    def test_the_flash_rate_steps_up_next_year(self):
        record = usage_record("read PDF", {
            "modelVersion": "gemini-3.8-flash",
            "usageMetadata": {"promptTokenCount": 1_000_000, "candidatesTokenCount": 0},
        }, date(2027, 1, 1))
        self.assertEqual(record["usd"], 1.5)

    def test_an_unknown_model_keeps_the_tokens_and_skips_the_price(self):
        record = usage_record("read PDF", {
            "modelVersion": "gemini-unknown",
            "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 2},
        }, date(2026, 11, 24))
        self.assertIsNone(record["usd"])
        self.assertIn("price unknown", record["line"])
