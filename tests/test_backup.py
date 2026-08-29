import re
import sqlite3
import time
from pathlib import Path

import pytest

from backend import db, plan, projects, stories
from cli.commands import run


def test_backup_creation(tmp_path):
    # Setup: a genuine planner DB (raw SQLite files without a schema_version
    # table are refused since story 111).
    db_file = tmp_path / "planner.db"
    conn = db.connect(str(db_file))
    stories.create_story(conn, "a story to back up")
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

    # Verify it's a valid planner DB
    conn = sqlite3.connect(backup_file)
    res = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='story'").fetchone()
    assert res is not None
    conn.close()

def test_backup_pruning(tmp_path):
    # Setup: a genuine planner DB (see test_backup_creation)
    db_file = tmp_path / "planner.db"
    db.connect(str(db_file)).close()

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
    from backend.backup import backup_db_file

    db_file = tmp_path / "planner.db"
    db_file.write_bytes(b"x")
    first = backup_db_file(db_file)
    second = backup_db_file(db_file)
    assert first.exists() and second.exists() and first != second


# --------------------------------------------------------------------------- #
# Backup pruning must never delete non-backup files (story 110)
# --------------------------------------------------------------------------- #

def test_prune_keeps_non_backup_siblings(tmp_path):
    """--keep 0 deletes only the tool's own timestamped backups; manual copies
    and unrelated files matching the db name survive (the story-110 bug)."""
    import os

    from backend.backup import prune_backups

    db_file = tmp_path / "planner.db"
    db_file.write_bytes(b"x")
    survivors = [
        tmp_path / "planner.db.backup",      # manual full copy
        tmp_path / "planner.db.backup.1",    # manual numbered copy
        tmp_path / "planner.db.2",           # unrelated sibling
        tmp_path / "planner.db.notes",       # anything else
    ]
    tool_backups = []
    for i, ts in enumerate(("20250101000000", "20250101000001", "20250101000002")):
        p = tmp_path / f"planner.db.{ts}"
        p.write_bytes(b"x")
        os.utime(p, (1_600_000_000.0 + i, 1_600_000_000.0 + i))
        tool_backups.append(p)
    for s in survivors:
        s.write_bytes(b"x")

    pruned = prune_backups(db_file, 0)

    # Deleted newest-first by the embedded creation order (mtimes irrelevant);
    # survivors are never in the list.
    assert [p.name for p in pruned] == [p.name for p in reversed(tool_backups)]
    assert not any(p.exists() for p in tool_backups)
    for s in survivors:
        assert s.exists(), f"{s.name} was wrongly deleted"
    assert db_file.exists()


def test_prune_counts_collision_suffixes_as_backups(tmp_path):
    """``.N``-suffixed same-second backups are tool backups: rotation prunes
    them too, so they cannot accumulate forever."""
    from backend.backup import backup_db_file, prune_backups

    db_file = tmp_path / "planner.db"
    db_file.write_bytes(b"x")
    made = [backup_db_file(db_file) for _ in range(3)]  # <ts>, <ts>.1, <ts>.2
    for p in made:
        assert p.exists()

    pruned = prune_backups(db_file, 1)
    assert len(pruned) == 2
    assert sorted(p.name for p in pruned) == sorted([made[0].name, made[1].name])
    assert made[2].exists()


def test_prune_keeps_fresh_backup_when_db_mtime_regresses(tmp_path):
    """Pruning follows the embedded creation order, not file mtimes.

    copy2 borrows the database's mtime for backups, so a restore that moves
    the db mtime backwards (cp -p / rsync -a) makes the just-taken backup
    look oldest by mtime; keep=1 must still keep it.
    """
    import os

    from backend.backup import backup_db_file, prune_backups

    db_file = tmp_path / "planner.db"
    db_file.write_bytes(b"x")
    first = backup_db_file(db_file)

    time.sleep(1.1)
    # Simulate restoring the db from an old copy: its mtime moves backwards,
    # and the next backup then borrows that stale mtime from it.
    os.utime(db_file, (1_000_000_000.0, 1_000_000_000.0))
    fresh = backup_db_file(db_file)
    assert fresh.stat().st_mtime == db_file.stat().st_mtime  # borrowed mtime

    pruned = prune_backups(db_file, 1)

    # An mtime sort would have pruned the new backup; the name order does not.
    assert [p.name for p in pruned] == [first.name]
    assert not first.exists()
    assert fresh.exists()


def test_plan_backup_rejects_negative_keep(tmp_path):
    """A negative --keep is refused, not interpreted as 'prune everything'."""
    db_file = tmp_path / "planner.db"
    sqlite3.connect(db_file).close()
    rc = run(["--db", str(db_file), "plan", "backup", "--keep", "-1"])
    assert rc == 1
    # No backup was written and nothing pruned.
    assert [p for p in tmp_path.iterdir() if p.name.startswith("planner.db.")] == []


def test_rotate_backup_does_not_touch_foreign_files(tmp_path):
    """The --rotate-backup path prunes via the same strict matcher.

    Four runs force a real prune (3 backups, keep 2); the foreign sibling is
    recorded before the runs and asserted byte-identical afterwards without
    being rewritten, so deletion cannot hide behind recreation.
    """
    import time

    db_file = tmp_path / "planner.db"
    foreign = tmp_path / "planner.db.backup"
    foreign.write_bytes(b"keep me")
    foreign_bytes_before = foreign.read_bytes()

    for i in range(4):
        rc = run(["--db", str(db_file), "--rotate-backup", "2",
                  "story", "create", "--name", f"rotation {i}"])
        assert rc == 0
        time.sleep(1.1)

    backups = [p for p in tmp_path.iterdir()
               if p.name.startswith("planner.db.") and p != foreign]
    assert len(backups) == 2  # exactly rotated down from 3
    assert foreign.exists(), "foreign sibling was deleted by rotation"
    assert foreign.read_bytes() == foreign_bytes_before
    assert db_file.exists()
