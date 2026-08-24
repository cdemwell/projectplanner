"""Headless Textual pilot tests for tui/app.py.

Drives the TUI without a terminal via ``App.run_test()``. Textual is required;
these tests are skipped if it isn't importable.
"""

from __future__ import annotations

import asyncio

import pytest

textual = pytest.importorskip("textual")  # noqa: F841

from textual.widgets import Button, Select, TextArea  # noqa: E402

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
            app._search_ids = None
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


def test_tui_search_entity_selector(seeded_db):
    """The search screen offers an entity scope; non-story entities show a
    generic results screen, and a bad query surfaces an error."""
    from tui.app import SearchInputScreen, SearchResultsScreen

    async def main():
        app = PlannerApp(seeded_db)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()

            # Open the search screen and pick the "epic" entity.
            await pilot.press("slash"); await pilot.pause()
            search_screen = app.screen
            assert isinstance(search_screen, SearchInputScreen)
            ent = search_screen.query_one("#s-entity", Select)
            ent.value = "epic"; await pilot.pause()
            await pilot.press(*"login"); await pilot.pause()
            await _ok(pilot, app)
            # A non-story entity opens the generic results screen.
            assert isinstance(app.screen, SearchResultsScreen)
            assert app.screen.query_one("#sr-table").row_count == 0
            app.screen.query_one("#cancel", Button).focus()
            await pilot.pause()
            await pilot.press("enter"); await pilot.pause(0.05); await pilot.pause()
            # The main story list is untouched by a non-story search.
            assert app.query_one("#stories").row_count == 2

            # A story-entity search still filters the story list (FTS5 backend).
            await pilot.press("slash"); await pilot.pause()
            assert isinstance(app.screen, SearchInputScreen)
            await pilot.press(*"login"); await pilot.pause()
            await _ok(pilot, app)
            assert app.query_one("#stories").row_count == 1

            assert getattr(app, "_exception", None) is None
            await pilot.press("q")
    _run(main())


def test_tui_search_results_error(seeded_db):
    """A malformed FTS query surfaces the backend error in the results screen."""
    from tui.app import SearchResultsScreen

    async def main():
        app = PlannerApp(seeded_db)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            # Bad FTS syntax (bare ':') should surface in the error label.
            app.push_screen(SearchResultsScreen(app.conn, "not a valid : query", "story"))
            await pilot.pause()
            err = str(app.screen.query_one("#sr-err").render())
            assert "error:" in err
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


def test_tui_task_action_delete(db_path):
    """The 'x' task-action modal deletes a task after confirmation."""
    from backend import db, errors
    from backend import stories as stories_mod
    from backend import tasks as tasks_mod

    c = db.connect(db_path)
    s = stories_mod.create_story(c, "x")
    t1 = tasks_mod.create_task(c, s.id, "write tests")
    t2 = tasks_mod.create_task(c, s.id, "keep me")
    c.close()

    async def main():
        app = PlannerApp(db_path)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.query_one("#stories").move_cursor(row=0); await pilot.pause()

            # cancel the confirmation -> task is kept
            await pilot.press("x"); await pilot.pause()
            app.screen.query_one("#delete", Button).focus(); await pilot.pause()
            await pilot.press("enter"); await pilot.pause(0.05); await pilot.pause()
            app.screen.query_one("#cancel", Button).focus(); await pilot.pause()
            await pilot.press("enter"); await pilot.pause(0.05); await pilot.pause()
            assert tasks_mod.get_task(app.conn, t1.id) is not None

            # confirm the deletion -> task is gone, sibling survives
            await pilot.press("x"); await pilot.pause()
            app.screen.query_one("#delete", Button).focus(); await pilot.pause()
            await pilot.press("enter"); await pilot.pause(0.05); await pilot.pause()
            app.screen.query_one("#yes", Button).focus(); await pilot.pause()
            await pilot.press("enter"); await pilot.pause(0.05); await pilot.pause()
            with pytest.raises(errors.NotFound):
                tasks_mod.get_task(app.conn, t1.id)
            assert tasks_mod.get_task(app.conn, t2.id) is not None

            assert getattr(app, "_exception", None) is None
            await pilot.press("q")
    _run(main())


