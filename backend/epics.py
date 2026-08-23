"""Epics — large bodies of work that group stories. State is a simple enum
('planned'/'in_progress'/'done'); entering 'done' stamps ``completed_at``."""

from __future__ import annotations

import sqlite3

from . import _util, db, errors
from .models import Epic

STATES = ("planned", "in_progress", "done")
EDITABLE = {"name", "description", "state", "milestone_id", "project_id"}


def list_epics(conn: sqlite3.Connection, *, project_id=None, milestone_id=None,
               limit: int | None = None, offset: int | None = None) -> list[Epic]:
    """List epics with optional filters.

    Args:
        conn: sqlite3.Connection from db.connect().
        project_id: int | None — filter by project.
        milestone_id: int | None — filter by milestone.
        limit: int | None — max rows (None = all).
        offset: int | None — rows to skip (None = 0).
    Returns:
        list[Epic] — the matching epics.
    """
    where, params = [], []
    if project_id is not None:
        where.append("project_id = ?"); params.append(project_id)
    if milestone_id is not None:
        where.append("milestone_id = ?"); params.append(milestone_id)
    return _util.list_rows(conn, Epic, "epic",
                           where=" AND ".join(where) or None, params=params, order="id",
                           limit=limit, offset=offset)


def get_epic(conn: sqlite3.Connection, id) -> Epic:
    """Fetch a single epic by ID.

    Args:
        conn: sqlite3.Connection from db.connect().
        id: int — the epic ID.
    Returns:
        Epic — the epic.
    Raises:
        NotFound: if the epic does not exist.
    """
    return _util.get(conn, Epic, "epic", id, resource="epic")


def create_epic(conn: sqlite3.Connection, name: str, *, description: str = "",
                state: str = "planned", milestone_id=None, project_id=None) -> Epic:
    """Create a new epic.

    Args:
        conn: sqlite3.Connection from db.connect().
        name: str — the epic name.
        description: str — optional description.
        state: str — 'planned' | 'in_progress' | 'done'.
        milestone_id: int | None — optional milestone parent.
        project_id: int | None — optional project parent.
    Returns:
        Epic — the created epic.
    Raises:
        ValidationError: if the state is unknown.
    """
    if state not in STATES:
        raise errors.ValidationError(f"unknown epic state {state!r}")
    with db.tx_write(conn):
        new_id = _util.insert(conn, "epic", {
            "name": name, "description": description, "state": state,
            "milestone_id": milestone_id, "project_id": project_id,
            "created_at": db.now(), "completed_at": None,
        })
    return get_epic(conn, new_id)


def update_epic(conn: sqlite3.Connection, id, **fields) -> Epic:
    """Update an epic's editable fields.

    Args:
        conn: sqlite3.Connection from db.connect().
        id: int — the epic ID.
        fields: dict — fields to update (subset of EDITABLE).
    Returns:
        Epic — the updated epic.
    Raises:
        NotFound: if the epic does not exist.
        ValidationError: if the provided state is unknown.
    Invariants:
        completed_at is automatically stamped when state is set to 'done'
        and cleared otherwise.
    """
    epic = get_epic(conn, id)
    fields = {k: v for k, v in fields.items() if k in EDITABLE}
    # Automate completed_at when state changes to/from 'done'.
    if "state" in fields:
        if fields["state"] not in STATES:
            raise errors.ValidationError(f"unknown epic state {fields['state']!r}")
        fields["completed_at"] = db.now() if fields["state"] == "done" else None
    elif epic.state != "done" and not fields:
        pass
    if fields:
        with db.tx_write(conn):
            _util.update(conn, "epic", id, fields)
    return get_epic(conn, id)


def delete_epic(conn: sqlite3.Connection, id) -> None:
    """Delete an epic by ID.

    Args:
        conn: sqlite3.Connection from db.connect().
        id: int — the epic ID.
    """
    with db.tx_write(conn):
        _util.delete(conn, "epic", id, resource="epic")


def list_epic_stories(conn: sqlite3.Connection, epic_id) -> list:
    """List all stories belonging to an epic.

    Args:
        conn: sqlite3.Connection from db.connect().
        epic_id: int — the epic ID.
    Returns:
        list[Story] — the stories associated with the epic.
    Note:
        Delegates to stories.list_stories.
    """
    from .stories import list_stories
    return list_stories(conn, epic_id=epic_id)


def epic_progress(conn: sqlite3.Connection, epic_id: int) -> dict:
    """Compute an epic's progress based on its stories.

    Args:
        conn: sqlite3.Connection from db.connect().
        epic_id: int — the epic ID.
    Returns:
        dict — {"done": int, "total": int, "pct": float}
    """
    row = conn.execute(
        "SELECT COUNT(*), COUNT(completed_at) FROM story WHERE epic_id = ?",
        (epic_id,)
    ).fetchone()
    total, done = row[0], row[1]
    pct = (done / total * 100) if total > 0 else 0.0
    return {"done": done, "total": total, "pct": pct}
