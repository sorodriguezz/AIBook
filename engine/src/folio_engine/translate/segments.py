from dataclasses import dataclass, field

from . import placeholders

SEGMENT_KINDS = ("heading", "para", "cell", "caption", "attr", "meta", "toc", "ocr")


@dataclass
class Segment:
    id: str
    chapter: int  # index used for batching and progress
    kind: str
    source: str  # text with ⟦n⟧ markers
    chapter_title: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def plain(self) -> str:
        return placeholders.strip(self.source)


def estimate_tokens(text: str) -> int:
    """Cheap token estimate; calibrated at runtime against real usage."""
    cjk = sum(1 for ch in text if _is_cjk_char(ch))
    other = len(text) - cjk
    return max(1, int(cjk * 1.05 + other / 3.7))


def _is_cjk_char(ch: str) -> bool:
    o = ord(ch)
    return (
        0x3040 <= o <= 0x30FF  # hiragana, katakana
        or 0x3400 <= o <= 0x4DBF
        or 0x4E00 <= o <= 0x9FFF
        or 0xF900 <= o <= 0xFAFF
        or 0xFF00 <= o <= 0xFFEF
        or 0x3000 <= o <= 0x303F
    )
