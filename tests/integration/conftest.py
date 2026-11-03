"""Fixtures shared by integration tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import Engine

from .harness import Harness


@pytest.fixture()
def h(engine: Engine, tmp_path: Path) -> Harness:
    return Harness(engine, tmp_path)
