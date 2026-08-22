"""Groups — teams a story can be assigned to. Soft-deleted via ``archived``."""

from __future__ import annotations

import sqlite3

from . import _util, db
from .models import Group, Story

EDITABLE = {"name", "description", "archived"}


def list_groups(conn: sqlite3.Connection, *, include_archived: bool = False,
                limit: int | None = None, offset: int | None = None) -> list[Group]:
    """List groups.

    Args:
        conn: sqlite3.Connection from db.connect().
        include_archived: Whether to include archived groups.
        limit: int | None — max rows (None = all).
        offset: int | None — rows to skip (None = 0).
    Returns:
        A list of Group dataclasses.
    """
    if include_archived:
        return _util.list_rows(conn, Group, "group", order="name",
                               limit=limit, offset=offset)
    return _util.list_rows(conn, Group, "group",
                          where="archived = 0", order="name",
                          limit=limit, offset=offset)


def get_group(conn: sqlite3.Connection, id) -> Group:
    """Get a group by ID.

    Args:
        conn: sqlite3.Connection from db.connect().
        id: Group ID.
    Returns:
        The Group dataclass.
    Raises:
        NotFound: if the group does not exist.
    """
    return _util.get(conn, Group, "group", id, resource="group")


def create_group(conn: sqlite3.Connection, name: str, *, description: str = "") -> Group:
    """Create a new group.

    Args:
        conn: sqlite3.Connection from db.connect().
        name: str — display name.
        description: Optional group description.
    Returns:
        The created Group.
    Invariants:
        archived is initialized to 0.
    """
    with db.tx_write(conn):
        new_id = _util.insert(conn, "group", {
            "name": name, "description": description,
            "archived": 0, "created_at": db.now(),
        })
    return get_group(conn, new_id)


def update_group(conn: sqlite3.Connection, id, **fields) -> Group:
    """Update group fields.

    Args:
        conn: sqlite3.Connection from db.connect().
        id: Group ID.
        fields: Fields to update (name, description, archived).
    Returns:
        The updated Group.
    Raises:
        NotFound: if the group does not exist.
    """
    get_group(conn, id)
    fields = {k: v for k, v in fields.items() if k in EDITABLE}
    if fields:
        with db.tx_write(conn):
            _util.update(conn, "group", id, fields)
    return get_group(conn, id)


def archive_group(conn: sqlite3.Connection, id, archived: bool = True) -> Group:
    """Archive or unarchive a group.

    Args:
        conn: sqlite3.Connection from db.connect().
        id: Group ID.
        archived: Whether to archive (True) or unarchive (False).
    Returns:
        The updated Group.
    Invariants:
        archived is stored as int 0/1.
    """
    return update_group(conn, id, archived=1 if archived else 0)


def delete_group(conn: sqlite3.Connection, id) -> None:
    """Delete a group.

    Args:
        conn: sqlite3.Connection from db.connect().
        id: Group ID.
    """
    with db.tx_write(conn):
        _util.delete(conn, "group", id, resource="group")


def list_group_stories(conn: sqlite3.Connection, group_id) -> list[Story]:
    """List stories associated with a group.

    Args:
        conn: sqlite3.Connection from db.connect().
        group_id: Group ID.
    Returns:
        A list of Story dataclasses.
    """
    from .models import Story as _S
    rows = conn.execute("SELECT * FROM story WHERE group_id = ? ORDER BY position, id",
                       (group_id,))
    return [_S.from_row(r) for r in rows]
