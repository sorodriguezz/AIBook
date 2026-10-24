from folio_engine.eta import EtaTracker, Work
from folio_engine.translate import langdetect, placeholders, validators
from folio_engine.translate.pipeline import batches, parse_segs
from folio_engine.translate.segments import Segment


def test_placeholder_check():
    src = "She said ⟦1⟧no⟦/1⟧,⟦2/⟧ firmly."
    assert placeholders.check(src, "Ella dijo ⟦1⟧no⟦/1⟧,⟦2/⟧ con firmeza.") is None
    assert placeholders.check(src, "⟦2/⟧Ella dijo ⟦1⟧no⟦/1⟧.") is None  # reordering is fine
    assert "differ" in placeholders.check(src, "Ella dijo no, con firmeza.")
    assert "unbalanced" in placeholders.check("⟦1⟧a⟦2⟧b⟦/2⟧⟦/1⟧", "⟦1⟧a⟦2⟧b⟦/1⟧⟦/2⟧")


def test_langdetect():
    assert langdetect.detect("The wind was cold and the house was dark, but she did not turn back.") == "en"
    assert langdetect.detect("El viento era frío y la casa estaba a oscuras, pero ella no se dio la vuelta.") == "es"
    assert langdetect.detect("Le vent était froid et la maison était sombre, mais elle ne se retourna pas.") == "fr"
    assert langdetect.detect("Der Wind war kalt und das Haus war dunkel, aber sie drehte sich nicht um.") == "de"
    assert langdetect.detect("Il vento era freddo e la casa era buia, ma lei non si voltò.") == "it"
    assert langdetect.detect("風は冷たく、家は暗かったが、彼女は振り返らなかった。") == "ja"
    assert langdetect.detect("风很冷，房子很暗，但她没有回头。") == "zh"


def test_validators():
    src = "Mira counted 12 coins and handed them to the old ferryman without a word."
    good = "Mira contó 12 monedas y se las entregó al viejo barquero sin decir palabra."
    assert validators.validate(src, good, "en", "es") == []
    assert any("numbers" in p for p in validators.validate(src, good.replace("12", "13"), "en", "es"))
    assert any("short" in p for p in validators.validate(src, "Mira contó.", "en", "es"))
    assert any("untranslated" in p for p in validators.validate(src, src, "en", "es"))


def test_parse_segs_and_batches():
    out = '<seg id="1">Uno</seg>\n<seg id="2" type="heading">Dos</seg>'
    assert parse_segs(out, 3) == ["Uno", "Dos", None]
    segs = [Segment(str(i), i // 5, "para", "word " * 60) for i in range(12)]
    bs = batches(segs)
    assert all(len({s.chapter for s in b}) == 1 for b in bs)  # never cross chapters


def test_eta_converges():
    eta = EtaTracker()
    eta.plan(Work(10000, 2000))
    assert eta.fraction_done() == 0
    eta.record(Work(1000, 200), 1000, 200, 2.0, 10.0)  # 500 pp/s, 20 tg/s
    assert eta.prompt_tps == 500 and eta.gen_tps == 20
    left = eta.seconds_left()
    assert 100 < left < 116  # 9000/500 + 1800/20 = 108
    assert 0.05 < eta.fraction_done() < 0.2


def test_ocr_skips_covers_and_artwork():
    from folio_engine.formats.ocr import Block, Line, is_prose_page

    title = [Block([Line(50, 100, 400, 160, "NIGHTFALL")]), Block([Line(80, 600, 300, 620, "A NOVEL")])]
    assert not is_prose_page(title, cjk=False)
    prose = [Block([Line(50, 100 + 14 * i, 500, 112 + 14 * i, "The river ran dark under the old bridge that night")
                    for i in range(8)])]
    assert is_prose_page(prose, cjk=False)
    cjk = [Block([Line(50, 100 + 14 * i, 500, 112 + 14 * i, "川は古い橋の下を暗く流れていた") for i in range(4)])]
    assert is_prose_page(cjk, cjk=True)
