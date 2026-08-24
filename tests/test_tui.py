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
    """Focus the #ok button and press Enter.
    Searches the app first, then the screen.
    """
    try:
        btn = app.query_one("#ok", Button)
    except Exception:
        btn = app.screen.query_one("#ok", Button)
    btn.focus()
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
                           "epic": None, "iteration": None, "milestone": None,
                           "owner": None, "label": None}
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
    """The in-pane edit updates fields and can clear a nullable FK (project)."""
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

            async def save():
                app.screen.query_one("#e-save", Button).focus(); await pilot.pause()
                await pilot.press("enter"); await pilot.pause(0.05); await pilot.pause()

            # rename via the in-pane edit
            await pilot.press("u"); await pilot.pause()
            app.screen.query_one("#e-name", Input).value = "Renamed"
            await save()
            assert app.conn.execute("SELECT name FROM story WHERE id=?", (sid,)).fetchone()[0] == "Renamed"

            # reopen and clear the project (choose the "(no project)" sentinel)
            await pilot.press("u"); await pilot.pause()
            app.screen.query_one("#e-proj", Select).value = _NONE_INT
            await save()
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


def test_tui_multiselect_bulk_delete(seeded_db):
    """'v' enters multi-select, Space toggles rows, and 'd' deletes all selected."""
    async def main():
        app = PlannerApp(seeded_db)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            assert app.query_one("#stories").row_count == 2

            # enter multi-select (visual) mode
            await pilot.press("v"); await pilot.pause()
            assert app._multi_select is True

            # select two stories with Space
            app.query_one("#stories").move_cursor(row=0); await pilot.pause()
            await pilot.press("space"); await pilot.pause()
            app.query_one("#stories").move_cursor(row=1); await pilot.pause()
            await pilot.press("space"); await pilot.pause()
            assert len(app._selected) == 2

            # delete all selected with confirmation
            await pilot.press("d"); await pilot.pause()
            app.screen.query_one("#yes", Button).focus(); await pilot.pause()
            await pilot.press("enter"); await pilot.pause(0.05); await pilot.pause()

            # both selected stories are gone
            assert app.conn.execute("SELECT COUNT(*) FROM story").fetchone()[0] == 0
            assert len(app._selected) == 0

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


def test_tui_command_palette(db_path):
    """Ctrl+P opens the palette, filters, and runs the selected command."""
    from textual.widgets import Input, OptionList

    from backend import db
    from backend import stories as stories_mod
    from tui.app import _PALETTE_COMMANDS, CommandPalette

    c = db.connect(db_path)
    s = stories_mod.create_story(c, "x")
    c.close()

    async def main():
        app = PlannerApp(db_path)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.query_one("#stories").move_cursor(row=0); await pilot.pause()
            sid = app._current_story_id()
            assert sid == s.id

            # open the palette with Ctrl+P
            await pilot.press("ctrl+p"); await pilot.pause()
            assert isinstance(app.screen, CommandPalette)
            opts = app.screen.query_one("#pal-options", OptionList)
            assert opts.option_count == len(_PALETTE_COMMANDS)

            # typing filters to a single matching command
            inp = app.screen.query_one("#pal-input", Input)
            inp.value = "complete"; await pilot.pause()
            assert opts.option_count == 1
            assert opts.get_option_at_index(0).id == "toggle_complete"

            # Enter runs the selected command: story becomes complete
            await pilot.press("enter"); await pilot.pause()
            assert app.conn.execute(
                "SELECT completed_at FROM story WHERE id=?", (s.id,)).fetchone()[0]

            assert getattr(app, "_exception", None) is None
            await pilot.press("q")
    _run(main())


