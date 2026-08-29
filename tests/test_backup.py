import re
import sqlite3
import time
from pathlib import Path

import pytest

from backend import db, plan, projects, stories
from cli.commands import run


def test_backup_creation(tmp_path):
    # Setup: create a dummy DB
    db_file = tmp_path / "planner.db"
    conn = sqlite3.connect(db_file)
    conn.execute("CREATE TABLE test (id INTEGER PRIMARY KEY, val TEXT)")
    conn.commit()
    conn.close()

    # Run backup
    # Use --db to specify the dummy DB path
    result_code = run(["--db", str(db_file), "plan", "backup"])
    assert result_code == 0

    # Verify backup file exists
    backups = list(tmp_path.glob("planner.db.*"))
    assert len(backups) == 1
    backup_file = backups[0]
    assert backup_file.exists()

    # Verify it's a valid SQLite DB
    conn = sqlite3.connect(backup_file)
    res = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='test'").fetchone()
    assert res is not None
    conn.close()

def test_backup_pruning(tmp_path):
    # Setup: create a dummy DB
    db_file = tmp_path / "planner.db"
    conn = sqlite3.connect(db_file)
    conn.execute("CREATE TABLE test (id INTEGER PRIMARY KEY, val TEXT)")
    conn.commit()
    conn.close()

    # Create some backups
    for _ in range(2):
        run(["--db", str(db_file), "plan", "backup"])
        time.sleep(1.1) # Ensure different timestamps/mtimes

    # Create 3rd backup and keep only 1
    run(["--db", str(db_file), "plan", "backup", "--keep", "1"])

    # Only the most recent one should remain
    backups = list(tmp_path.glob("planner.db.*"))
    assert len(backups) == 1


def _timestamped_backups(directory: Path, stem: str) -> list[Path]:
    """Files in ``directory`` named ``<stem>.<14 digits>`` (tool-made backups)."""
    pat = re.compile(rf"^{re.escape(stem)}\.\d{{14}}$")
    return sorted(p for p in directory.iterdir() if pat.fullmatch(p.name))


def test_plan_import_takes_pre_import_backup(tmp_path):
    """`plan import` snapshots the database before its destructive replace."""
    src = tmp_path / "src.db"
    dst = tmp_path / "planner.db"
    snap = tmp_path / "snap.json"

    c = db.connect(src)
    projects.create_project(c, "fresh")
    stories.create_story(c, "imported story")
    c.close()
    plan.export_to_file(db.connect(src), str(snap))

    # dst holds existing content that the import will replace.
    run(["--db", str(dst), "story", "create", "--name", "old story"])

    rc = run(["--db", str(dst), "plan", "import", "--file", str(snap)])
    assert rc == 0

    # Exactly one timestamped backup was written, holding the pre-import data.
    backups = _timestamped_backups(tmp_path, "planner.db")
    assert len(backups) == 1
    old = sqlite3.connect(backups[0])
    names = [r[0] for r in old.execute("SELECT name FROM story")]
    old.close()
    assert names == ["old story"]

    # And the live DB now carries the imported content.
    c = db.connect(dst)
    assert [s.name for s in stories.list_stories(c)] == ["imported story"]
    c.close()


def test_failed_import_leaves_no_backup(tmp_path):
    """A snapshot rejected at validation time must not write a backup copy."""
    import json

    dst = tmp_path / "planner.db"
    run(["--db", str(dst), "story", "create", "--name", "old story"])
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"_meta": {"schema_version": 99}}))

    rc = run(["--db", str(dst), "plan", "import", "--file", str(bad)])
    assert rc == 1
    assert _timestamped_backups(tmp_path, "planner.db") == []

    c = db.connect(dst)
    assert [s.name for s in stories.list_stories(c)] == ["old story"]
    c.close()


def test_plan_import_dry_run_skips_backup_and_writes_nothing(tmp_path):
    src = tmp_path / "src.db"
    dst = tmp_path / "planner.db"
    snap = tmp_path / "snap.json"

    c = db.connect(src)
    projects.create_project(c, "fresh")
    stories.create_story(c, "imported story")
    c.close()
    plan.export_to_file(db.connect(src), str(snap))
    run(["--db", str(dst), "story", "create", "--name", "old story"])

    rc = run(["--db", str(dst), "plan", "import", "--file", str(snap), "--dry-run"])
    assert rc == 0
    assert _timestamped_backups(tmp_path, "planner.db") == []
    c = db.connect(dst)
    assert [s.name for s in stories.list_stories(c)] == ["old story"]
    c.close()


def test_backup_db_file_collides_safely(tmp_path):
    """Two backups within the same second must not overwrite each other."""
    from cli.commands import _backup_db_file

    db_file = tmp_path / "planner.db"
    db_file.write_bytes(b"x")
    first = _backup_db_file(db_file)
    second = _backup_db_file(db_file)
    assert first.exists() and second.exists() and first != second
