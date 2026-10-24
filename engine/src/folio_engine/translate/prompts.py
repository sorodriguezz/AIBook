"""Prompt construction for the analysis, translation and review passes."""

import json

from ..langs import LANGUAGES
from .segments import Segment

MARKER_RULES = (
    "The text contains formatting markers like ⟦1⟧…⟦/1⟧ (styled span) and ⟦2/⟧ (line break or "
    "inline image). Keep every marker exactly as written, around the words that correspond to "
    "the marked words in your translation. Never add, drop, renumber or translate markers."
)

HONORIFICS = {
    "keep": "Keep Japanese honorifics (-san, -kun, -chan, -sama, -senpai, -sensei) attached to names.",
    "adapt": "Do not keep Japanese honorifics; convey the same respect or intimacy through natural "
             "target-language register (forms of address, formality, word choice).",
}


def style_block(bible: dict | None) -> str:
    if not bible:
        return ""
    style = bible.get("style") or {}
    parts = []
    if style.get("genre"):
        parts.append(f"Genre: {style['genre']}.")
    if style.get("tone"):
        parts.append(f"Tone and atmosphere: {style['tone']}.")
    if style.get("narration"):
        parts.append(f"Narration: {style['narration']}.")
    if style.get("register"):
        parts.append(f"Register: {style['register']}.")
    if style.get("notes"):
        parts.append(f"Style notes: {style['notes']}")
    return "\n".join(parts)


def relevant_glossary(bible: dict | None, text: str) -> tuple[list[dict], list[dict]]:
    """Characters and terms whose source form appears in `text`."""
    if not bible:
        return [], []
    low = text.lower()

    def hit(entry: dict) -> bool:
        names = [entry.get("source", "")] + list(entry.get("aliases") or [])
        return any(n and n.lower() in low for n in names)

    chars = [c for c in bible.get("characters", []) if hit(c)]
    terms = [t for t in bible.get("terms", []) if hit(t)]
    return chars, terms


def glossary_block(chars: list[dict], terms: list[dict]) -> str:
    lines = []
    if chars:
        lines.append("Characters (use these renderings and keep each voice consistent):")
        for c in chars:
            bits = [f"{c['source']} → {c.get('target') or c['source']}"]
            if c.get("gender"):
                bits.append(c["gender"])
            if c.get("voice"):
                bits.append(f"voice: {c['voice']}")
            if c.get("address"):
                bits.append(f"addressing: {c['address']}")
            lines.append("- " + "; ".join(bits))
    if terms:
        lines.append("Fixed terminology (always translate exactly like this):")
        for t in terms:
            note = f" ({t['note']})" if t.get("note") else ""
            lines.append(f"- {t['source']} → {t.get('target') or t['source']}{note}")
    return "\n".join(lines)


def translator_system(src: str, tgt: str, bible: dict | None, chars, terms, honorifics: str,
                      book_type: str = "literary") -> str:
    s, t = LANGUAGES[src], LANGUAGES[tgt]
    if book_type == "technical":
        return _technical_system(src, tgt, bible, terms)
    rules = [
        f"You are an award-winning literary translator from {s.name} into {t.name}, translating a "
        f"published book for {t.name}-speaking readers. Your translation must read as if it had "
        f"been written in {t.name} by a skilled author, while staying completely faithful to the "
        "meaning, tone, rhythm and intent of the original.",
        "",
        "Principles:",
        "1. Translate meaning, not words. Render idioms, proverbs, wordplay, slang and set phrases "
        f"with the {t.name} expression a native writer would use, preserving the effect (humor, "
        "irony, menace, tenderness). Never translate an idiom literally if it would sound odd.",
        "2. Preserve each character's voice: age, social class, formality, verbal tics, dialect "
        "and emotional state. A rough mercenary and a court lady must not sound alike.",
        "3. Keep the author's style: sentence length and rhythm, imagery, metaphors, repetitions "
        "used for effect, and the narrative tense and person.",
        "4. Be complete: translate every sentence. Do not summarize, soften, censor, explain or "
        "add anything. Romance, violence and profanity are rendered with the same intensity.",
        "5. Names, places and invented terms follow the glossary exactly; otherwise keep proper "
        "names as in the original (transliterate between scripts in the standard way).",
        f"6. Typography: {t.dialogue}",
        f"7. {MARKER_RULES}",
        "8. Output only the translation in the requested format — no notes, no explanations.",
    ]
    if src == "ja" or tgt == "ja":
        rules.append(f"9. {HONORIFICS.get(honorifics, HONORIFICS['adapt'])}")
    style = style_block(bible)
    if style:
        rules += ["", "About this book:", style]
    gl = glossary_block(chars, terms)
    if gl:
        rules += ["", gl]
    return "\n".join(rules)


