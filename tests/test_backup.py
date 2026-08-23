import sqlite3
import time
from pathlib import Path

import pytest

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
