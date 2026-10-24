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

from . import ocr

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


def pdf_to_epub_reflow(src: Path, out: Path, lang: str | None = None,
                       on_progress: Progress = lambda f, d: None, should_stop: ShouldStop = lambda: False) -> Path:
    import pymupdf

    doc = pymupdf.open(src)
    meta = doc.metadata or {}
    title = meta.get("title") or src.stem
    furniture = _running_furniture(doc)
    body_size = _body_font_size(doc)
    heading_sizes = _heading_sizes(doc, body_size)

    paras: list[Para] = []
    images: list[tuple[str, bytes]] = []
    for i, page in enumerate(doc):
        if should_stop():
            raise Cancelled()
        if ocr.is_scanned(page):
            paras.extend(_ocr_paras(page, i, lang or "en"))
        else:
            paras.extend(_page_paras(doc, page, i, body_size, heading_sizes, furniture, images))
        on_progress(0.9 * (i + 1) / doc.page_count, f"{i + 1}/{doc.page_count}")

    paras = _join_across_pages(paras)
    chapters = _split_chapters(paras, title)

    pages: list[tuple[str, str]] = []
    nav: list[tuple[str, str]] = []
    for n, ch in enumerate(chapters, 1):
        path = f"text/ch{n:03d}.xhtml"
        pages.append((path, _xhtml(ch.title, "\n".join(ch.items), lang=lang)))
        nav.append((ch.title, path))
    cover = None
    if doc.page_count:
        pix = doc[0].get_pixmap(dpi=110)
        images.insert(0, ("images/cover.jpg", pix.tobytes("jpeg", jpg_quality=88)))
        cover = "images/cover.jpg"
    _write_epub(out, title, meta.get("author") or "", pages, images, nav, cover=cover, lang=lang, css=REFLOW_CSS)
    on_progress(1.0, "")
    return out


REFLOW_CSS = """
body { font-family: serif; line-height: 1.5; margin: 0 5%; }
h1, h2, h3 { font-family: serif; line-height: 1.25; page-break-after: avoid; }
h1 { font-size: 1.6em; margin: 2em 0 1em; text-align: center; }
h2 { font-size: 1.3em; margin: 1.6em 0 0.8em; }
h3 { font-size: 1.1em; margin: 1.3em 0 0.6em; }
p { margin: 0; text-indent: 1.4em; text-align: justify; }
h1 + p, h2 + p, h3 + p, figure + p, table + p { text-indent: 0; }
figure { margin: 1em 0; text-align: center; }
figure img { max-width: 100%; height: auto; }
table { border-collapse: collapse; margin: 1em auto; font-size: 0.9em; }
td, th { border: 1px solid #999; padding: 0.25em 0.5em; vertical-align: top; }
"""


def _body_font_size(doc) -> float:
    sizes: Counter = Counter()
    for page in doc.pages(0, min(doc.page_count, 40)):
        for b in page.get_text("dict")["blocks"]:
            for line in b.get("lines", []):
                for s in line["spans"]:
                    sizes[round(s["size"], 1)] += len(s["text"])
    return sizes.most_common(1)[0][0] if sizes else 11.0


def _heading_sizes(doc, body: float) -> list[float]:
    """Distinct font sizes used for short lines noticeably larger than body text."""
    found: Counter = Counter()
    for page in doc:
        for b in page.get_text("dict")["blocks"]:
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
                key = re.sub(r"\d+", "#", b[4].strip().lower())
                counts[key] += 1
    return {k for k, c in counts.items() if c >= max(3, n * 0.2)}


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


