"""Tests for backend/db.py: connection, migrations, seeding, tx_write."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from backend import db, errors


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


# --------------------------------------------------------------------------- #
# --db must never mutate a foreign database (story 111)
# --------------------------------------------------------------------------- #

def test_connect_refuses_foreign_sqlite_file(db_path):
    """An unrelated SQLite file (its own tables, no schema_version) is refused
    untouched: no planner schema is added, no seed rows are injected."""
    p = Path(db_path)
    raw = sqlite3.connect(p)
    raw.execute("CREATE TABLE user_data (id INTEGER PRIMARY KEY, note TEXT)")
    raw.commit()
    raw.close()

    with pytest.raises(errors.ValidationError, match="not a planner database"):
        db.connect(p)

    # The foreign file is untouched: its single table, no planner schema.
    check = sqlite3.connect(p)
    names = {r[0] for r in check.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    check.close()
    assert names == {"user_data"}


def test_connect_refuses_directory(tmp_path):
    """A directory passed as --db is a refusal, not a traceback."""
    target = tmp_path / "somedir"
    target.mkdir()
    with pytest.raises(errors.ValidationError):
        db.connect(target)


def test_connect_refuses_non_sqlite_file(tmp_path):
    """A text file passed as --db is refused, not crashed into or overwritten."""
    p = tmp_path / "junk.db"
    p.write_text("this is not a database\n")
    with pytest.raises(errors.ValidationError, match="cannot be opened as a SQLite"):
        db.connect(p)
    assert p.read_text() == "this is not a database\n"  # content unchanged


def test_connect_refuses_text_schema_version(tmp_path):
    """A schema_version table holding non-integer junk is not a planner DB.

    (A foreign app's schema_version convention may use any column shape —
    here a TEXT version column, which a rowid-aliased INTEGER PRIMARY KEY
    could never legally hold.)"""
    p = tmp_path / "forged.db"
    forged = sqlite3.connect(p)
    forged.execute("CREATE TABLE schema_version (version TEXT)")
    forged.execute("INSERT INTO schema_version VALUES ('abc')")
    forged.execute("CREATE TABLE app_data (id INTEGER)")
    forged.commit()
    forged.close()
    with pytest.raises(errors.ValidationError, match="not a schema version"):
        db.connect(p)
    # The forged file is untouched (MAX(version) returned its TEXT value).
    check = sqlite3.connect(p)
    version = check.execute("SELECT version FROM schema_version").fetchone()[0]
    check.close()
    assert version == "abc"


def test_connect_refuses_future_schema_version(tmp_path):
    """A planner schema version newer than this build is refused, with the
    first-version-vs-build dating explained."""
    p = tmp_path / "future.db"
    future = sqlite3.connect(p)
    future.execute("CREATE TABLE schema_version (version INTEGER PRIMARY KEY)")
    future.execute("INSERT INTO schema_version VALUES (?)", (99,))
    future.commit()
    future.close()
    with pytest.raises(errors.ValidationError, match="newer than this build"):
        db.connect(p)


def test_connect_refuses_versionless_schema_version_collision(tmp_path):
    """A foreign `schema_version` table with no usable version row (empty, 0,
    or negative — a real planner DB stores 1..N) alongside other tables is a
    collision, not a first run: refused untouched. Only `schema_version` alone
    (the v1-crash window) self-heals."""
    for name, version_value in (("empty", None), ("zero", 0), ("negative", -1)):
        p = tmp_path / f"collide-{name}.db"
        collide = sqlite3.connect(p)
        collide.execute("CREATE TABLE schema_version (version INTEGER)")
        if version_value is not None:
            collide.execute("INSERT INTO schema_version VALUES (?)", (version_value,))
        collide.execute("CREATE TABLE app_data (id INTEGER)")
        collide.commit()
        collide.close()

        with pytest.raises(errors.ValidationError, match="not a planner database"):
            db.connect(p)

        check = sqlite3.connect(p)
        tables = {r[0] for r in check.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        check.close()
        assert tables == {"schema_version", "app_data"}, f"{name} file was modified"


def test_connect_seeds_v1_crash_window(tmp_path):
    """A DB holding only schema_version (crashed during first-run v1: the
    out-of-transaction CREATE committed, the v1 tx rolled back) self-heals on
    connect: migrate + seed."""
    p = tmp_path / "crashed.db"
    crashed = sqlite3.connect(p)
    crashed.execute("CREATE TABLE schema_version (version INTEGER PRIMARY KEY)")
    crashed.commit()
    crashed.close()

    c = db.connect(p)
    version = c.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
    members = c.execute("SELECT COUNT(*) FROM member").fetchone()[0]
    workflows = c.execute("SELECT COUNT(*) FROM workflow").fetchone()[0]
    c.close()
    assert version == db.CURRENT_SCHEMA_VERSION
    assert members == 1
    assert workflows == 1


def test_cli_refuses_bad_db_targets(tmp_path, capsys):
    """The CLI surfaces the refusal per the documented error contract
    (`error: ...`, exit 1), not as a traceback."""
    from cli.commands import run

    foreign = tmp_path / "foreign.db"
    raw = sqlite3.connect(foreign)
    raw.execute("CREATE TABLE user_data (id INTEGER)")
    raw.commit()
    raw.close()

    rc = run(["--db", str(foreign), "story", "list"])
    assert rc == 1
    assert "error:" in capsys.readouterr().err

    junk = tmp_path / "junk.db"
    junk.write_text("nope\n")
    rc = run(["--db", str(junk), "story", "list"])
    assert rc == 1
    assert "error:" in capsys.readouterr().err


def test_reconnect_never_reseeds(db_path):
    """A planner DB is never re-seeded, even with zero members.

    The old member-count heuristic resurrected a seeded member plus a second
    Default workflow after any plan import that carried no members.
    """
    c = db.connect(db_path)
    c.execute("DELETE FROM member")
    c.execute("DELETE FROM workflow_state")
    c.execute("DELETE FROM workflow")
    c.commit()
    c.close()

    c2 = db.connect(db_path)  # second connect must not re-seed
    assert c2.execute("SELECT COUNT(*) FROM member").fetchone()[0] == 0
    assert c2.execute("SELECT COUNT(*) FROM workflow").fetchone()[0] == 0
    c2.close()
