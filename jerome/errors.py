class JeromeError(Exception):
    """Base class for all Jerome errors."""


class NotFound(JeromeError):
    pass


class Conflict(JeromeError):
    pass


class UnsupportedDocumentError(JeromeError):
    pass


class InvalidChangeError(JeromeError):
    pass
