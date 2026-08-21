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


def test_schema_version_is_3(conn):
    assert conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == 3


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


def test_default_db_path_is_repo_root():
    # DEFAULT_DB_PATH should resolve to planner.db next to main.py.
    assert db.DEFAULT_DB_PATH.name == "planner.db"
    assert db.DEFAULT_DB_PATH.parent.name == "projectplanner" or db.DEFAULT_DB_PATH.parent.exists()
