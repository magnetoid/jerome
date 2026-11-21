from jerome.models.base import CompletionResult, SystemPart, Usage


class FakeProvider:
    name = "fake"

    def __init__(
        self,
        responses: list[dict | Exception],
        local: bool = False,
        usage: Usage = Usage(1000, 150),  # noqa: B008
        token_count: int | None = None,
    ):
        self.responses = list(responses)
        self.local = local
        self.usage = usage
        self.token_count = token_count
        self.calls: list[dict] = []

    def complete(self, **kwargs) -> CompletionResult:
        self.calls.append(kwargs)
        if not self.responses:
            raise AssertionError("FakeProvider has no responses left for this call")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return CompletionResult(item, self.usage, self.name, kwargs.get("model", "fake"))

    def count_tokens(self, *, model: str, system: list[SystemPart], user: str) -> int | None:
        return self.token_count
