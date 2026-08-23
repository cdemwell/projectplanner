"""Tests for backend/epics.py."""

from __future__ import annotations

import pytest

from backend import db, epics, stories


def _done_state_id(conn) -> int:
    return conn.execute("SELECT id FROM workflow_state WHERE type='done' ORDER BY id LIMIT 1").fetchone()[0]


def test_epic_progress(conn):
    e = epics.create_epic(conn, "Progress Epic")

    # Create 3 stories
    s1 = stories.create_story(conn, "Story 1", epic_id=e.id)
    s2 = stories.create_story(conn, "Story 2", epic_id=e.id)
    s3 = stories.create_story(conn, "Story 3", epic_id=e.id)

    # Initial progress: 0/3 (0%)
    prog = epics.epic_progress(conn, e.id)
    assert prog == {"done": 0, "total": 3, "pct": 0.0}

    # Mark 1 done
    stories.move_story_state(conn, s1.id, _done_state_id(conn))

    # Progress: 1/3 (33.3%)
    prog = epics.epic_progress(conn, e.id)
    assert prog["done"] == 1
    assert prog["total"] == 3
    assert pytest.approx(prog["pct"], 0.1) == 33.3

    # Mark another done
    stories.move_story_state(conn, s2.id, _done_state_id(conn))

    # Progress: 2/3 (66.7%)
    prog = epics.epic_progress(conn, e.id)
    assert prog["done"] == 2
    assert pytest.approx(prog["pct"], 0.1) == 66.7

    # Mark all done
    stories.move_story_state(conn, s3.id, _done_state_id(conn))

    # Progress: 3/3 (100%)
    prog = epics.epic_progress(conn, e.id)
    assert prog == {"done": 3, "total": 3, "pct": 100.0}


def test_epic_progress_no_stories(conn):
    e = epics.create_epic(conn, "Empty Epic")
    prog = epics.epic_progress(conn, e.id)
    assert prog == {"done": 0, "total": 0, "pct": 0.0}
