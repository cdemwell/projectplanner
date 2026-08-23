"""Property-based tests for the planner state machine and invariants.
Uses Hypothesis to generate random sequences of operations and verify correctness.
"""

from __future__ import annotations

import contextlib

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from backend import (
    db,
    errors,
    projects,
    stories,
    story_links,
    workflows,
)


def get_fresh_conn():
    """Return a fresh, seeded in-memory database connection as a context manager."""
    @contextlib.contextmanager
    def _conn():
        c = db.connect(":memory:")
        try:
            yield c
        finally:
            c.close()
    return _conn()


@settings(max_examples=50)
@given(st.text(min_size=1))
def test_prop_completed_at_automation(name):
    """Verify that move_story_state handles completed_at based on state type."""
    with get_fresh_conn() as conn:
        # Setup: Need at least one 'done' state and one 'non-done' state.
        # The seed already provides Unstarted, Started, and Done.
        all_states = workflows.list_workflow_states(conn, 1)
        done_states = [s.id for s in all_states if s.type == "done"]
        non_done_states = [s.id for s in all_states if s.type != "done"]

        assert done_states, "Seed should provide at least one 'done' state"
        assert non_done_states, "Seed should provide at least one 'non-done' state"

        story = stories.create_story(conn, name)

        # Move to done
        done_state_id = done_states[0]
        stories.move_story_state(conn, story.id, done_state_id)
        story = stories.get_story(conn, story.id)
        assert story.completed_at is not None

        # Move to non-done
        non_done_state_id = non_done_states[0]
        stories.move_story_state(conn, story.id, non_done_state_id)
        story = stories.get_story(conn, story.id)
        assert story.completed_at is None


@settings(max_examples=50)
@given(st.integers(min_value=1, max_value=20))
def test_prop_position_uniqueness(num_stories):
    """Verify that create_story always assigns unique positions within a project."""
    with get_fresh_conn() as conn:
        project = projects.create_project(conn, "Test Project")
        project_id = project.id

        for i in range(num_stories):
            stories.create_story(conn, f"Story {i}", project_id=project_id)

        # Check that all positions are unique within the project.
        rows = conn.execute("SELECT position FROM story WHERE project_id = ?", (project_id,)).fetchall()
        positions = [r["position"] for r in rows]
        assert len(positions) == len(set(positions)), f"Positions are not unique: {positions}"


@settings(max_examples=50)
@given(
    st.integers(min_value=1, max_value=10),
    st.integers(min_value=1, max_value=10),
    st.sampled_from(story_links.VERBS),
)
def test_prop_story_link_uniqueness(s_idx, o_idx, verb):
    """Verify that creating duplicate links (subject, verb, object) always fails with Conflict."""
    with get_fresh_conn() as conn:
        # Setup: create stories to link.
        story_ids = []
        for i in range(10):
            s = stories.create_story(conn, f"Story {i}")
            story_ids.append(s.id)

        # Ensure subject != object
        if s_idx == o_idx:
            return

        s_id = story_ids[s_idx % 10]
        o_id = story_ids[o_idx % 10]

        if s_id == o_id:
            return

        # Create link first time
        story_links.create_link(conn, s_id, verb, o_id)

        # Create link second time - should raise Conflict
        with pytest.raises(errors.Conflict):
            story_links.create_link(conn, s_id, verb, o_id)


@settings(max_examples=50)
@given(st.text(min_size=1))
def test_prop_delete_cascade(name):
    """Verify that deleting a story removes all child entities (tasks, comments, links, etc.)."""
    with get_fresh_conn() as conn:
        story = stories.create_story(conn, name)
        story_id = story.id

        with db.tx_write(conn):
            # Tasks
            conn.execute("INSERT INTO task(story_id, description, created_at) VALUES (?, ?, ?)",
                         (story_id, "Task 1", db.now()))

            # Comments
            conn.execute("INSERT INTO story_comment(story_id, text, created_at, updated_at) VALUES (?, ?, ?, ?)",
                         (story_id, "Comment 1", db.now(), db.now()))

            # Owners
            # Use the seeded member (ID 1).
            conn.execute("INSERT INTO story_owner(story_id, member_id) VALUES (?, ?)", (story_id, 1))

            # Labels
            # Create a label first.
            conn.execute("INSERT INTO label(name, created_at) VALUES (?, ?)", ("Label 1", db.now()))
            label_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.execute("INSERT INTO story_label(story_id, label_id) VALUES (?, ?)", (story_id, label_id))

        # Links
        # Need another story to link to.
        other = stories.create_story(conn, "Other Story")
        story_links.create_link(conn, story_id, "blocks", other.id)
        story_links.create_link(conn, other.id, "blocks_by", story_id)

        # Delete the story
        stories.delete_story(conn, story_id)

        # Verify cascade
        assert conn.execute("SELECT 1 FROM story WHERE id = ?", (story_id,)).fetchone() is None
        assert conn.execute("SELECT 1 FROM task WHERE story_id = ?", (story_id,)).fetchone() is None
        assert conn.execute("SELECT 1 FROM story_comment WHERE story_id = ?", (story_id,)).fetchone() is None
        assert conn.execute(
            "SELECT 1 FROM story_link WHERE subject_story_id = ? OR object_story_id = ?",
            (story_id, story_id),
        ).fetchone() is None
        assert conn.execute("SELECT 1 FROM story_owner WHERE story_id = ?", (story_id,)).fetchone() is None
        assert conn.execute("SELECT 1 FROM story_label WHERE story_id = ?", (story_id,)).fetchone() is None
