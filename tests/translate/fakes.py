"""FakeProvider response callables for the translate pass."""

import re
from collections.abc import Callable

_BLOCK = re.compile(r'<block id="(\d+)">(.*?)</block>', re.DOTALL)


def echo_translator(prefix: str = "DE ") -> Callable[[dict], dict]:
    """Reply with every requested block's tagged text, with ``prefix`` put in front."""

    def reply(kwargs: dict) -> dict:
        blocks = [
            {"block_id": int(block_id), "text": prefix + body}
            for block_id, body in _BLOCK.findall(kwargs["user"])
        ]
        return {"blocks": blocks}

    return reply


def scripted(replies: dict[int, str | None]) -> Callable[[dict], dict]:
    """Reply with the given tagged text per block id; a ``None`` value omits that block."""

    def reply(kwargs: dict) -> dict:
        blocks = [
            {"block_id": block_id, "text": text}
            for block_id, text in replies.items()
            if text is not None
        ]
        return {"blocks": blocks}

    return reply
