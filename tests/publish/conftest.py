import pytest

_WEASY: list = []  # [module] or [None, reason], tried once: a second failed import can crash


def _load():
    if not _WEASY:
        try:
            import weasyprint

            _WEASY.append(weasyprint)
        except (OSError, ImportError) as exc:
            _WEASY.extend([None, str(exc).splitlines()[0]])
    return _WEASY


@pytest.fixture
def weasy():
    """WeasyPrint, or a skip where its system libraries (Pango) are missing.

    Run these tests in the Docker test image: see docker/test.Dockerfile."""
    loaded = _load()
    if loaded[0] is None:
        pytest.skip(f"WeasyPrint needs Pango; run in the Docker test image ({loaded[1]})")
    return loaded[0]
