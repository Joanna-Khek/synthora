import mlflow
import pytest


@pytest.fixture(autouse=True, scope="session")
def no_tracing():
    """Tests never record traces."""
    mlflow.tracing.disable()
