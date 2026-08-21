"""Tests for backend/tasks.py."""

from __future__ import annotations

import pytest

from backend import errors, stories, tasks


def test_create_and_list_tasks(conn):
    s = stories.create_story(conn, "x")
    t1 = tasks.create_task(conn, s.id, "write tests")
    t2 = tasks.create_task(conn, s.id, "fix bug")
    assert [t.description for t in tasks.list_tasks(conn, s.id)] == ["write tests", "fix bug"]
    assert t1.position < t2.position


def test_complete_task_stamps_completed_at(conn):
    s = stories.create_story(conn, "x")
    t = tasks.create_task(conn, s.id, "t")
    assert t.complete == 0 and t.completed_at is None
    t = tasks.complete_task(conn, t.id)
    assert t.complete == 1 and t.completed_at is not None
    # toggle back
    t = tasks.complete_task(conn, t.id, complete=False)
    assert t.complete == 0 and t.completed_at is None


def test_update_task(conn):
    s = stories.create_story(conn, "x")
    t = tasks.create_task(conn, s.id, "t")
    t = tasks.update_task(conn, t.id, description="edited")
    assert t.description == "edited"


def test_task_not_found(conn):
    with pytest.raises(errors.NotFound):
        tasks.get_task(conn, 9999)


def test_list_tasks_story_not_found(conn):
    with pytest.raises(errors.NotFound):
        tasks.list_tasks(conn, 9999)


def test_task_cascade_on_story_delete(conn):
    s = stories.create_story(conn, "x")
    t = tasks.create_task(conn, s.id, "t")
    stories.delete_story(conn, s.id)
    with pytest.raises(errors.NotFound):
        tasks.get_task(conn, t.id)
