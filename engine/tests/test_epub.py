import zipfile

from lxml import etree

from folio_engine.formats.epub import EpubBook
from folio_engine.translate import placeholders

from .make_fixtures import make_epub


def fake_translate(text: str) -> str:
    """Uppercases words but keeps markers, like a well-behaved model would."""
    out = []
    for tok in placeholders.tokenize(text):
        if tok.kind == "text":
            out.append("«" + tok.value.upper() + "»" if tok.value.strip() else tok.value)
        elif tok.kind == "open":
            out.append(placeholders.open_tag(int(tok.value)))
        elif tok.kind == "close":
            out.append(placeholders.close_tag(int(tok.value)))
        else:
            out.append(placeholders.void_tag(int(tok.value)))
    return "".join(out)


def structure(tree) -> list:
    """Tag/attribute skeleton of a document, ignoring text."""
    return [(el.tag, sorted((k, v) for k, v in el.attrib.items() if k not in ("alt", "lang", "{http://www.w3.org/XML/1998/namespace}lang")))
            for el in tree.iter() if isinstance(el.tag, str) and not el.tag.endswith("style")]


def test_extract_segments(tmp_path):
    book = EpubBook(make_epub(tmp_path / "in.epub"))
    segs = book.extract()
    kinds = {s.kind for s in segs}
    assert {"heading", "para", "cell", "caption", "attr", "meta"} <= kinds
    first = next(s for s in segs if "Mira" in s.source)
    assert "⟦1⟧Not tonight⟦/1⟧" in first.source
    assert "⟦2⟧lantern⟦/2⟧" in first.source
    code = next(s for s in segs if "wizard" in s.source)
    assert code.source.startswith("⟦1/⟧")  # code travels untranslated
    assert any(s.source == "Loose text inside a container" for s in segs)
    assert book.title == "The Lantern of Vaelmoor"


def test_roundtrip_preserves_structure(tmp_path):
    src = make_epub(tmp_path / "in.epub")
    book = EpubBook(src)
    segs = book.extract()
    before = {p: structure(d.tree) for p, d in book.docs.items()}
    book.apply({s.id: fake_translate(s.source) for s in segs}, "es")
    out = tmp_path / "out.epub"
    book.write(out)

    with zipfile.ZipFile(out) as z:
        names = z.namelist()
        assert names[0] == "mimetype"
        assert z.getinfo("mimetype").compress_type == zipfile.ZIP_STORED
        assert z.read("OEBPS/images/map.png") == zipfile.ZipFile(src).read("OEBPS/images/map.png")
        assert z.read("OEBPS/style.css") == zipfile.ZipFile(src).read("OEBPS/style.css")
        ch1 = etree.fromstring(z.read("OEBPS/text/ch1.xhtml"))
        opf = z.read("OEBPS/content.opf").decode()

    after = EpubBook(out)
    for p, d in after.docs.items():
        assert structure(d.tree) == before[p], p

    text = etree.tostring(ch1, encoding="unicode")
    assert "«NOT TONIGHT»</em>" in text
    assert 'href="#n1" id="r1">«LANTERN»</a>' in text
    assert "<code>x = 1</code>" in text
    assert 'alt="«A MAP OF THE MARSHES OF VAELMOOR»"' in text
    assert "«LANTERN OIL»" in text
    assert ch1.get("lang") == "es"
    assert "<dc:language>es</dc:language>" in opf
    assert "«THE LANTERN OF VAELMOOR»" in opf


def test_missing_translation_keeps_original(tmp_path):
    book = EpubBook(make_epub(tmp_path / "in.epub"))
    book.extract()
    book.apply({}, "fr")
    out = tmp_path / "out.epub"
    book.write(out)
    with zipfile.ZipFile(out) as z:
        assert "Old Tobben" in z.read("OEBPS/text/ch1.xhtml").decode()


def test_cjk_target_adds_font_fallback(tmp_path):
    book = EpubBook(make_epub(tmp_path / "in.epub"))
    segs = book.extract()
    book.apply({s.id: s.source for s in segs}, "ja")
    out = tmp_path / "out.epub"
    book.write(out)
    with zipfile.ZipFile(out) as z:
        assert "Hiragino Mincho" in z.read("OEBPS/text/ch1.xhtml").decode()
