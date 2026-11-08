"""Check saved output independently from the translator and renderer."""

from collections import Counter
from pathlib import Path
from zipfile import ZipFile

from lxml import etree

from ..errors import UserError


def _protected_markup(data: bytes) -> Counter:
    root = etree.fromstring(data, etree.XMLParser(recover=True, resolve_entities=False, huge_tree=True))
    attrs = {"href", "src", "srcset", "style", "class", "id", "colspan", "rowspan", "poster"}
    tags = {"a", "img", "image", "table", "tr", "td", "th", "style", "link"}
    found: Counter = Counter()
    for node in root.iter():
        if not isinstance(node.tag, str):
            continue
        tag = etree.QName(node).localname
        for key, value in node.attrib.items():
            if etree.QName(key).localname in attrs:
                found[(tag, key, value)] += 1
        if tag in tags:
            found[(tag, node.text or "" if tag == "style" else "")] += 1
        if tag == "table":
            cells = tuple(tuple((etree.QName(cell).localname, cell.get("rowspan"), cell.get("colspan"))
                                for cell in row if isinstance(cell.tag, str) and etree.QName(cell).localname in ("td", "th"))
                          for row in node.iter() if isinstance(row.tag, str) and etree.QName(row).localname == "tr")
            found[("grid", cells)] += 1
    return found


def epub(source: Path, target: Path, edited: set[str]) -> None:
    with ZipFile(source) as old, ZipFile(target) as new:
        if new.testzip() or set(old.namelist()) | {"mimetype"} != set(new.namelist()):
            raise UserError("layout_invalid", "EPUB archive changed or is unreadable", 422)
        for entry in old.infolist():
            name = entry.filename
            if name in edited:
                if _protected_markup(old.read(name)) - _protected_markup(new.read(name)):
                    raise UserError("layout_invalid", f"EPUB protected markup missing in {name}", 422)
            elif name != "mimetype" and entry.CRC != new.getinfo(name).CRC:
                raise UserError("layout_invalid", f"EPUB resource changed in {name}", 422)


def _table_overflow(words: list[tuple], cells: list, rect_type) -> int:
    overflow = 0
    for word in words:
        box = rect_type(word[:4])
        center = (box.tl + box.br) / 2
        cell = next((c for c in cells if center in c), None)
        if cell is not None and (box.x0 < cell.x0 - 2 or box.x1 > cell.x1 + 2):
            overflow += 1
    return overflow


def pdf(source: Path, target: Path, digital_pages: list[int], should_stop) -> list[dict]:
    import pymupdf

    issues = []
    digital = set(digital_pages)
    with pymupdf.open(source) as old, pymupdf.open(target) as new:
        if len(old) != len(new):
            raise UserError("layout_invalid", "PDF page count changed", 422)
        for i, (before, after) in enumerate(zip(old, new)):
            if should_stop():
                break
            codes = []
            if abs(before.rect.width - after.rect.width) > 2 or abs(before.rect.height - after.rect.height) > 2:
                codes.append("page_size")
            if i in digital:
                if len(after.get_image_info()) < len(before.get_image_info()):
                    codes.append("image_count")
                drawings = before.get_drawings()
                if sum(item[0] in ("l", "re") for draw in drawings for item in draw["items"]) >= 6:
                    try:
                        tables = before.find_tables(strategy="lines_strict", paths=drawings).tables
                    except (ValueError, RuntimeError):
                        tables = []
                    if tables:
                        cells = [pymupdf.Rect(c) for t in tables for c in t.cells if c]
                        orig = _table_overflow(before.get_text("words"), cells, pymupdf.Rect)
                        actual = _table_overflow(after.get_text("words"), cells, pymupdf.Rect)
                        if actual > orig:
                            codes.append("table_overflow")
            issues.extend({"code": code, "page": i + 1} for code in codes)
    return issues
