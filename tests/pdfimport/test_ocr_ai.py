import io
from decimal import Decimal

import pytest
from PIL import Image

from jerome.config import Settings
from jerome.models import FakeProvider, ModelOutputError, Registry
from jerome.models.registry import PassConfig
from jerome.pdfimport.ocr_ai import SCHEMA, ai_page, estimate_ocr

CFG = PassConfig("fake", "claude-opus-5-5", "high")


def _page(width=100, height=200) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(out, "PNG")
    return out.getvalue()


def _block(kind, text="", level=1, note=None, box=None):
    return {"kind": kind, "text": text, "level": level, "note": note, "box": box}


def test_ai_page_parses_runs_and_crops_images():
    reply = {
        "blocks": [
            _block("heading", "Chapter One", 1, box=[0.1, 0.05, 0.9, 0.1]),
            _block("paragraph", "The *ship* came[^1] in.", box=[0.1, 0.2, 0.9, 0.4]),
            _block("footnote", "A **note**.", note=1, box=[0.1, 0.45, 0.9, 0.48]),
            _block("image", box=[0, 0.5, 1, 1]),
        ]
    }
    provider = FakeProvider([reply])
    result, usage = ai_page(provider, CFG, _page(), 3, "en-US")
    assert result.source == "ai" and result.page == 3
    heading, para, foot, image = result.blocks
    assert heading.kind == "heading" and heading.runs[0].text == "Chapter One"
    assert (heading.top, heading.bottom) == (0.05, 0.1)
    assert [(r.text, r.italic, r.note) for r in para.runs] == [
        ("The ", False, None),
        ("ship", True, None),
        (" came", False, None),
        ("", False, 1),
        (" in.", False, None),
    ]
    assert foot.note == 1 and [(r.text, r.bold) for r in foot.runs] == [
        ("A ", False),
        ("note", True),
        (".", False),
    ]
    assert image.kind == "image" and image.image_type == "image/png"
    assert abs(Image.open(io.BytesIO(image.image)).height - 100) <= 1
    assert (image.top, image.bottom) == (0.5, 1.0)
    assert usage == provider.usage
    call = provider.calls[0]
    assert call["schema"] == SCHEMA and call["model"] == "claude-opus-5-5"
    assert call["effort"] == "high" and call["images"] == [(_page(), "image/png")]
    assert "en-US" in call["user"] + "".join(p.text for p in call["system"])


def test_null_boxes():
    reply = {"blocks": [_block("paragraph", "Hi."), _block("image")]}
    page = _page()
    result, _ = ai_page(FakeProvider([reply]), CFG, page, 0, "de-DE")
    para, image = result.blocks
    assert para.top == para.bottom == 0.5
    assert image.image == page and (image.top, image.bottom) == (0.0, 1.0)


def test_boxes_are_clamped():
    reply = {"blocks": [_block("paragraph", "Hi.", box=[0, -0.2, 1, 1.3])]}
    result, _ = ai_page(FakeProvider([reply]), CFG, _page(), 0, "en-US")
    assert (result.blocks[0].top, result.blocks[0].bottom) == (0.0, 1.0)


def test_unusable_reply_is_a_billed_output_error():
    with pytest.raises(ModelOutputError) as err:
        ai_page(FakeProvider([{"nope": 1}]), CFG, _page(), 0, "en-US")
    assert err.value.usage is not None


def test_schema_has_box_on_every_block():
    item = SCHEMA["properties"]["blocks"]["items"]
    assert set(item["required"]) == {"kind", "text", "level", "note", "box"}


def test_estimate_ocr_uses_the_ocr_price():
    settings = Settings(
        _env_file=None,
        models={"ocr": {"provider": "anthropic", "model": "m1"}},
        prices={"m1": {"input": 10, "output": 50, "cache_read": 0, "cache_write": 0}},
    )
    registry = Registry(settings, {"anthropic": FakeProvider([])})
    # 3 pages x (1600 x 10 + 900 x 50) / 1e6
    assert estimate_ocr(registry, 3) == Decimal("0.183")


def test_estimate_ocr_is_zero_without_a_price():
    settings = Settings(_env_file=None, models={"ocr": {"provider": "anthropic", "model": "zz"}})
    assert estimate_ocr(Registry(settings, {}), 5) == Decimal(0)
    assert estimate_ocr(Registry(Settings(_env_file=None, models={}), {}), 5) == Decimal(0)