def _technical_system(src: str, tgt: str, bible: dict | None, terms) -> str:
    s, t = LANGUAGES[src], LANGUAGES[tgt]
    rules = [
        f"You are an expert technical translator from {s.name} into {t.name}, translating a published "
        f"technical or non-fiction book (software, engineering, science, business) for {t.name}-speaking "
        "professionals. The translation must be exact and read like a well-edited technical book "
        f"written in {t.name}.",
        "",
        "Principles:",
        "1. Accuracy first: keep the precise meaning, conditions, numbers, units and caveats. Do not "
        "simplify, summarize, explain or add anything.",
        f"2. Terminology: use the terms {t.name}-speaking practitioners actually use. Many stay in English "
        "(e.g. framework, commit, pod, deployment, backend, machine learning) when that is the established "
        "usage; otherwise use the standard technical translation. Follow the glossary exactly and use the "
        "same term for the same concept throughout the book.",
        "3. Never translate code, shell commands, command-line options, function/class/variable names, "
        "file names and paths, URLs, configuration keys, API fields, log or error messages quoted from "
        "software, or product names. Copy them exactly as written.",
        f"4. Write clear, direct, natural {t.name}; avoid calques and false friends. Keep the author's way "
        f"of addressing the reader using the usual convention of {t.name} technical books.",
        "5. Keep lists, headings and captions short and parallel, as in the original.",
        f"6. {MARKER_RULES}",
        "7. Output only the translation in the requested format — no notes, no explanations.",
    ]
    style = style_block(bible)
    if style:
        rules += ["", "About this book:", style]
    gl = glossary_block([], terms)
    if gl:
        rules += ["", gl]
    return "\n".join(rules)


def _seg_xml(i: int, seg: Segment, text: str | None = None) -> str:
    kind = {"heading": " type=\"heading\"", "toc": " type=\"heading\"", "cell": " type=\"table-cell\"",
            "caption": " type=\"caption\"", "attr": " type=\"image-description\""}.get(seg.kind, "")
    return f'<seg id="{i}"{kind}>{seg.source if text is None else text}</seg>'


def translate_user(
    batch: list[Segment],
    context: list[tuple[str, str]],
    lookahead: str,
    src: str,
    tgt: str,
) -> str:
    s, t = LANGUAGES[src], LANGUAGES[tgt]
    parts = []
    if context:
        parts.append(
            "Previous passage, already translated (for continuity only; do not translate again):"
        )
        for o, tr in context:
            parts.append(f"[{s.name}] {o}\n[{t.name}] {tr}")
        parts.append("")
    parts.append(
        f"Translate each <seg> into {t.name}. Return exactly one <seg> per input segment with the "
        "same id, in the same order, containing only the translation:"
    )
    parts.extend(_seg_xml(i + 1, seg) for i, seg in enumerate(batch))
    if lookahead:
        parts += ["", f"(What follows next, for context only, do not translate: {lookahead})"]
    return "\n".join(parts)


