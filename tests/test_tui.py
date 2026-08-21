"""Headless Textual pilot tests for tui/app.py.

Drives the TUI without a terminal via ``App.run_test()``. Textual is required;
these tests are skipped if it isn't importable.
"""

from __future__ import annotations

import asyncio

import pytest

textual = pytest.importorskip("textual")  # noqa: F841

from textual.widgets import Button  # noqa: E402

from tui.app import PlannerApp  # noqa: E402
from backend import projects, stories as stories_mod, comments as comments_mod  # noqa: E402


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
            app.filters = (None, None, None); app.refresh_stories(); await pilot.pause()

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