def test_tui_comment_action_edit_and_delete(db_path):
    """The 'C' comment-action modal edits and deletes a comment with confirmation."""
    from backend import comments as comments_mod
    from backend import db, errors
    from backend import stories as stories_mod

    c = db.connect(db_path)
    s = stories_mod.create_story(c, "c")
    c1 = comments_mod.create_comment(c, s.id, "original text")
    c2 = comments_mod.create_comment(c, s.id, "keep me")
    c.close()

    async def main():
        app = PlannerApp(db_path)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.query_one("#stories").move_cursor(row=0); await pilot.pause()

            # edit the first comment (editor is prefilled with its current text)
            await pilot.press("C"); await pilot.pause()
            assert app.screen.query_one("#ca-text", TextArea).text == "original text"
            app.screen.query_one("#ca-text", TextArea).text = "updated!"
            app.screen.query_one("#save", Button).focus(); await pilot.pause()
            await pilot.press("enter"); await pilot.pause(0.05); await pilot.pause()
            assert comments_mod.get_comment(app.conn, c1.id).text == "updated!"

            # cancel the confirmation -> comment is kept
            await pilot.press("C"); await pilot.pause()
            app.screen.query_one("#delete", Button).focus(); await pilot.pause()
            await pilot.press("enter"); await pilot.pause(0.05); await pilot.pause()
            app.screen.query_one("#cancel", Button).focus(); await pilot.pause()
            await pilot.press("enter"); await pilot.pause(0.05); await pilot.pause()
            assert comments_mod.get_comment(app.conn, c1.id) is not None

            # confirm the deletion -> comment is gone, sibling survives
            await pilot.press("C"); await pilot.pause()
            app.screen.query_one("#delete", Button).focus(); await pilot.pause()
            await pilot.press("enter"); await pilot.pause(0.05); await pilot.pause()
            app.screen.query_one("#yes", Button).focus(); await pilot.pause()
            await pilot.press("enter"); await pilot.pause(0.05); await pilot.pause()
            with pytest.raises(errors.NotFound):
                comments_mod.get_comment(app.conn, c1.id)
            assert comments_mod.get_comment(app.conn, c2.id) is not None

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

            # open the workflow manager (via its palette action; the 'w' key
            # now switches the parent pane to workflows instead)
            app.action_manage_workflows(); await pilot.pause()
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

            # open the epic manager (via its palette action; 'E' now switches
            # the parent pane to epics instead)
            app.action_manage_epics(); await pilot.pause()
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

            # open the iteration manager (via its palette action; 'I' now
            # switches the parent pane to iterations instead)
            app.action_manage_iterations(); await pilot.pause()
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


def test_tui_milestone_management(db_path):
    """The 'M' milestone manager creates, edits, and deletes milestones.

    Exercises every flow: create, edit (name/description/state), and delete
    with confirmation. All operations call the backend milestones module. See
    Story 44.
    """
    from textual.widgets import Button, Input, Select

    from backend import db
    from backend import milestones as ms_mod
    from tui.app import MilestoneManagerScreen

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

            # open the milestone manager (via its palette action; 'M' now
            # switches the parent pane to milestones instead)
            app.action_manage_milestones(); await pilot.pause()
            assert isinstance(app.screen, MilestoneManagerScreen)
            assert app.screen.query_one("#mm-milestones").option_count == 0

            # create a milestone
            await click("#mm-new")
            app.screen.query_one("#mf-name", Input).value = "MVP"
            app.screen.query_one("#mf-desc").text = "First release"
            await click("#ok")
            mss = ms_mod.list_milestones(app.conn)
            assert len(mss) == 1
            mid = mss[0].id
            assert mss[0].state == "planned"
            assert mss[0].description == "First release"

            # edit: rename + state
            await click("#mm-edit")
            app.screen.query_one("#mf-name", Input).value = "Launch"
            app.screen.query_one("#mf-state", Select).value = "in_progress"
            await click("#ok")
            ms = ms_mod.get_milestone(app.conn, mid)
            assert ms.name == "Launch"
            assert ms.state == "in_progress"

            # delete with confirmation
            await click("#mm-delete")
            await confirm_yes()
            assert ms_mod.list_milestones(app.conn) == []

            # Done closes the screen; refresh ran without error
            await click("#mm-done")
            assert getattr(app, "_exception", None) is None
            await pilot.press("q")
    _run(main())


def test_tui_project_management(db_path):
    """The 'P' project manager creates, edits, archives, and deletes projects.

    Exercises every flow: create, edit (name/abbreviation/color), archive,
    unarchive, and delete with confirmation. All operations call the backend
    projects module. See Story 45.
    """
    from textual.widgets import Button, Input

    from backend import db
    from backend import projects as proj_mod
    from tui.app import ProjectManagerScreen

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

            # open the project manager (via its palette action; 'P' now
            # switches the parent pane to projects instead)
            app.action_manage_projects(); await pilot.pause()
            assert isinstance(app.screen, ProjectManagerScreen)
            assert app.screen.query_one("#pm-projects").option_count == 0

            # create a project
            await click("#pm-new")
            app.screen.query_one("#pf-name", Input).value = "backend"
            app.screen.query_one("#pf-abbr", Input).value = "be"
            app.screen.query_one("#pf-color", Input).value = "#ff0000"
            await click("#ok")
            ps = proj_mod.list_projects(app.conn)
            assert len(ps) == 1
            pid = ps[0].id
            assert ps[0].name == "backend"
            assert ps[0].abbreviation == "be"
            assert ps[0].color == "#ff0000"
            assert ps[0].archived == 0

            # edit: rename + color (abbreviation preserved)
            await click("#pm-edit")
            app.screen.query_one("#pf-name", Input).value = "Platform"
            app.screen.query_one("#pf-color", Input).value = "#0000ff"
            await click("#ok")
            p = proj_mod.get_project(app.conn, pid)
            assert p.name == "Platform"
            assert p.color == "#0000ff"
            assert p.abbreviation == "be"

            # archive (non-destructive, no confirmation)
            await click("#pm-archive")
            assert proj_mod.get_project(app.conn, pid).archived == 1
            # archived projects still appear in the list
            assert app.screen.query_one("#pm-projects").option_count == 1

            # unarchive (pause lets the refresh settle before the same button
            # can be re-activated by Enter, mirroring a human's read-time)
            await pilot.pause(0.15)
            await click("#pm-archive")
            assert proj_mod.get_project(app.conn, pid).archived == 0

            # delete with confirmation
            await click("#pm-delete")
            await confirm_yes()
            assert proj_mod.list_projects(app.conn) == []

            # Done closes the screen; refresh ran without error
            await click("#pm-done")
            assert getattr(app, "_exception", None) is None
            await pilot.press("q")
    _run(main())


