import base64
import json

from jerome.models import FakeProvider, OpenAICompatProvider, SystemPart
from tests.models.test_models import SCHEMA, claude, claude_json_reply

IMAGE = (b"\x89PNG-bytes", "image/png")


def test_claude_sends_images_before_the_text():
    provider, rec = claude(claude_json_reply({"suggestions": []}))
    provider.complete(
        model="claude-opus-5-5",
        system=[SystemPart("rules")],
        user="read it",
        schema=SCHEMA,
        images=[IMAGE],
    )
    content = rec.body()["messages"][0]["content"]
    assert content == [
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": base64.b64encode(IMAGE[0]).decode(),
            },
        },
        {"type": "text", "text": "read it"},
    ]


def test_claude_without_images_keeps_a_plain_user_string():
    provider, rec = claude(claude_json_reply({"suggestions": []}))
    provider.complete(model="claude-opus-5-5", system=[SystemPart("r")], user="u", schema=SCHEMA)
    assert rec.body()["messages"][0]["content"] == "u"


def test_openai_compat_sends_image_url_parts(respx_mock):
    reply = {
        "model": "m",
        "choices": [{"finish_reason": "stop", "message": {"content": "{}"}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    }
    route = respx_mock.post("http://localhost:11434/v1/chat/completions").respond(200, json=reply)
    OpenAICompatProvider("http://localhost:11434/v1", None).complete(
        model="m", system=[SystemPart("r")], user="read it", schema=SCHEMA, images=[IMAGE]
    )
    user = json.loads(route.calls[0].request.content)["messages"][1]["content"]
    url = "data:image/png;base64," + base64.b64encode(IMAGE[0]).decode()
    assert user == [
        {"type": "image_url", "image_url": {"url": url}},
        {"type": "text", "text": "read it"},
    ]


def test_fake_provider_records_the_images():
    fake = FakeProvider([{}])
    fake.complete(model="m", system=[], user="u", schema={}, images=[IMAGE])
    assert fake.calls[0]["images"] == [IMAGE]
