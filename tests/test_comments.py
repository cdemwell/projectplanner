"""Tests for backend/comments.py."""

from __future__ import annotations

import pytest

from backend import comments, errors, stories


def test_create_list_update_delete(conn):
    s = stories.create_story(conn, "x")
    c = comments.create_comment(conn, s.id, "looks bad", author_id=1)
    assert c.author_id == 1
    assert [x.text for x in comments.list_comments(conn, s.id)] == ["looks bad"]
    c = comments.update_comment(conn, c.id, text="looks really bad")
    assert comments.get_comment(conn, c.id).text == "looks really bad"
    assert c.updated_at >= c.created_at
    comments.delete_comment(conn, c.id)
    with pytest.raises(errors.NotFound):
        comments.get_comment(conn, c.id)


def test_threaded_reply(conn):
    s = stories.create_story(conn, "x")
    parent = comments.create_comment(conn, s.id, "root")
    reply = comments.create_comment(conn, s.id, "child", parent_id=parent.id)
    assert reply.parent_id == parent.id


def test_reply_to_nonexistent_parent(conn):
    s = stories.create_story(conn, "x")
    with pytest.raises(errors.NotFound):
        comments.create_comment(conn, s.id, "child", parent_id=9999)


def test_comment_cascade_on_story_delete(conn):
    s = stories.create_story(conn, "x")
    c = comments.create_comment(conn, s.id, "c")
    stories.delete_story(conn, s.id)
    with pytest.raises(errors.NotFound):
        comments.get_comment(conn, c.id)
