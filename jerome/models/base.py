from dataclasses import dataclass
from typing import Protocol

from jerome.errors import JeromeError


@dataclass(frozen=True)
class SystemPart:
    text: str
    cache: bool = False


@dataclass
class Usage:
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


@dataclass
class CompletionResult:
    data: dict
    usage: Usage
    provider: str
    model: str


class ModelRefusal(JeromeError):
    """The model declined to answer."""


class ModelOutputError(JeromeError):
    """The model's output was truncated or not valid JSON."""


class Provider(Protocol):
    name: str
    local: bool

    def complete(
        self,
        *,
        model: str,
        system: list[SystemPart],
        user: str,
        schema: dict,
        max_tokens: int = 16000,
        effort: str | None = None,
    ) -> CompletionResult: ...

    def count_tokens(self, *, model: str, system: list[SystemPart], user: str) -> int | None: ...
