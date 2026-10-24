"""EPUB extraction and reconstruction.

Only text nodes and a few visible attributes change. Every element, attribute,
stylesheet, image and font is carried over byte-for-byte, so the translated
book renders with exactly the original layout.
"""

import copy
import posixpath
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

from lxml import etree

from ..langs import is_cjk
from ..translate import placeholders
from ..translate.segments import Segment

XHTML = "http://www.w3.org/1999/xhtml"
OPF = "http://www.idpf.org/2007/opf"
DC = "http://purl.org/dc/elements/1.1/"
NCX = "http://www.daisy.org/z3986/2005/ncx/"
EPUB_NS = "http://www.idpf.org/2007/ops"
XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"

BLOCK_TAGS = {
    "p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "td", "th", "caption", "figcaption",
    "dt", "dd", "blockquote", "div", "section", "article", "aside", "header", "footer",
    "pre", "address", "title", "summary", "legend", "label", "option", "nav", "figure",
    "table", "tr", "tbody", "thead", "tfoot", "ul", "ol", "dl", "body", "main", "hgroup",
}
SKIP_TAGS = {"script", "style", "code", "kbd", "samp", "var", "math", "svg", "rt", "rp", "head"}
VOID_INLINE = {"br", "img", "wbr", "image"}
HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6", "title"}
CELL_TAGS = {"td", "th"}
CAPTION_TAGS = {"caption", "figcaption"}
TRANSLATABLE_ATTRS = ("alt", "title")


def _local(tag) -> str:
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1].lower()


@dataclass
class Document:
    path: str  # path inside the zip
    tree: etree._ElementTree
    kind: str  # "xhtml" | "ncx" | "opf"


