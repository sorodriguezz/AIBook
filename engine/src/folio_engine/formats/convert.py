"""EPUB ⇄ PDF conversion.

EPUB → PDF uses MuPDF's own HTML/CSS layout engine, so the book's styles,
images and tables are typeset as an e-reader would, onto the chosen page size.

PDF → EPUB has two modes:
- "reflow": rebuilds real text (headings, paragraphs, emphasis, tables,
  images) so it adapts to any screen. Running headers, footers and page
  numbers are dropped and paragraphs split by page breaks are rejoined.
- "fixed": a pre-paginated EPUB 3 with one image per page, visually identical
  to the PDF (best for comics, art books or complex layouts).
"""

import html
import re
import statistics
import uuid
import zipfile
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ..errors import UserError
from ..langs import LANGUAGES
from ..translate import langdetect
from ..translate.booktype import MONO_FONT_RE
from . import ocr, pdf_text

Progress = Callable[[float, str], None]  # (fraction 0..1, detail)
ShouldStop = Callable[[], bool]

PAGE_SIZES = {  # points
    "a5": (420, 595),
    "6x9": (432, 648),
    "a4": (595, 842),
    "letter": (612, 792),
}


class Cancelled(Exception):
    pass


# ---- EPUB → PDF -----------------------------------------------------------------

def epub_to_pdf(src: Path, out: Path, page: str = "a5", font_size: float = 11.0, margin_mm: float = 16.0,
                on_progress: Progress = lambda f, d: None, should_stop: ShouldStop = lambda: False) -> Path:
    import pymupdf

    width, height = PAGE_SIZES.get(page, PAGE_SIZES["a5"])
    m = margin_mm * 72 / 25.4
    # Page margins via user CSS; the book's own styles still apply to everything else.
    pymupdf.mupdf.fz_set_user_css(f"@page {{ margin: {m:.1f}pt {m * 0.9:.1f}pt }}")
    try:
        book = pymupdf.open(src)
        book.layout(width=width, height=height, fontsize=font_size)
        total = book.page_count
        pdf = pymupdf.open()
        step = 20
        for start in range(0, total, step):
            if should_stop():
                raise Cancelled()
            end = min(total, start + step) - 1
            part = pymupdf.open("pdf", book.convert_to_pdf(from_page=start, to_page=end))
            pdf.insert_pdf(part)
            on_progress((end + 1) / total, f"{end + 1}/{total}")
        toc = book.get_toc()
        if toc:
            pdf.set_toc(toc)
        meta = book.metadata or {}
        pdf.set_metadata({"title": meta.get("title") or src.stem, "author": meta.get("author") or "",
                          "creator": "AIBook", "producer": "AIBook"})
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(".part.pdf")
        pdf.save(tmp, garbage=3, deflate=True)
        tmp.replace(out)
        return out
    finally:
        pymupdf.mupdf.fz_set_user_css("")


# ---- PDF → EPUB (fixed layout) -----------------------------------------------------

def pdf_to_epub_fixed(src: Path, out: Path, dpi: int = 150,
                      on_progress: Progress = lambda f, d: None, should_stop: ShouldStop = lambda: False) -> Path:
    import pymupdf

    doc = pymupdf.open(src)
    meta = doc.metadata or {}
    title = meta.get("title") or src.stem
    w0, h0 = doc[0].rect.width, doc[0].rect.height
    vw, vh = round(w0 * dpi / 72), round(h0 * dpi / 72)
    pages: list[tuple[str, str]] = []
    images: list[tuple[str, bytes]] = []
    for i, page in enumerate(doc):
        if should_stop():
            raise Cancelled()
        pix = page.get_pixmap(dpi=dpi)
        name = f"p{i + 1:04d}"
        images.append((f"images/{name}.jpg", pix.tobytes("jpeg", jpg_quality=88)))
        body = (f'<div style="width:{vw}px;height:{vh}px;margin:0;padding:0">'
                f'<img src="../images/{name}.jpg" alt="" style="width:{vw}px;height:{vh}px"/></div>')
        pages.append((f"text/{name}.xhtml", _xhtml(f"{i + 1}", body, viewport=(vw, vh))))
        on_progress((i + 1) / doc.page_count, f"{i + 1}/{doc.page_count}")
    toc = [(lvl, t, max(1, p)) for lvl, t, p in doc.get_toc()] or [(1, title, 1)]
    nav = [(t, f"text/p{p:04d}.xhtml") for lvl, t, p in toc if lvl == 1]
    _write_epub(out, title, meta.get("author") or "", pages, images, nav, cover=images[0][0] if images else None,
                fixed=(vw, vh))
    return out


