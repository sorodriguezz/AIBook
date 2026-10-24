"""Builds small synthetic books used by the tests (all text is original)."""

import zipfile
from pathlib import Path

CONTAINER = """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>"""

OPF = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="uid">folio-test</dc:identifier>
    <dc:title>The Lantern of Vaelmoor</dc:title>
    <dc:language>en</dc:language>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="c1" href="text/ch1.xhtml" media-type="application/xhtml+xml"/>
    <item id="css" href="style.css" media-type="text/css"/>
    <item id="img" href="images/map.png" media-type="image/png" properties="cover-image"/>
  </manifest>
  <spine><itemref idref="c1"/></spine>
</package>"""

NAV = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
<head><title>Contents</title></head>
<body><nav epub:type="toc"><ol><li><a href="text/ch1.xhtml">Chapter One: The Ferryman</a></li></ol></nav></body>
</html>"""

CH1 = """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml">
<head><title>Chapter One</title><link rel="stylesheet" href="../style.css"/></head>
<body>
  <section class="chapter">
    <h1 class="title">Chapter One: <span class="sub">The Ferryman</span></h1>
    <p class="first">Mira had never trusted the river, and the river, she suspected, had never
      trusted her. <em>Not tonight</em>, she told herself, gripping the <a href="#n1" id="r1">lantern</a>.</p>
    <p>“You’re late,” said Old Tobben, spitting into the reeds.<br/>“The tide doesn’t wait for anyone.”</p>
    <figure><img src="../images/map.png" alt="A map of the marshes of Vaelmoor"/>
      <figcaption>The marshes, as drawn by the <strong>Cartographers’ Guild</strong>.</figcaption></figure>
    <table class="ledger">
      <tr><th>Item</th><th>Price</th></tr>
      <tr><td>Lantern oil</td><td>3 coppers</td></tr>
    </table>
    <div class="note">Loose text inside a container<p>and a nested paragraph.</p></div>
    <p><code>x = 1</code> is what the wizard wrote on the door.</p>
    <p class="note" id="n1"><sup>1</sup> The lanterns of Vaelmoor burn whale oil.</p>
  </section>
</body>
</html>"""

def _png(width: int = 8, height: int = 8, rgb: tuple[int, int, int] = (196, 64, 43)) -> bytes:
    """A small, valid PNG (strict readers like MuPDF check every CRC and zlib stream)."""
    import struct
    import zlib

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    raw = b"".join(b"\x00" + bytes(rgb) * width for _ in range(height))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


PNG = _png()


def make_epub(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml", CONTAINER)
        z.writestr("OEBPS/content.opf", OPF)
        z.writestr("OEBPS/nav.xhtml", NAV)
        z.writestr("OEBPS/text/ch1.xhtml", CH1)
        z.writestr("OEBPS/style.css", "body { font-family: Georgia; } .first::first-letter { font-size: 3em; }")
        z.writestr("OEBPS/images/map.png", PNG)
    return path
