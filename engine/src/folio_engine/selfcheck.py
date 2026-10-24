"""Import and exercise the libraries translation loads lazily.

A frozen build only contains what PyInstaller saw imported, and several of
these libraries import parts of themselves dynamically. The engine starts fine
without them and only fails once a book is translated, so release builds run
`folio-engine --selfcheck` before shipping.
"""

import importlib
import traceback

MODULES = [
    "pymupdf",
    "rapidocr",
    "onnxruntime",
    "tiktoken",
    "bitstring",
    "bitstring.bitstore_bitarray",
    "bitstring.bitstore_tibs",
    "babeldoc.glossary",
    "babeldoc.docvision.doclayout",
    "babeldoc.format.pdf.high_level",
    "babeldoc.format.pdf.translation_config",
    "babeldoc.translator.translator",
    "folio_engine.formats.pdf",
    "folio_engine.formats.ocr",
    "folio_engine.formats.convert",
    "folio_engine.formats.epub",
]


def _exercise() -> None:
    import bitstring
    import pymupdf

    assert bitstring.Bits("0x0f").uint == 15
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "selfcheck")
    assert "selfcheck" in doc[0].get_text()


def run() -> int:
    failed = 0
    for name in MODULES:
        try:
            importlib.import_module(name)
            print(f"ok   {name}")
        except Exception:
            failed += 1
            print(f"FAIL {name}\n{traceback.format_exc()}")
    try:
        _exercise()
        print("ok   exercise")
    except Exception:
        failed += 1
        print(f"FAIL exercise\n{traceback.format_exc()}")
    print("selfcheck passed" if not failed else f"selfcheck failed: {failed}")
    return 1 if failed else 0
