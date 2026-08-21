"""Stories — the central unit of work. A story lives in a workflow state, may
belong to a project/epic/iteration/group, has owners and labels, and carries
tasks and comments. Moving a story to a ``done`` state stamps ``completed_at``
(and clears it when moved back out)."""

from __future__ import annotations

import sqlite3

from . import _util, db, errors, workflows
from .models import Story, StoryDetail, Member, Label, Task, WorkflowState

STORY_TYPES = ("bug", "feature", "chore")

# Columns a user may edit directly via update_story (completed_at is managed).
EDITABLE = {
    "name", "description", "story_type", "workflow_state_id", "epic_id",
    "iteration_id", "project_id", "group_id", "requested_by_id", "deadline",
    "position",
}


def list_stories(conn: sqlite3.Connection, *, project_id=None, epic_id=None,
                 iteration_id=None, state_type=None, group_id=None,
                 owner_id=None, label_id=None, q: str | None = None,
                 include_completed: bool = True) -> list[Story]:
    """List stories with optional filters. ``state_type`` is a workflow-state
    type ('unstarted'/'started'/'done'); ``q`` is a LIKE over name+description."""
    where: list[str] = []
    params: list = []
    if project_id is not None:
        where.append("s.project_id = ?"); params.append(project_id)
    if epic_id is not None:
        where.append("s.epic_id = ?"); params.append(epic_id)
    if iteration_id is not None:
        where.append("s.iteration_id = ?"); params.append(iteration_id)
    if group_id is not None:
        where.append("s.group_id = ?"); params.append(group_id)
    if state_type is not None:
        where.append("EXISTS (SELECT 1 FROM workflow_state ws "
                     "WHERE ws.id = s.workflow_state_id AND ws.type = ?)")
        params.append(state_type)
    if owner_id is not None:
        where.append("EXISTS (SELECT 1 FROM story_owner so "
                     "WHERE so.story_id = s.id AND so.member_id = ?)")
        params.append(owner_id)
    if label_id is not None:
        where.append("EXISTS (SELECT 1 FROM story_label sl "
                     "WHERE sl.story_id = s.id AND sl.label_id = ?)")
        params.append(label_id)
    if q is not None:
        where.append("(s.name LIKE ? OR s.description LIKE ?)")
        params += [f"%{q}%", f"%{q}%"]
    if not include_completed:
        where.append("s.completed_at IS NULL")
    sql = "SELECT s.* FROM story s"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY s.position, s.id"
    return [Story.from_row(r) for r in conn.execute(sql, params)]


def get_story(conn: sqlite3.Connection, id) -> Story:
    return _util.get(conn, Story, "story", id, resource="story")


def _load_detail(conn: sqlite3.Connection, story: Story) -> StoryDetail:
    owners = [Member.from_row(r) for r in conn.execute(
        "SELECT m.* FROM member m JOIN story_owner so ON so.member_id = m.id "
        "WHERE so.story_id = ? ORDER BY m.name", (story.id,))]
    labels = [Label.from_row(r) for r in conn.execute(
        "SELECT l.* FROM label l JOIN story_label sl ON sl.label_id = l.id "
        "WHERE sl.story_id = ? ORDER BY l.name", (story.id,))]
    tasks = [Task.from_row(r) for r in conn.execute(
        "SELECT * FROM task WHERE story_id = ? ORDER BY position, id", (story.id,))]
    ws: WorkflowState | None = None
    if story.workflow_state_id is not None:
        row = conn.execute("SELECT * FROM workflow_state WHERE id = ?",
                           (story.workflow_state_id,)).fetchone()
        ws = WorkflowState.from_row(row) if row else None
    return StoryDetail(story=story, owners=owners, labels=labels, tasks=tasks,
                       workflow_state=ws)


def get_story_detail(conn: sqlite3.Connection, id) -> StoryDetail:
    return _load_detail(conn, get_story(conn, id))


def _next_position(conn: sqlite3.Connection, project_id) -> float:
    """Append position: one past the current max within the same project
    (or globally if no project), so new stories sort to the end."""
    if project_id is not None:
        row = conn.execute(
            "SELECT COALESCE(MAX(position), 0) FROM story WHERE project_id = ?",
            (project_id,)).fetchone()
    else:
        row = conn.execute("SELECT COALESCE(MAX(position), 0) FROM story").fetchone()
    return float(row[0]) + 1.0


