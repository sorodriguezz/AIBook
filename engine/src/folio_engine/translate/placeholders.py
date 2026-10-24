"""Inline-formatting markers the model must carry through translation.

Inline markup (<em>, <a>, <span>…) is replaced by ⟦n⟧…⟦/n⟧ pairs and void
elements (<br/>, inline <img/>) by ⟦n/⟧. These are rare in natural text, short
in tokens, and easy to validate.
"""

import re
from dataclasses import dataclass

TOKEN_RE = re.compile(r"⟦(/?)(\d+)(/?)⟧")


def open_tag(n: int) -> str:
    return f"⟦{n}⟧"


def close_tag(n: int) -> str:
    return f"⟦/{n}⟧"


def void_tag(n: int) -> str:
    return f"⟦{n}/⟧"


@dataclass
class Token:
    kind: str  # "text" | "open" | "close" | "void"
    value: str  # text, or the marker id as a string


def tokenize(s: str) -> list[Token]:
    out: list[Token] = []
    pos = 0
    for m in TOKEN_RE.finditer(s):
        if m.start() > pos:
            out.append(Token("text", s[pos:m.start()]))
        closing, num, void = m.group(1), m.group(2), m.group(3)
        if closing:
            out.append(Token("close", num))
        elif void:
            out.append(Token("void", num))
        else:
            out.append(Token("open", num))
        pos = m.end()
    if pos < len(s):
        out.append(Token("text", s[pos:]))
    return out


def signature(s: str) -> list[str]:
    """Ordered list of markers, e.g. ['1', '/1', '2/']."""
    return [("/" if c else "") + n + ("/" if v else "") for c, n, v in TOKEN_RE.findall(s)]


def check(source: str, translated: str) -> str | None:
    """Return an error description if markers are missing, extra or unbalanced.

    Order may legitimately change between languages, so only the multiset of
    markers and correct nesting are enforced.
    """
    src, dst = signature(source), signature(translated)
    if sorted(src) != sorted(dst):
        return f"markers differ: expected {src}, got {dst}"
    stack: list[str] = []
    for tok in dst:
        if tok.endswith("/"):
            continue
        if tok.startswith("/"):
            if not stack or stack[-1] != tok[1:]:
                return f"unbalanced marker {tok}"
            stack.pop()
        else:
            stack.append(tok)
    if stack:
        return f"unclosed markers {stack}"
    return None


def strip(s: str) -> str:
    return TOKEN_RE.sub("", s)


def has_text(s: str) -> bool:
    return bool(strip(s).strip())
