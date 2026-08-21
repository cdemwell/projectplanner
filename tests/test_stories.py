"""Tests for backend/stories.py."""

from __future__ import annotations

import pytest

from backend import db, errors, stories, projects, labels, members, epics, iterations, groups


def _done_state_id(conn) -> int:
    return conn.execute("SELECT id FROM workflow_state WHERE type='done' ORDER BY id LIMIT 1").fetchone()[0]


def _state_id(conn, stype) -> int:
    return conn.execute("SELECT id FROM workflow_state WHERE type=? ORDER BY id LIMIT 1", (stype,)).fetchone()[0]


def test_create_story_defaults(conn):
    p = projects.create_project(conn, "backend")
    s = stories.create_story(conn, "Fix login", project_id=p.id, story_type="bug")
    assert s.id is not None
    assert s.story_type == "bug"
    # default state is the workflow default (Started).
    assert s.workflow_state_id == _state_id(conn, "started")
    # position defaults to 1.0 (first story in the project).
    assert s.position == 1.0
    assert s.completed_at is None


def test_create_story_position_increments_within_project(conn):
    p = projects.create_project(conn, "backend")
    s1 = stories.create_story(conn, "a", project_id=p.id)
    s2 = stories.create_story(conn, "b", project_id=p.id)
    s3 = stories.create_story(conn, "c")  # no project -> global pool
    assert s1.position == 1.0
    assert s2.position == 2.0
    # global max is 2.0 (from s2) + 1
    assert s3.position == 3.0


def test_create_story_with_owners_and_labels(conn):
    p = projects.create_project(conn, "backend")
    lbl = labels.create_label(conn, "auth")
    s = stories.create_story(conn, "x", project_id=p.id, owner_ids=[1], label_ids=[lbl.id])
    owners = stories.list_owners(conn, s.id)
    assert [o.id for o in owners] == [1]
    labs = stories.list_story_labels(conn, s.id)
    assert [l.id for l in labs] == [lbl.id]


def test_invalid_story_type(conn):
    with pytest.raises(errors.ValidationError):
        stories.create_story(conn, "x", story_type="bogus")


def test_invalid_fk_raises_validation_error(conn):
    with pytest.raises(errors.ValidationError):
        stories.create_story(conn, "x", project_id=9999)


def test_get_story_not_found(conn):
    with pytest.raises(errors.NotFound):
        stories.get_story(conn, 9999)


def test_move_state_stamps_and_clears_completed_at(conn):
    s = stories.create_story(conn, "x")
    sid = s.id
    # move to done -> completed_at set
    s = stories.move_story_state(conn, sid, _done_state_id(conn))
    assert s.completed_at is not None
    # move back to unstarted -> completed_at cleared
    s = stories.move_story_state(conn, sid, _state_id(conn, "unstarted"))
    assert s.completed_at is None


def test_move_state_to_nonexistent_state(conn):
    s = stories.create_story(conn, "x")
    with pytest.raises(errors.NotFound):
        stories.move_story_state(conn, s.id, 9999)


def test_update_story_clears_nullable_fk(conn):
    p = projects.create_project(conn, "backend")
    s = stories.create_story(conn, "x", project_id=p.id)
    assert s.project_id == p.id
    s = stories.update_story(conn, s.id, epic_id=None, description="edited")
    assert s.epic_id is None
    assert s.description == "edited"
    # updated_at should advance.
    assert s.updated_at >= s.created_at


def test_update_story_not_found(conn):
    with pytest.raises(errors.NotFound):
        stories.update_story(conn, 9999, name="nope")