def test_tui_label_management(db_path):
    """The 'L' label manager creates, edits (rename/color), and deletes labels.

    Exercises every flow: create, edit (rename + change color), and delete
    with confirmation. All operations call the backend labels module. See
    Story 46.
    """
    from textual.widgets import Button, Input

    from backend import db
    from backend import labels as lbl_mod
    from tui.app import LabelManagerScreen

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

            # open the label manager (via its palette action; 'L' now switches
            # the parent pane to labels instead)
            app.action_manage_label_catalog(); await pilot.pause()
            assert isinstance(app.screen, LabelManagerScreen)
            assert app.screen.query_one("#lm-labels").option_count == 0

            # create a label
            await click("#lm-new")
            app.screen.query_one("#lf-name", Input).value = "bug"
            app.screen.query_one("#lf-color", Input).value = "#ff0000"
            await click("#ok")
            ls = lbl_mod.list_labels(app.conn)
            assert len(ls) == 1
            lid = ls[0].id
            assert ls[0].name == "bug"
            assert ls[0].color == "#ff0000"

            # edit: rename + change color (description untouched)
            await click("#lm-edit")
            app.screen.query_one("#lf-name", Input).value = "critical"
            app.screen.query_one("#lf-color", Input).value = "#0000ff"
            await click("#ok")
            lbl = lbl_mod.get_label(app.conn, lid)
            assert lbl.name == "critical"
            assert lbl.color == "#0000ff"

            # delete with confirmation
            await click("#lm-delete")
            await confirm_yes()
            assert lbl_mod.list_labels(app.conn) == []

            # Done closes the screen; refresh ran without error
            await click("#lm-done")
            assert getattr(app, "_exception", None) is None
            await pilot.press("q")
    _run(main())


def test_tui_member_management(db_path):
    """The 'R' member manager creates, edits, and deletes members.

    Exercises every flow: create, edit (name + mention_name), and delete with
    confirmation. All operations call the backend members module. See
    Story 47.
    """
    from textual.widgets import Button, Input

    from backend import db
    from backend import members as members_mod
    from tui.app import MemberManagerScreen

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

            # open the member manager (via its palette action; 'R' now switches
            # the parent pane to members instead)
            app.action_manage_member_catalog(); await pilot.pause()
            assert isinstance(app.screen, MemberManagerScreen)
            # the db seeds one local member, so the roster starts at one
            assert app.screen.query_one("#mm-members").option_count == 1

            # create a member
            await click("#mm-new")
            app.screen.query_one("#mf-name", Input).value = "Ada Lovelace"
            app.screen.query_one("#mf-mention", Input).value = "@ada"
            await click("#ok")
            ms = members_mod.list_members(app.conn)
            assert len(ms) == 2
            mid = max(m.id for m in ms)
            ada = members_mod.get_member(app.conn, mid)
            assert ada.name == "Ada Lovelace"
            assert ada.mention_name == "@ada"

            # edit: rename + change mention_name (the new member is at idx 1;
            # idx 0 is the seeded local member)
            from textual.widgets import OptionList
            app.screen.query_one("#mm-members", OptionList).highlighted = 1
            await click("#mm-edit")
            app.screen.query_one("#mf-name", Input).value = "Grace Hopper"
            app.screen.query_one("#mf-mention", Input).value = "@grace"
            await click("#ok")
            m = members_mod.get_member(app.conn, mid)
            assert m.name == "Grace Hopper"
            assert m.mention_name == "@grace"

            # delete with confirmation
            app.screen.query_one("#mm-members", OptionList).highlighted = 1
            await click("#mm-delete")
            await confirm_yes()
            assert len(members_mod.list_members(app.conn)) == 1

            # Done closes the screen; refresh ran without error
            await click("#mm-done")
            assert getattr(app, "_exception", None) is None
            await pilot.press("q")
    _run(main())


