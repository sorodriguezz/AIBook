from types import SimpleNamespace as NS

from folio_engine.formats.pdf_layout import (
    breaks_before,
    is_text_rule,
    page_number_split,
    spread_pages,
    trim_leaders,
)


def box(x, y, x2, y2):
    return NS(x=x, y=y, x2=x2, y2=y2)


BODY = 450.0


def test_toc_entries_split_but_prose_does_not():
    short = box(86, 450, 116, 460)
    assert breaks_before("ONE", "TWO", short, box(86, 427, 119, 437), BODY)
    # A wide line ending in a page number is a TOC entry.
    wide = box(72, 450, 520, 460)
    assert breaks_before("Principles of Shaping 20", "Wireframes are too concrete 20", wide, wide, BODY)
    assert breaks_before("Targeting risk ........ 15", "How this book is organized ..... 16", wide, wide, BODY)
    # Prose: full-width lines, or a sentence running on in lowercase.
    assert not breaks_before("My fire-filled palm illuminated the jagged", "Tunnels twisted", wide, wide, BODY)
    assert not breaks_before("He was", "lost and only", short, short, BODY)
    assert not breaks_before("well-", "Known", short, short, BODY)


def test_centred_two_line_title_stays_together():
    assert not breaks_before("THE GIRL WHO", "DRANK THE MOON", box(250, 500, 360, 520), box(240, 470, 370, 490), BODY)


def test_page_number_split():
    def run(text, gap_at=None, gap=0.0):
        xs, x = [], 0.0
        for i, _ in enumerate(text):
            if i == gap_at:
                x += gap
            xs.append((x, x + 5))
            x += 5
        return page_number_split(list(text), xs, 10)

    assert run("Growing pains11", gap_at=13, gap=200) == 13
    assert run("Targeting risk .....15") == 20
    assert run("Born in 1984") is None  # no gap, no leader
    assert run("2024", gap_at=2, gap=100) is None  # no title
    assert run("Chapter") is None


def test_text_rule_is_an_underline_or_leader_only():
    line = [box(72, 580, 120, 593)]
    assert is_text_rule(box(72, 582, 120, 582), line)  # underline near the baseline
    assert is_text_rule(box(72, 586, 500, 586.5), line)  # leader through the line
    assert not is_text_rule(box(72, 540, 500, 540), line)  # a separate rule below
    assert not is_text_rule(box(300, 582, 400, 582), line)  # not under the text
    assert not is_text_rule(box(72, 560, 120, 600), line)  # a box, not a line


def unit(ch, w=5.0):
    return NS(char=None, unicode=ch, width=w, formular=None)


def test_trim_leaders_fits_the_line():
    units = [unit(c) for c in "Title "] + [unit(".") for _ in range(40)] + [unit(c) for c in " 12"]
    trim_leaders(units, 200)
    assert sum(u.width for u in units) * 1.03 + 1 <= 200 + 5
    assert "".join(u.unicode for u in units).startswith("Title ...")
    short = [unit(c) for c in "Wait... what?"]
    trim_leaders(short, 10)  # no leader run to trim
    assert len(short) == 13


def test_spread_pages():
    assert spread_pages(5, 30) == [0, 1, 2, 3, 4]
    pages = spread_pages(300, 30)
    assert len(pages) == 30 and pages[0] == 0 and pages[-1] == 290