# ---- PDF → EPUB (reflow) -----------------------------------------------------------

@dataclass
class Para:
    kind: str  # "h1" | "h2" | "h3" | "p" | "img" | "table"
    html: str
    text: str = ""
    size: float = 0.0
    page: int = 0
    ends_sentence: bool = True


@dataclass
class Chapter:
    title: str
    items: list[str] = field(default_factory=list)


_END_RE = re.compile(r"[.!?…:;»”\"’)\]]\s*$")
_PAGE_NUM_RE = re.compile(r"^\s*(?:page\s+|página\s+)?[\divxlcdm]{1,6}\s*$", re.I)
_LIST_START = re.compile(r"^(?:[•●▪◦]|[-–—]|\d{1,3}[.)])\s+")
_CODE_ROW = re.compile(r"^(\d{1,4})(?:\s+(.*))?$")
_SECTION_PAGES = 32


def pdf_to_epub_reflow(src: Path, out: Path, lang: str | None = None,
                       on_progress: Progress = lambda f, d: None, should_stop: ShouldStop = lambda: False) -> Path:
    import pymupdf

    paras: list[Para] = []
    images: list[tuple[str, bytes]] = []
    font_maps: dict[int, dict[int, str]] = {}
    with pymupdf.open(src) as doc:
        meta = doc.metadata or {}
        title = meta.get("title") or src.stem
        lang = lang or _document_language(doc)
        bookmarks = {page - 1: name for level, name, page in doc.get_toc() if level == 1 and page > 0}
        furniture = _running_furniture(doc)
        body_size = _body_font_size(doc)
        heading_sizes = _heading_sizes(doc, body_size)
        for i, page in enumerate(doc):
            if should_stop():
                raise Cancelled()
            if ocr.is_scanned(page, page.get_text()):
                items = _ocr_paras(page, i, lang or "en")
                if not items:
                    items = [_raster_figure(page, i, images)]
            else:
                items = _page_paras(page, i, body_size, heading_sizes, furniture, images, font_maps)
            paras.extend(items)
            on_progress(0.9 * (i + 1) / doc.page_count, f"{i + 1}/{doc.page_count}")
        cover = None
        if doc.page_count:
            pix = doc[0].get_pixmap(dpi=110)
            images.insert(0, ("images/cover.jpg", pix.tobytes("jpeg", jpg_quality=88)))
            cover = "images/cover.jpg"

    if should_stop():
        raise Cancelled()
    if not paras:
        raise UserError("pdf_no_text", "no content to convert in this PDF")
    paras = _join_across_pages(paras, bookmarks)
    chapters = _split_chapters(paras, title, bookmarks)

    pages: list[tuple[str, str]] = []
    nav: list[tuple[str, str]] = []
    for n, ch in enumerate(chapters, 1):
        path = f"text/ch{n:03d}.xhtml"
        pages.append((path, _xhtml(ch.title, "\n".join(ch.items), lang=lang)))
        nav.append((ch.title, path))
    _write_epub(out, title, meta.get("author") or "", pages, images, nav, cover=cover, lang=lang, css=REFLOW_CSS)
    on_progress(1.0, "")
    return out


REFLOW_CSS = """
body { font-family: serif; line-height: 1.5; margin: 0 5%; }
h1, h2, h3 { font-family: serif; line-height: 1.25; page-break-after: avoid; }
h1 { font-size: 1.6em; margin: 2em 0 1em; text-align: center; }
h2 { font-size: 1.3em; margin: 1.6em 0 0.8em; }
h3 { font-size: 1.1em; margin: 1.3em 0 0.6em; }
p { margin: 0 0 0.75em; }
pre { font-family: monospace; font-size: 0.9em; white-space: pre-wrap; margin: 0.75em 0; }
figure { margin: 1em 0; text-align: center; }
figure img { max-width: 100%; height: auto; }
table { border-collapse: collapse; margin: 1em auto; font-size: 0.9em; }
td, th { border: 1px solid #999; padding: 0.25em 0.5em; vertical-align: top; }
"""