def test_tui_group_management(db_path):
    """The 'G' group manager creates, edits, archives, and deletes groups.

    Exercises every flow: create, edit (name + description), archive/unarchive,
    and delete with confirmation. All operations call the backend groups module.
    See Story 48.
    """
    from textual.widgets import Button, Input, TextArea

    from backend import db
    from backend import groups as groups_mod
    from tui.app import GroupManagerScreen

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
                await pilot.pause(0.1)
                await pilot.pause()

            async def confirm_yes():
                app.screen.query_one("#yes", Button).focus()
                await pilot.pause()
                await pilot.press("enter")
                await pilot.pause(0.1)
                await pilot.pause()

            # open the group manager (via its palette action; 'G' now switches
            # the parent pane to groups instead)
            app.action_manage_group_catalog(); await pilot.pause()
            assert isinstance(app.screen, GroupManagerScreen)
            assert app.screen.query_one("#gm-groups").option_count == 0

            # create a group
            await click("#gm-new")
            app.screen.query_one("#gf-name", Input).value = "Frontend"
            app.screen.query_one("#gf-desc", TextArea).text = "Web team"
            await click("#ok")
            gs = groups_mod.list_groups(app.conn)
            assert len(gs) == 1
            gid = gs[0].id
            g = groups_mod.get_group(app.conn, gid)
            assert g.name == "Frontend"
            assert g.description == "Web team"
            assert g.archived == 0

            # edit: rename + change description
            from textual.widgets import OptionList
            app.screen.query_one("#gm-groups", OptionList).highlighted = 0
            await click("#gm-edit")
            app.screen.query_one("#gf-name", Input).value = "Frontend Guild"
            app.screen.query_one("#gf-desc", TextArea).text = "Web + design"
            await click("#ok")
            g = groups_mod.get_group(app.conn, gid)
            assert g.name == "Frontend Guild"
            assert g.description == "Web + design"

            # archive, then unarchive
            app.screen.query_one("#gm-groups", OptionList).highlighted = 0
            await pilot.pause()
            await click("#gm-archive")
            assert groups_mod.get_group(app.conn, gid).archived == 1
            app.screen.query_one("#gm-groups", OptionList).highlighted = 0
            await pilot.pause()
            await click("#gm-archive")
            assert groups_mod.get_group(app.conn, gid).archived == 0

            # delete with confirmation
            app.screen.query_one("#gm-groups", OptionList).highlighted = 0
            await pilot.pause()
            await click("#gm-delete")
            await confirm_yes()
            assert len(groups_mod.list_groups(app.conn)) == 0

            # Done closes the screen; refresh ran without error
            await click("#gm-done")
            assert getattr(app, "_exception", None) is None
            await pilot.press("q")
    _run(main())


def test_tui_plan_export_import_backup(db_path, tmp_path):
    """The 'S' plan manager exports a snapshot, backs up the DB file, and
    imports a snapshot (overwriting the current plan) after confirmation.
    Export/import go through the backend plan module; backup copies the SQLite
    file. See Story 52."""
    import json
    from pathlib import Path

    from textual.widgets import Button, Input

    from backend import db
    from backend import plan as plan_mod
    from backend import projects as proj_mod
    from backend import stories as stories_mod
    from tui.app import PlanManagerScreen

    c = db.connect(db_path)
    p = proj_mod.create_project(c, "backend")
    stories_mod.create_story(c, "alpha", project_id=p.id)
    stories_mod.create_story(c, "beta", project_id=p.id)
    c.close()

    snap = str(tmp_path / "snap.json")

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

            # open the plan manager with 'S'
            await pilot.press("S"); await pilot.pause()
            assert isinstance(app.screen, PlanManagerScreen)

            # export a JSON snapshot; the count matches the backend's own tally
            expected = sum(len(v) for k, v in plan_mod.export_plan(app.conn).items()
                           if k != "_meta")
            await click("#plan-export")
            app.screen.query_one("#p-value", Input).value = snap
            await click("#ok")
            status = str(app.screen.query_one("#plan-status").render()).strip()
            assert status == f"Exported {expected} rows to {snap}"
            with open(snap) as f:
                snap_data = json.load(f)
            assert snap_data["story"][0]["name"] == "alpha"

            # backup copies the DB file to a timestamped sibling
            await click("#plan-backup")
            backups = list(Path(db_path).parent.glob(f"{Path(db_path).name}.*"))
            assert len(backups) == 1
            assert backups[0].exists()

            # wipe every story, then import the snapshot to restore them
            for s in stories_mod.list_stories(app.conn):
                stories_mod.delete_story(app.conn, s.id)
            assert stories_mod.list_stories(app.conn) == []

            # cancel the destructive confirmation -> still empty
            await click("#plan-import")
            app.screen.query_one("#p-value", Input).value = snap
            await click("#ok")
            app.screen.query_one("#cancel", Button).focus(); await pilot.pause()
            await pilot.press("enter"); await pilot.pause(0.05); await pilot.pause()
            assert stories_mod.list_stories(app.conn) == []

            # confirm the import -> stories restored, plan overwritten
            await click("#plan-import")
            app.screen.query_one("#p-value", Input).value = snap
            await click("#ok")
            await confirm_yes()
            restored = stories_mod.list_stories(app.conn)
            assert {s.name for s in restored} == {"alpha", "beta"}

            # Done closes the screen; refresh ran without error
            await click("#plan-done")
            assert getattr(app, "_exception", None) is None
            await pilot.press("q")
    _run(main())


def test_tui_config_init_and_show(db_path, tmp_path):
    """The 'B' config manager writes a default config file (init) and prints the
    resolved settings (show). Init/show go through the backend config module
    (save_config / load_config); errors surface in the status label. See Story 53.
    """
    from pathlib import Path

    from textual.widgets import Button, Input

    from backend import config as config_mod
    from tui.app import ConfigManagerScreen

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

            # open the config manager with 'B'
            await pilot.press("B"); await pilot.pause()
            assert isinstance(app.screen, ConfigManagerScreen)

            # show with no file present -> resolves to built-in defaults
            await click("#cfg-show")
            app.screen.query_one("#p-value", Input).value = cfg_path
            await click("#ok")
            out = str(app.screen.query_one("#cfg-output").render()).strip()
            assert "default_project: backend" in out
            assert "auto_refresh_seconds: 5" in out

            # init writes the default config file via the backend
            await click("#cfg-init")
            app.screen.query_one("#p-value", Input).value = cfg_path
            await click("#ok")
            status = str(app.screen.query_one("#cfg-status").render()).strip()
            assert status == f"Initialized {cfg_path}"
            assert Path(cfg_path).exists()
            # the file round-trips through the backend loader
            loaded = config_mod.load_config(cfg_path)
            assert loaded.default_project == "backend"
            assert loaded.auto_refresh_seconds == 5

            # Done closes the screen; no exception surfaced
            await click("#cfg-done")
            assert getattr(app, "_exception", None) is None
            await pilot.press("q")

    cfg_path = str(tmp_path / "planner.yaml")
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