def _page_paras(doc, page, index, body, heading_sizes, furniture, images) -> list[Para]:
    h = page.rect.height
    out: list[Para] = []
    tables = []
    try:
        tables = list(page.find_tables().tables)
    except Exception:
        pass
    table_rects = [t.bbox for t in tables]

    items: list[tuple[float, Para]] = []
    for t in tables:
        # Read each cell from its rectangle: the table extractor drops spaces
        # with some fonts ("Lanternoil"), plain text extraction does not.
        rows = [[" ".join(page.get_textbox(c).split()) if c else "" for c in row.cells] for row in t.rows]
        if not rows:
            continue
        cells = "".join(
            "<tr>" + "".join(f"<{'th' if r == 0 else 'td'}>{html.escape(c or '')}</{'th' if r == 0 else 'td'}>"
                             for c in row) + "</tr>"
            for r, row in enumerate(rows))
        items.append((t.bbox[1], Para("table", f"<table>{cells}</table>", page=index)))

    for b in page.get_text("dict", sort=True)["blocks"]:
        x0, y0, x1, y1 = b["bbox"]
        if any(x0 >= r[0] - 2 and y0 >= r[1] - 2 and x1 <= r[2] + 2 and y1 <= r[3] + 2 for r in table_rects):
            continue
        if b["type"] == 1:  # image
            if (x1 - x0) * (y1 - y0) < 40 * 40:
                continue
            name = f"images/i{len(images) + 1:04d}.{b.get('ext', 'png')}"
            images.append((name, b["image"]))
            items.append((y0, Para("img", f'<figure><img src="../{name}" alt=""/></figure>', page=index)))
            continue
        lines = b.get("lines", [])
        text = " ".join("".join(s["text"] for s in ln["spans"]) for ln in lines).strip()
        if not text:
            continue
        key = re.sub(r"\d+", "#", text.lower())
        edge = y0 < h * 0.08 or y1 > h * 0.92
        if edge and (key in furniture or _PAGE_NUM_RE.match(text)):
            continue
        size = max((s["size"] for ln in lines for s in ln["spans"]), default=body)
        level = None
        if len(text) <= 120 and heading_sizes:
            for k, hs in enumerate(heading_sizes):
                if abs(round(size) - hs) <= 0.5:
                    level = k + 1
                    break
        inner = _join_lines(lines)
        if level:
            items.append((y0, Para(f"h{level}", inner, text=text, size=size, page=index)))
        else:
            items.append((y0, Para("p", inner, text=text, size=size, page=index,
                                   ends_sentence=bool(_END_RE.search(text)))))
    items.sort(key=lambda it: it[0])
    out.extend(p for _, p in items)
    return out


def _join_lines(lines: list[dict]) -> str:
    parts: list[str] = []
    for ln in lines:
        frag = "".join(_span_html(s) for s in ln["spans"]).strip()
        if not frag:
            continue
        if parts and parts[-1].endswith("-") and frag[:1].islower():
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
    return [Para("p", html.escape(b.text(cjk)), text=b.text(cjk), page=index) for b in blocks]


def _join_across_pages(paras: list[Para]) -> list[Para]:
    """Rejoin paragraphs that a page break split in two."""
    out: list[Para] = []
    for p in paras:
        prev = out[-1] if out else None
        if (prev and prev.kind == "p" and p.kind == "p" and p.page == prev.page + 1
                and not prev.ends_sentence and p.text[:1].islower()):
            joined = prev.html[:-1] + p.html if prev.html.endswith("-") else prev.html + " " + p.html
            out[-1] = Para("p", joined, text=prev.text + " " + p.text, page=p.page,
                           ends_sentence=p.ends_sentence)
        else:
            out.append(p)
    return out


def _split_chapters(paras: list[Para], book_title: str) -> list[Chapter]:
    top = "h1" if any(p.kind == "h1" for p in paras) else "h2"
    chapters: list[Chapter] = [Chapter(book_title)]
    for p in paras:
        if p.kind == top and chapters[-1].items:
            chapters.append(Chapter(p.text[:80]))
        elif p.kind == top:
            chapters[-1].title = p.text[:80]
        tag = {"img": None, "table": None}.get(p.kind, p.kind)
        chapters[-1].items.append(p.html if tag is None else f"<{tag}>{p.html}</{tag}>")
    return [c for c in chapters if c.items]


# ---- EPUB writer ----------------------------------------------------------------

def _xhtml(title: str, body: str, lang: str | None = None, viewport: tuple[int, int] | None = None) -> str:
    vp = f'<meta name="viewport" content="width={viewport[0]}, height={viewport[1]}"/>' if viewport else ""
    style = "body{margin:0;padding:0}" if viewport else ""
    lang_attr = f' xml:lang="{lang}" lang="{lang}"' if lang else ""
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
        f'<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"{lang_attr}>\n'
        f"<head><meta charset=\"utf-8\"/>{vp}<title>{html.escape(title)}</title>"
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
        f'<dc:identifier id="uid">{uid}</dc:identifier><dc:title>{html.escape(title)}</dc:title>'
        f'<dc:creator>{html.escape(author)}</dc:creator><dc:language>{lang or "und"}</dc:language>'
        f'<meta property="dcterms:modified">2026-01-01T00:00:00Z</meta>{fixed_meta}'
        f'</metadata><manifest>{"".join(manifest)}</manifest><spine>{spine}</spine></package>'
    )
    nav_items = "".join(f'<li><a href="{href}">{html.escape(t)}</a></li>' for t, href in nav)
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
