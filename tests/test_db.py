"""Tests for backend/db.py: connection, migrations, seeding, tx_write."""

from __future__ import annotations

import sqlite3

from backend import db


def test_connect_creates_and_seeds(db_path):
    c = db.connect(db_path)
    # schema_version table present and at the current version.
    assert c.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == db.CURRENT_SCHEMA_VERSION
    # one member seeded from $USER.
    members = c.execute("SELECT name, mention_name FROM member").fetchall()
    assert len(members) == 1
    # default workflow with the three standard states.
    wf = c.execute("SELECT id, name, default_state_id FROM workflow").fetchone()
    assert wf["name"] == "Default"
    states = c.execute(
        "SELECT name, type, position FROM workflow_state WHERE workflow_id = ? ORDER BY position",
        (wf["id"],)).fetchall()
    assert [s["name"] for s in states] == ["Unstarted", "Started", "Done"]
    assert [s["type"] for s in states] == ["unstarted", "started", "done"]
    # the default state points at the Started state.
    started = c.execute("SELECT id FROM workflow_state WHERE type='started'").fetchone()
    assert wf["default_state_id"] == started["id"]
    c.close()


def test_connect_is_idempotent(db_path):
    db.connect(db_path).close()
    c = db.connect(db_path)  # second connect must not re-seed
    assert c.execute("SELECT COUNT(*) FROM member").fetchone()[0] == 1
    assert c.execute("SELECT COUNT(*) FROM workflow").fetchone()[0] == 1
    assert c.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == db.CURRENT_SCHEMA_VERSION
    c.close()


def test_pragmas_set(conn):
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000


def test_fts_tables_and_triggers_exist(conn):
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%_fts'")}
    assert tables == {"story_fts", "epic_fts", "project_fts",
                      "milestone_fts", "iteration_fts", "label_fts",
                      "story_comment_fts", "task_fts"}
    n_triggers = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='trigger'").fetchone()[0]
    # 8 FTS tables x 3 triggers (insert/delete/update) = 24.
    assert n_triggers == 24


def test_schema_version_matches_current(conn):
    assert conn.execute(
        "SELECT MAX(version) FROM schema_version").fetchone()[0] == db.CURRENT_SCHEMA_VERSION


def test_v5_migration_adds_case_insensitive_unique_indexes(conn):
    """The v5 migration must add the CI-unique indexes for label and state names."""
    idx = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index'")}
    assert "label_name_ci" in idx
    assert "workflow_state_wf_name_ci" in idx


def test_v4_migration_adds_description_column(conn):
    """The v4 migration must add a NOT NULL description column, default ''."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(workflow_state)")}
    assert "description" in cols
    # Existing seeded states carry an empty default.
    for r in conn.execute("SELECT description FROM workflow_state"):
        assert r["description"] == ""


def test_migrations_idempotent(db_path):
    """Reconnecting to an already-migrated DB must be a no-op."""
    db.connect(db_path).close()
    db.connect(db_path).close()  # second connect: schema_version already current
    c = db.connect(db_path)
    assert c.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == \
        db.CURRENT_SCHEMA_VERSION
    c.close()


def test_tx_write_commits_and_rolls_back(conn):
    with db.tx_write(conn):
        conn.execute("INSERT INTO project(name, description, abbreviation, color, archived, created_at)"
                     " VALUES ('x','','',0,'',?)", (db.now(),))
    assert conn.execute("SELECT COUNT(*) FROM project").fetchone()[0] == 1

    # an exception rolls back.
    try:
        with db.tx_write(conn):
            conn.execute("INSERT INTO project(name, description, abbreviation, color, archived, created_at)"
                         " VALUES ('y','','',0,'',?)", (db.now(),))
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert conn.execute("SELECT COUNT(*) FROM project").fetchone()[0] == 1  # 'y' not persisted


def test_bare_connect_never_touches_repo_db():
    """A bare db.connect() must use the patched temp default, never the repo db.

    The autouse conftest fixture redirects ``db.DEFAULT_DB_PATH`` to a temp file,
    so a bare ``connect()`` (or a CLI ``run()`` that forgot ``--db``) can never
    read or write the real ``planner.db`` in the repo root.
    """
    from pathlib import Path
    repo_db = Path(__file__).resolve().parent.parent / "planner.db"
    c = db.connect()  # no path -> uses the patched temp default
    connected = Path(c.execute("PRAGMA database_list").fetchone()[2]).resolve()
    assert connected != repo_db.resolve()
    assert connected.name == "default-test.db"
    c.close()
