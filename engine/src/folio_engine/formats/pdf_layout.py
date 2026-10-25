"""Layout fixes patched into BabelDOC for tables of contents, headings and links.

BabelDOC reflows each paragraph inside its original box, which goes wrong for
text that is really a list of separate lines:

- consecutive TOC entries are merged into one paragraph and reflowed, so a
  column of chapter names comes out as one tiny run-on line, or a word per line;
- link underlines and TOC leader rules are vector lines that stay where the
  original text was, so they strike through the translation;
- a heading or entry that grows in translation is shrunk or wrapped inside the
  original word's box instead of using the free space beside it.
"""

import re
import statistics
from pathlib import Path

# Book-wide body text width per run, keyed by id(TranslationConfig).
BODY_WIDTH: dict[int, float] = {}

_PAGE_REF_RE = re.compile(r"\w.*?(?:\s|\.{2,}|…|·)\s*\d{1,4}$")
_NO_BREAK_END = "-‐‑,;:"
_installed = False


def spread_pages(n: int, count: int) -> list[int]:
    """Up to `count` page indexes spread evenly through an `n`-page document."""
    k = min(count, n)
    return sorted({int(i * n / k) for i in range(k)})


def body_width(path: Path) -> float:
    """Median width of the text lines in ~30 pages spread through the book."""
    import pymupdf

    widths = []
    with pymupdf.open(path) as doc:
        for i in spread_pages(doc.page_count, 30):
            for block in doc[i].get_text("dict")["blocks"]:
                for line in block.get("lines", []):
                    text = "".join(s["text"] for s in line["spans"])
                    if len(text.split()) >= 3:
                        widths.append(line["bbox"][2] - line["bbox"][0])
    return statistics.median(widths) if widths else 0.0


def _text(line) -> str:
    return "".join(c.char_unicode for c in line.pdf_character).strip()


def breaks_before(prev: str, nxt: str, prev_box, next_box, body: float) -> bool:
    """Whether two lines of one BabelDOC paragraph are really separate entries."""
    if not prev or not nxt or prev[-1] in _NO_BREAK_END:
        return False
    first = next((c for c in nxt if c.isalpha()), "")
    if first.islower():  # a sentence running on
        return False
    if _PAGE_REF_RE.search(prev):  # "Introduction ...... 11"
        return True
    width = prev_box.x2 - prev_box.x
    if not body or width >= 0.6 * body:
        return False
    # A centred title wrapped over two lines stays one paragraph.
    centred = (abs((prev_box.x + prev_box.x2) - (next_box.x + next_box.x2)) < 4
               and abs(prev_box.x - next_box.x) > 2)
    return not centred


def _split_entries(finder, paragraphs: list, body: float) -> None:
    from babeldoc.format.pdf.document_il.il_version_1 import Box, PdfParagraph
    from babeldoc.format.pdf.document_il.midend.paragraph_finder import (
        generate_base58_id,
    )

    i = 0
    while i < len(paragraphs):
        para = paragraphs[i]
        comps = para.pdf_paragraph_composition
        if len(comps) > 1 and all(c.pdf_line for c in comps):
            for j in range(1, len(comps)):
                a, b = comps[j - 1].pdf_line, comps[j].pdf_line
                if breaks_before(_text(a), _text(b), a.box, b.box, body):
                    rest = PdfParagraph(
                        box=Box(0, 0, 0, 0), pdf_paragraph_composition=comps[j:], unicode="",
                        debug_id=generate_base58_id(), layout_label=para.layout_label, layout_id=para.layout_id,
                    )
                    para.pdf_paragraph_composition = comps[:j]
                    finder.update_paragraph_data(para)
                    finder.update_paragraph_data(rest)
                    paragraphs.insert(i + 1, rest)
                    break
        i += 1


def page_number_split(chars: list[str], xs: list[tuple[float, float]], height: float) -> int | None:
    """Index where a right-hand page number starts ("Title ...... 12", "Title      12"), if any."""
    end = len(chars)
    while end and chars[end - 1].isspace():
        end -= 1
    start = end
    while start and chars[start - 1].isdigit():
        start -= 1
    if start == end or end - start > 4:
        return None
    prev = start - 1
    while prev >= 0 and chars[prev].isspace():
        prev -= 1
    if prev < 0 or not any(c.isalpha() for c in chars[:prev + 1]):
        return None
    if chars[prev] in ".·…" or xs[start][0] - xs[prev][1] > 1.5 * height:
        return start
    return None


def _detach_page_numbers(finder, paragraphs: list) -> None:
    """Keep TOC page numbers in their column: the title then grows up to them, not over them."""
    from babeldoc.format.pdf.document_il.il_version_1 import Box, PdfParagraph
    from babeldoc.format.pdf.document_il.midend.paragraph_finder import (
        generate_base58_id,
    )

    i = 0
    while i < len(paragraphs):
        para = paragraphs[i]
        comps = para.pdf_paragraph_composition
        if len(comps) == 1 and comps[0].pdf_line:
            chars = comps[0].pdf_line.pdf_character
            text = [c.char_unicode for c in chars]
            boxes = [(c.box.x, c.box.x2) for c in chars]
            height = max(c.box.y2 - c.box.y for c in chars)
            cut = page_number_split(text, boxes, height)
            if cut is not None:
                title = [c for c in chars[:cut]]
                while title and title[-1].char_unicode.isspace():
                    title.pop()
                number = [c for c in chars[cut:] if not c.char_unicode.isspace()]
                para.pdf_paragraph_composition = [finder.create_line(title)]
                finder.update_paragraph_data(para)
                num = PdfParagraph(
                    box=Box(0, 0, 0, 0), pdf_paragraph_composition=[finder.create_line(number)], unicode="",
                    debug_id=generate_base58_id(), layout_label=para.layout_label, layout_id=para.layout_id,
                )
                finder.update_paragraph_data(num)
                paragraphs.insert(i + 1, num)
                i += 1
        i += 1