class EpubBook:
    """Holds the parsed book between extraction and rebuild."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.zip = zipfile.ZipFile(path)
        container = etree.fromstring(self.zip.read("META-INF/container.xml"))
        self.opf_path = container.find(".//{*}rootfile").get("full-path")
        self.opf = etree.ElementTree(etree.fromstring(self.zip.read(self.opf_path)))
        self.base = posixpath.dirname(self.opf_path)
        self.docs: dict[str, Document] = {}
        # Per segment: how to put the translation back.
        self._slots: dict[str, tuple] = {}
        self._load()

    def _href(self, href: str) -> str:
        return posixpath.normpath(posixpath.join(self.base, href)) if self.base else href

    def _load(self) -> None:
        manifest = {it.get("id"): it for it in self.opf.iter(f"{{{OPF}}}item")}
        order: list[str] = []
        for ref in self.opf.iter(f"{{{OPF}}}itemref"):
            item = manifest.get(ref.get("idref"))
            if item is not None and "html" in (item.get("media-type") or ""):
                order.append(self._href(item.get("href")))
        # Navigation documents and other XHTML not in the spine (still visible in readers).
        for item in manifest.values():
            mt = item.get("media-type") or ""
            p = self._href(item.get("href"))
            if "html" in mt and p not in order:
                order.append(p)
            elif mt == "application/x-dtbncx+xml":
                self.docs[p] = Document(p, self._parse(p), "ncx")
        for p in order:
            if p in self.zip.namelist():
                self.docs[p] = Document(p, self._parse(p), "xhtml")
        self.spine_order = [p for p in order if p in self.docs]

    def _parse(self, p: str) -> etree._ElementTree:
        parser = etree.XMLParser(recover=True, resolve_entities=False, remove_blank_text=False, huge_tree=True)
        return etree.ElementTree(etree.fromstring(self.zip.read(p), parser))

    @property
    def title(self) -> str:
        el = self.opf.find(f".//{{{DC}}}title")
        return (el.text or "").strip() if el is not None else self.path.stem

    @property
    def language(self) -> str | None:
        el = self.opf.find(f".//{{{DC}}}language")
        return (el.text or "").strip().lower()[:2] if el is not None and el.text else None

    def cover_bytes(self) -> tuple[bytes, str] | None:
        manifest = list(self.opf.iter(f"{{{OPF}}}item"))
        cover_id = None
        meta = self.opf.find(f".//{{{OPF}}}meta[@name='cover']")
        if meta is not None:
            cover_id = meta.get("content")
        for it in manifest:
            props = it.get("properties") or ""
            if "cover-image" in props or (cover_id and it.get("id") == cover_id):
                p = self._href(it.get("href"))
                if p in self.zip.namelist():
                    return self.zip.read(p), it.get("media-type") or "image/jpeg"
        return None

    # ---- extraction -----------------------------------------------------
    def extract(self) -> list[Segment]:
        segs: list[Segment] = []
        for ci, p in enumerate(self.spine_order):
            doc = self.docs[p]
            title = self._chapter_title(doc)
            body = doc.tree.getroot()
            for el in self._iter_segment_roots(body):
                self._add_element_segment(segs, p, ci, title, el)
            for el in body.iter():
                if _local(el.tag) in SKIP_TAGS:
                    continue
                for attr in TRANSLATABLE_ATTRS:
                    val = el.get(attr)
                    if val and val.strip() and _local(el.tag) in ("img", "image", "abbr", "a", "area"):
                        sid = f"{p}#a{len(segs)}"
                        segs.append(Segment(sid, ci, "attr", val, title))
                        self._slots[sid] = ("attr", el, attr)
        nav_chapter = len(self.spine_order)
        for p, doc in self.docs.items():
            if doc.kind != "ncx":
                continue
            for text_el in doc.tree.iter(f"{{{NCX}}}text"):
                if text_el.text and text_el.text.strip():
                    sid = f"{p}#n{len(segs)}"
                    segs.append(Segment(sid, nav_chapter, "toc", text_el.text.strip()))
                    self._slots[sid] = ("text", text_el)
        for tag in ("title", "description"):
            for el in self.opf.iter(f"{{{DC}}}{tag}"):
                if el.text and el.text.strip():
                    sid = f"opf#{tag}{len(segs)}"
                    segs.append(Segment(sid, nav_chapter, "meta", el.text.strip()))
                    self._slots[sid] = ("text", el)
        return segs

    def _chapter_title(self, doc: Document) -> str:
        for el in doc.tree.iter():
            if _local(el.tag) in ("h1", "h2", "h3"):
                t = "".join(el.itertext()).strip()
                if t:
                    return t[:120]
        return ""

    def _iter_segment_roots(self, root):
        """Yield leaf-level text blocks, plus loose text inside container blocks."""
        stack = [root]
        while stack:
            el = stack.pop()
            name = _local(el.tag)
            if name in SKIP_TAGS or not isinstance(el.tag, str):
                continue
            if name == "head":
                continue
            block_children = [c for c in el if _local(c.tag) in BLOCK_TAGS]
            if name in BLOCK_TAGS and not block_children:
                if _has_visible_text(el):
                    yield el
                continue
            if name in BLOCK_TAGS and _has_loose_text(el):
                yield ("loose", el)
            stack.extend(reversed(list(el)))

    def _add_element_segment(self, segs, p, ci, title, el) -> None:
        if isinstance(el, tuple):
            _, parent = el
            # Text directly inside a container next to block children: translate each run.
            runs = [("text", parent)] if parent.text and parent.text.strip() else []
            runs += [("tail", c) for c in parent if c.tail and c.tail.strip()]
            for where, node in runs:
                raw = node.text if where == "text" else node.tail
                sid = f"{p}#{len(segs)}"
                segs.append(Segment(sid, ci, "para", raw.strip(), title))
                self._slots[sid] = ("loose", node, where)
            return
        name = _local(el.tag)
        source, mapping = serialize_inline(el, preserve_ws=name == "pre")
        if not placeholders.has_text(source):
            return
        kind = "heading" if name in HEADING_TAGS else "cell" if name in CELL_TAGS else \
            "caption" if name in CAPTION_TAGS else "para"
        sid = f"{p}#{len(segs)}"
        segs.append(Segment(sid, ci, kind, source, title))
        self._slots[sid] = ("block", el, mapping)

    # ---- rebuild --------------------------------------------------------
    def apply(self, translations: dict[str, str], tgt: str) -> None:
        for sid, slot in self._slots.items():
            tr = translations.get(sid)
            if tr is None:
                continue
            kind = slot[0]
            if kind == "block":
                _, el, mapping = slot
                rebuild_inline(el, tr, mapping)
            elif kind == "attr":
                _, el, attr = slot
                el.set(attr, placeholders.strip(tr))
            elif kind == "text":
                slot[1].text = placeholders.strip(tr)
            elif kind == "loose":
                _, node, where = slot
                raw = node.text if where == "text" else node.tail
                lead = raw[: len(raw) - len(raw.lstrip())]
                trail = raw[len(raw.rstrip()):]
                val = lead + placeholders.strip(tr) + trail
                if where == "text":
                    node.text = val
                else:
                    node.tail = val
        self._set_language(tgt)

    def _set_language(self, tgt: str) -> None:
        bcp = {"zh": "zh-Hans"}.get(tgt, tgt)
        for el in self.opf.iter(f"{{{DC}}}language"):
            el.text = bcp
        for doc in self.docs.values():
            root = doc.tree.getroot()
            if doc.kind == "xhtml":
                root.set(XML_LANG, bcp)
                root.set("lang", bcp)
                # Right-to-left/vertical layouts are kept; only fonts need a CJK fallback.
                if is_cjk(tgt):
                    self._ensure_cjk_fallback(root, tgt)

    def _ensure_cjk_fallback(self, root, tgt: str) -> None:
        head = next((c for c in root if _local(c.tag) == "head"), None)
        if head is None:
            return
        families = (
            '"Hiragino Mincho ProN", "Yu Mincho", "Noto Serif CJK JP", serif' if tgt == "ja"
            else '"Songti SC", "SimSun", "Noto Serif CJK SC", serif'
        )
        style = etree.SubElement(head, f"{{{XHTML}}}style")
        style.set("type", "text/css")
        # Applied at lowest specificity so the book's own font choices still win.
        style.text = f":root {{ font-family: {families}; }}"

    def write(self, out: Path) -> None:
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(out.suffix + ".part")
        with zipfile.ZipFile(tmp, "w") as zout:
            # The OCF spec requires `mimetype` first and stored uncompressed.
            zout.writestr(zipfile.ZipInfo("mimetype"), b"application/epub+zip", compress_type=zipfile.ZIP_STORED)
            for info in self.zip.infolist():
                if info.filename == "mimetype":
                    continue
                if info.filename in self.docs:
                    data = _serialize(self.docs[info.filename].tree)
                elif info.filename == self.opf_path:
                    data = _serialize(self.opf)
                else:
                    data = self.zip.read(info.filename)
                zi = zipfile.ZipInfo(info.filename, date_time=info.date_time)
                zi.compress_type = zipfile.ZIP_DEFLATED
                zi.external_attr = info.external_attr
                zout.writestr(zi, data)
        tmp.replace(out)


# ---- inline serialization ----------------------------------------------------

def serialize_inline(el, preserve_ws: bool = False) -> tuple[str, dict[str, etree._Element]]:
    """Turn a block's content into text with ⟦n⟧ markers for inline children."""
    mapping: dict[str, etree._Element] = {}
    counter = [0]

    def walk(node) -> str:
        parts = [node.text or ""]
        for child in node:
            if not isinstance(child.tag, str):  # comments, processing instructions
                parts.append(child.tail or "")
                continue
            name = _local(child.tag)
            if name == "ruby":
                # Furigana belongs to the source language; keep only the base text.
                base = (child.text or "") + "".join(
                    (c.tail or "") if _local(c.tag) in ("rt", "rp") else "".join(c.itertext()) + (c.tail or "")
                    for c in child
                )
                parts.append(base)
            elif name in SKIP_TAGS or name in VOID_INLINE or (len(child) == 0 and not (child.text or "").strip()):
                # Line breaks, images, empty anchors and untranslatable content (code,
                # math) travel as opaque void markers and are restored verbatim.
                counter[0] += 1
                n = counter[0]
                mapping[str(n)] = child
                parts.append(placeholders.void_tag(n))
            else:
                counter[0] += 1
                n = counter[0]
                mapping[str(n)] = child
                parts.append(placeholders.open_tag(n) + walk(child) + placeholders.close_tag(n))
            parts.append(child.tail or "")
        return "".join(parts)

    text = walk(el)
    return (text.strip("\n") if preserve_ws else _normalize_ws(text)), mapping


