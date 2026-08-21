"""Labels — tags applied to stories for cross-cutting classification."""

from __future__ import annotations

import sqlite3

from . import _util, db
from .models import Label

EDITABLE = {"name", "color", "description"}


def list_labels(conn: sqlite3.Connection) -> list[Label]:
    """List all labels.

    Args:
        conn: sqlite3.Connection from db.connect().
    Returns:
        A list of Label dataclasses.
    """
    return _util.list_rows(conn, Label, "label", order="name")


def get_label(conn: sqlite3.Connection, id) -> Label:
    """Get a label by ID.

    Args:
        conn: sqlite3.Connection from db.connect().
        id: Label ID.
    Returns:
        The Label dataclass.
    Raises:
        NotFound: if the label does not exist.
    """
    return _util.get(conn, Label, "label", id, resource="label")


def create_label(conn: sqlite3.Connection, name: str, *, color: str = "",
                 description: str = "") -> Label:
    """Create a new label.

    Args:
        conn: sqlite3.Connection from db.connect().
        name: str — display name.
        color: Optional color hex code.
        description: Optional label description.
    Returns:
        The created Label.
    """
    with db.tx_write(conn):
        new_id = _util.insert(conn, "label", {
            "name": name, "color": color, "description": description,
            "created_at": db.now(),
        })
    return get_label(conn, new_id)


def update_label(conn: sqlite3.Connection, id, **fields) -> Label:
    """Update label fields.

    Args:
        conn: sqlite3.Connection from db.connect().
        id: Label ID.
        fields: Fields to update (name, color, description).
    Returns:
        The updated Label.
    Raises:
        NotFound: if the label does not exist.
    """
    get_label(conn, id)
    fields = {k: v for k, v in fields.items() if k in EDITABLE}
    if fields:
        with db.tx_write(conn):
            _util.update(conn, "label", id, fields)
    return get_label(conn, id)


def delete_label(conn: sqlite3.Connection, id) -> None:
    """Delete a label.

    Args:
        conn: sqlite3.Connection from db.connect().
        id: Label ID.
    """
    with db.tx_write(conn):
        _util.delete(conn, "label", id, resource="label")