def test_tui_story_link_add_and_delete(db_path):
    """The 'h' story-link modal adds a directed link and deletes one with
    confirmation. All operations call the backend story_links module. See
    Story 51."""
    from textual.widgets import Button

    from backend import db
    from backend import stories as stories_mod
    from backend import story_links as links_mod
    from tui.app import _NONE_INT, StoryLinkActionScreen

    c = db.connect(db_path)
    a = stories_mod.create_story(c, "alpha")
    b = stories_mod.create_story(c, "beta")
    c.close()

    async def main():
        app = PlannerApp(db_path)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()

            # select story 'alpha' (row 0) and open the link manager with 'h'
            app.query_one("#stories").move_cursor(row=0); await pilot.pause()
            assert app._current_story_id() == a.id
            await pilot.press("h"); await pilot.pause()
            assert isinstance(app.screen, StoryLinkActionScreen)
            assert app.screen.query_one("#sl-link").value == _NONE_INT  # "(no links)"

            # add a link: alpha --blocks--> beta
            app.screen.query_one("#add", Button).focus(); await pilot.pause()
            await pilot.press("enter"); await pilot.pause(0.05); await pilot.pause()
            # the LinkAddScreen defaults to the first target story (beta) + "blocks"
            app.screen.query_one("#ok", Button).focus(); await pilot.pause()
            await pilot.press("enter"); await pilot.pause(0.05); await pilot.pause()
            links = links_mod.list_links(app.conn, a.id)
            assert len(links) == 1
            assert links[0].subject_story_id == a.id
            assert links[0].object_story_id == b.id
            assert links[0].verb == "blocks"

            # reopen and delete the link (cancel first, then confirm)
            await pilot.press("h"); await pilot.pause()
            assert app.screen.query_one("#sl-link").value != _NONE_INT  # link listed
            app.screen.query_one("#delete", Button).focus(); await pilot.pause()
            await pilot.press("enter"); await pilot.pause(0.05); await pilot.pause()
            app.screen.query_one("#cancel", Button).focus(); await pilot.pause()
            await pilot.press("enter"); await pilot.pause(0.05); await pilot.pause()
            assert len(links_mod.list_links(app.conn, a.id)) == 1  # kept

            await pilot.press("h"); await pilot.pause()
            app.screen.query_one("#delete", Button).focus(); await pilot.pause()
            await pilot.press("enter"); await pilot.pause(0.05); await pilot.pause()
            app.screen.query_one("#yes", Button).focus(); await pilot.pause()
            await pilot.press("enter"); await pilot.pause(0.05); await pilot.pause()
            assert links_mod.list_links(app.conn, a.id) == []

            assert getattr(app, "_exception", None) is None
            await pilot.press("q")
    _run(main())


def test_tui_entity_list_pane_stories(seeded_db):
    """Story 67: the story list is an EntityListPane with the story column
    schema, and every row is keyed by the story's DB id."""
    from tui.app import EntityListPane, _story_columns

    async def main():
        app = PlannerApp(seeded_db)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            pane = app.query_one("#stories", EntityListPane)
            assert [c.label for c in pane.columns_schema] == \
                ["ID", "Name", "Type", "State", "Project", "Owners", "✓"]
            assert pane.row_count == 2
            sids = [s.id for s in stories_mod.list_stories(app.conn)]
            assert pane.row_keys == [str(i) for i in sids]
            # selection (cursor) resolves back to the DB id of the row
            assert pane.current_id == sids[0]
            assert app._current_story_id() == sids[0]
            # an empty filter shows no rows and no cursor id
            pane.set_items([], conn=app.conn)
            assert pane.row_count == 0
            assert pane.current_id is None
            assert getattr(app, "_exception", None) is None
            await pilot.press("q")
    _run(main())


def _pane_text(pane):
    """Flatten the EntityDetailPane's rendered Static text for assertions."""
    from textual.widgets import Static
    return "\n".join(str(w.content) for w in pane.query(Static))


def test_detail_pane_renders_story_with_related_links(db_path):
    """The generic detail pane renders a story's details and carries related
    entity links with their target ids."""
    from backend import db
    from backend import epics as epics_mod
    from backend import milestones as ms_mod
    from tui.detail import EntityDetailPane

    c = db.connect(db_path)
    p = projects.create_project(c, "backend")
    ms = ms_mod.create_milestone(c, "M1")
    e = epics_mod.create_epic(c, "Epic A", milestone_id=ms.id, project_id=p.id)
    stories_mod.create_story(c, "Fix login bug", project_id=p.id, epic_id=e.id,
                             story_type="bug")
    c.close()

    async def main():
        app = PlannerApp(db_path)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.query_one("#stories").move_cursor(row=0)
            await pilot.pause(0.05)
            await pilot.pause()
            pane = app.query_one("#detail-view", EntityDetailPane)
            assert "Fix login bug" in _pane_text(pane)
            kinds = {lk.entity: lk.target_id for lk in pane.related_links()}
            assert kinds.get("epic") == e.id
            assert kinds.get("project") == p.id
            assert kinds.get("milestone") is None  # not a story parent
            assert getattr(app, "_exception", None) is None
            await pilot.press("q")
    _run(main())