def _document_language(doc) -> str | None:
    import pymupdf

    kind, value = doc.xref_get_key(doc.pdf_catalog(), "Lang")
    if kind == "string" and (lang := value.lower().split("-")[0]) in LANGUAGES:
        return lang
    flags = pymupdf.TEXTFLAGS_TEXT & ~pymupdf.TEXT_USE_CID_FOR_UNKNOWN_UNICODE
    sample = "\n".join(page.get_text(flags=flags)[:1200] for page in doc.pages(0, min(doc.page_count, 20)))
    return langdetect.detect(sample.replace("\ufffd", " "))


def _body_font_size(doc) -> float:
    import pymupdf

    sizes: Counter = Counter()
    for page in doc.pages(0, min(doc.page_count, 40)):
        for b in page.get_text("dict", flags=pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES)["blocks"]:
            for line in b.get("lines", []):
                for s in line["spans"]:
                    sizes[round(s["size"], 1)] += len(s["text"])
    return sizes.most_common(1)[0][0] if sizes else 11.0


def _heading_sizes(doc, body: float) -> list[float]:
    """Distinct font sizes used for short lines noticeably larger than body text."""
    import pymupdf

    found: Counter = Counter()
    for page in doc:
        for b in page.get_text("dict", flags=pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES)["blocks"]:
            for line in b.get("lines", []):
                text = "".join(s["text"] for s in line["spans"]).strip()
                if not text or len(text) > 120:
                    continue
                size = max(s["size"] for s in line["spans"])
                if size >= body * 1.18:
                    found[round(size)] += 1
    return sorted(found, reverse=True)[:3]


def _running_furniture(doc) -> set[str]:
    """Text that repeats at the top or bottom of many pages (headers, footers)."""
    counts: Counter = Counter()
    n = doc.page_count
    for page in doc:
        h = page.rect.height
        for b in page.get_text("blocks"):
            if b[6] != 0:
                continue
            if b[1] < h * 0.08 or b[3] > h * 0.92:
                key = _furniture_key(b[4])
                counts[key] += 1
    return {k for k, c in counts.items() if c >= max(3, n * 0.2)}


def _furniture_key(text: str) -> str:
    return re.sub(r"\d+", "#", " ".join(pdf_text.xml_text(text).lower().split()))


def _span_html(span: dict) -> str:
    text = html.escape(span["text"])
    if not text.strip():
        return text
    flags, font = span["flags"], span.get("font", "").lower()
    bold = bool(flags & 16) or "bold" in font or "black" in font
    italic = bool(flags & 2) or "italic" in font or "oblique" in font
    if bold:
        text = f"<strong>{text}</strong>"
    if italic:
        text = f"<em>{text}</em>"
    return text


def _raster_figure(page, index, images, bbox=None) -> Para:
    import pymupdf

    clip = pymupdf.Rect(bbox) * page.rotation_matrix if bbox else None
    pix = page.get_pixmap(clip=clip, dpi=144)
    return _figure(index, images, pix.tobytes("png"), "png")


def _figure(index, images, data, ext) -> Para:
    name = f"images/i{len(images) + 1:04d}.{ext}"
    images.append((name, data))
    return Para("img", f'<figure><img src="../{name}" alt=""/></figure>', page=index)


def _line_text(line) -> str:
    return "".join(s["text"] for s in line["spans"]).strip()


def _cell_text(cell, lines) -> str:
    if not cell:
        return ""
    x0, y0, x1, y1 = cell
    return " ".join(" ".join("".join(
        char["c"] for span in line["spans"] for char in span["chars"]
        if x0 <= (char["bbox"][0] + char["bbox"][2]) / 2 <= x1
    ) for line in lines if y0 <= (line["bbox"][1] + line["bbox"][3]) / 2 <= y1).split())


