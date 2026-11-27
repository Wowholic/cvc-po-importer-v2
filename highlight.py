"""Yellow highlights over the text Gemini used, so a person can check the form quickly.

Gemini returns that text as printed (its "Sources"). The code finds those words in
the PDF's text layer and draws see-through yellow shapes over them. All shapes on
a page are filled as one path, so where they overlap they merge into one shape
instead of getting darker, and neighbouring lines join into one block. A scanned
PDF has no text layer, so it gets no highlights.

Also merges PDFs, for printing the Zoho sales order and the client PO at once.
"""

import io
import logging
import re

import pdfplumber
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, FloatObject, NameObject

logger = logging.getLogger("purchase_desk")

# Points added around each word box. Enough for lines of one block to touch.
PAD_X = 1.5
PAD_Y = 2.0
# Boxes on lines closer than this are joined into one shape.
LINE_GAP = 6.0
# Text found more often than this, such as "1" or "EA", says nothing about where to look.
MAX_REPEATS = 4
# A word this long may be found inside a longer printed word, such as 62600619 in With:62600619*.
PARTIAL_LENGTH = 4
OPACITY = 0.35
STATE = "/CvcHighlight"


def highlight_pdf(pdf_bytes: bytes, sources) -> bytes | None:
    """A copy of the PDF with the sources highlighted, or None when nothing was found."""
    snippets = _snippets(sources)
    if not snippets:
        return None
    try:
        boxes = find_boxes(pdf_bytes, snippets)
        if not any(boxes.values()):
            return None
        return draw_boxes(pdf_bytes, boxes)
    except Exception:
        logger.exception("highlighting the purchase order failed")
        return None


def merge_pdfs(documents: list[bytes]) -> bytes:
    writer = PdfWriter()
    for document in documents:
        writer.append(PdfReader(io.BytesIO(document)))
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def find_boxes(pdf_bytes: bytes, snippets: list[str]) -> dict[int, list[tuple]]:
    """Boxes (x0, top, x1, bottom) per page index, in pdfplumber's top-down coordinates."""
    found = {}
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as document:
        for index, page in enumerate(document.pages):
            if (page.rotation or 0) % 360:
                continue
            words = page.extract_words()
            keys = [_key(word["text"]) for word in words]
            boxes = []
            for snippet in snippets:
                for match in _find(snippet, keys):
                    boxes.extend(_line_boxes([words[position] for position in match]))
            found[index] = boxes + _bridges(boxes)
    return found


def _bridges(boxes: list[tuple]) -> list[tuple]:
    """Fill the gap between boxes on neighbouring lines, so a highlighted block such
    as an address becomes one shape (a polygon when its lines differ in width)."""
    bridges = []
    for upper in boxes:
        for lower in boxes:
            gap = lower[1] - upper[3]
            left, right = max(upper[0], lower[0]), min(upper[2], lower[2])
            if 0 < gap <= LINE_GAP and right > left:
                bridges.append((left, upper[3], right, lower[1]))
    return bridges


def draw_boxes(pdf_bytes: bytes, boxes: dict[int, list[tuple]]) -> bytes:
    reader = PdfReader(io.BytesIO(pdf_bytes))
    if reader.is_encrypted and not reader.decrypt(""):
        raise ValueError("encrypted PDF")
    writer = PdfWriter(clone_from=reader)
    for index, page_boxes in boxes.items():
        if not page_boxes:
            continue
        page = writer.pages[index]
        box = page.mediabox
        left, top = float(box.left), float(box.top)
        path = []
        for x0, y_top, x1, y_bottom in page_boxes:
            x = left + x0 - PAD_X
            y = top - y_bottom - PAD_Y
            width = x1 - x0 + 2 * PAD_X
            height = y_bottom - y_top + 2 * PAD_Y
            path.append(f"{x:.2f} {y:.2f} {width:.2f} {height:.2f} re")
        # One path filled once with the nonzero rule paints the union of all boxes,
        # so overlapping boxes become one shape with one even yellow.
        overlay = f"q {STATE} gs 1 0.85 0 rg\n" + "\n".join(path) + "\nf Q\n"
        _add_state(page)
        original = page.get_contents()
        data = b"q\n" + (original.get_data() if original is not None else b"") + b"\nQ\n" + overlay.encode("ascii")
        stream = DecodedStreamObject()
        stream.set_data(data)
        page[NameObject("/Contents")] = writer._add_object(stream)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def _add_state(page) -> None:
    resources = page.get("/Resources")
    resources = resources.get_object() if resources is not None else DictionaryObject()
    page[NameObject("/Resources")] = resources
    states = resources.get("/ExtGState")
    states = states.get_object() if states is not None else DictionaryObject()
    resources[NameObject("/ExtGState")] = states
    # Multiply keeps the text underneath fully dark and readable.
    states[NameObject(STATE)] = DictionaryObject({
        NameObject("/Type"): NameObject("/ExtGState"),
        NameObject("/ca"): FloatObject(OPACITY),
        NameObject("/BM"): NameObject("/Multiply"),
    })


def _snippets(sources) -> list[str]:
    seen = []
    for source in sources if isinstance(sources, list) else []:
        text = " ".join(str(source or "").split())
        if text and text not in seen and len(re.sub(r"\W", "", text)) >= 2:
            seen.append(text)
    return seen


def _find(snippet: str, keys: list[str]) -> list[list[int]]:
    """Word positions for each place the snippet is printed. Falls back to its comma
    parts when the whole snippet isn't printed in one piece, such as an address
    Gemini put on one line."""
    matches = _find_words([_key(token) for token in snippet.split()], keys)
    if matches:
        return matches
    found = []
    for part in snippet.split(","):
        tokens = [_key(token) for token in part.split()]
        if len(tokens) >= 2 or len("".join(tokens)) >= 5:
            found.extend(_find_words(tokens, keys))
    return found


def _find_words(tokens: list[str], keys: list[str]) -> list[list[int]]:
    tokens = [token for token in tokens if token]
    if not tokens:
        return []
    matches = []
    last = len(tokens) - 1
    position = 0
    while position + last < len(keys):
        if all(_same(tokens[step], keys[position + step], step == 0, step == last) for step in range(len(tokens))):
            matches.append(list(range(position, position + len(tokens))))
            position += len(tokens)
        else:
            position += 1
    return matches if len(matches) <= MAX_REPEATS else []


def _same(token: str, word: str, first: bool, last: bool) -> bool:
    if token == word:
        return True
    if len(token) < PARTIAL_LENGTH:
        return False
    # The first word may be glued to what is printed before it, the last to what follows.
    return (first and word.endswith(token)) or (last and word.startswith(token))


def _line_boxes(words: list[dict]) -> list[tuple]:
    """One box per printed line of a match, from its first word to its last."""
    lines = []
    for word in words:
        line = next((line for line in lines if abs(line[0]["top"] - word["top"]) < 2), None)
        if line is None:
            lines.append([word])
        else:
            line.append(word)
    return [
        (
            min(word["x0"] for word in line),
            min(word["top"] for word in line),
            max(word["x1"] for word in line),
            max(word["bottom"] for word in line),
        )
        for line in lines
    ]


def _key(text: str) -> str:
    """A printed word for comparing: case and punctuation ignored, so Inc. = Inc and 3072, = 3072."""
    folded = str(text or "").casefold()
    stripped = re.sub(r"[^\w]", "", folded)
    return stripped or folded