def is_text_rule(box, line_boxes) -> bool:
    """A thin horizontal line on or just under a line of text: an underline or leader."""
    if box.y2 - box.y > 1.5 or box.x2 - box.x < 2:
        return False
    for lb in line_boxes:
        h = lb.y2 - lb.y
        if lb.y - 0.3 * h <= box.y <= lb.y2 and box.x < lb.x2 and box.x2 > lb.x:
            return True
    return False


def _drop_text_rules(page) -> None:
    """Remove underlines and leaders: they cannot follow the translated text."""
    line_boxes = [c.pdf_line.box for p in page.pdf_paragraph
                  if "table" not in (p.layout_label or "") and "figure" not in (p.layout_label or "")
                  for c in p.pdf_paragraph_composition if c.pdf_line]
    if not line_boxes:
        return
    page.pdf_curve = [c for c in page.pdf_curve if not (c.box and is_text_rule(c.box, line_boxes))]
    page.pdf_rectangle = [r for r in page.pdf_rectangle
                          if r.debug_info or not (r.box and is_text_rule(r.box, line_boxes))]


def _single_line(paragraph, units) -> bool:
    glyphs = [u for u in units if not u.formular]
    box = paragraph.box
    return bool(box and glyphs) and box.y2 - box.y <= 1.7 * max(u.height for u in glyphs)


def _fit_single_line(typesetter, paragraph, page, units) -> None:
    """Give a one-line heading or entry the room it needs before BabelDOC shrinks it."""
    box = paragraph.box
    if not _single_line(paragraph, units):
        return
    need = sum(u.width for u in units) * 1.03 + 1
    crop = page.cropbox.box
    page_w = crop.x2 - crop.x
    centre, page_centre = (box.x + box.x2) / 2, (crop.x + crop.x2) / 2
    # Centred: near the page centre, narrow, and not one of a column of left-aligned items.
    aligned = any(p.box is not box and p.box and abs(p.box.x - box.x) < 1.5 for p in page.pdf_paragraph)
    if abs(centre - page_centre) < 0.04 * page_w and box.x2 - box.x < 0.5 * page_w and not aligned:
        half = min(need, 0.84 * page_w) / 2  # centred: grow (or shrink) around the centre
        paragraph.box = type(box)(page_centre - half, box.y, page_centre + half, box.y2)
    elif need > box.x2 - box.x:
        right = min(box.x + need, typesetter.get_max_right_space(box, page) - 5)
        if right > box.x2:
            paragraph.box = type(box)(box.x, box.y, right, box.y2)


def _unit_text(u) -> str:
    return (u.char.char_unicode if u.char else u.unicode) or ""


def trim_leaders(units: list, width: float) -> None:
    """Drop dots from the longest leader run ("Title ........ 12") until the line fits `width`."""
    excess = sum(u.width for u in units) * 1.03 + 1 - width
    if excess <= 0:
        return
    best, start = (0, 0), None
    for i, u in enumerate([*units, None]):
        if u is not None and _unit_text(u) in ".·…":
            start = i if start is None else start
        elif start is not None:
            best = max(best, (i - start, start))
            start = None
    run, at = best
    if run < 4:
        return
    dot = units[at].width or 1
    drop = min(run - 3, int(excess / dot) + 1)
    del units[at:at + drop]


def install() -> None:
    global _installed
    if _installed:
        return
    from babeldoc.format.pdf.document_il.midend.paragraph_finder import ParagraphFinder
    from babeldoc.format.pdf.document_il.midend.typesetting import Typesetting

    orig_independent = ParagraphFinder.process_independent_paragraphs
    orig_merge = ParagraphFinder.merge_alternating_line_number_paragraphs
    orig_page = ParagraphFinder.process_page
    orig_fit = Typesetting._find_optimal_scale_and_layout

    def process_independent_paragraphs(self, paragraphs, median_width):
        _split_entries(self, paragraphs, BODY_WIDTH.get(id(self.translation_config), 0.0))
        return orig_independent(self, paragraphs, median_width)

    def merge_alternating_line_number_paragraphs(self, paragraphs):
        # After this merge, which would otherwise glue "title, number, title" back together.
        orig_merge(self, paragraphs)
        _detach_page_numbers(self, paragraphs)

    def process_page(self, page):
        orig_page(self, page)
        _drop_text_rules(page)

    def find_optimal_scale_and_layout(self, paragraph, page, typesetting_units, *args, **kwargs):
        # Runs twice per paragraph (scale estimate, then layout); widen only once.
        fitted = self.__dict__.setdefault("_folio_fitted", set())  # IL objects have __slots__
        if typesetting_units and id(paragraph) not in fitted:
            fitted.add(id(paragraph))
            _fit_single_line(self, paragraph, page, typesetting_units)
        if typesetting_units and _single_line(paragraph, typesetting_units):  # units are rebuilt per call
            trim_leaders(typesetting_units, paragraph.box.x2 - paragraph.box.x)
        return orig_fit(self, paragraph, page, typesetting_units, *args, **kwargs)

    ParagraphFinder.process_independent_paragraphs = process_independent_paragraphs
    ParagraphFinder.merge_alternating_line_number_paragraphs = merge_alternating_line_number_paragraphs
    ParagraphFinder.process_page = process_page
    Typesetting._find_optimal_scale_and_layout = find_optimal_scale_and_layout
    _installed = True
