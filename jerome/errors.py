class JeromeError(Exception):
    """Base class for all Jerome errors."""


class NotFound(JeromeError):
    pass


class Conflict(JeromeError):
    pass


class AiCheckError(JeromeError):
    """The AI gave no usable verdict on a LanguageTool mark (the call is still billed)."""


class UnsupportedDocumentError(JeromeError):
    pass


class InvalidChangeError(JeromeError):
    pass


class UnknownModelPrice(JeromeError):
    """A model has no known price, so its cost cannot be estimated or capped."""


class UnsupportedLanguagePair(JeromeError):
    """Source and target of a translation must differ in language."""


class InvalidFeedback(JeromeError):
    """Feedback sent from the web app that cannot be kept as it is."""


class MissingApiKey(JeromeError):
    """A model provider's API key is not set: the owner has to save it on the Settings page."""

    def __init__(self, provider: str) -> None:
        self.provider = provider
        super().__init__(
            f"{provider} API key is not set. Save it under Settings → Secrets, "
            "or pick another model under Settings → Models."
        )
