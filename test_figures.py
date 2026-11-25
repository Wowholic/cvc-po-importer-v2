import unittest
from unittest import mock

from figures import _read_figures, figure_choices


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


class ReadFiguresTest(unittest.TestCase):
    def test_reads_the_size_beside_each_figure(self):
        values = [
            ["SIZE", "Fig. No. QB"],
            ['1/8"', "906BS001"],
            ['1"', "906-BS-010"],
            ["", "180TSS010"],
            ['1/2"', "214002"],
            ['3/4"', "214 002"],
        ]
        sheets = mock.MagicMock()
        sheets.spreadsheets.return_value.values.return_value.get.return_value.execute.return_value = {
            "values": values,
        }
        with mock.patch("googleapiclient.discovery.build", return_value=sheets), \
                mock.patch("figures.credentials"):
            figures, sizes = _read_figures()
        self.assertEqual(figures, ["906BS001", "906-BS-010", "180TSS010", "214002"])
        self.assertEqual(sizes, {"906BS001": '1/8"', "906BS010": '1"'})