def test_tui_entity_list_pane_epics(db_path):
    """Story 67: an EntityListPane renders any entity type — here epics with a
    different column schema — and still keys rows by DB id and preserves the
    cursor across a refresh."""
    from textual.app import App as TextualApp
    from textual.containers import Vertical

    from backend import db
    from backend import epics as epics_mod
    from backend import projects as proj_mod
    from tui.app import EntityListPane, _epic_columns

    c = db.connect(db_path)
    p = proj_mod.create_project(c, "backend")
    epics_mod.create_epic(c, "Auth", project_id=p.id)
    epics_mod.create_epic(c, "Payments", project_id=p.id)
    c.close()

    class PaneHost(TextualApp):
        """Minimal host that mounts a single pane for headless pilot control."""

        def __init__(self, pane: EntityListPane) -> None:
            super().__init__()
            self.pane = pane

        def compose(self):
            yield Vertical(self.pane)

    async def main():
        conn = db.connect(db_path)
        pane = EntityListPane(columns=_epic_columns(), id="epics")
        host = PaneHost(pane)
        async with host.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            items = epics_mod.list_epics(conn)
            pane.set_items(items, conn=conn)
            await pilot.pause()
            assert [c.label for c in pane.columns_schema] == \
                ["ID", "Name", "State", "Project", "Milestone"]
            assert pane.row_count == 2
            assert pane.row_keys == [str(e.id) for e in items]
            assert pane.current_id == items[0].id
            # a refresh rebuilds the rows but keeps the cursor on the same epic
            pane.move_cursor(row=1); await pilot.pause()
            assert pane.current_id == items[1].id
            pane.set_items(items, conn=conn); await pilot.pause()
            assert pane.current_id == items[1].id
            assert getattr(host, "_exception", None) is None
        conn.close()
    _run(main())


def test_detail_pane_renders_epic_with_related_links(db_path):
    """The generic detail pane renders a non-story entity (epic) and its
    related milestone/project links, independent of the story selection."""
    from backend import db
    from backend import epics as epics_mod
    from backend import milestones as ms_mod
    from tui.detail import EntityDetailPane

    c = db.connect(db_path)
    p = projects.create_project(c, "backend")
    ms = ms_mod.create_milestone(c, "M1")
    e = epics_mod.create_epic(c, "Epic A", milestone_id=ms.id, project_id=p.id)
    c.close()

    async def main():
        app = PlannerApp(db_path)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            pane = app.query_one("#detail-view", EntityDetailPane)
            pane.show(app.conn, "epic", e.id)
            await pilot.pause(0.05)
            await pilot.pause()
            assert "Epic A" in _pane_text(pane)
            kinds = {lk.entity: lk.target_id for lk in pane.related_links()}
            assert kinds.get("milestone") == ms.id
            assert kinds.get("project") == p.id
            assert getattr(app, "_exception", None) is None
            await pilot.press("q")
    _run(main())


def test_three_pane_layout_and_tab_focus_cycle(db_path):
    """Story 69: the Miller-columns layout has three panes (parent list, child
    list, detail) and Tab/Shift+Tab cycle focus through them in order, with the
    focused pane visually distinguished via the 'pane-focused' class."""
    from backend import db
    from backend import tasks as tasks_mod

    c = db.connect(db_path)
    p = projects.create_project(c, "backend")
    st = stories_mod.create_story(c, "Fix login bug", project_id=p.id)
    tk = tasks_mod.create_task(c, st.id, "write a test")
    c.close()

    async def main():
        app = PlannerApp(db_path)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()

            # All three panes exist.
            assert app.query_one("#stories").row_count == 1
            assert app.query_one("#children").row_count == 1
            app.query_one("#detail")

            # The child pane shows the selected story's task (minimal chain).
            assert app.query_one("#children").row_keys == [str(tk.id)]

            # Initial focus is on the parent pane, which is visually marked.
            assert app.focused.id == "stories"
            assert app.query_one("#stories").has_class("pane-focused")
            assert not app.query_one("#children").has_class("pane-focused")
            assert not app.query_one("#detail").has_class("pane-focused")

            # Tab cycles parent -> child -> detail -> parent.
            await pilot.press("tab"); await pilot.pause()
            assert app.focused.id == "children"
            assert app.query_one("#children").has_class("pane-focused")
            assert not app.query_one("#stories").has_class("pane-focused")

            await pilot.press("tab"); await pilot.pause()
            assert app.focused.id == "detail"
            assert app.query_one("#detail").has_class("pane-focused")
            assert not app.query_one("#children").has_class("pane-focused")

            await pilot.press("tab"); await pilot.pause()
            assert app.focused.id == "stories"
            assert app.query_one("#stories").has_class("pane-focused")

            # Shift+Tab reverses the cycle (parent -> detail -> child).
            await pilot.press("shift+tab"); await pilot.pause()
            assert app.focused.id == "detail"
            await pilot.press("shift+tab"); await pilot.pause()
            assert app.focused.id == "children"

            # Existing story browsing still works: the detail renders.
            app.query_one("#stories").move_cursor(row=0); await pilot.pause()
            assert "Fix login bug" in _pane_text(app.query_one("#detail-view"))
            assert getattr(app, "_exception", None) is None
            await pilot.press("q")
    _run(main())


