"""Members — the people who own/request stories. The single local user is seeded."""

from __future__ import annotations

import sqlite3

from . import _util, _validate, db
from .models import Member

EDITABLE = {"name", "mention_name"}


def list_members(conn: sqlite3.Connection, *, limit: int | None = None,
                 offset: int | None = None) -> list[Member]:
    """List all members.

    Args:
        conn: sqlite3.Connection from db.connect().
        limit: int | None — max rows (None = all).
        offset: int | None — rows to skip (None = 0).
    Returns:
        A list of Member dataclasses.
    """
    return _util.list_rows(conn, Member, "member", order="id",
                           limit=limit, offset=offset)


def get_member(conn: sqlite3.Connection, id) -> Member:
    """Get a member by ID.

    Args:
        conn: sqlite3.Connection from db.connect().
        id: Member ID.
    Returns:
        The Member dataclass.
    Raises:
        NotFound: if the member does not exist.
    """
    return _util.get(conn, Member, "member", id, resource="member")


def create_member(conn: sqlite3.Connection, name: str, *, mention_name: str | None = None) -> Member:
    """Create a new member.

    Mention name is derived from name if not provided.

    Args:
        conn: sqlite3.Connection from db.connect().
        name: str — display name.
        mention_name: Optional override for the mention identifier.
    Returns:
        The created Member.
    """
    _validate.require_name(name)
    mention_name = (mention_name or name).strip().lower().replace(" ", "_")
    with db.tx_write(conn):
        new_id = _util.insert(conn, "member", {
            "name": name,
            "mention_name": mention_name,
            "created_at": db.now(),
        })
    return get_member(conn, new_id)


def update_member(conn: sqlite3.Connection, id, **fields) -> Member:
    """Update member fields.

    Args:
        conn: sqlite3.Connection from db.connect().
        id: Member ID.
        fields: Fields to update (name, mention_name).
    Returns:
        The updated Member.
    Raises:
        NotFound: if the member does not exist.
    """
    get_member(conn, id)  # raises NotFound if absent
    fields = {k: v for k, v in fields.items() if k in EDITABLE}
    if "name" in fields:
        _validate.require_name(fields["name"])
    if fields:
        with db.tx_write(conn):
            _util.update(conn, "member", id, fields)
    return get_member(conn, id)


def delete_member(conn: sqlite3.Connection, id) -> None:
    """Delete a member.

    Args:
        conn: sqlite3.Connection from db.connect().
        id: Member ID.
    """
    with db.tx_write(conn):
        _util.delete(conn, "member", id, resource="member")
