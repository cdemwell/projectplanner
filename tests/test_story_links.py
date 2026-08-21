"""Tests for backend/story_links.py."""

from __future__ import annotations

import pytest

from backend import story_links, stories, errors


def test_create_and_list(conn):
    a = stories.create_story(conn, "a")
    b = stories.create_story(conn, "b")
    ln = story_links.create_link(conn, a.id, "blocks", b.id)
    assert ln.verb == "blocks"
    # list involving a (as subject) includes it
    assert len(story_links.list_links(conn, a.id)) == 1
    # list involving b (as object) also includes it
    assert len(story_links.list_links(conn, b.id)) == 1
    assert len(story_links.list_links(conn)) == 1


def test_invalid_verb(conn):
    a = stories.create_story(conn, "a")
    b = stories.create_story(conn, "b")
    with pytest.raises(errors.ValidationError):
        story_links.create_link(conn, a.id, "bogus", b.id)


def test_self_link_rejected(conn):
    a = stories.create_story(conn, "a")
    with pytest.raises(errors.ValidationError):
        story_links.create_link(conn, a.id, "relates_to", a.id)


def test_duplicate_link_conflict(conn):
    a = stories.create_story(conn, "a")
    b = stories.create_story(conn, "b")
    story_links.create_link(conn, a.id, "blocks", b.id)
    with pytest.raises(errors.Conflict):
        story_links.create_link(conn, a.id, "blocks", b.id)


def test_link_to_nonexistent_story(conn):
    a = stories.create_story(conn, "a")
    with pytest.raises(errors.NotFound):
        story_links.create_link(conn, a.id, "blocks", 9999)


def test_link_cascade_on_either_story_delete(conn):
    a = stories.create_story(conn, "a")
    b = stories.create_story(conn, "b")
    story_links.create_link(conn, a.id, "blocks", b.id)
    stories.delete_story(conn, b.id)  # delete the object story
    assert story_links.list_links(conn, a.id) == []