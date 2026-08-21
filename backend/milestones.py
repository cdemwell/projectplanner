"""Milestones — checkpoints that group epics. State enum
('planned'/'in_progress'/'done'); entering 'done' stamps ``completed_at``."""

from __future__ import annotations

import sqlite3

from . import _util, db, errors
from .models import Milestone

STATES = ("planned", "in_progress", "done")
EDITABLE = {"name", "description", "state"}


def list_milestones(conn: sqlite3.Connection, *, state: str | None = None) -> list[Milestone]:
    if state is not None:
        return _util.list_rows(conn, Milestone, "milestone", where="state = ?",
                               params=(state,), order="id")
    return _util.list_rows(conn, Milestone, "milestone", order="id")


def get_milestone(conn: sqlite3.Connection, id) -> Milestone:
    return _util.get(conn, Milestone, "milestone", id, resource="milestone")


def create_milestone(conn: sqlite3.Connection, name: str, *, description: str = "",
                     state: str = "planned") -> Milestone:
    if state not in STATES:
        raise errors.ValidationError(f"unknown milestone state {state!r}")
    with db.tx_write(conn):
        new_id = _util.insert(conn, "milestone", {
            "name": name, "description": description, "state": state,
            "created_at": db.now(), "completed_at": None,
        })
    return get_milestone(conn, new_id)


def update_milestone(conn: sqlite3.Connection, id, **fields) -> Milestone:
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
    with db.tx_write(conn):
        _util.delete(conn, "milestone", id, resource="milestone")


def list_milestone_epics(conn: sqlite3.Connection, milestone_id) -> list:
    from .epics import list_epics
    return list_epics(conn, milestone_id=milestone_id)