import pytest

from kudo_agent.evidence.store import Store
from kudo_agent.research_core.catalog import Catalog


@pytest.fixture(scope="session")
def catalog():
    return Catalog()


@pytest.fixture
def store(tmp_path):
    storage = Store(tmp_path / "data")
    yield storage
    storage.close()
