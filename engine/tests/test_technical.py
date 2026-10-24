import zipfile

from folio_engine.formats.epub import EpubBook, read_info
from folio_engine.translate import booktype, prompts

NOVEL = """
—No pienso volver —dijo Ruby, apretando los puños—. Nunca.
Arcus la miró en silencio. El viento helado golpeaba las ventanas del castillo.
"Then stay," he whispered. "Stay with me tonight."
—¿Y si nos encuentran? —preguntó ella.
""" * 20

TECH = """
Para desplegar la aplicación en el clúster, ejecuta kubectl apply -f deployment.yaml.
El servidor expone una API REST en el puerto 8080; la configuración se guarda en config.json.
La función get_user() devuelve un objeto con los datos del usuario desde la base de datos.
""" * 20


def test_classify_literary_and_technical():
    assert booktype.classify(NOVEL) == "literary"
    assert booktype.classify(TECH) == "technical"
    # Monospace-heavy layout tips a borderline sample.
    assert booktype.classify("El sistema y el proceso. " * 50, code_font_share=0.25) == "technical"


def test_technical_prompts():
    sys_ = prompts.translator_system("en", "es", None, [], [{"source": "pod", "target": "pod"}], "adapt", "technical")
    assert "Never translate code" in sys_ and "pod → pod" in sys_ and "character's voice" not in sys_
    review = prompts.review_system("en", "es", None, [], [], "adapt", "technical")
    assert "technical editor" in review
    extract = prompts.bible_extract_messages("en", "text", {"characters": [], "terms": []}, "technical")
    assert "terminology" in extract[0]["content"] and "characters" not in extract[1]["content"]
    assert "character's voice" in prompts.translator_system("en", "es", None, [], [], "adapt")


CONTAINER = """<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>"""
OPF = """<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Guía</dc:title><dc:language>en</dc:language><dc:identifier id="id">x</dc:identifier></metadata>
<manifest><item id="c1" href="c1.xhtml" media-type="application/xhtml+xml"/></manifest><spine><itemref idref="c1"/></spine></package>"""
CH = """<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml"><head><title>c</title></head><body>
<p class="para">Run the <span class="courier">deploy_all</span> script to install the server.</p>
<p class="CaptCodigo1">def deploy_all(nodes):</p>
<p class="programs">    return [n.start() for n in nodes]</p>
</body></html>"""


def _epub(path):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip")
        z.writestr("META-INF/container.xml", CONTAINER)
        z.writestr("OEBPS/content.opf", OPF)
        z.writestr("OEBPS/c1.xhtml", CH)
    return path


def test_technical_epub_leaves_publisher_code_alone(tmp_path):
    path = _epub(tmp_path / "g.epub")
    literary = [s.source for s in EpubBook(path).extract() if s.kind == "para"]
    assert any("def deploy_all" in s for s in literary)  # literary mode translates everything
    book = EpubBook(path, technical=True)
    segs = [s for s in book.extract() if s.kind == "para"]
    assert [s.plain for s in segs] == ["Run the  script to install the server."]
    assert "⟦1/⟧" in segs[0].source  # inline code travels as an opaque marker
    book.apply({segs[0].id: segs[0].source.replace("Run the", "Ejecuta el").replace(
        "script to install the server.", "script para instalar el servidor.")}, "es")
    out = tmp_path / "out.epub"
    book.write(out)
    html = zipfile.ZipFile(out).read("OEBPS/c1.xhtml").decode()
    assert "deploy_all</span> script para instalar" in html and "def deploy_all(nodes):" in html


def test_inspect_reports_book_type(tmp_path):
    assert read_info(_epub(tmp_path / "g.epub"))["book_type"] in ("technical", "literary")


def test_code_font_detection():
    assert booktype.MONO_FONT_RE.search("Courier") and booktype.MONO_FONT_RE.search("FreeMono")
    assert not booktype.MONO_FONT_RE.search("Arial Unicode MS Regular")  # "Unicode" is not code


def test_code_fonts_not_protected_when_body_is_monospace(tmp_path):
    import pymupdf
    from folio_engine.formats import pdf

    doc = pymupdf.open()
    for _ in range(3):
        doc.new_page().insert_textbox(pymupdf.Rect(50, 50, 550, 750), "Typewriter body text. " * 40, fontname="cour")
    doc.save(tmp_path / "t.pdf")
    assert pdf._code_font_pattern(tmp_path / "t.pdf") is None
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_textbox(pymupdf.Rect(50, 50, 550, 600), "Prose text. " * 60, fontname="helv")
    page.insert_text((60, 700), "$ ls -la", fontname="cour")
    doc.save(tmp_path / "m.pdf")
    assert pdf._code_font_pattern(tmp_path / "m.pdf") == pdf.CODE_FONT_PATTERN
