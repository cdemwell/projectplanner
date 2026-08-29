"""``completed_at`` must be stamped consistently by every path that can put
an entity into a done state — create, update, and move.

These tests target the invariant, not the individual bugs (84/85/86): each
entity exposes several write paths, and all of them must derive the same
``completed_at`` value. Enum-state entities (epic, milestone) stamp on
``state == 'done'``; story stamps on the linked ``workflow_state`` type;
tasks stamp/clear on ``complete``.
"""

from __future__ import annotations

import pytest

from backend import comments, epics, errors, milestones, stories, tasks


def _done_state_id(conn) -> int:
    return conn.execute(
        "SELECT id FROM workflow_state WHERE type='done' ORDER BY id LIMIT 1"
    ).fetchone()[0]


# Enum-state entities: state is a plain string ('planned'/'in_progress'/'done').
_ENUM_ENTITIES = {
    "epic": (epics.create_epic, epics.update_epic),
    "milestone": (milestones.create_milestone, milestones.update_milestone),
}


class TestEnumStateEntities:
    """Epic/milestone: create-in-done must match update-to-done."""

    @pytest.mark.parametrize("kind", ["epic", "milestone"])
    def test_create_in_done_state_stamps_completed_at(self, conn, kind):
        create, _ = _ENUM_ENTITIES[kind]
        made = create(conn, f"born-done {kind}", state="done")
        assert made.completed_at is not None

    @pytest.mark.parametrize("kind", ["epic", "milestone"])
    def test_create_in_done_matches_update_to_done(self, conn, kind):
        create, update = _ENUM_ENTITIES[kind]
        moved = update(conn, create(conn, f"s {kind}").id, state="done")
        made = create(conn, f"born-done {kind}", state="done")
        assert made.completed_at is not None
        assert moved.completed_at is not None

    @pytest.mark.parametrize("kind", ["epic", "milestone"])
    def test_create_in_non_done_state_leaves_null(self, conn, kind):
        create, _ = _ENUM_ENTITIES[kind]
        for state in ("planned", "in_progress"):
            obj = create(conn, f"{kind}-{state}", state=state)
            assert obj.completed_at is None, f"state={state} must not stamp"

    @pytest.mark.parametrize("kind", ["epic", "milestone"])
    def test_update_out_of_done_clears_completed_at(self, conn, kind):
        create, update = _ENUM_ENTITIES[kind]
        obj = create(conn, f"{kind} out", state="done")
        assert obj.completed_at is not None
        obj = update(conn, obj.id, state="in_progress")
        assert obj.completed_at is None


class TestStoryCreateVsMove:
    """Story: create-in-done, update-to-done and move-to-done must agree."""

    def _assert_all_stamped(self, story):
        assert story.completed_at is not None

    def test_create_in_done_state_stamps_completed_at(self, conn):
        s = stories.create_story(conn, "born done",
                                 workflow_state_id=_done_state_id(conn))
        self._assert_all_stamped(s)

    def test_create_matches_move_invariant(self, conn):
        made = stories.create_story(conn, "born done",
                                    workflow_state_id=_done_state_id(conn))
        moved = stories.move_story_state(conn, stories.create_story(conn, "m").id,
                                         _done_state_id(conn))
        assert made.completed_at is not None
        assert moved.completed_at is not None

    def test_update_state_stamps_like_move(self, conn):
        via_update = stories.update_story(
            conn, stories.create_story(conn, "u").id,
            workflow_state_id=_done_state_id(conn))
        via_move = stories.move_story_state(
            conn, stories.create_story(conn, "m").id,
            _done_state_id(conn))
        assert via_update.completed_at is not None
        assert via_move.completed_at is not None

    def test_update_state_out_of_done_clears(self, conn):
        s = stories.create_story(conn, "x",
                                 workflow_state_id=_done_state_id(conn))
        assert s.completed_at is not None
        plain = conn.execute(
            "SELECT id FROM workflow_state WHERE type='unstarted' LIMIT 1"
        ).fetchone()[0]
        s = stories.update_story(conn, s.id, workflow_state_id=plain)
        assert s.completed_at is None


class TestTaskUpdateComplete:
    """update_task(complete=...) and complete_task must agree."""

    def test_update_task_complete_stamps_like_complete_task(self, conn):
        s = stories.create_story(conn, "x")
        a = tasks.create_task(conn, s.id, "a")
        b = tasks.create_task(conn, s.id, "b")
        a = tasks.update_task(conn, a.id, complete=True)
        b = tasks.complete_task(conn, b.id)
        assert a.complete == 1 and a.completed_at is not None
        assert b.complete == 1 and b.completed_at is not None

    def test_update_task_uncomplete_clears_stamp(self, conn):
        s = stories.create_story(conn, "x")
        t = tasks.complete_task(conn, tasks.create_task(conn, s.id, "t").id)
        assert t.completed_at is not None
        t = tasks.update_task(conn, t.id, complete=False)
        assert t.complete == 0 and t.completed_at is None


class TestCommentThreading:
    """A reply must belong to the same story as its parent comment."""

    def test_reply_rejects_cross_story_parent(self, conn):
        s1 = stories.create_story(conn, "one")
        s2 = stories.create_story(conn, "two")
        parent = comments.create_comment(conn, s1.id, "root on s1")
        with pytest.raises(errors.ValidationError):
            comments.create_comment(conn, s2.id, "reply on s2", parent_id=parent.id)

    def test_reply_allows_same_story_parent(self, conn):
        s = stories.create_story(conn, "one")
        parent = comments.create_comment(conn, s.id, "root")
        reply = comments.create_comment(conn, s.id, "reply", parent_id=parent.id)
        assert reply.parent_id == parent.id
        assert reply.story_id == s.id
