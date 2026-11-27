import io
import unittest

from pypdf import PdfReader

from extract_po import parse_reply
from highlight import find_boxes, highlight_pdf, merge_pdfs


def make_pdf(lines: list[str]) -> bytes:
    """A one-page PDF with each line of text 14 points under the last."""
    text = "".join(
        f"BT /F1 10 Tf 72 {720 - 14 * index} Td ({line}) Tj ET\n" for index, line in enumerate(lines)
    ).encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(text)).encode() + b" >>\nstream\n" + text + b"endstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    output = b"%PDF-1.4\n"
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(output))
        output += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    start = len(output)
    output += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    output += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets)
    output += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n".encode()
    return output


PO = make_pdf([
    "Acme Supply Co.",
    "823 Communication Drive",
    "Appleton, WI 54914",
    "Mark Packages With:62600619",
    "1 EA 1 EA 1 EA 1 EA 1 EA",
])


class FindTest(unittest.TestCase):
    def boxes(self, sources):
        return find_boxes(PO, sources)[0]

    def test_text_is_found_ignoring_case_and_punctuation(self):
        self.assertEqual(len(self.boxes(["acme supply co"])), 1)

    def test_lines_of_a_block_are_joined(self):
        # Two line boxes and the bridge that joins them into one shape.
        self.assertEqual(len(self.boxes(["823 Communication Drive", "Appleton, WI 54914"])), 3)

    def test_a_number_glued_to_a_label_is_found(self):
        self.assertEqual(len(self.boxes(["62600619"])), 1)

    def test_an_address_on_one_line_is_found_by_its_parts(self):
        self.assertEqual(len(self.boxes(["823 Communication Drive, Appleton, WI 54914"])), 3)

    def test_text_printed_too_often_is_left_alone(self):
        self.assertEqual(self.boxes(["EA"]), [])

    def test_text_not_printed_is_left_alone(self):
        self.assertEqual(self.boxes(["Nowhere Street"]), [])


class HighlightTest(unittest.TestCase):
    def test_highlighted_copy_keeps_the_text(self):
        marked = highlight_pdf(PO, ["Acme Supply Co."])
        page = PdfReader(io.BytesIO(marked)).pages[0]
        self.assertIn(b"/CvcHighlight gs", page.get_contents().get_data())
        self.assertIn("Acme Supply Co.", page.extract_text())

    def test_nothing_to_highlight(self):
        self.assertIsNone(highlight_pdf(PO, []))
        self.assertIsNone(highlight_pdf(PO, ["Nowhere Street"]))
        with self.assertLogs("purchase_desk", "ERROR"):
            self.assertIsNone(highlight_pdf(b"not a pdf", ["Acme"]))

    def test_merge_keeps_every_page_in_order(self):
        merged = PdfReader(io.BytesIO(merge_pdfs([make_pdf(["first"]), PO])))
        self.assertEqual(len(merged.pages), 2)
        self.assertIn("first", merged.pages[0].extract_text())


class SourcesTest(unittest.TestCase):
    def test_sources_are_collected_once_and_kept_off_the_rows(self):
        rows, sources = parse_reply(
            '[{"Company": "Acme", "Sources": ["Acme Supply Co.", "1837"]},'
            ' {"Company": "Acme", "Sources": ["1837", "  2  EA "]}]'
        )
        self.assertEqual(sources, ["Acme Supply Co.", "1837", "2 EA"])
        self.assertNotIn("Sources", rows[0])


if __name__ == "__main__":
    unittest.main()