def _paragraph_lines(lines, body):
    """Split a PDF text block at real paragraph breaks, retaining wrapped lines."""
    group = []
    left = min(line["bbox"][0] for line in lines)
    right = max(line["bbox"][2] for line in lines)
    for line in lines:
        if group:
            prev = group[-1]
            y_gap = line["bbox"][1] - prev["bbox"][3]
            new_indent = line["bbox"][0] - left > 0.75 * body and prev["bbox"][0] - left < 0.4 * body
            prev_short = right - prev["bbox"][2] > 2 * body
            new_item = _LIST_START.match(_line_text(line))
            if (y_gap > 0.6 * body or new_item or
                    (new_indent or prev_short) and _END_RE.search(_line_text(prev))):
                yield group
                group = []
        group.append(line)
    if group:
        yield group


def _numbered_code(lines, body: float) -> str | None:
    """Preserve numbered listings even when they use a proportional font."""
    rows: list[list[dict]] = []
    for line in sorted(lines, key=lambda line: line["bbox"][1]):
        if not rows or line["bbox"][1] - rows[-1][0]["bbox"][1] > body * 0.25:
            rows.append([])
        rows[-1].append(line)
    listing = []
    for row in rows:
        row.sort(key=lambda line: line["bbox"][0])
        parts = [_line_text(line) for line in row]
        match = _CODE_ROW.fullmatch(" ".join(parts))
        if not match:
            return None
        x = row[1]["bbox"][0] if len(row) > 1 and parts[0].isdigit() else None
        listing.append((int(match[1]), match[2] or "", x))
    if len(listing) < 3 or sum(text.endswith(("{", "}", ";")) for _, text, _ in listing) < 2:
        return None
    if any(b[0] != a[0] + 1 for a, b in zip(listing, listing[1:])):
        return None
    left = min((x for _, _, x in listing if x is not None), default=0)
    width = len(str(listing[-1][0]))
    output = []
    for number, text, x in listing:
        indent = round((x - left) / (body * 0.5)) if x is not None else 0
        output.append(f"{number:>{width}}  {' ' * indent}{text}".rstrip())
    return "\n".join(output)


def _page_paras(page, index, body, heading_sizes, furniture, images, font_maps) -> list[Para]:
    h = page.rect.height
    blocks = pdf_text.page_blocks(page, font_maps)
    tables = []
    try:
        tables = list(page.find_tables(strategy="lines_strict").tables)
    except Exception:
        pass
    table_rects = [t.bbox for t in tables]

    items: list[tuple[float, Para]] = []
    text_lines = [line for block in blocks for line in block.get("lines", [])]
    for t in tables:
        lines = [line for line in text_lines if t.bbox[1] <= line["bbox"][1] < t.bbox[3]]
        rows = [[_cell_text(cell, lines) for cell in row.cells] for row in t.rows]
        if not rows:
            continue
        if any("\ufffd" in cell for row in rows for cell in row):
            items.append((t.bbox[1], _raster_figure(page, index, images, t.bbox)))
            continue
        cells = "".join(
            "<tr>" + "".join(f"<{'th' if r == 0 else 'td'}>{html.escape(c or '')}</{'th' if r == 0 else 'td'}>"
                             for c in row) + "</tr>"
            for r, row in enumerate(rows))
        items.append((t.bbox[1], Para("table", f"<table>{cells}</table>", page=index)))

    for b in blocks:
        x0, y0, x1, y1 = b["bbox"]
        if any(x0 >= r[0] - 2 and y0 >= r[1] - 2 and x1 <= r[2] + 2 and y1 <= r[3] + 2 for r in table_rects):
            continue
        if b["type"] == 1:  # image
            if (x1 - x0) * (y1 - y0) < 40 * 40:
                continue
            ext = b.get("ext", "png")
            figure = (_figure(index, images, b["image"], ext) if ext in ("jpeg", "jpg", "png", "gif")
                      else _raster_figure(page, index, images, b["bbox"]))
            items.append((y0, figure))
            continue
        lines = [line for line in b.get("lines", []) if _line_text(line)]
        if not lines:
            continue
        text = " ".join(_line_text(line) for line in lines)
        key = _furniture_key(text)
        edge = y0 < h * 0.08 or y1 > h * 0.92
        if edge and (key in furniture or _PAGE_NUM_RE.match(text)):
            continue
        spans = [s for line in lines for s in line["spans"] if s["text"].strip()]
        listing = _numbered_code(lines, body) if len(lines) >= 3 and re.search(r"[{};]", text) else None
        code = listing is not None or all(s["flags"] & 8 or MONO_FONT_RE.search(s["font"]) for s in spans)
        for group in [lines] if code else _paragraph_lines(lines, body):
            text = " ".join(_line_text(line) for line in group)
            top = group[0]["bbox"][1]
            if "\ufffd" in text:
                bbox = (min(line["bbox"][0] for line in group), min(line["bbox"][1] for line in group),
                        max(line["bbox"][2] for line in group), max(line["bbox"][3] for line in group))
                items.append((top, _raster_figure(page, index, images, bbox)))
                continue
            size = max(s["size"] for line in group for s in line["spans"])
            level = next((n for n, hs in enumerate(heading_sizes, 1) if abs(round(size) - hs) <= 0.5), None)
            if code:
                left = min(line["bbox"][0] for line in group)
                raw = listing or "\n".join(" " * round((line["bbox"][0] - left) / (size * 0.6)) + _line_text(line)
                                          for line in group)
                para = Para("pre", f"<code>{html.escape(raw)}</code>", text=text, page=index)
            elif level and len(text) <= 120:
                para = Para(f"h{level}", _join_lines(group), text=text, page=index)
            else:
                para = Para("p", _join_lines(group), text=text, page=index, ends_sentence=bool(_END_RE.search(text)))
            items.append((top, para))
    items.sort(key=lambda it: it[0])
    return [p for _, p in items]


