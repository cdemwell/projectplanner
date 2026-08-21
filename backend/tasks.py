"""Tasks — checklist items within a story. Owned by the story (CASCADE delete)."""

from __future__ import annotations

import sqlite3

from . import _util, db, errors, stories
from .models import Task

EDITABLE = {"description", "complete", "position"}


def list_tasks(conn: sqlite3.Connection, story_id) -> list[Task]:
    stories.get_story(conn, story_id)  # raises NotFound if story missing
    rows = conn.execute("SELECT * FROM task WHERE story_id = ? ORDER BY position, id",
                       (story_id,))
    return [Task.from_row(r) for r in rows]


def get_task(conn: sqlite3.Connection, id) -> Task:
    return _util.get(conn, Task, "task", id, resource="task")


def create_task(conn: sqlite3.Connection, story_id: int, description: str, *,
                complete: bool = False, position: float | None = None) -> Task:
    stories.get_story(conn, story_id)
    if position is None:
        maxpos = conn.execute(
            "SELECT COALESCE(MAX(position), 0) FROM task WHERE story_id = ?",
            (story_id,)).fetchone()[0]
        position = float(maxpos) + 1.0
    with db.tx_write(conn):
        new_id = _util.insert(conn, "task", {
            "story_id": story_id, "description": description,
            "complete": 1 if complete else 0, "position": position,
            "created_at": db.now(), "completed_at": db.now() if complete else None,
        })
    return get_task(conn, new_id)


def update_task(conn: sqlite3.Connection, id, **fields) -> Task:
    get_task(conn, id)
    fields = {k: v for k, v in fields.items() if k in EDITABLE}
    if fields:
        with db.tx_write(conn):
            _util.update(conn, "task", id, fields)
    return get_task(conn, id)


def complete_task(conn: sqlite3.Connection, id, complete: bool = True) -> Task:
    """Toggle a task's completion, stamping ``completed_at`` accordingly."""
    get_task(conn, id)  # raises NotFound
    with db.tx_write(conn):
        _util.update(conn, "task", id, {
            "complete": 1 if complete else 0,
            "completed_at": db.now() if complete else None,
        })
    return get_task(conn, id)


def delete_task(conn: sqlite3.Connection, id) -> None:
    with db.tx_write(conn):
        _util.delete(conn, "task", id, resource="task")