"""Members — the people who own/request stories. The single local user is seeded."""

from __future__ import annotations

import sqlite3

from . import _util, db
from .models import Member

EDITABLE = {"name", "mention_name"}


def list_members(conn: sqlite3.Connection) -> list[Member]:
    return _util.list_rows(conn, Member, "member", order="id")


def get_member(conn: sqlite3.Connection, id) -> Member:
    return _util.get(conn, Member, "member", id, resource="member")


def create_member(conn: sqlite3.Connection, name: str, *, mention_name: str | None = None) -> Member:
    mention_name = (mention_name or name).strip().lower().replace(" ", "_")
    with db.tx_write(conn):
        new_id = _util.insert(conn, "member", {
            "name": name,
            "mention_name": mention_name,
            "created_at": db.now(),
        })
    return get_member(conn, new_id)


def update_member(conn: sqlite3.Connection, id, **fields) -> Member:
    get_member(conn, id)  # raises NotFound if absent
    fields = {k: v for k, v in fields.items() if k in EDITABLE}
    if fields:
        with db.tx_write(conn):
            _util.update(conn, "member", id, fields)
    return get_member(conn, id)


def delete_member(conn: sqlite3.Connection, id) -> None:
    with db.tx_write(conn):
        _util.delete(conn, "member", id, resource="member")