def _join_lines(lines: list[dict]) -> str:
    parts: list[str] = []
    for ln in lines:
        frag = "".join(_span_html(s) for s in ln["spans"]).strip()
        if not frag:
            continue
        if parts and parts[-1].endswith(("-", "\u00ad")) and frag[:1].islower():
            parts[-1] = parts[-1][:-1] + frag  # re-join hyphenated words
        else:
            parts.append(frag)
    return " ".join(parts)


def _ocr_paras(page, index: int, lang: str) -> list[Para]:
    cjk = lang in ("ja", "zh")
    blocks = ocr.group_blocks(ocr.ocr_page(page, lang))
    if not ocr.is_prose_page(blocks, cjk):
        # Artwork or a title page: keep it as an image so nothing is lost.
        return []
    return [Para("p", html.escape(t), text=t, page=index, ends_sentence=bool(_END_RE.search(t)))
            for b in blocks if (t := pdf_text.xml_text(b.text(cjk))).strip()]


def _join_across_pages(paras: list[Para], bookmarks: dict[int, str]) -> list[Para]:
    """Rejoin paragraphs that a page break split in two."""
    out: list[Para] = []
    for p in paras:
        prev = out[-1] if out else None
        if (prev and prev.kind == "p" and p.kind == "p" and p.page == prev.page + 1
                and p.page not in bookmarks
                and not prev.ends_sentence and p.text[:1].islower()):
            joined = prev.html[:-1] + p.html if prev.html.endswith("-") else prev.html + " " + p.html
            out[-1] = Para("p", joined, text=prev.text + " " + p.text, page=p.page,
                           ends_sentence=p.ends_sentence)
        else:
            out.append(p)
    return out


def _split_chapters(paras: list[Para], book_title: str, bookmarks: dict[int, str]) -> list[Chapter]:
    top = "h1" if any(p.kind == "h1" for p in paras) else "h2"
    chapters: list[Chapter] = [Chapter(book_title)]
    marks = iter(sorted(bookmarks.items()))
    mark = next(marks, None)
    start = 0
    for p in paras:
        title = None
        while mark and mark[0] <= p.page:
            title = mark[1]
            mark = next(marks, None)
        if not bookmarks and p.kind == top:
            title = p.text[:80]
        if title is None and p.page - start >= _SECTION_PAGES:
            title = f"{book_title} · {p.page + 1}"
        if title is not None:
            if chapters[-1].items:
                chapters.append(Chapter(title))
            else:
                chapters[-1].title = title
            start = p.page
        tag = {"img": None, "table": None}.get(p.kind, p.kind)
        chapters[-1].items.append(p.html if tag is None else f"<{tag}>{p.html}</{tag}>")
    return [c for c in chapters if c.items]


