"""Tasks — checklist items within a story. Owned by the story (CASCADE delete)."""

from __future__ import annotations

import sqlite3

from . import _util, db, stories
from .models import Task

EDITABLE = {"description", "complete", "position"}


def list_tasks(conn: sqlite3.Connection, story_id) -> list[Task]:
    """List checklist items for a story.

    Args:
        conn: sqlite3.Connection from db.connect().
        story_id: int — owning story.
    Returns:
        list[Task] — tasks ordered by position and id.
    Raises:
        NotFound: if the story does not exist.
    """
    stories.get_story(conn, story_id)  # raises NotFound if story missing
    rows = conn.execute("SELECT * FROM task WHERE story_id = ? ORDER BY position, id",
                       (story_id,))
    return [Task.from_row(r) for r in rows]


def get_task(conn: sqlite3.Connection, id) -> Task:
    """Get a single task.

    Args:
        conn: sqlite3.Connection from db.connect().
        id: int — task id.
    Returns:
        Task — the found task.
    Raises:
        NotFound: if the task does not exist.
    """
    return _util.get(conn, Task, "task", id, resource="task")


def create_task(conn: sqlite3.Connection, story_id: int, description: str, *,
                complete: bool = False, position: float | None = None) -> Task:
    """Create a task.

    If position is None, it is auto-positioned as max(position) + 1 within the story.

    Args:
        conn: sqlite3.Connection from db.connect().
        story_id: int — owning story.
        description: str — task text.
        complete: bool — initial completion state.
        position: float | None — custom position.
    Returns:
        Task — the created task.
    Raises:
        NotFound: if the story does not exist.
    Invariants:
        complete is stored as int 0/1.
    """
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
    """Update task fields.

    Only fields in EDITABLE (description, complete, position) are updated.

    Args:
        conn: sqlite3.Connection from db.connect().
        id: int — task id.
        fields: kwargs — fields to update.
    Returns:
        Task — the updated task.
    Raises:
        NotFound: if the task does not exist.
    """
    get_task(conn, id)
    fields = {k: v for k, v in fields.items() if k in EDITABLE}
    # Automate completed_at whenever completion is toggled through here,
    # matching complete_task (same invariant, one derivation).
    if "complete" in fields:
        fields["complete"] = 1 if fields["complete"] else 0
        fields["completed_at"] = db.now() if fields["complete"] else None
    if fields:
        with db.tx_write(conn):
            _util.update(conn, "task", id, fields)
    return get_task(conn, id)


def complete_task(conn: sqlite3.Connection, id, complete: bool = True) -> Task:
    """Toggle a task's completion, stamping ``completed_at`` accordingly.

    Thin wrapper over update_task, which derives ``completed_at`` from
    ``complete`` (single derivation shared by both entry points).

    Args:
        conn: sqlite3.Connection from db.connect().
        id: int — task id.
        complete: bool — completion state.
    Returns:
        Task — the updated task.
    Raises:
        NotFound: if the task does not exist.
    Invariants:
        completed_at is set to now() if complete is True, else cleared (NULL).
    """
    return update_task(conn, id, complete=complete)


def delete_task(conn: sqlite3.Connection, id) -> None:
    """Delete a task.

    Args:
        conn: sqlite3.Connection from db.connect().
        id: int — task id.
    """
    with db.tx_write(conn):
        _util.delete(conn, "task", id, resource="task")
