"""Concurrency test: writers serialize via busy_timeout + BEGIN IMMEDIATE.

SQLite's file lock serializes writers; with ``busy_timeout`` a second writer
blocks (rather than erroring) until the first commits. Each connection is created
inside the thread that uses it (sqlite connections are thread-bound by default).
"""

from __future__ import annotations

import threading
import time

from backend import db


def test_writers_block_until_first_commits(db_path):
    # Open one connection in the main thread (for the assertion query later).
    main = db.connect(db_path)

    hold_seconds = 0.3
    started = threading.Event()

    def hold():
        # Connection created in the worker thread that uses it.
        c = db.connect(db_path)
        with db.tx_write(c):
            started.set()
            time.sleep(hold_seconds)
        c.close()

    t = threading.Thread(target=hold)
    t.start()
    assert started.wait(timeout=5)  # worker has the write lock

    start = time.monotonic()
    with db.tx_write(main):  # should block until the worker commits
        main.execute("INSERT INTO project(name, description, abbreviation, color, archived, created_at)"
                     " VALUES ('p','','',0,'',?)", (db.now(),))
    blocked = time.monotonic() - start
    t.join()

    # The second writer waited roughly the hold time, and well under the 5s timeout.
    assert blocked >= hold_seconds * 0.8
    assert blocked < 5.0  # busy_timeout is 5000ms
    # both writes committed
    assert main.execute("SELECT COUNT(*) FROM project").fetchone()[0] == 1
    main.close()