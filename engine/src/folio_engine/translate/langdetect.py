"""Small, dependency-free language identifier for the seven supported languages.

Script ranges separate ja/zh from the rest; stop-word hits separate the Latin
languages. Good enough to validate that a translation is in the target
language and to pre-select the source language of a book.
"""

import re
from collections import Counter

_WORD_RE = re.compile(r"[a-zA-ZÀ-ÿßœæ']+")

_STOPWORDS = {
    "en": "the and of to a in that is was he she it you for with his her on as had but not at be they said".split(),
    "es": "el la de que y en los se del las un por con no una su para es lo como más pero sus le ya o fue dijo".split(),
    "fr": "le la de et les des un une du en que est dans qui il elle pas ne pour sur au avec se mais dit vous".split(),
    "de": "der die und das ist nicht ein eine zu den mit sich des auf für er sie es im dem war ich sagte aber".split(),
    "it": "il la di che e un una per non è del della in le con si lo gli ma sono era disse anche come".split(),
}
_STOP_SETS = {k: set(v) for k, v in _STOPWORDS.items()}


def detect(text: str) -> str | None:
    if not text or not text.strip():
        return None
    kana = han = latin = 0
    for ch in text:
        o = ord(ch)
        if 0x3040 <= o <= 0x30FF:
            kana += 1
        elif 0x4E00 <= o <= 0x9FFF or 0x3400 <= o <= 0x4DBF:
            han += 1
        elif ch.isalpha() and o < 0x250:
            latin += 1
    if kana + han > latin * 0.5 and kana + han >= 2:
        # Japanese text almost always contains kana; Chinese never does.
        return "ja" if kana >= max(1, (kana + han) * 0.08) else "zh"
    words = [w.lower() for w in _WORD_RE.findall(text)]
    if not words:
        return None
    scores = Counter()
    for w in words:
        for lang, stops in _STOP_SETS.items():
            if w in stops:
                scores[lang] += 1
    if not scores:
        return None
    best, hits = scores.most_common(1)[0]
    return best if hits >= 1 else None


def is_language(text: str, lang: str) -> bool | None:
    """True/False when confident, None when the text is too short to tell."""
    stripped = text.strip()
    if len(stripped) < (6 if lang in ("ja", "zh") else 25):
        return None
    found = detect(stripped)
    if found is None:
        return None
    return found == lang
