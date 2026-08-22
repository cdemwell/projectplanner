"""Headless Textual pilot tests for tui/app.py.

Drives the TUI without a terminal via ``App.run_test()``. Textual is required;
these tests are skipped if it isn't importable.
"""

from __future__ import annotations

import asyncio

import pytest

textual = pytest.importorskip("textual")  # noqa: F841

from textual.widgets import Button  # noqa: E402

from backend import comments as comments_mod
from backend import projects  # noqa: E402
from backend import stories as stories_mod
from tui.app import PlannerApp  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def seeded_db(db_path):
    """Seed a db with a project and two stories; return the db_path."""
    from backend import db
    c = db.connect(db_path)
    p = projects.create_project(c, "backend")
    stories_mod.create_story(c, "Fix login bug", project_id=p.id)
    stories_mod.create_story(c, "Add 2FA", project_id=p.id)
    c.close()
    return db_path


async def _ok(pilot, app):
    """Focus the active modal's #ok button and press Enter."""
    app.screen.query_one("#ok", Button).focus()
    await pilot.pause()
    await pilot.press("enter")
    await pilot.pause(0.05)
    await pilot.pause()


def test_tui_lists_seeded_stories(seeded_db):
    async def main():
        app = PlannerApp(seeded_db)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            assert app.query_one("#stories").row_count == 2
            await pilot.press("q")
    _run(main())


def test_tui_create_toggle_search_delete(seeded_db):
    async def main():
        app = PlannerApp(seeded_db)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            assert app.query_one("#stories").row_count == 2

            # create a story via 'n'
            await pilot.press("n"); await pilot.pause()
            await pilot.press(*"NoSpaces"); await pilot.pause()
            await _ok(pilot, app)
            assert app.conn.execute("SELECT COUNT(*) FROM story").fetchone()[0] == 3

            # select first story and toggle complete with 'e'
            app.query_one("#stories").move_cursor(row=0); await pilot.pause()
            sid = app._current_story_id()
            await pilot.press("e"); await pilot.pause()
            assert app.conn.execute(
                "SELECT completed_at FROM story WHERE id=?", (sid,)).fetchone()[0]

            # search filters the list
            await pilot.press("slash"); await pilot.pause()
            await pilot.press(*"login"); await pilot.pause()
            await _ok(pilot, app)
            assert app.query_one("#stories").row_count == 1

            # clear filter
            app.filters = {"project": None, "state_type": None, "q": None,
                           "epic": None, "iteration": None, "milestone": None}
            app.refresh_stories(); await pilot.pause()

            # add a comment
            app.query_one("#stories").move_cursor(row=0); await pilot.pause()
            sid = app._current_story_id()
            await pilot.press("c"); await pilot.pause()
            await pilot.press(*"looks bad"); await pilot.pause()
            await _ok(pilot, app)
            assert len(comments_mod.list_comments(app.conn, sid)) == 1

            # delete the selected story
            await pilot.press("d"); await pilot.pause()
            app.screen.query_one("#yes", Button).focus(); await pilot.pause()
            await pilot.press("enter"); await pilot.pause(0.05); await pilot.pause()
            # one story was deleted (3 -> 2)
            assert app.conn.execute("SELECT COUNT(*) FROM story").fetchone()[0] == 2

            assert getattr(app, "_exception", None) is None
            await pilot.press("q")
    _run(main())


def test_tui_edit_story_renames_and_clears_project(db_path):
    """The edit modal updates fields and can clear a nullable FK (project)."""
    from textual.widgets import Input, Select

    from backend import db, projects
    from backend import stories as stories_mod
    from tui.app import _NONE_INT

    c = db.connect(db_path)
    p = projects.create_project(c, "backend")
    s = stories_mod.create_story(c, "Fix login", project_id=p.id)
    c.close()

    async def main():
        app = PlannerApp(db_path)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.query_one("#stories").move_cursor(row=0); await pilot.pause()
            sid = app._current_story_id()
            assert sid == s.id

            # rename via the edit modal
            await pilot.press("u"); await pilot.pause()
            scr = app.screen
            scr.query_one("#e-name", Input).value = "Renamed"
            await _ok(pilot, app)
            assert app.conn.execute("SELECT name FROM story WHERE id=?", (sid,)).fetchone()[0] == "Renamed"

            # reopen and clear the project (choose the "(no project)" sentinel)
            await pilot.press("u"); await pilot.pause()
            app.screen.query_one("#e-proj", Select).value = _NONE_INT
            await _ok(pilot, app)
            assert app.conn.execute(
                "SELECT project_id FROM story WHERE id=?", (sid,)).fetchone()[0] is None

            assert getattr(app, "_exception", None) is None
            await pilot.press("q")
    _run(main())


