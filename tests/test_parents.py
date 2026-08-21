"""Tests for the parent/container backend modules."""

from __future__ import annotations

import pytest

from backend import (
    db,
    epics,
    errors,
    groups,
    iterations,
    labels,
    members,
    milestones,
    projects,
    stories,
    workflows,
)


# --- epics ---------------------------------------------------------------- #
def test_epic_completed_at_automation(conn):
    e = epics.create_epic(conn, "Auth")
    assert e.completed_at is None
    e = epics.update_epic(conn, e.id, state="done")
    assert e.completed_at is not None
    e = epics.update_epic(conn, e.id, state="in_progress")
    assert e.completed_at is None


def test_epic_invalid_state(conn):
    e = epics.create_epic(conn, "Auth")
    with pytest.raises(errors.ValidationError):
        epics.update_epic(conn, e.id, state="bogus")


def test_epic_list_filters(conn):
    p = projects.create_project(conn, "backend")
    m = milestones.create_milestone(conn, "M1")
    e1 = epics.create_epic(conn, "Auth", project_id=p.id, milestone_id=m.id)
    e2 = epics.create_epic(conn, "Other")
    assert {e.id for e in epics.list_epics(conn, project_id=p.id)} == {e1.id}
    assert {e.id for e in epics.list_epics(conn, milestone_id=m.id)} == {e1.id}
    assert {e.id for e in epics.list_epics(conn)} == {e1.id, e2.id}


def test_list_epic_stories(conn):
    e = epics.create_epic(conn, "Auth")
    s = stories.create_story(conn, "x", epic_id=e.id)
    assert {x.id for x in epics.list_epic_stories(conn, e.id)} == {s.id}


# --- milestones ----------------------------------------------------------- #
def test_milestone_completed_at_automation(conn):
    m = milestones.create_milestone(conn, "M1")
    m = milestones.update_milestone(conn, m.id, state="done")
    assert m.completed_at is not None
    m = milestones.update_milestone(conn, m.id, state="planned")
    assert m.completed_at is None


def test_milestone_list_state_filter(conn):
    m1 = milestones.create_milestone(conn, "M1", state="planned")
    m2 = milestones.create_milestone(conn, "M2", state="in_progress")
    assert {m.id for m in milestones.list_milestones(conn, state="planned")} == {m1.id}
    assert {m.id for m in milestones.list_milestones(conn, state="in_progress")} == {m2.id}


def test_milestone_epics(conn):
    m = milestones.create_milestone(conn, "M1")
    e = epics.create_epic(conn, "Auth", milestone_id=m.id)
    assert {x.id for x in milestones.list_milestone_epics(conn, m.id)} == {e.id}


# --- iterations ----------------------------------------------------------- #
def test_iteration_create_and_update(conn):
    it = iterations.create_iteration(conn, "Sprint 1", status="active",
                                     start_date="2026-09-01", end_date="2026-09-14")
    assert it.status == "active"
    it = iterations.update_iteration(conn, it.id, status="done")
    assert it.status == "done"


def test_iteration_invalid_status(conn):
    it = iterations.create_iteration(conn, "Sprint 1")
    with pytest.raises(errors.ValidationError):
        iterations.update_iteration(conn, it.id, status="bogus")


def test_iteration_stories(conn):
    it = iterations.create_iteration(conn, "Sprint 1")
    s = stories.create_story(conn, "x", iteration_id=it.id)
    assert {x.id for x in iterations.list_iteration_stories(conn, it.id)} == {s.id}


# --- projects ------------------------------------------------------------- #
def test_project_archive_soft_delete(conn):
    p = projects.create_project(conn, "backend")
    assert [x.id for x in projects.list_projects(conn)] == [p.id]
    projects.archive_project(conn, p.id)
    assert projects.list_projects(conn) == []           # hidden by default
    assert projects.get_project(conn, p.id).archived == 1
    archived = projects.list_projects(conn, include_archived=True)
    assert [x.id for x in archived] == [p.id]
    assert archived[0].archived == 1


def test_project_stories(conn):
    p = projects.create_project(conn, "backend")
    s = stories.create_story(conn, "x", project_id=p.id)
    assert {x.id for x in projects.list_project_stories(conn, p.id)} == {s.id}


# --- groups --------------------------------------------------------------- #
def test_group_archive(conn):
    g = groups.create_group(conn, "platform")
    assert len(groups.list_groups(conn)) == 1
    groups.archive_group(conn, g.id)
    assert groups.list_groups(conn) == []
    archived = groups.list_groups(conn, include_archived=True)
    assert [x.id for x in archived] == [g.id]
    assert archived[0].archived == 1


def test_group_stories(conn):
    g = groups.create_group(conn, "platform")
    s = stories.create_story(conn, "x", group_id=g.id)
    assert {x.id for x in groups.list_group_stories(conn, g.id)} == {s.id}


# --- labels --------------------------------------------------------------- #
def test_label_crud(conn):
    lbl = labels.create_label(conn, "auth", color="#f00", description="d")
    assert labels.get_label(conn, lbl.id).name == "auth"
    lbl = labels.update_label(conn, lbl.id, color="#0f0")
    assert lbl.color == "#0f0"
    labels.delete_label(conn, lbl.id)
    with pytest.raises(errors.NotFound):
        labels.get_label(conn, lbl.id)


# --- members -------------------------------------------------------------- #
def test_member_mention_derived(conn):
    m = members.create_member(conn, "Chris Demwell")
    assert m.mention_name == "chris_demwell"
    # explicit override
    m2 = members.create_member(conn, "Other", mention_name="oth")
    assert m2.mention_name == "oth"


def test_member_duplicate_mention_conflict(conn):
    members.create_member(conn, "Chris", mention_name="chris")
    with pytest.raises(errors.Conflict):
        members.create_member(conn, "Another", mention_name="chris")


# --- workflows ------------------------------------------------------------ #
def test_workflow_create_with_states(conn):
    wf = workflows.create_workflow(conn, "Kanban", states=[
        {"name": "Todo", "type": "unstarted"},
        {"name": "Doing", "type": "started"},
        {"name": "Done", "type": "done"},
    ])
    states = workflows.list_workflow_states(conn, wf.id)
    assert [s.name for s in states] == ["Todo", "Doing", "Done"]
    # default state is the first 'started' one.
    assert wf.default_state_id == [s for s in states if s.type == "started"][0].id


def test_workflow_add_state_auto_position(conn):
    wf = workflows.create_workflow(conn, "Kanban")
    s1 = workflows.create_workflow_state(conn, wf.id, "Todo", "unstarted")
    s2 = workflows.create_workflow_state(conn, wf.id, "Doing", "started")
    assert s1.position < s2.position


def test_workflow_state_invalid_type(conn):
    wf = workflows.create_workflow(conn, "Kanban")
    with pytest.raises(errors.ValidationError):
        workflows.create_workflow_state(conn, wf.id, "Weird", "bogus")


def test_workflow_delete_cascades_states(conn):
    wf = workflows.create_workflow(conn, "Kanban")
    workflows.create_workflow_state(conn, wf.id, "Todo", "unstarted")
    workflows.delete_workflow(conn, wf.id)
    assert conn.execute("SELECT COUNT(*) FROM workflow_state WHERE workflow_id=?",
                        (wf.id,)).fetchone()[0] == 0
