import pytest


@pytest.fixture
def weasy():
    """WeasyPrint, or a skip where its system libraries (Pango) are missing.

    Run these tests in the Docker test image: see docker/test.Dockerfile."""
    try:
        import weasyprint
    except (OSError, ImportError):
        pytest.skip("WeasyPrint needs Pango; run in the Docker test image")
    return weasyprint