def test_tui_task_action_toggle_and_edit(db_path):
    """The 'x' task-action modal toggles completion and edits a description."""
    from textual.widgets import Select, TextArea

    from backend import db
    from backend import stories as stories_mod
    from backend import tasks as tasks_mod

    c = db.connect(db_path)
    s = stories_mod.create_story(c, "x")
    t = tasks_mod.create_task(c, s.id, "write tests")
    c.close()

    async def main():
        app = PlannerApp(db_path)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.query_one("#stories").move_cursor(row=0); await pilot.pause()

            # toggle the task complete via 'x' -> Toggle
            await pilot.press("x"); await pilot.pause()
            app.screen.query_one("#toggle", Button).focus(); await pilot.pause()
            await pilot.press("enter"); await pilot.pause(0.05); await pilot.pause()
            assert tasks_mod.get_task(app.conn, t.id).complete == 1

            # edit the task description via 'x' -> set desc -> Save Desc
            await pilot.press("x"); await pilot.pause()
            app.screen.query_one("#ta-desc", TextArea).text = "write unit tests"
            app.screen.query_one("#save", Button).focus(); await pilot.pause()
            await pilot.press("enter"); await pilot.pause(0.05); await pilot.pause()
            assert tasks_mod.get_task(app.conn, t.id).description == "write unit tests"

            assert getattr(app, "_exception", None) is None
            await pilot.press("q")
    _run(main())


def test_tui_manage_owners_and_labels(db_path):
    """'o' and 'l' modals toggle owners and labels on the selected story."""
    from backend import db
    from backend import labels as labels_mod
    from backend import stories as stories_mod

    c = db.connect(db_path)
    s = stories_mod.create_story(c, "x")
    labels_mod.create_label(c, "auth")
    c.close()

    async def main():
        app = PlannerApp(db_path)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.query_one("#stories").move_cursor(row=0); await pilot.pause()

            # add an owner via 'o' (first member is the seeded user)
            await pilot.press("o"); await pilot.pause()
            app.screen.query_one("#toggle", Button).focus(); await pilot.pause()
            await pilot.press("enter"); await pilot.pause(0.05); await pilot.pause()
            assert len(stories_mod.list_owners(app.conn, s.id)) == 1

            # add a label via 'l' (first label is "auth")
            await pilot.press("l"); await pilot.pause()
            app.screen.query_one("#toggle", Button).focus(); await pilot.pause()
            await pilot.press("enter"); await pilot.pause(0.05); await pilot.pause()
            assert len(stories_mod.list_story_labels(app.conn, s.id)) == 1

            assert getattr(app, "_exception", None) is None
            await pilot.press("q")
    _run(main())


def test_tui_reorder_stories(db_path):
    """'J'/'K' swap a story with its neighbor by updating position."""
    from backend import db
    from backend import stories as stories_mod

    c = db.connect(db_path)
    a = stories_mod.create_story(c, "a")
    b = stories_mod.create_story(c, "b")
    cc = stories_mod.create_story(c, "c")
    c.close()

    async def main():
        app = PlannerApp(db_path)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            # initial order: a, b, c
            assert [s.id for s in app._filtered_neighbors()] == [a.id, b.id, cc.id]

            # move 'a' down -> b, a, c
            app.query_one("#stories").move_cursor(row=0); await pilot.pause()
            await pilot.press("J"); await pilot.pause(0.05); await pilot.pause()
            order = [s.id for s in stories_mod.list_stories(app.conn)]
            assert order == [b.id, a.id, cc.id], order

            # move 'a' (now at index 1) back up -> a, b, c
            await pilot.press("K"); await pilot.pause(0.05); await pilot.pause()
            order = [s.id for s in stories_mod.list_stories(app.conn)]
            assert order == [a.id, b.id, cc.id], order

            assert getattr(app, "_exception", None) is None
            await pilot.press("q")
    _run(main())
