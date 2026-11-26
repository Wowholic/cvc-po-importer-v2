import unittest
from datetime import date
from fractions import Fraction

from extract_po import usage_record
from resolve import family_figure, match_item, pdf_size, pdf_sizes, resolve_order, size_value

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


SIZES_906BS = [("001", '1/8"'), ("002", '1/4"'), ("004", '3/8"'), ("005", '1/2"'), ("007", '3/4"'), ("010", '1"')]
FAMILY = [
    {"id": f"s{suffix}", "name": f"BRONZE - 906BS{suffix}", "sku": "", "status": "active"}
    for suffix, _size in SIZES_906BS
] + [{"id": "stem", "name": 'STEM FOR 1/2" 906BS', "sku": "", "status": "active"}]
SIZES = {f"906BS{suffix}": size for suffix, size in SIZES_906BS}


class SizeMatchTest(unittest.TestCase):
    def test_the_description_size_picks_one_item_of_a_family(self):
        chosen, pending = match_item(
            "1LUNKENHEIMER906BS", "1 400# BRZ THR NEEDLE VL #906 BS", FAMILY, SIZES,
        )
        self.assertEqual(chosen["name"], "BRONZE - 906BS010")
        self.assertEqual(pending, [])

    def test_an_inch_mark_anywhere_in_the_description_counts(self):
        chosen, _pending = match_item("906BS", "BRZ NEEDLE VALVE 3/4\" NPT", FAMILY, SIZES)
        self.assertEqual(chosen["name"], "BRONZE - 906BS007")

    def test_no_size_in_the_description_leaves_it_for_gemini(self):
        chosen, pending = match_item("906BS", "BRZ NEEDLE VALVE", FAMILY, SIZES)
        self.assertIsNone(chosen)
        self.assertTrue(pending)

    def test_a_size_no_candidate_has_leaves_it_for_gemini(self):
        chosen, pending = match_item("906BS", '2" BRZ NEEDLE VALVE', FAMILY, SIZES)
        self.assertIsNone(chosen)
        self.assertTrue(pending)

    def test_two_candidates_with_that_size_leave_it_for_gemini(self):
        sizes = dict(SIZES, **{"906BS007": '1"'})
        chosen, pending = match_item("906BS", '1" BRZ NEEDLE VALVE', FAMILY, sizes)
        self.assertIsNone(chosen)
        self.assertTrue(pending)

    def test_without_the_size_column_nothing_changes(self):
        chosen, pending = match_item("906BS", '1" BRZ NEEDLE VALVE', FAMILY, {})
        self.assertIsNone(chosen)
        self.assertTrue(pending)

    def test_size_spellings(self):
        for text in ['1-1/2"', "1 1/2", '1.5"', "1½", "1-1/2 IN"]:
            self.assertEqual(size_value(text), Fraction(3, 2), text)
        self.assertIsNone(size_value("E"))
        self.assertIsNone(pdf_size("150# BRONZE GATE"))

    def test_resolve_does_not_ask_gemini_for_a_sized_family(self):
        rows = [{"FIG#": "1LUNKENHEIMER906BS", "Description": "1 400# BRZ THR NEEDLE VL #906 BS"}]
        catalog = {"items": FAMILY, "figureSizes": SIZES}

        def ask(_pending):
            raise AssertionError("Gemini should not be asked")

        result = resolve_order(rows, catalog, ask)
        self.assertEqual(result["items"]["1LUNKENHEIMER906BS"]["name"], "BRONZE - 906BS010")


FAMILY_1837 = [
    {"id": "f1837", "name": "1837", "sku": "", "status": "active"},
    {"id": "f40", "name": "BRONZE - 1837-40-005", "sku": "", "status": "active"},
    {"id": "b002", "name": "BRONZE - 1837002", "sku": "", "status": "active"},
    {"id": "b004", "name": "BRONZE - 1837004", "sku": "", "status": "active"},
    {"id": "b015", "name": "BRONZE - 1837015", "sku": "", "status": "active"},
]
SIZES_1837 = {"1837002": '1/4"', "1837004": '3/8"', "1837015": '1-1/2"', "906BS002": '1/4"'}


