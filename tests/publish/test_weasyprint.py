from jerome.publish import FONT_DIR


def test_weasyprint_renders_a_page(weasy):
    assert weasy.HTML(string="<p>x</p>").write_pdf().startswith(b"%PDF-")


def test_fonts_are_shipped():
    names = sorted(p.name for p in FONT_DIR.glob("*.ttf"))
    assert names == [
        "EBGaramond-Bold.ttf",
        "EBGaramond-BoldItalic.ttf",
        "EBGaramond-Italic.ttf",
        "EBGaramond-Regular.ttf",
    ]
    assert "SIL Open Font License, Version 1.1" in (FONT_DIR / "OFL.txt").read_text()
