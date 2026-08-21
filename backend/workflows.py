"""Workflows and their states. A workflow is an ordered set of states typed
'unstarted' / 'started' / 'done'. The default workflow is seeded on first run."""

from __future__ import annotations

import sqlite3

from . import _util, db, errors
from .models import Workflow, WorkflowState

STATE_TYPES = ("unstarted", "started", "done")

_WORKFLOW_EDITABLE = {"name", "default_state_id"}
_STATE_EDITABLE = {"name", "type", "position"}


def list_workflows(conn: sqlite3.Connection) -> list[Workflow]:
    return _util.list_rows(conn, Workflow, "workflow", order="id")


def get_workflow(conn: sqlite3.Connection, id) -> Workflow:
    return _util.get(conn, Workflow, "workflow", id, resource="workflow")


def create_workflow(conn: sqlite3.Connection, name: str, *,
                    states: list[dict] | None = None) -> Workflow:
    """Create a workflow. ``states`` is an optional list of
    ``{"name", "type", "position"}`` dicts; the first ``started``-type state
    becomes the default."""
    with db.tx_write(conn):
        new_id = _util.insert(conn, "workflow", {"name": name, "default_state_id": None,
                                                 "created_at": db.now()})
        started_id = None
        for i, s in enumerate(states or []):
            stype = s.get("type", "unstarted")
            if stype not in STATE_TYPES:
                raise errors.ValidationError(f"unknown state type {stype!r}")
            pos = s.get("position", float(i))
            sid = _util.insert(conn, "workflow_state", {
                "workflow_id": new_id, "name": s["name"], "type": stype,
                "position": pos, "created_at": db.now(),
            })
            if stype == "started" and started_id is None:
                started_id = sid
        if started_id is not None:
            _util.update(conn, "workflow", new_id, {"default_state_id": started_id})
    return get_workflow(conn, new_id)


def update_workflow(conn: sqlite3.Connection, id, **fields) -> Workflow:
    get_workflow(conn, id)
    fields = {k: v for k, v in fields.items() if k in _WORKFLOW_EDITABLE}
    if fields:
        with db.tx_write(conn):
            _util.update(conn, "workflow", id, fields)
    return get_workflow(conn, id)


def delete_workflow(conn: sqlite3.Connection, id) -> None:
    with db.tx_write(conn):
        _util.delete(conn, "workflow", id, resource="workflow")


def list_workflow_states(conn: sqlite3.Connection, workflow_id) -> list[WorkflowState]:
    rows = conn.execute(
        "SELECT * FROM workflow_state WHERE workflow_id = ? ORDER BY position, id",
        (workflow_id,))
    return [WorkflowState.from_row(r) for r in rows]


def get_workflow_state(conn: sqlite3.Connection, id) -> WorkflowState:
    return _util.get(conn, WorkflowState, "workflow_state", id, resource="workflow_state")


def state_type(conn: sqlite3.Connection, state_id) -> str | None:
    """Return the type of a state, or None if it doesn't exist."""
    row = conn.execute("SELECT type FROM workflow_state WHERE id = ?", (state_id,)).fetchone()
    return row["type"] if row else None


def create_workflow_state(conn: sqlite3.Connection, workflow_id, name: str, type: str, *,
                          position: float | None = None) -> WorkflowState:
    if type not in STATE_TYPES:
        raise errors.ValidationError(f"unknown state type {type!r}")
    get_workflow(conn, workflow_id)  # ensure parent exists
    if position is None:
        maxpos = conn.execute(
            "SELECT COALESCE(MAX(position), 0) FROM workflow_state WHERE workflow_id = ?",
            (workflow_id,)).fetchone()[0]
        position = maxpos + 1.0
    with db.tx_write(conn):
        new_id = _util.insert(conn, "workflow_state", {
            "workflow_id": workflow_id, "name": name, "type": type,
            "position": position, "created_at": db.now(),
        })
    return get_workflow_state(conn, new_id)


def update_workflow_state(conn: sqlite3.Connection, id, **fields) -> WorkflowState:
    get_workflow_state(conn, id)
    fields = {k: v for k, v in fields.items() if k in _STATE_EDITABLE}
    if fields:
        with db.tx_write(conn):
            _util.update(conn, "workflow_state", id, fields)
    return get_workflow_state(conn, id)


def delete_workflow_state(conn: sqlite3.Connection, id) -> None:
    with db.tx_write(conn):
        _util.delete(conn, "workflow_state", id, resource="workflow_state")