"""Comments on a story. Support threaded replies via ``parent_id``."""

from __future__ import annotations

import sqlite3

from . import _util, db, stories
from .models import StoryComment

EDITABLE = {"text"}


def list_comments(conn: sqlite3.Connection, story_id) -> list[StoryComment]:
    stories.get_story(conn, story_id)
    rows = conn.execute(
        "SELECT * FROM story_comment WHERE story_id = ? ORDER BY created_at, id",
        (story_id,))
    return [StoryComment.from_row(r) for r in rows]


def get_comment(conn: sqlite3.Connection, id) -> StoryComment:
    return _util.get(conn, StoryComment, "story_comment", id, resource="comment")


def create_comment(conn: sqlite3.Connection, story_id: int, text: str, *,
                   author_id: int | None = None, parent_id: int | None = None) -> StoryComment:
    stories.get_story(conn, story_id)
    if parent_id is not None:
        get_comment(conn, parent_id)  # ensure parent comment exists
    ts = db.now()
    with db.tx_write(conn):
        new_id = _util.insert(conn, "story_comment", {
            "story_id": story_id, "author_id": author_id, "text": text,
            "parent_id": parent_id, "created_at": ts, "updated_at": ts,
        })
    return get_comment(conn, new_id)


def update_comment(conn: sqlite3.Connection, id, **fields) -> StoryComment:
    get_comment(conn, id)
    fields = {k: v for k, v in fields.items() if k in EDITABLE}
    if fields:
        fields["updated_at"] = db.now()
        with db.tx_write(conn):
            _util.update(conn, "story_comment", id, fields)
    return get_comment(conn, id)


def delete_comment(conn: sqlite3.Connection, id) -> None:
    with db.tx_write(conn):
        _util.delete(conn, "story_comment", id, resource="comment")