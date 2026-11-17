import pytest

from pitpat import config


@pytest.fixture(autouse=True)
def private_config(tmp_path, monkeypatch):
    """No test may touch the real settings or workout history."""
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
