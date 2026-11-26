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
