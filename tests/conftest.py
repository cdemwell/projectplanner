"""Shared pytest fixtures.

Each test gets a fresh, seeded SQLite database in a temporary directory (via
``tmp_path``), so tests are isolated and run in parallel-safe temp dirs.

An autouse fixture redirects ``db.DEFAULT_DB_PATH`` to a temp file for every
test, so no test can ever read or write the repo's real ``planner.db`` — even if
a call forgets to pass an explicit path (e.g. a CLI ``run()`` without ``--db``).
"""

from __future__ import annotations

import pytest

from backend import db


@pytest.fixture(autouse=True)
def _isolate_default_db(tmp_path, monkeypatch):
    """Redirect the backend's default DB path to a temp file for this test.

    Any ``db.connect()`` called with no path would otherwise open
    ``planner.db`` in the repo root; redirecting it makes an accidental bare
    ``connect()`` (or a ``cli.run`` without ``--db``) impossible to corrupt the
    real database.
    """
    monkeypatch.setattr(db, "DEFAULT_DB_PATH", tmp_path / "default-test.db")


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
