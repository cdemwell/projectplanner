"""Iterations — time-boxed periods (sprints) a story can be scheduled into."""

from __future__ import annotations

import sqlite3

from . import _util, db, errors
from .models import Iteration

STATUSES = ("planned", "active", "done")
EDITABLE = {"name", "description", "status", "start_date", "end_date"}


def list_iterations(conn: sqlite3.Connection, *, status: str | None = None) -> list[Iteration]:
    """List all iterations, optionally filtered by status.

    Args:
        conn: sqlite3.Connection from db.connect().
        status: str | None — filter by status ('planned', 'active', 'done').
    Returns:
        list[Iteration] — the list of matching iterations.
    """
    if status is not None:
        return _util.list_rows(conn, Iteration, "iteration", where="status = ?",
                               params=(status,), order="id")
    return _util.list_rows(conn, Iteration, "iteration", order="id")


def get_iteration(conn: sqlite3.Connection, id) -> Iteration:
    """Fetch a single iteration by ID.

    Args:
        conn: sqlite3.Connection from db.connect().
        id: int — the iteration ID.
    Returns:
        Iteration — the iteration.
    Raises:
        NotFound: if the iteration does not exist.
    """
    return _util.get(conn, Iteration, "iteration", id, resource="iteration")


def create_iteration(conn: sqlite3.Connection, name: str, *, description: str = "",
                     status: str = "planned", start_date: str | None = None,
                     end_date: str | None = None) -> Iteration:
    """Create a new iteration.

    Args:
        conn: sqlite3.Connection from db.connect().
        name: str — the iteration name.
        description: str — optional description.
        status: str — 'planned' | 'active' | 'done'.
        start_date: str | None — ISO date string.
        end_date: str | None — ISO date string.
    Returns:
        Iteration — the created iteration.
    Raises:
        ValidationError: if the status is unknown.
    """
    if status not in STATUSES:
        raise errors.ValidationError(f"unknown iteration status {status!r}")
    with db.tx_write(conn):
        new_id = _util.insert(conn, "iteration", {
            "name": name, "description": description, "status": status,
            "start_date": start_date, "end_date": end_date, "created_at": db.now(),
        })
    return get_iteration(conn, new_id)


def update_iteration(conn: sqlite3.Connection, id, **fields) -> Iteration:
    """Update an iteration's editable fields.

    Args:
        conn: sqlite3.Connection from db.connect().
        id: int — the iteration ID.
        fields: dict — fields to update (subset of EDITABLE).
    Returns:
        Iteration — the updated iteration.
    Raises:
        NotFound: if the iteration does not exist.
        ValidationError: if the provided status is unknown.
    """
    get_iteration(conn, id)
    fields = {k: v for k, v in fields.items() if k in EDITABLE}
    if "status" in fields and fields["status"] not in STATUSES:
        raise errors.ValidationError(f"unknown iteration status {fields['status']!r}")
    if fields:
        with db.tx_write(conn):
            _util.update(conn, "iteration", id, fields)
    return get_iteration(conn, id)


def delete_iteration(conn: sqlite3.Connection, id) -> None:
    """Delete an iteration by ID.

    Args:
        conn: sqlite3.Connection from db.connect().
        id: int — the iteration ID.
    """
    with db.tx_write(conn):
        _util.delete(conn, "iteration", id, resource="iteration")


def list_iteration_stories(conn: sqlite3.Connection, iteration_id) -> list:
    """List all stories scheduled into an iteration.

    Args:
        conn: sqlite3.Connection from db.connect().
        iteration_id: int — the iteration ID.
    Returns:
        list[Story] — the stories associated with the iteration.
    Note:
        Delegates to stories.list_stories.
    """
    from .stories import list_stories
    return list_stories(conn, iteration_id=iteration_id)