def test_tui_switch_parent_entity_pane(db_path):
    """Story 71: a dedicated key (and matching palette action) switches the
    upper-left parent pane to another entity — re-schemaing its columns and
    re-deriving the child pane from the chain model — and switching back to
    stories restores the story list."""
    from backend import db
    from backend import epics as epics_mod
    from tui.app import EntityListPane

    c = db.connect(db_path)
    p = projects.create_project(c, "backend")
    e = epics_mod.create_epic(c, "Auth epic", project_id=p.id)
    stories_mod.create_story(c, "Fix login bug", project_id=p.id, epic_id=e.id)
    c.close()

    async def main():
        app = PlannerApp(db_path)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()

            # Default parent is the story list with the story column schema.
            stories_pane = app.query_one("#stories", EntityListPane)
            assert app.parent_entity == "story"
            assert [c.label for c in stories_pane.columns_schema] == \
                ["ID", "Name", "Type", "State", "Project", "Owners", "✓"]
            assert stories_pane.row_count == 1

            # Dedicated key 'E' switches the parent pane to epics.
            await pilot.press("E"); await pilot.pause()
            assert app.parent_entity == "epic"
            assert [c.label for c in stories_pane.columns_schema] == \
                ["ID", "Name", "State", "Project", "Milestone"]
            assert stories_pane.row_count == 1
            assert stories_pane.current_id == e.id
            # The child pane re-derives to epics' children (the selected
            # epic's stories) via the chain model.
            assert app.query_one("#children").row_keys == \
                [str(s.id) for s in epics_mod.list_epic_stories(app.conn, e.id)]

            # A palette command (dispatched to the action method) switches to
            # projects; switching back to stories restores the story list.
            app.action_switch_to_project()
            await pilot.pause()
            assert app.parent_entity == "project"
            assert [c.label for c in stories_pane.columns_schema] == \
                ["ID", "Name", "Abbreviation", "Archived"]

            await pilot.press("s"); await pilot.pause()
            assert app.parent_entity == "story"
            assert [c.label for c in stories_pane.columns_schema] == \
                ["ID", "Name", "Type", "State", "Project", "Owners", "✓"]
            assert stories_pane.row_count == 1
            assert getattr(app, "_exception", None) is None
            await pilot.press("q")
    _run(main())


def test_tui_story_only_actions_guarded_outside_story_mode(db_path):
    """Story-only actions must no-op (bell) when the parent entity is not
    'story', instead of acting on another entity's highlighted id (which could
    crash with NotFound or silently edit the wrong story)."""
    from backend import db
    from backend import epics as epics_mod
    from tui.app import EntityListPane

    c = db.connect(db_path)
    p = projects.create_project(c, "backend")
    e = epics_mod.create_epic(c, "Auth epic", project_id=p.id)
    s = stories_mod.create_story(c, "Fix login bug", project_id=p.id, epic_id=e.id)
    c.close()

    async def main():
        app = PlannerApp(db_path)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()

            # Switch the parent pane to epics (key 'E'). The highlighted row is
            # now an epic id, not a story id.
            await pilot.press("E"); await pilot.pause()
            assert app.parent_entity == "epic"
            stories_pane = app.query_one("#stories", EntityListPane)
            assert stories_pane.current_id == e.id

            # Pressing 'u' (action_edit_story) must NOT edit a story: it should
            # bell and no-op, leaving no edit pane and the story untouched.
            await pilot.press("u"); await pilot.pause()
            assert app.parent_entity == "epic"
            assert app._edit_pane is None
            assert getattr(app, "_exception", None) is None

            # The same guard applies to the other story-only actions.
            app.action_move_state()
            app.action_add_comment()
            app.action_comment_action()
            app.action_add_task()
            app.action_task_action()
            app.action_manage_owners()
            app.action_manage_labels()
            app.action_manage_links()
            app.action_new_story()
            await pilot.pause()
            assert app._create_pane is None
            assert getattr(app, "_exception", None) is None

            # No story was modified.
            c = db.connect(db_path)
            updated = stories_mod.get_story(c, s.id)
            c.close()
            assert updated.name == "Fix login bug"

            await pilot.press("q")
    _run(main())


