"""Tests for backend/workflows.py: workflow and workflow-state operations."""

from __future__ import annotations

from backend import workflows


def _default_workflow(conn):
    return conn.execute("SELECT id FROM workflow ORDER BY id LIMIT 1").fetchone()[0]


def test_create_workflow_state_with_description(conn):
    wf_id = _default_workflow(conn)
    s = workflows.create_workflow_state(conn, wf_id, "Awaiting QA", "started",
                                        description="Awaiting QA sign-off")
    assert s.description == "Awaiting QA sign-off"
    # persisted, not just on the returned object.
    row = conn.execute("SELECT description FROM workflow_state WHERE id = ?", (s.id,)).fetchone()
    assert row["description"] == "Awaiting QA sign-off"


def test_create_workflow_state_defaults_description_to_empty(conn):
    wf_id = _default_workflow(conn)
    s = workflows.create_workflow_state(conn, wf_id, "Plain", "unstarted")
    assert s.description == ""


def test_update_workflow_state_description(conn):
    wf_id = _default_workflow(conn)
    s = workflows.create_workflow_state(conn, wf_id, "Doing", "started")
    updated = workflows.update_workflow_state(conn, s.id, description="In progress")
    assert updated.description == "In progress"
