"""Telling technical books (software, engineering, science) from literary ones.

A cheap heuristic run when a book is opened, before any model is loaded. It
only preselects the option in the UI; the user has the last word.
"""

import re

# Fonts used for code listings. BabelDOC already treats "*Mono*" and "*Code*"
# as untranslatable; these are the common ones it misses.
MONO_FONT_RE = re.compile(
    r"mono|(?<!uni)code|courier|consola|menlo|monaco|inconsolata|lucida ?console|typewriter|andale|"
    r"letter ?gothic|fira ?code|cousine|hack-|ocr-?a|\btt\d|cmtt",
    re.I,
)

_CODE_RE = re.compile(
    r"\b\w+\(\)|\b[a-z]+_[a-z_]+\b|\b[a-z]+[A-Z]\w*\(|^\s*\$ \w|--[a-z][\w-]+|"
    r"\b(?:https?://|www\.)|[{};]\s*$|</?\w+>|\b\w+\.(?:py|js|ts|java|go|rs|yaml|yml|json|sh|xml|sql|md)\b",
    re.M,
)
_TECH_WORDS = re.compile(
    r"\b(?:algorithm|api|application|architecture|cluster|code|command|compiler|configur\w*|container|"
    r"database|debug\w*|deploy\w*|developer|docker|endpoint|example|file|framework|function|"
    r"implementation|install\w*|interface|kubernetes|library|linux|method|module|network|node|"
    r"object|package|parameter|programming|protocol|python|query|repository|request|runtime|"
    r"script|security|server|service|software|syntax|system|terminal|variable|version|"
    r"figure|table|section|chapter \d+|diagram|data|computer|binary|memory|design|user|product|research|"
    # Spanish, French, German, Italian: books get translated from these too.
    r"aplicaci[oó]n|arquitectura|c[oó]digo|configuraci[oó]n|datos|desarrollo|despliegue|funci[oó]n|"
    r"gesti[oó]n|herramienta|implementaci[oó]n|infraestructura|plataforma|seguridad|servidor|"
    r"sistema|usuario|nube|proceso|base de datos|algoritmos?|ordenador|computadora|programa|programaci[oó]n|"
    r"binario|memoria|dise[ñn]o|producto|interfaz|investigaci[oó]n|m[ée]todo|ejemplo|figura|tabla|"
    r"donn[ée]es|serveur|r[ée]seau|logiciel|s[ée]curit[ée]|syst[èe]me|fonction|"
    r"Daten|Server|Netzwerk|Sicherheit|Anwendung|Funktion|"
    r"dati|sicurezza|applicazione|funzione|sistema)\b",
    re.I,
)
_ACRONYM_RE = re.compile(r"\b[A-Z][A-Z0-9]{1,5}s?\b")
# Dialogue: paragraphs that open with a dash or quote, and speech verbs. Quote
# marks elsewhere are common in non-fiction («término») and say nothing.
_DIALOGUE_RE = re.compile(
    r"^\s*[—―“\"«]|\b(?:said|asked|whispered|replied|shouted|murmured|"
    r"dijo|preguntó|respondió|susurró|gritó|murmuró|dit|demanda|sagte|fragte|disse)\b",
    re.M,
)
# Class names publishers give to code listings in EPUBs.
CODE_CLASS_RE = re.compile(r"code|c[oó]digo|courier|listing|program|mono|console|terminal|snippet|\bpre\b", re.I)


def classify(text: str, code_font_share: float = 0.0, code_blocks: int = 0) -> str:
    """'technical' or 'literary' from a text sample and layout hints."""
    words = max(1, len(text.split()))
    tech = len(_TECH_WORDS.findall(text)) / words
    code = len(_CODE_RE.findall(text)) / words
    dialogue = len(_DIALOGUE_RE.findall(text)) / words
    acronyms = len(_ACRONYM_RE.findall(text)) / words
    score = tech * 6 + code * 10 + min(acronyms, 0.08) * 3 + min(code_font_share, 0.3) * 4 + min(code_blocks, 40) / 40 - dialogue * 8
    return "technical" if score >= 0.15 else "literary"