def test_list_filters(conn):
    p = projects.create_project(conn, "backend")
    e = epics.create_epic(conn, "Auth", project_id=p.id)
    it = iterations.create_iteration(conn, "Sprint 1", status="active")
    lbl = labels.create_label(conn, "auth")
    g = groups.create_group(conn, "platform")
    s1 = stories.create_story(conn, "Fix login", project_id=p.id, epic_id=e.id,
                              iteration_id=it.id, group_id=g.id, owner_ids=[1], label_ids=[lbl.id])
    s2 = stories.create_story(conn, "Add 2FA", project_id=p.id)
    # move s1 to done
    stories.move_story_state(conn, s1.id, _done_state_id(conn))

    assert {s.id for s in stories.list_stories(conn, project_id=p.id)} == {s1.id, s2.id}
    assert {s.id for s in stories.list_stories(conn, state_type="done")} == {s1.id}
    assert {s.id for s in stories.list_stories(conn, state_type="unstarted")} == set()
    assert {s.id for s in stories.list_stories(conn, label_id=lbl.id)} == {s1.id}
    assert {s.id for s in stories.list_stories(conn, owner_id=1)} == {s1.id}
    assert {s.id for s in stories.list_stories(conn, epic_id=e.id)} == {s1.id}
    assert {s.id for s in stories.list_stories(conn, iteration_id=it.id)} == {s1.id}
    assert {s.id for s in stories.list_stories(conn, group_id=g.id)} == {s1.id}
    assert {s.id for s in stories.list_stories(conn, q="login")} == {s1.id}
    # include_completed=False hides s1
    assert {s.id for s in stories.list_stories(conn, include_completed=False)} == {s2.id}


def test_get_story_detail_shape(conn):
    p = projects.create_project(conn, "backend")
    lbl = labels.create_label(conn, "auth")
    s = stories.create_story(conn, "Fix login", project_id=p.id, owner_ids=[1], label_ids=[lbl.id])
    from backend import tasks
    tasks.create_task(conn, s.id, "write tests")
    d = stories.get_story_detail(conn, s.id)
    assert d.story.id == s.id
    assert [o.id for o in d.owners] == [1]
    assert [l.id for l in d.labels] == [lbl.id]
    assert len(d.tasks) == 1
    assert d.workflow_state is not None
    assert d.workflow_state.type == "started"


def test_owner_and_label_helpers(conn):
    s = stories.create_story(conn, "x")
    lbl = labels.create_label(conn, "auth")
    stories.assign_owner(conn, s.id, 1)
    stories.add_label(conn, s.id, lbl.id)
    assert 1 in [o.id for o in stories.list_owners(conn, s.id)]
    # duplicate assign is a no-op (not an error)
    stories.assign_owner(conn, s.id, 1)
    assert len(stories.list_owners(conn, s.id)) == 1
    stories.remove_owner(conn, s.id, 1)
    assert stories.list_owners(conn, s.id) == []
    stories.remove_label(conn, s.id, lbl.id)
    assert stories.list_story_labels(conn, s.id) == []


def test_assign_owner_invalid_member(conn):
    s = stories.create_story(conn, "x")
    with pytest.raises(errors.NotFound):
        stories.assign_owner(conn, s.id, 9999)


def test_delete_story_cascades(conn):
    p = projects.create_project(conn, "backend")
    lbl = labels.create_label(conn, "auth")
    s = stories.create_story(conn, "x", project_id=p.id, owner_ids=[1], label_ids=[lbl.id])
    from backend import tasks, comments, story_links
    tasks.create_task(conn, s.id, "t1")
    comments.create_comment(conn, s.id, "c1")
    other = stories.create_story(conn, "y")
    story_links.create_link(conn, s.id, "relates_to", other.id)

    stories.delete_story(conn, s.id)
    # owned children gone (query directly: list_tasks/list_comments validate the
    # story exists and would raise NotFound now that it's deleted).
    assert conn.execute("SELECT COUNT(*) FROM task WHERE story_id=?", (s.id,)).fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM story_comment WHERE story_id=?", (s.id,)).fetchone()[0] == 0
    assert story_links.list_links(conn, s.id) == []
    # junction rows gone
    assert conn.execute("SELECT COUNT(*) FROM story_owner WHERE story_id=?", (s.id,)).fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM story_label WHERE story_id=?", (s.id,)).fetchone()[0] == 0
    # the other story survives
    assert stories.get_story(conn, other.id).id == other.id


def test_delete_parent_sets_story_fk_null(conn):
    p = projects.create_project(conn, "backend")
    e = epics.create_epic(conn, "Auth", project_id=p.id)
    s = stories.create_story(conn, "x", project_id=p.id, epic_id=e.id)
    assert s.epic_id == e.id
    epics.delete_epic(conn, e.id)
    s = stories.get_story(conn, s.id)
    assert s.epic_id is None  # SET NULL, story survives
    assert s.project_id == p.id