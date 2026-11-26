from jerome.pdfimport.model import PageBlock, PageResult, Run, from_json, to_json


def test_a_page_result_survives_json():
    result = PageResult(
        page=3,
        source="text",
        blocks=[
            PageBlock(
                kind="paragraph",
                runs=[Run("It came", italic=True), Run("", note=1), Run(" in.", bold=True)],
                top=0.1,
                bottom=0.2,
                size=11.5,
                centered=True,
            ),
            PageBlock(kind="footnote", runs=[Run("A note.")], note=1),
            PageBlock(kind="image", image=b"\x89PNG\x00\xff", image_type="image/png"),
            PageBlock(kind="heading", runs=[Run("Arrival")], level=2),
        ],
        error="partly unreadable",
    )
    data = to_json(result)
    assert data["blocks"][2]["image"] == "iVBOR" + data["blocks"][2]["image"][5:]  # base64 text
    assert from_json(data) == result


def test_none_fields_survive_json():
    result = PageResult(page=0, source="failed", blocks=[PageBlock(kind="paragraph")])
    back = from_json(to_json(result))
    assert back == result
    assert back.error is None and back.blocks[0].image is None