def rebuild_inline(el, translated: str, mapping: dict[str, etree._Element]) -> None:
    """Replace a block's content with the translation, re-creating inline elements."""
    originals = dict(mapping)
    for child in list(el):
        el.remove(child)
    el.text = None

    stack = [el]
    last: dict[int, etree._Element | None] = {id(el): None}

    def append_text(s: str) -> None:
        if not s:
            return
        cur = stack[-1]
        prev = last.get(id(cur))
        if prev is None:
            cur.text = (cur.text or "") + s
        else:
            prev.tail = (prev.tail or "") + s

    for tok in placeholders.tokenize(translated):
        if tok.kind == "text":
            append_text(tok.value)
        elif tok.kind == "void":
            orig = originals.get(tok.value)
            if orig is None:
                continue
            node = copy.deepcopy(orig)
            node.tail = None
            stack[-1].append(node)
            last[id(stack[-1])] = node
        elif tok.kind == "open":
            orig = originals.get(tok.value)
            if orig is None:
                stack.append(stack[-1])  # unknown marker: keep text in the current parent
                continue
            shell = etree.Element(orig.tag, attrib=dict(orig.attrib), nsmap=None)
            stack[-1].append(shell)
            last[id(stack[-1])] = shell
            stack.append(shell)
            last[id(shell)] = None
        elif tok.kind == "close" and len(stack) > 1:
            stack.pop()


def _has_visible_text(el) -> bool:
    for node in el.iter():
        if node is not el and node.tail and node.tail.strip():
            return True
        if _local(node.tag) not in SKIP_TAGS and node.text and node.text.strip():
            return True
    return False


def _has_loose_text(el) -> bool:
    if el.text and el.text.strip():
        return True
    return any(c.tail and c.tail.strip() for c in el)


_WS_RE = re.compile(r"[ \t\r\n]+")


def _normalize_ws(s: str) -> str:
    return _WS_RE.sub(" ", s).strip()


def _serialize(tree: etree._ElementTree) -> bytes:
    return etree.tostring(tree, xml_declaration=True, encoding="utf-8", standalone=None)


def read_info(path: Path) -> dict:
    book = EpubBook(path)
    segs = book.extract()
    sample = " ".join(s.plain for s in segs if s.kind == "para")[:4000]
    from ..translate.segments import estimate_tokens

    return {"title": book.title, "language": book.language, "sample": sample,
            "segments": len(segs), "chapters": len(book.spine_order),
            "tokens": sum(estimate_tokens(s.source) for s in segs)}
