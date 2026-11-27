"""Lines from real purchase orders, matched against the saved catalog (cache/catalog.json).

Add a row whenever a PDF is matched wrong, with what Gemini read and the right answer.
Skipped when there is no saved catalog (run Sync first).
"""

import json
import unittest
from pathlib import Path

from resolve import hinted_figure, match_item

CATALOG = Path(__file__).resolve().parent / "cache" / "catalog.json"

# (source PDF, FIG# as read, description as read, expected Zoho item; None = left for the person)
CASES = [
    ("Motion IA04-00413265", "1837", "1837 - 1/2 BRZ QO VALVE LUNK 150 MFG HAS STOCK", "BRONZE - 1837005"),
    ("Applied 4515804109", 'MSP_18374X1/4"', "", "BRONZE - 1837002"),
    ("Applied 4515804109, FIG# shortened", "1837", 'MSP_18374X1/4"', "BRONZE - 1837002"),
    ("Vierk 21472-01 line 1", "LV-123-2", "123 GLOBE VALVE, 2in.", "BRONZE - 123020"),
    ("Vierk 21472-01 line 2", "LV-180-2", "BRZ. Y BLOW-OFF VALVE, 2in.", "BRONZE - 180020"),
    ("Vistra 2845966", "T-811X", "GASKET,BONNET;14 IN;FIGURE 3013;LUNKENHEIMER;CR13", "T-811X"),
    ("906BS with a size", "1LUNKENHEIMER906BS", '1" BRZ GATE VALVE', "BRONZE - 906BS010"),
    ("Plain figure, nothing else", "1837", "", "1837"),
    ("Two items share 180020, no material", "LV-180-2", "Y BLOW-OFF VALVE, 2in.", None),
]

# What Gemini picks from the inventory lists, then the expected item and spreadsheet figure.
# (source PDF, FIG# as read, description, family, size, material, expected Zoho item, expected figure)
HINTED_CASES = [
    ("Affiliated 1121094", "LHSWCV300300-3072",
     "Lunkenheimer Swing Check Valve Fig. 3072, 3-Inch CL 300 RF Flanged Connections, Cast Carbon Steel Body",
     "3072", '3"', "STEEL", "STEEL - 3072030", "3072030"),
    ("Motion IA04-00413265", "1837", "1837 - 1/2 BRZ QO VALVE", "1837", '1/2"', "BRONZE", "BRONZE - 1837005", "1837005"),
    ("Applied 4515804109", 'MSP_18374X1/4"', "", "1837", '1/4"', "", "BRONZE - 1837002", "1837002"),
    ("Vierk 21472-01 line 2", "LV-180-2", "BRZ. Y BLOW-OFF VALVE, 2in.", "180", '2"', "BRONZE", "BRONZE - 180020", "180020"),
    ("Family but no size", "LHSWCV300300-3072", "Swing Check Valve", "3072", "", "STEEL", None, ""),
]

@unittest.skipUnless(CATALOG.exists(), "no saved catalog")
class RealOrderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
        cls.items = catalog.get("items") or []
        cls.sizes = catalog.get("figureSizes") or {}

    def test_lines(self):
        for source, figure, description, expected in CASES:
            with self.subTest(source):
                chosen, _options = match_item(figure, description, self.items, self.sizes)
                self.assertEqual(chosen["name"] if chosen else None, expected)

    def test_lines_with_gemini_picks(self):
        for source, figure, description, family, size, material, item, inventory in HINTED_CASES:
            with self.subTest(source):
                chosen, options = match_item(figure, description, self.items, self.sizes, family, size, material)
                self.assertEqual(chosen["name"] if chosen else None, item)
                if item is None:
                    self.assertTrue(options)
                self.assertEqual(hinted_figure(family, size, self.sizes), inventory)


if __name__ == "__main__":
    unittest.main()
