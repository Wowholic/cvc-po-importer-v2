import unittest
from unittest import mock

from figures import figure_choices


class FigureMatchTest(unittest.TestCase):
    def test_spacing_uses_the_inventory_figure_without_asking(self):
        catalog = ["123001", "180TSS010"]
        with mock.patch("figures.list_figures", return_value=catalog), \
                mock.patch("figures._ask_gemini") as ask:
            result = figure_choices(["123-001", "180 TSS 010"])
        ask.assert_not_called()
        self.assertEqual(result["matches"]["123-001"], "123001")
        self.assertEqual(result["matches"]["180 TSS 010"], "180TSS010")

    def test_a_model_guess_must_be_on_the_inventory_list(self):
        catalog = ["123001", "214002"]

        def answer(sources, inventory, allowed):
            return {"FIG 214002": "214002", "nope": "999999"}

        with mock.patch("figures.list_figures", return_value=catalog), \
                mock.patch("figures._ask_gemini", side_effect=answer):
            result = figure_choices(["FIG 214002", "nope"])
        self.assertEqual(result["matches"], {"FIG 214002": "214002"})
        self.assertEqual(result["figures"], catalog)
