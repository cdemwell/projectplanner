"""Iterations — time-boxed periods (sprints) a story can be scheduled into."""

from __future__ import annotations

import sqlite3

from . import _util, _validate, db, errors
from .models import Iteration

STATUSES = ("planned", "active", "done")
EDITABLE = {"name", "description", "status", "start_date", "end_date"}


def _check_dates(start_date, end_date) -> None:
    """Validate both dates as ISO and reject start > end.

    Args:
        start_date: str | None — iteration start (ISO date).
        end_date: str | None — iteration end (ISO date).
    Raises:
        ValidationError: on a malformed date or an inverted range.
    """
    _validate.require_iso_date(start_date, "start_date")
    _validate.require_iso_date(end_date, "end_date")
    if start_date and end_date and start_date > end_date:
        raise errors.ValidationError(
            f"start_date {start_date!r} is after end_date {end_date!r}")


def list_iterations(conn: sqlite3.Connection, *, status: str | None = None,
                    limit: int | None = None, offset: int | None = None) -> list[Iteration]:
    """List all iterations, optionally filtered by status.

    Args:
        conn: sqlite3.Connection from db.connect().
        status: str | None — filter by status ('planned', 'active', 'done').
        limit: int | None — max rows (None = all).
        offset: int | None — rows to skip (None = 0).
    Returns:
        list[Iteration] — the list of matching iterations.
    """
    if status is not None:
        return _util.list_rows(conn, Iteration, "iteration", where="status = ?",
                               params=(status,), order="id",
                               limit=limit, offset=offset)
    return _util.list_rows(conn, Iteration, "iteration", order="id",
                           limit=limit, offset=offset)


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
    _validate.require_name(name)
    _check_dates(start_date, end_date)
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
    iteration = get_iteration(conn, id)
    fields = {k: v for k, v in fields.items() if k in EDITABLE}
    if "status" in fields and fields["status"] not in STATUSES:
        raise errors.ValidationError(f"unknown iteration status {fields['status']!r}")
    # Validate the *effective* range: a new start must not exceed the existing
    # end, and vice versa.
    _check_dates(fields.get("start_date", iteration.start_date),
                 fields.get("end_date", iteration.end_date))
    if "name" in fields:
        _validate.require_name(fields["name"])
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