def test_tui_manage_workflows_and_states(db_path):
    """The 'w' workflow manager creates/renames/deletes workflows and states.

    Exercises every button: rename workflow, add state, rename state, reorder a
    state, delete a state, delete a workflow — all via the backend. Deletes
    confirm first. See Story 41.
    """
    from textual.widgets import Button, Input

    from backend import db, workflows
    from tui.app import WorkflowManagerScreen

    c = db.connect(db_path)  # db_path fixture seeds the default workflow
    wf = workflows.list_workflows(c)[0]
    c.close()

    async def main():
        app = PlannerApp(db_path)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()

            async def click(btn_id: str):
                app.screen.query_one(btn_id, Button).focus()
                await pilot.pause()
                await pilot.press("enter")
                await pilot.pause(0.05)
                await pilot.pause()

            async def confirm_yes():
                app.screen.query_one("#yes", Button).focus()
                await pilot.pause()
                await pilot.press("enter")
                await pilot.pause(0.05)
                await pilot.pause()

            # open the workflow manager with 'w'
            await pilot.press("w"); await pilot.pause()
            assert isinstance(app.screen, WorkflowManagerScreen)
            assert app.screen.query_one("#wm-workflows").option_count == 1

            # rename the workflow
            await click("#wf-rename")
            app.screen.query_one("#p-value", Input).value = "Renamed WF"
            await click("#ok")
            assert workflows.get_workflow(app.conn, wf.id).name == "Renamed WF"

            # add a state (name "Review", default unstarted type)
            await click("#st-add")
            app.screen.query_one("#as-name", Input).value = "Review"
            await click("#ok")
            assert [s.name for s in workflows.list_workflow_states(app.conn, wf.id)] \
                == ["Unstarted", "Started", "Done", "Review"]

            # rename the just-added state
            app.screen.query_one("#wm-states").highlighted = 3
            await click("#st-rename")
            app.screen.query_one("#p-value", Input).value = "QA"
            await click("#ok")
            assert [s.name for s in workflows.list_workflow_states(app.conn, wf.id)] \
                == ["Unstarted", "Started", "Done", "QA"]

            # reorder: move the first state (Unstarted) down one slot
            app.screen.query_one("#wm-states").highlighted = 0
            await click("#st-down")
            names = [s.name for s in workflows.list_workflow_states(app.conn, wf.id)]
            assert names == ["Started", "Unstarted", "Done", "QA"], names

            # delete the QA state (with confirmation)
            app.screen.query_one("#wm-states").highlighted = 3
            await click("#st-delete")
            await confirm_yes()
            assert [s.name for s in workflows.list_workflow_states(app.conn, wf.id)] \
                == ["Started", "Unstarted", "Done"]

            # delete the workflow (with confirmation)
            await click("#wf-delete")
            await confirm_yes()
            assert workflows.list_workflows(app.conn) == []

            # Done closes the screen; refresh ran without error
            await click("#done")
            assert getattr(app, "_exception", None) is None
            await pilot.press("q")
    _run(main())


def test_tui_epic_management(db_path):
    """The 'E' epic manager creates, edits, and deletes epics via the backend.

    Exercises every flow: create, edit (name/state/project/milestone), and
    delete with confirmation. All operations call the backend epics module.
    See Story 42.
    """
    from textual.widgets import Button, Input, Select

    from backend import db
    from backend import epics as epics_mod
    from backend import milestones as ms_mod
    from backend import projects as proj_mod
    from tui.app import EpicManagerScreen

    c = db.connect(db_path)
    p = proj_mod.create_project(c, "backend")
    ms = ms_mod.create_milestone(c, "M1")
    c.close()

    async def main():
        app = PlannerApp(db_path)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()

            async def click(btn_id: str):
                app.screen.query_one(btn_id, Button).focus()
                await pilot.pause()
                await pilot.press("enter")
                await pilot.pause(0.05)
                await pilot.pause()

            async def confirm_yes():
                app.screen.query_one("#yes", Button).focus()
                await pilot.pause()
                await pilot.press("enter")
                await pilot.pause(0.05)
                await pilot.pause()

            # open the epic manager with 'E'
            await pilot.press("E"); await pilot.pause()
            assert isinstance(app.screen, EpicManagerScreen)
            assert app.screen.query_one("#em-epics").option_count == 0

            # create an epic
            await click("#em-new")
            app.screen.query_one("#ef-name", Input).value = "Auth"
            await click("#ok")
            epics = epics_mod.list_epics(app.conn)
            assert len(epics) == 1
            eid = epics[0].id
            assert epics[0].state == "planned"

            # edit: rename + state + project + milestone
            await click("#em-edit")
            app.screen.query_one("#ef-name", Input).value = "MFA"
            app.screen.query_one("#ef-state", Select).value = "in_progress"
            app.screen.query_one("#ef-proj", Select).value = p.id
            app.screen.query_one("#ef-ms", Select).value = ms.id
            await click("#ok")
            e = epics_mod.get_epic(app.conn, eid)
            assert e.name == "MFA"
            assert e.state == "in_progress"
            assert e.project_id == p.id
            assert e.milestone_id == ms.id

            # delete with confirmation
            await click("#em-delete")
            await confirm_yes()
            assert epics_mod.list_epics(app.conn) == []

            # Done closes the screen; refresh ran without error
            await click("#em-done")
            assert getattr(app, "_exception", None) is None
            await pilot.press("q")
    _run(main())