class FamilySizeTest(unittest.TestCase):
    def test_a_size_in_the_figure_finds_the_inventory_row_and_its_zoho_item(self):
        chosen, pending = match_item("MSP_18374X1/4", "", FAMILY_1837, SIZES_1837)
        self.assertEqual(chosen["name"], "BRONZE - 1837002")
        self.assertEqual(pending, [])

    def test_a_mixed_fraction_in_the_figure(self):
        chosen, _pending = match_item("MSP_1837X1-1/2", "", FAMILY_1837, SIZES_1837)
        self.assertEqual(chosen["name"], "BRONZE - 1837015")

    def test_the_size_can_come_from_the_description(self):
        self.assertEqual(family_figure("MSP_1837", '3/8" GLOBE', SIZES_1837), "1837004")

    def test_a_family_right_after_another_digit_does_not_count(self):
        self.assertEqual(family_figure("MSP_91837X1/4", "", SIZES_1837), "")

    def test_the_figure_repeated_in_the_description_is_not_read_as_a_size(self):
        # Motion PO IA04-00413265: FIG# 1837, description "1837 - 1/2 BRZ QO VALVE LUNK 150".
        description = "1837 - 1/2 BRZ QO VALVE LUNK 150 MFG HAS STOCK"
        self.assertEqual(pdf_sizes("1837", description), {Fraction(1, 2)})
        sizes = {**SIZES_1837, "1837005": '1/2"'}
        items = FAMILY_1837 + [{"id": "b005", "name": "BRONZE - 1837005", "sku": "", "status": "active"}]
        chosen, pending = match_item("1837", description, items, sizes)
        self.assertEqual(chosen["name"], "BRONZE - 1837005")
        self.assertEqual(pending, [])

    def test_an_all_digit_figure_still_matches_by_part_number(self):
        items = [
            {"id": "x", "name": "NON LUNK DESIGN - 15011B040", "sku": "", "status": "active"},
            {"id": "b002", "name": "BRONZE - 1837002", "sku": "", "status": "active"},
        ]
        chosen, _pending = match_item("1837", "BRZ QO VALVE LUNK 150", items, {})
        self.assertEqual(chosen["name"], "BRONZE - 1837002")

    def test_without_a_size_or_material_the_item_named_exactly_like_the_figure_wins(self):
        chosen, pending = match_item("1837", "QO VALVE LUNK 150", FAMILY_1837, SIZES_1837)
        self.assertEqual(chosen["name"], "1837")
        self.assertEqual(pending, [])

    def test_a_material_in_the_description_beats_the_exact_item(self):
        for description in ("BRZ QO VALVE", "BZ QO VALVE", "BRONZE QO VALVE"):
            chosen, _pending = match_item("1837", description, FAMILY_1837, SIZES_1837)
            self.assertTrue(chosen["name"].startswith("BRONZE - 1837"), description)

    def test_a_size_inside_a_part_code_in_the_description_counts(self):
        # Applied PO 4515804109: FIG# shortened to 1837, the full MSP_18374X1/4" left in the description.
        self.assertEqual(pdf_sizes("1837", 'MSP_18374X1/4"'), {Fraction(1, 4)})
        chosen, _pending = match_item("1837", 'MSP_18374X1/4"', FAMILY_1837, SIZES_1837)
        self.assertEqual(chosen["name"], "BRONZE - 1837002")

    def test_no_size_means_no_family_match(self):
        self.assertEqual(family_figure("MSP_1837", "", SIZES_1837), "")

    def test_bare_digits_in_the_figure_are_not_a_size(self):
        self.assertEqual(pdf_sizes("1LUNKENHEIMER906BS", ""), set())
        self.assertEqual(pdf_sizes("MSP_18374X1/4", ""), {Fraction(1, 4)})

    def test_resolve_fills_both_figures_without_gemini(self):
        rows = [{"FIG#": "MSP_18374X1/4", "Description": ""}]
        catalog = {
            "items": FAMILY_1837,
            "figures": ["1837002", "1837004", "1837015"],
            "figureSizes": SIZES_1837,
        }

        def ask(_pending):
            raise AssertionError("Gemini should not be asked")

        result = resolve_order(rows, catalog, ask)
        self.assertEqual(result["figures"], {"MSP_18374X1/4": "1837002"})
        self.assertEqual(result["items"]["MSP_18374X1/4"]["name"], "BRONZE - 1837002")


class FigureFirstTest(unittest.TestCase):
    def test_a_description_code_does_not_outrank_the_fig_number(self):
        items = [
            {"id": f"cr{n}", "name": f"STEEL - 1111{n:03d}-CR13-16.00", "sku": "", "status": "active"}
            for n in range(12)
        ] + [{"id": "t", "name": "T-811X", "sku": "", "status": "active"}]
        chosen, pending = match_item(
            "T-811X", "GASKET,BONNET;14 IN;FIGURE 3013;LUNKENHEIMER;CR13", items,
        )
        self.assertEqual(chosen["name"], "T-811X")
        self.assertEqual(pending, [])


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