def test_tui_drill_in_out_and_related_link_navigation(db_path):
    """Story 72: Enter/Right drills into the selected parent row's children,
    Left/Esc returns to the parent level, and a focused RelatedLink in the
    detail pane jumps to that related entity."""
    from backend import db
    from backend import epics as epics_mod
    from tui.app import EntityListPane
    from tui.detail import EntityDetailPane

    c = db.connect(db_path)
    p = projects.create_project(c, "backend")
    e = epics_mod.create_epic(c, "Auth epic", project_id=p.id)
    stories_mod.create_story(c, "Fix login bug", project_id=p.id, epic_id=e.id)
    c.close()

    async def main():
        app = PlannerApp(db_path)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()

            # Root context: switch the parent pane to epics.
            await pilot.press("E"); await pilot.pause()
            parent = app.query_one("#stories", EntityListPane)
            assert app.parent_entity == "epic"
            assert app._drill_stack == []
            assert parent.current_id == e.id

            # Drill in (Enter) from the epic into its stories.
            await pilot.press("enter"); await pilot.pause()
            assert app.parent_entity == "story"
            assert app._drill_stack == [("epic", e.id, "story")]
            assert parent.row_keys == [str(s.id)
                                       for s in epics_mod.list_epic_stories(app.conn, e.id)]

            # Drill in again on the story -> its tasks (none yet, so empty).
            await pilot.press("right"); await pilot.pause()
            assert app.parent_entity == "task"
            assert app._drill_stack == [("epic", e.id, "story"),
                                        ("story", stories_mod.list_stories(app.conn)[0].id, "task")]
            assert parent.row_keys == []

            # Drill back out (Left) twice, restoring the story then epic view.
            await pilot.press("left"); await pilot.pause()
            assert app.parent_entity == "story"
            assert parent.row_keys == [str(stories_mod.list_stories(app.conn)[0].id)]
            await pilot.press("left"); await pilot.pause()
            assert app.parent_entity == "epic"
            assert app._drill_stack == []
            assert parent.row_keys == [str(e.id)]

            # A related link in a story detail jumps to its parent epic.
            await pilot.press("s"); await pilot.pause()  # back to story root
            assert app.parent_entity == "story"
            detail = app.query_one("#detail-view", EntityDetailPane)
            epic_link = next(lk for lk in detail.related_links()
                             if lk.entity == "epic")
            epic_link.focus(); await pilot.pause()
            await pilot.press("enter"); await pilot.pause()
            assert app.parent_entity == "epic"
            assert app._drill_stack == []
            assert parent.current_id == e.id
            assert "Auth epic" in _pane_text(
                app.query_one("#detail-view", EntityDetailPane))

            assert getattr(app, "_exception", None) is None
            await pilot.press("q")
    _run(main())


def test_tui_zoom_stories_two_pane_and_restore(db_path):
    """Story 73: 'z' collapses the three-pane browser to a two-pane
    master-detail (parent list + detail), re-deriving the right detail from the
    left list's selection; pressing 'z' again restores the three panes with the
    selection preserved."""
    from backend import db
    from backend import tasks as tasks_mod
    from tui.app import EntityListPane

    c = db.connect(db_path)
    p = projects.create_project(c, "backend")
    st = stories_mod.create_story(c, "Fix login bug", project_id=p.id)
    tasks_mod.create_task(c, st.id, "write a test")
    c.close()

    async def main():
        app = PlannerApp(db_path)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()

            # Three panes initially (parent list, child list, detail).
            stories = app.query_one("#stories", EntityListPane)
            children = app.query_one("#children", EntityListPane)
            assert stories.row_count == 1
            assert children.row_count == 1
            assert app.query_one("#detail")
            sid = stories.current_id
            assert sid == st.id

            # Zoom in: collapse to a two-pane master-detail on the parent list.
            await pilot.press("z"); await pilot.pause()
            assert app._zoomed is True
            assert app._zoom_left == "stories"
            assert app.query_one("#left-col").has_class("zoom-master-stories")
            # The child list is hidden, so only the parent list + detail remain.
            assert children.display is False
            # The surviving list fills the whole left column and has focus.
            assert app.focused.id == "stories"
            # The right detail shows the selected story's detail.
            assert "Fix login bug" in _pane_text(app.query_one("#detail-view"))

            # Zoom back out: restore the three-pane view and the selection.
            await pilot.press("z"); await pilot.pause()
            assert app._zoomed is False
            assert not app.query_one("#left-col").has_class("zoom-master-stories")
            assert children.display is True
            assert children.row_count == 1
            assert stories.current_id == sid
            assert "Fix login bug" in _pane_text(app.query_one("#detail-view"))

            assert getattr(app, "_exception", None) is None
            await pilot.press("q")
    _run(main())


def test_tui_zoom_children_master_detail(db_path):
    """Story 73: zooming while the child list is focused makes the child list
    the master on the left (hiding the parent list) and re-derives the right
    detail from the child list's selection."""
    from backend import db
    from backend import epics as epics_mod

    c = db.connect(db_path)
    p = projects.create_project(c, "backend")
    e = epics_mod.create_epic(c, "Auth epic", project_id=p.id)
    stories_mod.create_story(c, "Fix login bug", project_id=p.id, epic_id=e.id)
    c.close()

    async def main():
        app = PlannerApp(db_path)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            # Switch parent to epics; the child pane lists the epic's stories.
            await pilot.press("E"); await pilot.pause()
            assert app.parent_entity == "epic"
            assert app.query_one("#children").row_count == 1

            # Focus the child pane, then zoom.
            await pilot.press("tab"); await pilot.pause()
            assert app.focused.id == "children"
            await pilot.press("z"); await pilot.pause()
            assert app._zoomed is True
            assert app._zoom_left == "children"
            assert app.query_one("#left-col").has_class("zoom-master-children")
            # The parent list is hidden; the child list fills the left column.
            assert app.query_one("#stories").display is False
            assert app.focused.id == "children"
            # The right detail re-derives from the child (story) selection.
            assert "Fix login bug" in _pane_text(app.query_one("#detail-view"))

            # Zoom back out restores the three-pane view.
            await pilot.press("z"); await pilot.pause()
            assert app._zoomed is False
            assert app.query_one("#stories").display is True
            assert not app.query_one("#left-col").has_class("zoom-master-children")

            assert getattr(app, "_exception", None) is None
            await pilot.press("q")
    _run(main())
