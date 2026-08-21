"""Epics — large bodies of work that group stories. State is a simple enum
('planned'/'in_progress'/'done'); entering 'done' stamps ``completed_at``."""

from __future__ import annotations

import sqlite3

from . import _util, db, errors
from .models import Epic

STATES = ("planned", "in_progress", "done")
EDITABLE = {"name", "description", "state", "milestone_id", "project_id"}


def list_epics(conn: sqlite3.Connection, *, project_id=None, milestone_id=None) -> list[Epic]:
    where, params = [], []
    if project_id is not None:
        where.append("project_id = ?"); params.append(project_id)
    if milestone_id is not None:
        where.append("milestone_id = ?"); params.append(milestone_id)
    return _util.list_rows(conn, Epic, "epic",
                           where=" AND ".join(where) or None, params=params, order="id")


def get_epic(conn: sqlite3.Connection, id) -> Epic:
    return _util.get(conn, Epic, "epic", id, resource="epic")


def create_epic(conn: sqlite3.Connection, name: str, *, description: str = "",
                state: str = "planned", milestone_id=None, project_id=None) -> Epic:
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
    with db.tx_write(conn):
        _util.delete(conn, "epic", id, resource="epic")


def list_epic_stories(conn: sqlite3.Connection, epic_id) -> list:
    from .stories import list_stories
    return list_stories(conn, epic_id=epic_id)