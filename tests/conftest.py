import pytest

from app.llm.retry import reset_pacers


@pytest.fixture(autouse=True)
def _isolated_pacers():
    """Request pacing is shared per host+key across clients; keep tests independent."""
    reset_pacers()
    yield
    reset_pacers()