# ---- EPUB writer ----------------------------------------------------------------

def _xhtml(title: str, body: str, lang: str | None = None, viewport: tuple[int, int] | None = None) -> str:
    vp = f'<meta name="viewport" content="width={viewport[0]}, height={viewport[1]}"/>' if viewport else ""
    style = "body{margin:0;padding:0}" if viewport else ""
    lang_attr = f' xml:lang="{html.escape(lang)}" lang="{html.escape(lang)}"' if lang else ""
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
        f'<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"{lang_attr}>\n'
        f"<head><meta charset=\"utf-8\"/>{vp}<title>{html.escape(pdf_text.xml_text(title))}</title>"
        '<link rel="stylesheet" type="text/css" href="../style.css"/>'
        f"<style>{style}</style></head>\n<body>\n{body}\n</body>\n</html>\n"
    )


def _write_epub(out: Path, title: str, author: str, pages: list[tuple[str, str]], images: list[tuple[str, bytes]],
                nav: list[tuple[str, str]], cover: str | None = None, lang: str | None = None,
                fixed: tuple[int, int] | None = None, css: str = "") -> None:
    uid = f"urn:uuid:{uuid.uuid4()}"
    media = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "gif": "image/gif",
             "webp": "image/webp", "jxr": "image/jxr", "bmp": "image/bmp"}
    manifest = ['<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
                '<item id="css" href="style.css" media-type="text/css"/>']
    for k, (path, _) in enumerate(pages):
        props = ' properties="rendition:layout-pre-paginated"' if fixed else ""
        manifest.append(f'<item id="x{k}" href="{path}" media-type="application/xhtml+xml"{props}/>')
    for k, (path, _) in enumerate(images):
        ext = path.rsplit(".", 1)[-1].lower()
        props = ' properties="cover-image"' if path == cover else ""
        manifest.append(f'<item id="i{k}" href="{path}" media-type="{media.get(ext, "image/png")}"{props}/>')
    spine = "".join(f'<itemref idref="x{k}"/>' for k in range(len(pages)))
    fixed_meta = ('<meta property="rendition:layout">pre-paginated</meta>'
                  '<meta property="rendition:spread">auto</meta>') if fixed else ""
    opf = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        f'<dc:identifier id="uid">{uid}</dc:identifier><dc:title>{html.escape(pdf_text.xml_text(title))}</dc:title>'
        f'<dc:creator>{html.escape(pdf_text.xml_text(author))}</dc:creator><dc:language>{html.escape(lang or "und")}</dc:language>'
        f'<meta property="dcterms:modified">2026-01-01T00:00:00Z</meta>{fixed_meta}'
        f'</metadata><manifest>{"".join(manifest)}</manifest><spine>{spine}</spine></package>'
    )
    nav_items = "".join(f'<li><a href="{href}">{html.escape(pdf_text.xml_text(t))}</a></li>' for t, href in nav)
    nav_doc = _xhtml(title, f'<nav epub:type="toc" id="toc"><ol>{nav_items}</ol></nav>', lang=lang).replace(
        "../style.css", "style.css")
    container = ('<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                 '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
                 '</rootfiles></container>')
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".part.epub")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml", container)
        z.writestr("OEBPS/content.opf", opf)
        z.writestr("OEBPS/nav.xhtml", nav_doc)
        z.writestr("OEBPS/style.css", css)
        for path, data in pages:
            z.writestr(f"OEBPS/{path}", data)
        for path, data in images:
            z.writestr(f"OEBPS/{path}", data, compress_type=zipfile.ZIP_STORED)
    tmp.replace(out)


def output_name(src: Path, fmt: str, out_dir: Path) -> Path:
    """`<stem>.<fmt>` in out_dir without overwriting anything."""
    target = out_dir / f"{src.stem}.{fmt}"
    n = 2
    while target.exists():
        target = out_dir / f"{src.stem} ({n}).{fmt}"
        n += 1
    return target