def review_system(src: str, tgt: str, bible: dict | None, chars, terms, honorifics: str,
                  book_type: str = "literary") -> str:
    s, t = LANGUAGES[src], LANGUAGES[tgt]
    base = translator_system(src, tgt, bible, chars, terms, honorifics, book_type)
    if book_type == "technical":
        return base + "\n\n" + "\n".join([
            f"You are now acting as the technical editor reviewing a draft {t.name} translation against "
            f"the {s.name} original. Check each segment for:",
            "- omissions, additions, or changes in meaning, numbers, units or conditions;",
            "- code, commands, identifiers, paths or product names that were translated or altered;",
            "- inconsistent or non-standard terminology; glossary violations;",
            "- calques, false friends and unnatural phrasing.",
            "Return the final, corrected translation for every segment. If a segment is already "
            "correct, return it unchanged. Keep all ⟦…⟧ markers.",
        ])
    return base + "\n\n" + "\n".join([
        f"You are now acting as the senior editor reviewing a draft {t.name} translation against "
        f"the {s.name} original. Check each segment for:",
        "- omissions, additions, or mistranslations (wrong meaning, wrong subject, wrong tense);",
        "- literal renderings of idioms or set phrases that a native reader would find unnatural;",
        "- voice and register inconsistencies; glossary violations;",
        "- awkward rhythm, calques, false friends, and punctuation conventions.",
        "Return the final, corrected translation for every segment. If a segment is already "
        "excellent, return it unchanged. Keep all ⟦…⟧ markers.",
    ])


def review_user(batch: list[Segment], drafts: list[str], context: list[tuple[str, str]], src: str, tgt: str) -> str:
    s, t = LANGUAGES[src], LANGUAGES[tgt]
    parts = []
    if context:
        parts.append("Preceding text (final version, for continuity):")
        parts.extend(f"[{t.name}] {tr}" for _, tr in context)
        parts.append("")
    parts.append("Segments to review:")
    for i, (seg, d) in enumerate(zip(batch, drafts), 1):
        parts.append(f'<orig id="{i}">{seg.source}</orig>')
        parts.append(f'<draft id="{i}">{d}</draft>')
    parts += [
        "",
        "Return exactly one <seg id=\"n\">…</seg> per segment with the final translation, in order.",
    ]
    return "\n".join(parts)


# ---- Book analysis ("bible") -------------------------------------------------

BIBLE_SCHEMA_HINT = {
    "characters": [
        {"source": "name as written in the book", "aliases": ["nicknames", "titles"],
         "gender": "male/female/other/unknown", "voice": "how they speak (formal, crude, archaic, childish…)",
         "address": "how they address others / are addressed (formal/informal, honorifics)"}
    ],
    "terms": [
        {"source": "invented or special term, place, title, spell, creature, organization",
         "note": "what it is"}
    ],
}


TECH_SCHEMA_HINT = {
    "terms": [
        {"source": "technical term, concept, acronym, tool or product name as written",
         "note": "what it means in this book"}
    ],
}


def bible_extract_messages(src: str, excerpt: str, known: dict, book_type: str = "literary") -> list[dict]:
    s = LANGUAGES[src]
    if book_type == "technical":
        known_terms = [t["source"] for t in known.get("terms", [])][:150]
        return [
            {"role": "system", "content": (
                f"You analyze {s.name} technical books to prepare a terminology glossary for translators. "
                "Respond with a single JSON object only."
            )},
            {"role": "user", "content": (
                "From the excerpt below, list NEW technical terms, concepts, acronyms, tools and product "
                "names that a translator must render consistently across the book. Skip everyday words "
                "and code identifiers.\n"
                f"Already known terms: {json.dumps(known_terms, ensure_ascii=False)}\n"
                f"JSON shape: {json.dumps(TECH_SCHEMA_HINT, ensure_ascii=False)}\n"
                "Use exact spellings from the text.\n\n"
                f"Excerpt:\n{excerpt}"
            )},
        ]
    known_names = [c["source"] for c in known.get("characters", [])][:80]
    known_terms = [t["source"] for t in known.get("terms", [])][:120]
    return [
        {"role": "system", "content": (
            f"You analyze {s.name} books to prepare a translation bible. "
            "Respond with a single JSON object only."
        )},
        {"role": "user", "content": (
            "From the excerpt below, list NEW named characters and NEW special terms (places, "
            "titles, invented words, magic, creatures, organizations, recurring catchphrases) that "
            "are not already known. Also add details for known characters if the excerpt reveals "
            "their gender or speech style.\n"
            f"Already known characters: {json.dumps(known_names, ensure_ascii=False)}\n"
            f"Already known terms: {json.dumps(known_terms, ensure_ascii=False)}\n"
            f"JSON shape: {json.dumps(BIBLE_SCHEMA_HINT, ensure_ascii=False)}\n"
            "Use exact spellings from the text. Skip common nouns.\n\n"
            f"Excerpt:\n{excerpt}"
        )},
    ]


