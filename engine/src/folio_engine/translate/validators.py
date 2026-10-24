"""Checks run on every translated segment before it is accepted."""

import re

from .. import langs
from . import langdetect, placeholders

_NUM_RE = re.compile(r"\d+(?:[.,]\d+)*")
_FULLWIDTH_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")


def _numbers(s: str) -> list[str]:
    s = placeholders.strip(s).translate(_FULLWIDTH_DIGITS)
    return sorted(re.sub(r"[.,]", "", n) for n in _NUM_RE.findall(s))


def validate(source: str, translated: str, src: str, tgt: str) -> list[str]:
    """Return a list of problems; empty means the translation is acceptable."""
    problems: list[str] = []
    if not translated or not placeholders.has_text(translated):
        return ["empty translation"] if placeholders.has_text(source) else []

    err = placeholders.check(source, translated)
    if err:
        problems.append(err)

    src_plain = placeholders.strip(source).strip()
    dst_plain = placeholders.strip(translated).strip()

    # Very short strings (names, "IV", "***") may legitimately stay identical.
    if len(src_plain) > 30 and src_plain == dst_plain:
        problems.append("left untranslated")

    if len(src_plain) >= 40:
        lo, hi = langs.char_length_bounds(src, tgt)
        ratio = len(dst_plain) / max(1, len(src_plain))
        if ratio < lo:
            problems.append(f"too short (ratio {ratio:.2f}); possible omission")
        elif ratio > hi:
            problems.append(f"too long (ratio {ratio:.2f}); possible addition or commentary")

    if langdetect.is_language(dst_plain, tgt) is False:
        problems.append(f"output does not look like {langs.LANGUAGES[tgt].name}")

    # Numbers should survive. CJK targets may spell numbers out, so only Latin↔Latin
    # and CJK→Latin pairs are held to this.
    if not langs.is_cjk(tgt) and not langs.is_cjk(src):
        if _numbers(source) != _numbers(translated):
            problems.append("numbers changed")

    if re.search(r"(?i)^(here is|translation:|traducción:|note:)", dst_plain):
        problems.append("contains commentary")
    return problems
