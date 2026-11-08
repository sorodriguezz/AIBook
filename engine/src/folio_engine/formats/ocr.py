"""Translation of scanned pages: OCR → paragraphs → translate → repaint.

Scanned pages have no text layer, so the original lettering is painted over
with the surrounding background colour and the translation is typeset into
the same box, shrinking the font only as much as needed to fit.
"""

import html
from dataclasses import dataclass
from functools import lru_cache

OCR_DPI = 300


@dataclass
class Line:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str

    @property
    def height(self) -> float:
        return self.y1 - self.y0


@dataclass
class Block:
    lines: list[Line]

    @property
    def rect(self) -> tuple[float, float, float, float]:
        return (min(l.x0 for l in self.lines), min(l.y0 for l in self.lines),
                max(l.x1 for l in self.lines), max(l.y1 for l in self.lines))

    def text(self, cjk: bool) -> str:
        joiner = "" if cjk else " "
        out = ""
        for ln in self.lines:
            t = ln.text.strip()
            if not cjk and out.endswith("-") and t[:1].islower():
                out = out[:-1] + t  # re-join hyphenated words
            else:
                out = (out + joiner + t) if out else t
        return out


@lru_cache(maxsize=4)
def _engine(src: str):
    from rapidocr import RapidOCR

    # The bundled PP-OCRv6 model takes individual language codes, including
    # Spanish and French. The old "latin" model family is specific to v5.
    return RapidOCR(params={"Rec.lang_type": {"ja": "japan", "zh": "ch"}.get(src, src)})


def release_models() -> None:
    _engine.cache_clear()


def is_scanned(page, text: str) -> bool:
    text = text.strip()
    if len(text) >= 25:
        return False
    images = page.get_image_info()
    if not images:
        return False
    if not text:
        return True
    # Mostly covered by an image and no real text layer.
    import pymupdf

    bounds = page.rect * page.derotation_matrix
    return any((pymupdf.Rect(image["bbox"]) & bounds).get_area() > bounds.get_area() * 0.5
               for image in images)


def ocr_page(page, src: str) -> list[Line]:
    import numpy as np
    import pymupdf

    pix = page.get_pixmap(dpi=OCR_DPI, colorspace=pymupdf.csRGB)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    result = _engine(src)(img)
    scale = 72 / OCR_DPI
    lines: list[Line] = []
    if result.boxes is None:
        return lines
    for box, txt, score in zip(result.boxes, result.txts, result.scores):
        if score < 0.5 or not txt.strip():
            continue
        xs = [float(p[0]) for p in box]
        ys = [float(p[1]) for p in box]
        lines.append(Line(min(xs) * scale, min(ys) * scale, max(xs) * scale, max(ys) * scale, txt))
    return lines


def group_blocks(lines: list[Line]) -> list[Block]:
    """Merge OCR lines into paragraphs by vertical proximity and left alignment."""
    lines = sorted(lines, key=lambda l: (round(l.y0 / 4), l.x0))
    blocks: list[Block] = []
    for ln in lines:
        target = None
        for b in reversed(blocks[-4:]):
            last = b.lines[-1]
            gap = ln.y0 - last.y1
            similar_size = abs(ln.height - last.height) < 0.35 * max(ln.height, last.height)
            overlaps_x = ln.x0 < last.x1 and ln.x1 > last.x0
            aligned = abs(ln.x0 - last.x0) < 1.2 * last.height
            if -0.2 * last.height <= gap < 0.75 * last.height and similar_size and overlaps_x and aligned:
                target = b
                break
        if target:
            target.lines.append(ln)
        else:
            blocks.append(Block([ln]))
    return blocks


# Pages below this are covers, illustrations or title cards: lettering that is
# part of the artwork, which repainting would ruin.
MIN_PROSE_LINES = 3
MIN_PROSE_CHARS = 120


def is_prose_page(blocks: list[Block], cjk: bool) -> bool:
    """True when OCR found running text worth translating on this page."""
    lines = sum(len(b.lines) for b in blocks)
    chars = sum(len(b.text(cjk)) for b in blocks)
    min_chars = MIN_PROSE_CHARS // 3 if cjk else MIN_PROSE_CHARS
    return lines >= MIN_PROSE_LINES and chars >= min_chars


def background_color(page, rect) -> tuple[float, float, float]:
    """Median colour of a thin frame around the box: the paper behind the text."""
    import numpy as np
    import pymupdf

    r = pymupdf.Rect(rect) + (-3, -3, 3, 3)
    r *= page.rotation_matrix
    r &= page.rect
    pix = page.get_pixmap(clip=r, dpi=72, colorspace=pymupdf.csRGB)
    a = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    if a.size == 0:
        return (1, 1, 1)
    frame = np.concatenate([a[0], a[-1], a[:, 0], a[:, -1]])
    med = np.median(frame, axis=0)
    return tuple(float(c) / 255 for c in med[:3])


def repaint(page, block: Block, translated: str, tgt: str) -> bool:
    import pymupdf

    visual_rect = pymupdf.Rect(block.rect)
    rect = visual_rect * page.derotation_matrix
    color = background_color(page, rect)
    line_h = sum(l.height for l in block.lines) / len(block.lines)
    font_pt = max(5.0, line_h * 0.78)
    family = {"ja": "serif", "zh": "serif"}.get(tgt, "serif")
    css = f"* {{ font-family: {family}; font-size: {font_pt:.1f}pt; line-height: 1.15; margin: 0; }}"
    body = html.escape(translated)
    # Layout first, so a failed fit cannot erase the original lettering.
    with pymupdf.open() as overlay:
        tile = overlay.new_page(width=visual_rect.width, height=visual_rect.height)
        spare, scale = tile.insert_htmlbox(tile.rect, f'<p lang="{tgt}">{body}</p>', css=css, scale_low=0.55)
        if spare < 0 or font_pt * scale < min(font_pt, 6):
            return False
        page.draw_rect(rect + (-1, -1, 1, 1), color=None, fill=color, overlay=True)
        page.show_pdf_page(rect, overlay, 0, rotate=page.rotation, overlay=True)
        return True
