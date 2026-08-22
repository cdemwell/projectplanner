"""Milestones — checkpoints that group epics. State enum
('planned'/'in_progress'/'done'); entering 'done' stamps ``completed_at``."""

from __future__ import annotations

import sqlite3

from . import _util, db, errors
from .models import Milestone

STATES = ("planned", "in_progress", "done")
EDITABLE = {"name", "description", "state"}


def list_milestones(conn: sqlite3.Connection, *, state: str | None = None,
                    limit: int | None = None, offset: int | None = None) -> list[Milestone]:
    """List all milestones, optionally filtered by state.

    Args:
        conn: sqlite3.Connection from db.connect().
        state: str | None — filter by state ('planned', 'in_progress', 'done').
        limit: int | None — max rows (None = all).
        offset: int | None — rows to skip (None = 0).
    Returns:
        list[Milestone] — the list of matching milestones.
    """
    if state is not None:
        return _util.list_rows(conn, Milestone, "milestone", where="state = ?",
                               params=(state,), order="id",
                               limit=limit, offset=offset)
    return _util.list_rows(conn, Milestone, "milestone", order="id",
                           limit=limit, offset=offset)


def get_milestone(conn: sqlite3.Connection, id) -> Milestone:
    """Fetch a single milestone by ID.

    Args:
        conn: sqlite3.Connection from db.connect().
        id: int — the milestone ID.
    Returns:
        Milestone — the milestone.
    Raises:
        NotFound: if the milestone does not exist.
    """
    return _util.get(conn, Milestone, "milestone", id, resource="milestone")


def create_milestone(conn: sqlite3.Connection, name: str, *, description: str = "",
                     state: str = "planned") -> Milestone:
    """Create a new milestone.

    Args:
        conn: sqlite3.Connection from db.connect().
        name: str — the milestone name.
        description: str — optional description.
        state: str — 'planned' | 'in_progress' | 'done'.
    Returns:
        Milestone — the created milestone.
    Raises:
        ValidationError: if the state is unknown.
    """
    if state not in STATES:
        raise errors.ValidationError(f"unknown milestone state {state!r}")
    with db.tx_write(conn):
        new_id = _util.insert(conn, "milestone", {
            "name": name, "description": description, "state": state,
            "created_at": db.now(), "completed_at": None,
        })
    return get_milestone(conn, new_id)


def update_milestone(conn: sqlite3.Connection, id, **fields) -> Milestone:
    """Update a milestone's editable fields.

    Args:
        conn: sqlite3.Connection from db.connect().
        id: int — the milestone ID.
        fields: dict — fields to update (subset of EDITABLE).
    Returns:
        Milestone — the updated milestone.
    Raises:
        NotFound: if the milestone does not exist.
        ValidationError: if the provided state is unknown.
    Invariants:
        completed_at is automatically stamped when state is set to 'done'
        and cleared otherwise.
    """
    get_milestone(conn, id)
    fields = {k: v for k, v in fields.items() if k in EDITABLE}
    if "state" in fields:
        if fields["state"] not in STATES:
            raise errors.ValidationError(f"unknown milestone state {fields['state']!r}")
        fields["completed_at"] = db.now() if fields["state"] == "done" else None
    if fields:
        with db.tx_write(conn):
            _util.update(conn, "milestone", id, fields)
    return get_milestone(conn, id)


def delete_milestone(conn: sqlite3.Connection, id) -> None:
    """Delete a milestone by ID.

    Args:
        conn: sqlite3.Connection from db.connect().
        id: int — the milestone ID.
    """
    with db.tx_write(conn):
        _util.delete(conn, "milestone", id, resource="milestone")


def list_milestone_epics(conn: sqlite3.Connection, milestone_id) -> list:
    """List all epics associated with a milestone.

    Args:
        conn: sqlite3.Connection from db.connect().
        milestone_id: int — the milestone ID.
    Returns:
        list[Epic] — the list of matching epics.
    """
    from .epics import list_epics
    return list_epics(conn, milestone_id=milestone_id)