def bible_style_messages(src: str, tgt: str, sample: str, known: dict, book_type: str = "literary") -> list[dict]:
    s, t = LANGUAGES[src], LANGUAGES[tgt]
    if book_type == "technical":
        return [
            {"role": "system", "content": "You are a technical editor. Respond with a single JSON object only."},
            {"role": "user", "content": (
                f"Characterize this {s.name} book for a translator working into {t.name}. "
                "Return JSON: {\"genre\": str (subject area), \"tone\": str, \"narration\": str (how the "
                "author addresses the reader), \"register\": str (intended audience and level), \"notes\": "
                "str (conventions to keep: code listings, notation, units, recurring examples)}. Write the "
                f"values in English, concisely.\n\nOpening of the book:\n{sample}"
            )},
        ]
    chars = [c["source"] for c in known.get("characters", [])][:30]
    return [
        {"role": "system", "content": "You are a literary editor. Respond with a single JSON object only."},
        {"role": "user", "content": (
            f"Characterize this {s.name} book for a translator working into {t.name}. "
            "Return JSON: {\"genre\": str, \"tone\": str, \"narration\": str (person and tense), "
            "\"register\": str, \"notes\": str (anything a translator must keep: archaic speech, "
            "dialects, poetry, humor, recurring motifs)}. Write the values in English, concisely.\n"
            f"Main characters: {json.dumps(chars, ensure_ascii=False)}\n\nOpening of the book:\n{sample}"
        )},
    ]


def bible_render_messages(src: str, tgt: str, entries: list[dict], kind: str, style: dict,
                          book_type: str = "literary") -> list[dict]:
    s, t = LANGUAGES[src], LANGUAGES[tgt]
    if book_type == "technical":
        return [
            {"role": "system", "content": f"You are a technical translator from {s.name} into {t.name}. Respond with JSON only."},
            {"role": "user", "content": (
                f"For each term give the form a published {t.name} technical book would use. Keep it exactly "
                f"as in the original when {t.name}-speaking professionals use it that way (product and tool "
                "names, most acronyms, established English jargon); otherwise give the standard translation.\n"
                f"Book: {json.dumps(style, ensure_ascii=False)}\n"
                "Return {\"items\": [{\"source\": str, \"target\": str}]} preserving every source exactly.\n"
                f"{json.dumps([{'source': e['source'], 'note': e.get('note') or ''} for e in entries], ensure_ascii=False)}"
            )},
        ]
    rule = {
        "characters": (
            "For each character give the name as it should appear in the translation. Keep Latin-"
            "script names unchanged between European languages; transliterate between scripts "
            "using the standard system (Hepburn romaji, pinyin without tones, conventional katakana "
            "or Chinese transliteration). Meaningful nicknames may be translated."
        ),
        "terms": (
            f"For each term give the rendering a published {t.name} edition would use: translate "
            "meaningful invented words so they evoke the same image, keep or transliterate pure "
            "proper names."
        ),
    }[kind]
    return [
        {"role": "system", "content": f"You are a literary translator from {s.name} into {t.name}. Respond with JSON only."},
        {"role": "user", "content": (
            f"{rule}\nBook: {json.dumps(style, ensure_ascii=False)}\n"
            "Return {\"items\": [{\"source\": str, \"target\": str}]} preserving every source exactly.\n"
            f"{json.dumps([{'source': e['source'], 'note': e.get('note') or e.get('voice') or ''} for e in entries], ensure_ascii=False)}"
        )},
    ]