def test_tui_iteration_management(db_path):
    """The 'I' iteration manager creates, edits, and deletes iterations.

    Exercises every flow: create, edit (name/status/start/end dates), and
    delete with confirmation. All operations call the backend iterations
    module. See Story 43.
    """
    from textual.widgets import Button, Input, Select

    from backend import db
    from backend import iterations as iter_mod
    from tui.app import IterationManagerScreen

    c = db.connect(db_path)
    c.close()

    async def main():
        app = PlannerApp(db_path)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()

            async def click(btn_id: str):
                app.screen.query_one(btn_id, Button).focus()
                await pilot.pause()
                await pilot.press("enter")
                await pilot.pause(0.05)
                await pilot.pause()

            async def confirm_yes():
                app.screen.query_one("#yes", Button).focus()
                await pilot.pause()
                await pilot.press("enter")
                await pilot.pause(0.05)
                await pilot.pause()

            # open the iteration manager with 'I'
            await pilot.press("I"); await pilot.pause()
            assert isinstance(app.screen, IterationManagerScreen)
            assert app.screen.query_one("#im-iterations").option_count == 0

            # create an iteration
            await click("#im-new")
            app.screen.query_one("#if-name", Input).value = "Sprint 1"
            app.screen.query_one("#if-start", Input).value = "2026-09-01"
            app.screen.query_one("#if-end", Input).value = "2026-09-14"
            await click("#ok")
            its = iter_mod.list_iterations(app.conn)
            assert len(its) == 1
            iid = its[0].id
            assert its[0].status == "planned"
            assert its[0].start_date == "2026-09-01"
            assert its[0].end_date == "2026-09-14"

            # edit: rename + status + dates
            await click("#im-edit")
            app.screen.query_one("#if-name", Input).value = "Sprint 2"
            app.screen.query_one("#if-status", Select).value = "active"
            app.screen.query_one("#if-start", Input).value = "2026-09-15"
            app.screen.query_one("#if-end", Input).value = ""
            await click("#ok")
            it = iter_mod.get_iteration(app.conn, iid)
            assert it.name == "Sprint 2"
            assert it.status == "active"
            assert it.start_date == "2026-09-15"
            assert it.end_date is None

            # delete with confirmation
            await click("#im-delete")
            await confirm_yes()
            assert iter_mod.list_iterations(app.conn) == []

            # Done closes the screen; refresh ran without error
            await click("#im-done")
            assert getattr(app, "_exception", None) is None
            await pilot.press("q")
    _run(main())


def test_tui_refresh_keeps_cursor_position(db_path):
    """refresh_stories must not yank the cursor back to the top row."""
    from backend import db
    from backend import stories as stories_mod

    c = db.connect(db_path)
    stories_mod.create_story(c, "a")
    b = stories_mod.create_story(c, "b")
    stories_mod.create_story(c, "c")
    c.close()

    async def main():
        app = PlannerApp(db_path)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            table = app.query_one("#stories")
            table.move_cursor(row=1); await pilot.pause()
            assert app._current_story_id() == b.id

            # A refresh (e.g. the auto-refresh tick) rebuilds the table.
            app.refresh_stories(); await pilot.pause()

            assert table.cursor_row == 1, table.cursor_row
            assert app._current_story_id() == b.id

            # If the selected story leaves the view, clamp instead of crashing.
            stories_mod.delete_story(app.conn, b.id)
            app.refresh_stories(); await pilot.pause()
            assert table.cursor_row == 1, table.cursor_row

            assert getattr(app, "_exception", None) is None
            await pilot.press("q")
    _run(main())