def create_story(conn: sqlite3.Connection, name: str, *, description: str = "",
                 story_type: str = "feature", workflow_state_id=None, epic_id=None,
                 iteration_id=None, project_id=None, group_id=None,
                 requested_by_id=None, deadline: str | None = None,
                 owner_ids: list | None = None, label_ids: list | None = None,
                 position: float | None = None) -> Story:
    if story_type not in STORY_TYPES:
        raise errors.ValidationError(f"unknown story_type {story_type!r}")
    ts = db.now()
    if position is None:
        position = _next_position(conn, project_id)
    # If no explicit state, fall back to the default workflow's default state.
    if workflow_state_id is None:
        wf = conn.execute("SELECT default_state_id FROM workflow ORDER BY id LIMIT 1").fetchone()
        if wf is not None:
            workflow_state_id = wf["default_state_id"]
    with db.tx_write(conn):
        new_id = _util.insert(conn, "story", {
            "name": name, "description": description, "story_type": story_type,
            "workflow_state_id": workflow_state_id, "epic_id": epic_id,
            "iteration_id": iteration_id, "project_id": project_id, "group_id": group_id,
            "requested_by_id": requested_by_id, "deadline": deadline,
            "position": position, "created_at": ts, "updated_at": ts,
        })
        for mid in owner_ids or []:
            _util.insert(conn, "story_owner", {"story_id": new_id, "member_id": mid})
        for lid in label_ids or []:
            _util.insert(conn, "story_label", {"story_id": new_id, "label_id": lid})
    return get_story(conn, new_id)


def update_story(conn: sqlite3.Connection, id, **fields) -> Story:
    """Update editable fields. Passing a nullable FK as None clears it."""
    get_story(conn, id)  # raises NotFound if absent
    fields = {k: v for k, v in fields.items() if k in EDITABLE}
    if fields:
        fields["updated_at"] = db.now()
        with db.tx_write(conn):
            _util.update(conn, "story", id, fields)
    return get_story(conn, id)


def move_story_state(conn: sqlite3.Connection, id, new_state_id) -> Story:
    """Move a story to a workflow state. Stamps ``completed_at`` when entering a
    ``done`` state and clears it when leaving."""
    state = workflows.get_workflow_state(conn, new_state_id)
    with db.tx_write(conn):
        fields = {"workflow_state_id": new_state_id, "updated_at": db.now()}
        fields["completed_at"] = db.now() if state.type == "done" else None
        if not _util.update(conn, "story", id, fields):
            raise errors.NotFound("story", id)
    return get_story(conn, id)


def delete_story(conn: sqlite3.Connection, id) -> None:
    with db.tx_write(conn):
        _util.delete(conn, "story", id, resource="story")


# --- owners -------------------------------------------------------------- #
def list_owners(conn: sqlite3.Connection, story_id) -> list[Member]:
    get_story(conn, story_id)
    rows = conn.execute(
        "SELECT m.* FROM member m JOIN story_owner so ON so.member_id = m.id "
        "WHERE so.story_id = ? ORDER BY m.name", (story_id,))
    return [Member.from_row(r) for r in rows]


def assign_owner(conn: sqlite3.Connection, story_id, member_id) -> None:
    get_story(conn, story_id)
    # Validate member exists -> raises NotFound
    from . import members
    members.get_member(conn, member_id)
    with db.tx_write(conn):
        try:
            conn.execute("INSERT INTO story_owner(story_id, member_id) VALUES (?, ?)",
                         (story_id, member_id))
        except sqlite3.IntegrityError as e:
            # Duplicate (already assigned) -> not an error worth failing on.
            if "UNIQUE" in str(e).upper():
                return
            raise errors.ValidationError(str(e))


def remove_owner(conn: sqlite3.Connection, story_id, member_id) -> None:
    get_story(conn, story_id)
    with db.tx_write(conn):
        conn.execute("DELETE FROM story_owner WHERE story_id = ? AND member_id = ?",
                     (story_id, member_id))


# --- labels -------------------------------------------------------------- #
def list_story_labels(conn: sqlite3.Connection, story_id) -> list[Label]:
    get_story(conn, story_id)
    rows = conn.execute(
        "SELECT l.* FROM label l JOIN story_label sl ON sl.label_id = l.id "
        "WHERE sl.story_id = ? ORDER BY l.name", (story_id,))
    return [Label.from_row(r) for r in rows]


def add_label(conn: sqlite3.Connection, story_id, label_id) -> None:
    get_story(conn, story_id)
    from . import labels
    labels.get_label(conn, label_id)
    with db.tx_write(conn):
        try:
            conn.execute("INSERT INTO story_label(story_id, label_id) VALUES (?, ?)",
                         (story_id, label_id))
        except sqlite3.IntegrityError as e:
            if "UNIQUE" in str(e).upper():
                return
            raise errors.ValidationError(str(e))


def remove_label(conn: sqlite3.Connection, story_id, label_id) -> None:
    get_story(conn, story_id)
    with db.tx_write(conn):
        conn.execute("DELETE FROM story_label WHERE story_id = ? AND label_id = ?",
                     (story_id, label_id))