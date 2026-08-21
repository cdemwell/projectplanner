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
    """List all workflows.

    Args:
        conn: sqlite3.Connection from db.connect().
    Returns:
        A list of Workflow dataclasses.
    """
    return _util.list_rows(conn, Workflow, "workflow", order="id")


def get_workflow(conn: sqlite3.Connection, id) -> Workflow:
    """Get a workflow by ID.

    Args:
        conn: sqlite3.Connection from db.connect().
        id: Workflow ID.
    Returns:
        The Workflow dataclass.
    Raises:
        NotFound: if the workflow does not exist.
    """
    return _util.get(conn, Workflow, "workflow", id, resource="workflow")


def create_workflow(conn: sqlite3.Connection, name: str, *,
                    states: list[dict] | None = None) -> Workflow:
    """Create a workflow.

    The first ``started``-type state becomes the default.

    Args:
        conn: sqlite3.Connection from db.connect().
        name: str — display name.
        states: Optional list of ``{"name", "type", "position"}`` dicts.
    Returns:
        The created Workflow.
    Raises:
        ValidationError: if a state has an unknown type.
    """
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
    """Update workflow fields.

    Args:
        conn: sqlite3.Connection from db.connect().
        id: Workflow ID.
        fields: Fields to update (name, default_state_id).
    Returns:
        The updated Workflow.
    Raises:
        NotFound: if the workflow does not exist.
    """
    get_workflow(conn, id)
    fields = {k: v for k, v in fields.items() if k in _WORKFLOW_EDITABLE}
    if fields:
        with db.tx_write(conn):
            _util.update(conn, "workflow", id, fields)
    return get_workflow(conn, id)


def delete_workflow(conn: sqlite3.Connection, id) -> None:
    """Delete a workflow.

    Args:
        conn: sqlite3.Connection from db.connect().
        id: Workflow ID.
    Invariants:
        Deletes associated states via FK CASCADE.
    """
    with db.tx_write(conn):
        _util.delete(conn, "workflow", id, resource="workflow")


def list_workflow_states(conn: sqlite3.Connection, workflow_id) -> list[WorkflowState]:
    """List states for a workflow.

    Args:
        conn: sqlite3.Connection from db.connect().
        workflow_id: Workflow ID.
    Returns:
        A list of WorkflowState dataclasses ordered by position.
    """
    rows = conn.execute(
        "SELECT * FROM workflow_state WHERE workflow_id = ? ORDER BY position, id",
        (workflow_id,))
    return [WorkflowState.from_row(r) for r in rows]


def get_workflow_state(conn: sqlite3.Connection, id) -> WorkflowState:
    """Get a workflow state by ID.

    Args:
        conn: sqlite3.Connection from db.connect().
        id: State ID.
    Returns:
        The WorkflowState dataclass.
    Raises:
        NotFound: if the state does not exist.
    """
    return _util.get(conn, WorkflowState, "workflow_state", id, resource="workflow_state")


def state_type(conn: sqlite3.Connection, state_id) -> str | None:
    """Return the type of a state, or None if it doesn't exist.

    Args:
        conn: sqlite3.Connection from db.connect().
        state_id: State ID.
    Returns:
        The state type ('unstarted', 'started', 'done') or None.
    """
    row = conn.execute("SELECT type FROM workflow_state WHERE id = ?", (state_id,)).fetchone()
    return row["type"] if row else None


def create_workflow_state(conn: sqlite3.Connection, workflow_id, name: str, type: str, *,
                          position: float | None = None) -> WorkflowState:
    """Create a new workflow state.

    Args:
        conn: sqlite3.Connection from db.connect().
        workflow_id: Parent Workflow ID.
        name: str — display name.
        type: str — state type ('unstarted', 'started', 'done').
        position: Optional position. Auto-positions at end if None.
    Returns:
        The created WorkflowState.
    Raises:
        ValidationError: if type is unknown.
        NotFound: if the workflow does not exist.
    """
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
    """Update workflow state fields.

    Args:
        conn: sqlite3.Connection from db.connect().
        id: State ID.
        fields: Fields to update (name, type, position).
    Returns:
        The updated WorkflowState.
    Raises:
        NotFound: if the state does not exist.
    """
    get_workflow_state(conn, id)
    fields = {k: v for k, v in fields.items() if k in _STATE_EDITABLE}
    if fields:
        with db.tx_write(conn):
            _util.update(conn, "workflow_state", id, fields)
    return get_workflow_state(conn, id)


def delete_workflow_state(conn: sqlite3.Connection, id) -> None:
    """Delete a workflow state.

    Args:
        conn: sqlite3.Connection from db.connect().
        id: State ID.
    """
    with db.tx_write(conn):
        _util.delete(conn, "workflow_state", id, resource="workflow_state")