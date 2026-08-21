"""Groups — teams a story can be assigned to. Soft-deleted via ``archived``."""

from __future__ import annotations

import sqlite3

from . import _util, db
from .models import Group, Story

EDITABLE = {"name", "description", "archived"}


def list_groups(conn: sqlite3.Connection, *, include_archived: bool = False) -> list[Group]:
    if include_archived:
        return _util.list_rows(conn, Group, "group", order="name")
    return _util.list_rows(conn, Group, "group",
                          where="archived = 0", order="name")


def get_group(conn: sqlite3.Connection, id) -> Group:
    return _util.get(conn, Group, "group", id, resource="group")


def create_group(conn: sqlite3.Connection, name: str, *, description: str = "") -> Group:
    with db.tx_write(conn):
        new_id = _util.insert(conn, "group", {
            "name": name, "description": description,
            "archived": 0, "created_at": db.now(),
        })
    return get_group(conn, new_id)


def update_group(conn: sqlite3.Connection, id, **fields) -> Group:
    get_group(conn, id)
    fields = {k: v for k, v in fields.items() if k in EDITABLE}
    if fields:
        with db.tx_write(conn):
            _util.update(conn, "group", id, fields)
    return get_group(conn, id)


def archive_group(conn: sqlite3.Connection, id, archived: bool = True) -> Group:
    return update_group(conn, id, archived=1 if archived else 0)


def delete_group(conn: sqlite3.Connection, id) -> None:
    with db.tx_write(conn):
        _util.delete(conn, "group", id, resource="group")


def list_group_stories(conn: sqlite3.Connection, group_id) -> list[Story]:
    from .models import Story as _S
    rows = conn.execute("SELECT * FROM story WHERE group_id = ? ORDER BY position, id",
                       (group_id,))
    return [_S.from_row(r) for r in rows]