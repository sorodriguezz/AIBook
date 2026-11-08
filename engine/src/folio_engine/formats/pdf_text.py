"""Recover text from PDF fonts whose ToUnicode maps omit drawn glyphs."""

import re

_INVALID_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]")


def xml_text(text: str) -> str:
    return _INVALID_XML.sub(" ", text)


def _font_key(name: str) -> str:
    return re.sub(r"[\s-]", "", name.split("+")[-1]).lower()


def _origin_key(font: str, origin: tuple) -> tuple:
    return _font_key(font), round(origin[0], 2), round(origin[1], 2)


def _glyph_map(doc, xref: int) -> dict[int, str]:
    import pymupdf

    _, _, _, data = doc.extract_font(xref)
    if not data:
        return {}
    try:
        font = pymupdf.Font(fontbuffer=data)
        chars = {}
        for code in font.valid_codepoints():
            if code <= 32 or code >= 127:
                char = chr(code)
                if char != " " and (char == "\ufffd" or not (char.isprintable() or char == "\u00ad")):
                    continue
            glyph = font.has_glyph(code, fallback=False)
            if glyph:
                chars.setdefault(glyph, chr(code))
        return chars
    except (RuntimeError, ValueError):
        return {}


def _recover(page, cache: dict[int, dict[int, str]]) -> dict[tuple, str]:
    fonts = {}
    for xref, _, _, name, *_ in page.get_fonts():
        if xref:
            fonts.setdefault(_font_key(name), set()).add(xref)
    restored = {}
    for span in page.get_texttrace():
        refs = fonts.get(_font_key(span["font"]), ())
        if len(refs) != 1:
            continue
        xref = next(iter(refs))
        for code, glyph, origin, _ in span["chars"]:
            if code != 0xFFFD and not _INVALID_XML.fullmatch(chr(code)):
                continue
            if xref not in cache:
                cache[xref] = _glyph_map(page.parent, xref)
            if char := cache[xref].get(glyph):
                restored[_origin_key(span["font"], origin)] = char
    return restored


def page_blocks(page, cache: dict[int, dict[int, str]]) -> list[dict]:
    import pymupdf

    # With CID fallback disabled, unknown glyphs stay identifiable instead of
    # masquerading as punctuation, control codes or arbitrary letters.
    flags = pymupdf.TEXTFLAGS_RAWDICT & ~pymupdf.TEXT_USE_CID_FOR_UNKNOWN_UNICODE
    blocks = page.get_text("rawdict", flags=flags, sort=True)["blocks"]
    recovered = None
    for block in blocks:
        for line in block.get("lines", []):
            for span in line["spans"]:
                for char in span["chars"]:
                    value = char["c"]
                    if value == "\ufffd" or _INVALID_XML.fullmatch(value):
                        if recovered is None:
                            recovered = _recover(page, cache)
                        char["c"] = recovered.get(_origin_key(span["font"], char["origin"]), xml_text(value))
                span["text"] = "".join(c["c"] for c in span["chars"])
    return blocks
