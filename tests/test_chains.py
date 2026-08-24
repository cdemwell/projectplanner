"""Tests for the declarative TUI entity-chain model (``tui/chains.py``).

Story 70: the model + resolver only. No layout / interaction is exercised here.
"""

from __future__ import annotations

from backend import (
    epics,
    groups,
    iterations,
    labels,
    members,
    milestones,
    projects,
    stories,
    tasks,
    workflows,
)
from tui import chains


def test_valid_children_of_project(conn):
    assert chains.valid_children("project") == ["epic", "story"]


def test_valid_children_of_unknown_parent(conn):
    assert chains.valid_children("does_not_exist") == []


def test_project_epic_story_chain(conn):
    """project -> epic -> story resolves at each hop."""
    project = projects.create_project(conn, "backend")
    epic = epics.create_epic(conn, "Auth", project_id=project.id)
    story = stories.create_story(conn, "Login", project_id=project.id, epic_id=epic.id)

    epics_under = chains.resolve_children(conn, "project", project.id, "epic")
    assert [e.id for e in epics_under] == [epic.id]

    stories_under_epic = chains.resolve_children(conn, "epic", epic.id, "story")
    assert [s.id for s in stories_under_epic] == [story.id]

    stories_under_project = chains.resolve_children(conn, "project", project.id, "story")
    assert [s.id for s in stories_under_project] == [story.id]


def test_milestone_epic_chain(conn):
    milestone = milestones.create_milestone(conn, "M1")
    epic = epics.create_epic(conn, "E", milestone_id=milestone.id)
    epics_under = chains.resolve_children(conn, "milestone", milestone.id, "epic")
    assert [e.id for e in epics_under] == [epic.id]


def test_iteration_story_chain(conn):
    iteration = iterations.create_iteration(conn, "I1")
    story = stories.create_story(conn, "S", iteration_id=iteration.id)
    rows = chains.resolve_children(conn, "iteration", iteration.id, "story")
    assert [s.id for s in rows] == [story.id]


def test_group_story_chain(conn):
    group = groups.create_group(conn, "team-a")
    story = stories.create_story(conn, "S", group_id=group.id)
    rows = chains.resolve_children(conn, "group", group.id, "story")
    assert [s.id for s in rows] == [story.id]


def test_label_story_chain(conn):
    label = labels.create_label(conn, "bug")
    story = stories.create_story(conn, "S", label_ids=[label.id])
    rows = chains.resolve_children(conn, "label", label.id, "story")
    assert [s.id for s in rows] == [story.id]


def test_member_story_chain(conn):
    member = members.list_members(conn)[0]  # the seeded local user
    story = stories.create_story(conn, "S", owner_ids=[member.id])
    rows = chains.resolve_children(conn, "member", member.id, "story")
    assert [s.id for s in rows] == [story.id]


def test_workflow_state_chain(conn):
    workflow = workflows.list_workflows(conn)[0]  # the seeded default workflow
    states = chains.resolve_children(conn, "workflow", workflow.id, "workflow_state")
    assert states, "seeded workflow should have states"
    assert all(s.workflow_id == workflow.id for s in states)


def test_story_task_chain(conn):
    story = stories.create_story(conn, "S")
    tasks.create_task(conn, story.id, "write tests")
    rows = chains.resolve_children(conn, "story", story.id, "task")
    assert len(rows) == 1


def test_invalid_chain_returns_empty(conn):
    # Not a valid (parent, child) pair -> the browser has nothing to show.
    assert chains.resolve_children(conn, "label", 1, "epic") == []
    assert chains.resolve_children(conn, "story", 1, "epic") == []
    assert chains.resolve_children(conn, "member", 1, "epic") == []
