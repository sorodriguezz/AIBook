"""Supported languages and the typographic conventions each one expects."""

from dataclasses import dataclass

from .errors import UserError


@dataclass(frozen=True)
class Language:
    code: str
    name: str  # English name, used inside prompts
    native: str
    cjk: bool
    dialogue: str  # how dialogue is punctuated in published fiction
    quotes: str


LANGUAGES: dict[str, Language] = {
    "en": Language("en", "English", "English", False,
                   "Dialogue in double quotation marks “…”, with single quotes ‘…’ for nested quotes.",
                   "“…” and ‘…’"),
    "es": Language("es", "Spanish", "Español", False,
                   "Dialogue introduced with the em dash (raya) —, following RAE conventions: "
                   "—Texto —dijo él—. Más texto. Never use quotation marks for spoken dialogue.",
                   "« » for quotations, “ ” for nested ones"),
    "fr": Language("fr", "French", "Français", False,
                   "Dialogue with guillemets « … » (with non-breaking spaces inside) and/or the tiret — "
                   "for changes of speaker, following French publishing conventions.",
                   "« … »"),
    "de": Language("de", "German", "Deutsch", False,
                   "Dialogue in German quotation marks „…“ (or »…« if the source style is chevrons).",
                   "„…“"),
    "it": Language("it", "Italian", "Italiano", False,
                   "Dialogue in caporali « … » or with the lineetta —, as in Italian published fiction.",
                   "« … »"),
    "ja": Language("ja", "Japanese", "日本語", True,
                   "Dialogue in 「…」, nested quotes in 『…』. Use full-width punctuation (。、！？).",
                   "「…」 and 『…』"),
    "zh": Language("zh", "Chinese (Simplified)", "中文", True,
                   "Dialogue in “…” (Simplified Chinese convention), full-width punctuation (，。！？：).",
                   "“…” and ‘…’"),
}

# Rough ratio of target tokens to source tokens, used for ETA estimates before
# real measurements arrive. CJK scripts pack more meaning per token.
_LATIN = {"en", "es", "fr", "de", "it"}
_RATIO_TO_EN = {"en": 1.0, "es": 1.15, "fr": 1.18, "de": 1.2, "it": 1.15, "ja": 1.1, "zh": 0.9}


def expansion_ratio(src: str, tgt: str) -> float:
    return _RATIO_TO_EN[tgt] / _RATIO_TO_EN[src]


def is_cjk(code: str) -> bool:
    return LANGUAGES[code].cjk


def validate_pair(src: str, tgt: str) -> None:
    if src not in LANGUAGES or tgt not in LANGUAGES:
        raise UserError("unsupported_language", f"Unsupported language pair {src}->{tgt}")
    if src == tgt:
        raise UserError("same_language", "Source and target languages must differ", lang=src)


def char_length_bounds(src: str, tgt: str) -> tuple[float, float]:
    """Acceptable range for len(translation)/len(source) in characters.

    Wide on purpose: it only exists to catch truncated or runaway outputs.
    """
    s_cjk, t_cjk = is_cjk(src), is_cjk(tgt)
    if s_cjk == t_cjk:
        return (0.45, 2.4)
    if s_cjk and not t_cjk:
        return (1.1, 6.0)  # 1 CJK char ≈ 2–4 Latin chars
    return (0.12, 1.0)
