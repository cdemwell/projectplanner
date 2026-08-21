"""Story links — directed relationships between two stories.

A link has a ``verb`` drawn from the Shortcut set: blocks / blocks_by /
duplicates / duplicated_by / relates_to. A story links to a distinct other
story; the (subject, verb, object) triple is unique.
"""

from __future__ import annotations

import sqlite3

from . import _util, db, errors, stories
from .models import StoryLink

# _util re-raises IntegrityError as our error types, but the story_link insert
# needs the raw UNIQUE violation to map to a Conflict with a helpful message.

VERBS = ("blocks", "blocks_by", "duplicates", "duplicated_by", "relates_to")


def list_links(conn: sqlite3.Connection, story_id: int | None = None) -> list[StoryLink]:
    """List all links, or those involving ``story_id`` as subject or object."""
    if story_id is None:
        rows = conn.execute("SELECT * FROM story_link ORDER BY id")
    else:
        rows = conn.execute(
            "SELECT * FROM story_link WHERE subject_story_id = ? OR object_story_id = ? "
            "ORDER BY id", (story_id, story_id))
    return [StoryLink.from_row(r) for r in rows]


def get_link(conn: sqlite3.Connection, id) -> StoryLink:
    return _util.get(conn, StoryLink, "story_link", id, resource="story_link")


def create_link(conn: sqlite3.Connection, subject_story_id: int, verb: str,
                object_story_id: int) -> StoryLink:
    if verb not in VERBS:
        raise errors.ValidationError(f"unknown verb {verb!r}")
    if subject_story_id == object_story_id:
        raise errors.ValidationError("a story cannot link to itself")
    stories.get_story(conn, subject_story_id)
    stories.get_story(conn, object_story_id)
    with db.tx_write(conn):
        try:
            cur = conn.execute(
                "INSERT INTO story_link(subject_story_id, verb, object_story_id, created_at) "
                "VALUES (?, ?, ?, ?)",
                (subject_story_id, verb, object_story_id, db.now()))
        except sqlite3.IntegrityError as e:  # UNIQUE(subject, verb, object) violation
            raise errors.Conflict(
                f"link already exists: {subject_story_id} --{verb}--> {object_story_id}") from e
    return get_link(conn, cur.lastrowid)


def delete_link(conn: sqlite3.Connection, id) -> None:
    with db.tx_write(conn):
        _util.delete(conn, "story_link", id, resource="story_link")