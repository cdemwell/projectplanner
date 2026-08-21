"""Iterations — time-boxed periods (sprints) a story can be scheduled into."""

from __future__ import annotations

import sqlite3

from . import _util, db, errors
from .models import Iteration

STATUSES = ("planned", "active", "done")
EDITABLE = {"name", "description", "status", "start_date", "end_date"}


def list_iterations(conn: sqlite3.Connection, *, status: str | None = None) -> list[Iteration]:
    if status is not None:
        return _util.list_rows(conn, Iteration, "iteration", where="status = ?",
                               params=(status,), order="id")
    return _util.list_rows(conn, Iteration, "iteration", order="id")


def get_iteration(conn: sqlite3.Connection, id) -> Iteration:
    return _util.get(conn, Iteration, "iteration", id, resource="iteration")


def create_iteration(conn: sqlite3.Connection, name: str, *, description: str = "",
                     status: str = "planned", start_date: str | None = None,
                     end_date: str | None = None) -> Iteration:
    if status not in STATUSES:
        raise errors.ValidationError(f"unknown iteration status {status!r}")
    with db.tx_write(conn):
        new_id = _util.insert(conn, "iteration", {
            "name": name, "description": description, "status": status,
            "start_date": start_date, "end_date": end_date, "created_at": db.now(),
        })
    return get_iteration(conn, new_id)


def update_iteration(conn: sqlite3.Connection, id, **fields) -> Iteration:
    get_iteration(conn, id)
    fields = {k: v for k, v in fields.items() if k in EDITABLE}
    if "status" in fields and fields["status"] not in STATUSES:
        raise errors.ValidationError(f"unknown iteration status {fields['status']!r}")
    if fields:
        with db.tx_write(conn):
            _util.update(conn, "iteration", id, fields)
    return get_iteration(conn, id)


def delete_iteration(conn: sqlite3.Connection, id) -> None:
    with db.tx_write(conn):
        _util.delete(conn, "iteration", id, resource="iteration")


def list_iteration_stories(conn: sqlite3.Connection, iteration_id) -> list:
    from .stories import list_stories
    return list_stories(conn, iteration_id=iteration_id)