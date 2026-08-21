"""Shared pytest fixtures.

Each test gets a fresh, seeded SQLite database in a temporary directory (via
``tmp_path``), so tests are isolated and run in parallel-safe temp dirs.
"""

from __future__ import annotations

import pytest

from backend import db


@pytest.fixture
def db_path(tmp_path) -> str:
    """Path to a fresh planner.db inside the test's temp dir (not yet created)."""
    return str(tmp_path / "test.db")


@pytest.fixture
def conn(db_path):
    """A configured connection to a fresh, seeded database (closed after test)."""
    c = db.connect(db_path)
    yield c
    c.close()