from __future__ import annotations

from collections.abc import Callable

import pytest

from app.config import Settings
from app.database import Repository


@pytest.fixture
def settings_factory(tmp_path) -> Callable[..., Settings]:
    def make_settings(**overrides) -> Settings:
        values = {"database_path": str(tmp_path / "health-monitor.db")}
        values.update(overrides)
        return Settings(**values)

    return make_settings


@pytest.fixture
def settings(settings_factory) -> Settings:
    return settings_factory()


@pytest.fixture
def repository(settings: Settings) -> Repository:
    repo = Repository(settings.database_path)
    repo.initialize()
    return repo
