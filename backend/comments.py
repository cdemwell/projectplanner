"""Comments on a story. Support threaded replies via ``parent_id``."""

from __future__ import annotations

import sqlite3

from . import _util, db, stories
from .models import StoryComment

EDITABLE = {"text"}


def list_comments(conn: sqlite3.Connection, story_id) -> list[StoryComment]:
    """List threaded comments for a story.

    Args:
        conn: sqlite3.Connection from db.connect().
        story_id: int — owning story.
    Returns:
        list[StoryComment] — comments ordered by created_at and id.
    Raises:
        NotFound: if the story does not exist.
    """
    stories.get_story(conn, story_id)
    rows = conn.execute(
        "SELECT * FROM story_comment WHERE story_id = ? ORDER BY created_at, id",
        (story_id,))
    return [StoryComment.from_row(r) for r in rows]


def get_comment(conn: sqlite3.Connection, id) -> StoryComment:
    """Get a single comment.

    Args:
        conn: sqlite3.Connection from db.connect().
        id: int — comment id.
    Returns:
        StoryComment — the found comment.
    Raises:
        NotFound: if the comment does not exist.
    """
    return _util.get(conn, StoryComment, "story_comment", id, resource="comment")


def create_comment(conn: sqlite3.Connection, story_id: int, text: str, *,
                   author_id: int | None = None, parent_id: int | None = None) -> StoryComment:
    """Create a comment.

    Supports threaded replies via ``parent_id``.

    Args:
        conn: sqlite3.Connection from db.connect().
        story_id: int — owning story.
        text: str — comment text.
        author_id: int | None — author id.
        parent_id: int | None — parent comment id for threaded replies.
    Returns:
        StoryComment — the created comment.
    Raises:
        NotFound: if the story or parent comment does not exist.
    """
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
    """Update a comment's text.

    Only fields in EDITABLE (text) are updated. This also updates ``updated_at``.

    Args:
        conn: sqlite3.Connection from db.connect().
        id: int — comment id.
        fields: kwargs — fields to update.
    Returns:
        StoryComment — the updated comment.
    Raises:
        NotFound: if the comment does not exist.
    """
    get_comment(conn, id)
    fields = {k: v for k, v in fields.items() if k in EDITABLE}
    if fields:
        fields["updated_at"] = db.now()
        with db.tx_write(conn):
            _util.update(conn, "story_comment", id, fields)
    return get_comment(conn, id)


def delete_comment(conn: sqlite3.Connection, id) -> None:
    """Delete a comment.

    Args:
        conn: sqlite3.Connection from db.connect().
        id: int — comment id.
    """
    with db.tx_write(conn):
        _util.delete(conn, "story_comment", id, resource="comment")
