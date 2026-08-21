"""Full-screen interactive TUI for the project planner.

Built with Textual. Shares the *same* backend functions as the CLI — no separate
data layer. Layout: a filterable story list (left) + a detail pane (right), with
modal screens for create / move / comment / task / filter / search and keyboard
shortcuts shown in the footer. See CONTEXT.md §10.

Run: ``python main.py`` (no args). Requires the ``textual`` package.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    RichLog,
    Select,
    Static,
    TextArea,
)
from textual.widgets import Select as _Select

from backend import (
    comments,
    db,
    epics,
    errors,
    groups,
    iterations,
    members,
    projects,
    stories,
    tasks,
    workflows,
)

# Sentinels for "no selection" inside Select widgets (kept as int/str so all
# option values share a type and we dodge the blank-selection API).
_NONE_INT = -1
"""Integer sentinel for no selection in Select widgets."""
_NONE_STR = ""
"""String sentinel for no selection in Select widgets."""


def _sel(value: Any) -> Any:
    """Normalize a Select value: blank/sentinel -> None.

    Args:
        value: The value from a Select widget.
    Returns:
        The value if not a sentinel/blank, otherwise None.
    """
    if value is _Select.BLANK or value == _NONE_INT or value == _NONE_STR:
        return None
    return value


# --------------------------------------------------------------------------- #
# Modal screens
# --------------------------------------------------------------------------- #

class FilterScreen(ModalScreen[tuple]):
    """Collects project and state-type filters.

    Dismisses with:
        tuple: (project_id|None, state_type|None), or None on cancel.
    """

    def __init__(self, conn: sqlite3.Connection, current: tuple) -> None:
        super().__init__()
        self.conn = conn
        self.current = current  # (project_id|None, state_type|None)

    def compose(self) -> ComposeResult:
        proj_opts = [("(all projects)", _NONE_INT)]
        for p in projects.list_projects(self.conn, include_archived=True):
            proj_opts.append((p.name, p.id))
        proj_opts.append(("(archived-only view: use CLI)", _NONE_INT))  # noqa
        type_opts = [("(any state)", _NONE_STR), ("unstarted", "unstarted"),
                     ("started", "started"), ("done", "done")]
        cur_proj, cur_type = self.current
        yield Vertical(
            Static("Filter stories", classes="modal-title"),
            Label("Project:"),
            Select(proj_opts, value=cur_proj if cur_proj is not None else _NONE_INT, id="f-proj"),
            Label("State type:"),
            Select(type_opts, value=cur_type or _NONE_STR, id="f-type"),
            Horizontal(
                Button("Apply", id="ok", variant="primary"),
                Button("Clear", id="clear"),
                Button("Cancel", id="cancel"),
            ),
            classes="modal-box",
        )

    @on(Button.Pressed, "#ok")
    def _apply(self) -> None:
        proj = _sel(self.query_one("#f-proj", Select).value)
        stype = _sel(self.query_one("#f-type", Select).value)
        self.dismiss((proj, stype))

    @on(Button.Pressed, "#clear")
    def _clear(self) -> None:
        self.dismiss((None, None))

    @on(Button.Pressed, "#cancel")
    def _cancel(self) -> None:
        self.dismiss(None)


class CreateStoryScreen(ModalScreen[int]):
    """Collects name, description, type, project, owner, and state for a new story.

    Dismisses with:
        int: The new story id, or None on cancel.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        super().__init__()
        self.conn = conn

    def compose(self) -> ComposeResult:
        proj_opts = [("(no project)", _NONE_INT)]
        for p in projects.list_projects(self.conn, include_archived=True):
            proj_opts.append((p.name, p.id))
        owner_opts = [("(no owner)", _NONE_INT)]
        for m in members.list_members(self.conn):
            owner_opts.append((m.name, m.id))
        # States from the default (first) workflow.
        wfs = workflows.list_workflows(self.conn)
        state_opts = [("(workflow default)", _NONE_INT)]
        if wfs:
            for s in workflows.list_workflow_states(self.conn, wfs[0].id):
                state_opts.append((f"{s.name} ({s.type})", s.id))
        yield Vertical(
            Static("New story", classes="modal-title"),
            Label("Name:"), Input(id="s-name"),
            Label("Description:"), TextArea(id="s-desc"),
            Label("Type:"), Select([("feature", "feature"), ("bug", "bug"), ("chore", "chore")],
                                   value="feature", id="s-type"),
            Label("Project:"), Select(proj_opts, value=_NONE_INT, id="s-proj"),
            Label("Owner:"), Select(owner_opts, value=_NONE_INT, id="s-owner"),
            Label("State:"), Select(state_opts, value=_NONE_INT, id="s-state"),
            Horizontal(Button("Create", id="ok", variant="primary"), Button("Cancel", id="cancel")),
            Label("", id="s-err", classes="err"),
            classes="modal-box",
        )

    def on_mount(self) -> None:
        self.query_one("#s-name", Input).focus()

    @on(Button.Pressed, "#ok")
    def _create(self) -> None:
        name = self.query_one("#s-name", Input).value.strip()
        if not name:
            self.query_one("#s-err", Label).update("Name is required.")
            return
        desc = self.query_one("#s-desc", TextArea).text
        stype = self.query_one("#s-type", Select).value
        proj = _sel(self.query_one("#s-proj", Select).value)
        owner = _sel(self.query_one("#s-owner", Select).value)
        state = _sel(self.query_one("#s-state", Select).value)
        owner_ids = [owner] if owner is not None else None
        try:
            sid = stories.create_story(
                self.conn, name, description=desc, story_type=stype,
                workflow_state_id=state, project_id=proj,
                owner_ids=owner_ids)
        except errors.PlannerError as e:
            self.query_one("#s-err", Label).update(f"error: {e}")
            return
        self.dismiss(sid)

    @on(Button.Pressed, "#cancel")
    def _cancel(self) -> None:
        self.dismiss(None)


class EditStoryScreen(ModalScreen[int]):
    """Edit an existing story's fields.

    Fields: name, description, type, state, project, epic, iteration, group,
    deadline. Nullable parents are cleared by choosing the ``(no …)`` option
    (which maps to None). State changes go through ``move_story_state`` so
    ``completed_at`` stays consistent with the done-state rule.

    Dismisses with:
        int: the edited story id, or None on cancel.
    """

    def __init__(self, conn: sqlite3.Connection, story_id: int) -> None:
        super().__init__()
        self.conn = conn
        self.story_id = story_id
        self.story = stories.get_story(conn, story_id)

    def compose(self) -> ComposeResult:
        s = self.story
        proj_opts = [("(no project)", _NONE_INT)]
        for p in projects.list_projects(self.conn, include_archived=True):
            proj_opts.append((p.name, p.id))
        epic_opts = [("(no epic)", _NONE_INT)]
        for e in epics.list_epics(self.conn):
            epic_opts.append((e.name, e.id))
        iter_opts = [("(no iteration)", _NONE_INT)]
        for it in iterations.list_iterations(self.conn):
            iter_opts.append((it.name, it.id))
        group_opts = [("(no group)", _NONE_INT)]
        for g in groups.list_groups(self.conn, include_archived=True):
            group_opts.append((g.name, g.id))
        # State: pre-select the current state; the sentinel means "leave as is".
        state_opts = [("(leave as is)", _NONE_INT)]
        for wf in workflows.list_workflows(self.conn):
            for st in workflows.list_workflow_states(self.conn, wf.id):
                state_opts.append((f"{st.name} ({st.type})", st.id))
        cur_state = s.workflow_state_id if s.workflow_state_id is not None else _NONE_INT
        yield Vertical(
            Static(f"Edit story #{s.id}", classes="modal-title"),
            Label("Name:"), Input(id="e-name", value=s.name),
            Label("Description:"), TextArea(id="e-desc"),
            Label("Type:"), Select([("feature", "feature"), ("bug", "bug"), ("chore", "chore")],
                                   value=s.story_type, id="e-type"),
            Label("State:"), Select(state_opts, value=cur_state, id="e-state"),
            Label("Project:"), Select(proj_opts, value=s.project_id or _NONE_INT, id="e-proj"),
            Label("Epic:"), Select(epic_opts, value=s.epic_id or _NONE_INT, id="e-epic"),
            Label("Iteration:"), Select(iter_opts, value=s.iteration_id or _NONE_INT, id="e-iter"),
            Label("Group:"), Select(group_opts, value=s.group_id or _NONE_INT, id="e-group"),
            Label("Deadline:"), Input(id="e-deadline", value=s.deadline or ""),
            Horizontal(Button("Save", id="ok", variant="primary"), Button("Cancel", id="cancel")),
            Label("", id="e-err", classes="err"),
            classes="modal-box",
        )

    def on_mount(self) -> None:
        self.query_one("#e-desc", TextArea).text = self.story.description
        self.query_one("#e-name", Input).focus()

    def _save(self) -> None:
        name = self.query_one("#e-name", Input).value.strip()
        if not name:
            self.query_one("#e-err", Label).update("Name is required.")
            return
        desc = self.query_one("#e-desc", TextArea).text
        stype = self.query_one("#e-type", Select).value
        state = _sel(self.query_one("#e-state", Select).value)
        proj = _sel(self.query_one("#e-proj", Select).value)
        epic = _sel(self.query_one("#e-epic", Select).value)
        iteration = _sel(self.query_one("#e-iter", Select).value)
        group = _sel(self.query_one("#e-group", Select).value)
        deadline = self.query_one("#e-deadline", Input).value.strip() or None
        try:
            stories.update_story(
                self.conn, self.story_id, name=name, description=desc,
                story_type=stype, project_id=proj, epic_id=epic,
                iteration_id=iteration, group_id=group, deadline=deadline)
            # State changes go through move_story_state so completed_at is handled.
            if state is not None and state != self.story.workflow_state_id:
                stories.move_story_state(self.conn, self.story_id, state)
        except errors.PlannerError as e:
            self.query_one("#e-err", Label).update(f"error: {e}")
            return
        self.dismiss(self.story_id)

    @on(Button.Pressed, "#ok")
    def _ok(self) -> None:
        self._save()

    @on(Button.Pressed, "#cancel")
    def _cancel(self) -> None:
        self.dismiss(None)


class MoveStateScreen(ModalScreen[int]):
    """Collects a new workflow state for the selected story.

    Dismisses with:
        int: The new state id, or None on cancel.
    """

    def __init__(self, conn: sqlite3.Connection, story_id: int) -> None:
        super().__init__()
        self.conn = conn
        self.story_id = story_id

    def compose(self) -> ComposeResult:
        wfs = workflows.list_workflows(self.conn)
        opts: list[tuple[str, int]] = []
        for wf in wfs:
            for s in workflows.list_workflow_states(self.conn, wf.id):
                opts.append((f"{s.name} ({s.type})", s.id))
        yield Vertical(
            Static("Move story to state", classes="modal-title"),
            Select(opts, value=opts[0][1] if opts else _NONE_INT, id="m-state"),
            Horizontal(Button("Move", id="ok", variant="primary"), Button("Cancel", id="cancel")),
            classes="modal-box",
        )

    @on(Button.Pressed, "#ok")
    def _move(self) -> None:
        sid = _sel(self.query_one("#m-state", Select).value)
        if sid is None:
            self.dismiss(None)
            return
        stories.move_story_state(self.conn, self.story_id, sid)
        self.dismiss(sid)


class TextScreen(ModalScreen[str]):
    """Collects multi-line text via a TextArea (used for comments and tasks).

    Dismisses with:
        str: The entered text, or None on cancel.
    """

    def __init__(self, title: str) -> None:
        super().__init__()
        self.title_text = title

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static(self.title_text, classes="modal-title"),
            TextArea(id="t-body"),
            Horizontal(Button("Submit", id="ok", variant="primary"), Button("Cancel", id="cancel")),
            classes="modal-box",
        )

    def on_mount(self) -> None:
        self.query_one("#t-body", TextArea).focus()

    def _submit(self) -> None:
        self.dismiss(self.query_one("#t-body", TextArea).text)

    @on(Button.Pressed, "#ok")
    def _ok(self) -> None:
        self._submit()

    @on(Button.Pressed, "#cancel")
    def _cancel(self) -> None:
        self.dismiss(None)


class SearchInputScreen(ModalScreen[str]):
    """Collects a search query string.

    Dismisses with:
        str: The search query, or None on cancel.
    """

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static("Search stories (name/description)", classes="modal-title"),
            Input(id="q", placeholder="login OR auth"),
            Horizontal(Button("Search", id="ok", variant="primary"), Button("Cancel", id="cancel")),
            classes="modal-box",
        )

    def on_mount(self) -> None:
        self.query_one("#q", Input).focus()

    @on(Button.Pressed, "#ok")
    def _ok(self) -> None:
        self.dismiss(self.query_one("#q", Input).value)

    @on(Button.Pressed, "#cancel")
    def _cancel(self) -> None:
        self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)


class ConfirmScreen(ModalScreen[bool]):
    """Collects a yes/no confirmation.

    Dismisses with:
        bool: True if confirmed, False otherwise.
    """

    def __init__(self, message: str) -> None:
        super().__init__()
        self.message = message

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static(self.message, classes="modal-title"),
            Horizontal(Button("Delete", id="yes", variant="error"), Button("Cancel", id="cancel")),
            classes="modal-box",
        )

    @on(Button.Pressed, "#yes")
    def _yes(self) -> None:
        self.dismiss(True)

    @on(Button.Pressed, "#cancel")
    def _cancel(self) -> None:
        self.dismiss(False)


class TaskActionScreen(ModalScreen[bool]):
    """Toggle completion or edit the description of a task on a story.

    The task is chosen from a Select of the story's tasks. "Toggle" flips
    ``complete`` (stamping/clearing ``completed_at``); "Save Desc" updates the
    description from the TextArea.

    Dismisses with:
        bool: True if a change was made, None on cancel.
    """

    def __init__(self, conn: sqlite3.Connection, story_id: int) -> None:
        super().__init__()
        self.conn = conn
        self.story_id = story_id

    def compose(self) -> ComposeResult:
        opts = [(f"[{'x' if t.complete else ' '}] #{t.id} {t.description}", t.id)
                for t in tasks.list_tasks(self.conn, self.story_id)]
        if not opts:
            opts = [("(no tasks)", _NONE_INT)]
        yield Vertical(
            Static("Task actions", classes="modal-title"),
            Label("Task:"), Select(opts, value=opts[0][1], id="ta-task"),
            Label("New description:"), TextArea(id="ta-desc"),
            Horizontal(Button("Toggle", id="toggle", variant="primary"),
                       Button("Save Desc", id="save"), Button("Cancel", id="cancel")),
            Label("", id="ta-err", classes="err"),
            classes="modal-box",
        )

    def on_mount(self) -> None:
        self.query_one("#ta-task", Select).focus()

    def _task_id(self) -> int | None:
        return _sel(self.query_one("#ta-task", Select).value)

    @on(Button.Pressed, "#toggle")
    def _toggle(self) -> None:
        tid = self._task_id()
        if tid is None:
            self.query_one("#ta-err", Label).update("No task to toggle.")
            return
        t = tasks.get_task(self.conn, tid)
        tasks.complete_task(self.conn, tid, complete=not bool(t.complete))
        self.dismiss(True)

    @on(Button.Pressed, "#save")
    def _save(self) -> None:
        tid = self._task_id()
        if tid is None:
            self.query_one("#ta-err", Label).update("No task to edit.")
            return
        desc = self.query_one("#ta-desc", TextArea).text.strip()
        if not desc:
            self.query_one("#ta-err", Label).update("Description is required.")
            return
        tasks.update_task(self.conn, tid, description=desc)
        self.dismiss(True)

    @on(Button.Pressed, "#cancel")
    def _cancel(self) -> None:
        self.dismiss(None)


# --------------------------------------------------------------------------- #
# Main app
# --------------------------------------------------------------------------- #

class PlannerApp(App):
    """Full-screen project planner TUI."""

    CSS = """
    #filter-bar { background: $panel; height: 1; padding: 0 1; color: $text-muted; }
    #stories { width: 1fr; border: solid $primary; }
    #detail { width: 1fr; border: solid $accent; }
    .modal-box {
        width: 64; height: auto; max-height: 80%;
        background: $panel; border: solid $primary; padding: 1 2;
    }
    .modal-title { text-style: bold; margin-bottom: 1; }
    .err { color: $error; }
    TextArea { height: 6; }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("n", "new_story", "New"),
        Binding("u", "edit_story", "Update"),
        Binding("m", "move_state", "Move"),
        Binding("c", "add_comment", "Comment"),
        Binding("t", "add_task", "Task"),
        Binding("x", "task_action", "Task⇄"),
        Binding("f", "filter", "Filter"),
        Binding("slash", "search", "Search"),  # '/'
        Binding("r", "refresh", "Refresh"),
        Binding("d", "delete_story", "Delete"),
        Binding("e", "toggle_complete", "Complete"),
    ]

    def __init__(self, db_path: str | None = None) -> None:
        super().__init__()
        self.db_path = db_path
        self.conn: sqlite3.Connection | None = None
        # Active filters: (project_id|None, state_type|None, search_q|None)
        self.filters: tuple = (None, None, None)

    # --- lifecycle --------------------------------------------------------- #
    def on_mount(self) -> None:
        self.conn = db.connect(self.db_path)
        self.title = "Project Planner"
        self.refresh_stories()

    def on_unmount(self) -> None:
        if self.conn is not None:
            self.conn.close()

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("(all stories)", id="filter-bar")
        with Horizontal():
            with Vertical(id="list-pane"):
                yield DataTable(id="stories", cursor_type="row")
            yield RichLog(id="detail", wrap=True, markup=True)
        yield Footer()

    # --- story list -------------------------------------------------------- #
    def refresh_stories(self) -> None:
        """Query stories based on active filters and populate the DataTable.

        The filters tuple shape is (project_id|None, state_type|None, search_q|None).
        """
        assert self.conn is not None
        proj, stype, q = self.filters
        items = stories.list_stories(self.conn, project_id=proj, state_type=stype, q=q)
        table = self.query_one("#stories", DataTable)
        table.clear(columns=True)
        if not table.columns:
            table.add_columns("ID", "Name", "Type", "State", "Project", "Owners", "✓")
        for s in items:
            state = ""
            if s.workflow_state_id is not None:
                row = self.conn.execute("SELECT name FROM workflow_state WHERE id = ?",
                                        (s.workflow_state_id,)).fetchone()
                state = row["name"] if row else ""
            projname = ""
            if s.project_id is not None:
                row = self.conn.execute("SELECT name FROM project WHERE id = ?",
                                        (s.project_id,)).fetchone()
                projname = row["name"] if row else ""
            owners = ",".join(m["mention_name"] for m in self.conn.execute(
                "SELECT m.mention_name AS mention_name FROM member m "
                "JOIN story_owner so ON so.member_id = m.id WHERE so.story_id = ?", (s.id,)))
            table.add_row(str(s.id), s.name, s.story_type, state, projname,
                          owners, "✓" if s.completed_at else "", key=str(s.id))
        # Filter-bar caption.
        parts = []
        parts.append(f"project={'any' if proj is None else self.name_of('project', proj)}")
        parts.append(f"state={'any' if stype is None else stype}")
        parts.append(f"q={'-' if q is None else q!r}")
        parts.append(f"  ({len(items)} stories)")
        self.query_one("#filter-bar", Static).update("  ".join(parts))
        # Keep a selection; refresh detail for the cursor row.
        if items:
            if table.cursor_coordinate is None or table.cursor_row < 0:
                table.cursor_coordinate = (0, 0)  # type: ignore[assignment]
            self.show_current_detail()
        else:
            log = self.query_one("#detail", RichLog)
            log.clear()
            log.write("(no stories match the current filter — press 'n' to create one)")

    def name_of(self, table: str, id: int) -> str:
        """Look up a name for a given ID in the specified table.

        Note: named `name_of` to avoid collision with Textual's `Widget._name`.

        Args:
            table: Table name (e.g., 'project', 'epic').
            id: The primary key ID.
        Returns:
            The name string if found, otherwise the ID as a string.
        """
        assert self.conn is not None
        row = self.conn.execute(f'SELECT name FROM "{table}" WHERE id = ?', (id,)).fetchone()
        return row["name"] if row else str(id)

    def _current_story_id(self) -> int | None:
        """Retrieve the ID of the story currently highlighted in the DataTable.

        Returns:
            The story ID as an int, or None if no row is selected.
        """
        table = self.query_one("#stories", DataTable)
        try:
            key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
        except Exception:
            return None
        if key is None or key.value is None:
            return None
        try:
            return int(key.value)
        except (TypeError, ValueError):
            return None

    def show_current_detail(self) -> None:
        """Render the highlighted story's full details into the RichLog.
        """
        sid = self._current_story_id()
        if sid is None:
            return
        assert self.conn is not None
        try:
            detail = stories.get_story_detail(self.conn, sid)
        except errors.NotFound:
            return
        log = self.query_one("#detail", RichLog)
        log.clear()
        s = detail.story
        log.write(f"[bold]#{s.id}  {s.name}[/bold]  [{s.story_type}]")
        st = detail.workflow_state
        log.write(f"state: {st.name} ({st.type})" if st else "state: (none)")
        log.write(f"project: {self.name_of('project', s.project_id) if s.project_id else '-'}"
                  f"   epic: {self.name_of('epic', s.epic_id) if s.epic_id else '-'}"
                  f"   iteration: {self.name_of('iteration', s.iteration_id) if s.iteration_id else '-'}")
        log.write(f"owners: {', '.join(o.name for o in detail.owners) or '-'}"
                  f"   labels: {', '.join(lb.name for lb in detail.labels) or '-'}")
        if s.description:
            log.write(f"desc: {s.description}")
        log.write("tasks:")
        if detail.tasks:
            for t in detail.tasks:
                log.write(f"  [{'x' if t.complete else ' '}] #{t.id} {t.description}")
        else:
            log.write("  (none)")
        log.write("comments:")
        cms = comments.list_comments(self.conn, sid)
        if cms:
            for cm in cms:
                author = self.name_of("member", cm.author_id) if cm.author_id else "-"
                indent = "    " if cm.parent_id else "  "
                log.write(f"{indent}#{cm.id} {author}: {cm.text}")
        else:
            log.write("  (none)")
        if s.completed_at:
            log.write(f"[green]completed: {s.completed_at}[/green]")

    @on(DataTable.RowHighlighted)
    def _on_highlight(self, event: DataTable.RowHighlighted) -> None:
        if event.data_table.id == "stories":
            self.show_current_detail()

    # --- actions ----------------------------------------------------------- #
    def action_refresh(self) -> None:
        """Refresh the story list based on current filters."""
        self.refresh_stories()

    def action_new_story(self) -> None:
        """Open modal to create a new story."""
        assert self.conn is not None
        self.push_screen(CreateStoryScreen(self.conn), self._after_new)

    def _after_new(self, sid: int | None) -> None:
        if sid is None:
            return
        self.refresh_stories()
        # Select the newly created row.
        table = self.query_one("#stories", DataTable)
        try:
            table.move_cursor(row=table.get_row_index(str(sid)))
        except Exception:
            pass
        self.show_current_detail()

    def action_edit_story(self) -> None:
        """Open a modal to edit the selected story's fields."""
        assert self.conn is not None
        sid = self._current_story_id()
        if sid is None:
            self.bell()
            return
        try:
            self.push_screen(EditStoryScreen(self.conn, sid), self._after_edit)
        except errors.NotFound:
            self.bell()

    def _after_edit(self, sid: int | None) -> None:
        if sid is None:
            return
        self.refresh_stories()
        # Keep the cursor on the edited story.
        table = self.query_one("#stories", DataTable)
        try:
            table.move_cursor(row=table.get_row_index(str(sid)))
        except Exception:
            pass
        self.show_current_detail()

    def action_move_state(self) -> None:
        """Open modal to change the workflow state of the selected story."""
        assert self.conn is not None
        sid = self._current_story_id()
        if sid is None:
            self.bell()
            return
        self.push_screen(MoveStateScreen(self.conn, sid), lambda _: self.refresh_stories())

    def action_add_comment(self) -> None:
        """Open modal to add a comment to the selected story."""
        sid = self._current_story_id()
        if sid is None or self.conn is None:
            self.bell()
            return
        self.push_screen(TextScreen("Add comment"),
                         lambda text: self._do_comment(sid, text))

    def action_add_task(self) -> None:
        """Open modal to add a task to the selected story."""
        sid = self._current_story_id()
        if sid is None or self.conn is None:
            self.bell()
            return
        self.push_screen(TextScreen("Add task"),
                         lambda text: self._do_task(sid, text))

    def action_task_action(self) -> None:
        """Open modal to toggle completion or edit a task on the selected story."""
        sid = self._current_story_id()
        if sid is None or self.conn is None:
            self.bell()
            return
        self.push_screen(TaskActionScreen(self.conn, sid),
                         lambda changed: self.show_current_detail() if changed else None)

    def _do_comment(self, sid: int, text: str | None) -> None:
        if not text or not text.strip() or self.conn is None:
            return
        comments.create_comment(self.conn, sid, text.strip())
        self.show_current_detail()

    def _do_task(self, sid: int, text: str | None) -> None:
        if not text or not text.strip() or self.conn is None:
            return
        tasks.create_task(self.conn, sid, text.strip())
        self.show_current_detail()

    def action_filter(self) -> None:
        """Open modal to adjust project and state filters."""
        assert self.conn is not None
        proj, stype, _q = self.filters
        self.push_screen(FilterScreen(self.conn, (proj, stype)), self._after_filter)

    def _after_filter(self, result: tuple | None) -> None:
        if result is None:
            return
        proj, stype = result
        self.filters = (proj, stype, self.filters[2])
        self.refresh_stories()

    def action_search(self) -> None:
        """Open modal to search stories by keyword."""
        self.push_screen(SearchInputScreen(), self._after_search)

    def _after_search(self, q: str | None) -> None:
        if q is None:
            return
        self.filters = (self.filters[0], self.filters[1], q or None)
        self.refresh_stories()

    def action_delete_story(self) -> None:
        """Open confirmation modal to delete the selected story."""
        sid = self._current_story_id()
        if sid is None:
            self.bell()
            return
        name = self.name_of("story", sid) if self.conn else str(sid)
        self.push_screen(ConfirmScreen(f"Delete story #{sid} ({name})?"),
                         lambda ok: self._after_delete(sid, ok))

    def _after_delete(self, sid: int, ok: bool | None) -> None:
        if not ok:
            return
        assert self.conn is not None
        stories.delete_story(self.conn, sid)
        self.refresh_stories()

    def action_toggle_complete(self) -> None:
        """Toggle the selected story between 'done' and 'unstarted' states.
        """
        sid = self._current_story_id()
        if sid is None or self.conn is None:
            self.bell()
            return
        s = stories.get_story(self.conn, sid)
        # Toggle between a 'done' state and an 'unstarted' state in the default workflow.
        if s.completed_at:
            # move back to an unstarted state
            target = self._state_of_type("unstarted") or self._state_of_type("started")
        else:
            target = self._state_of_type("done")
        if target is None:
            self.bell()
            return
        stories.move_story_state(self.conn, sid, target)
        self.refresh_stories()

    def _state_of_type(self, stype: str) -> int | None:
        assert self.conn is not None
        row = self.conn.execute(
            "SELECT id FROM workflow_state WHERE type = ? ORDER BY id LIMIT 1", (stype,)).fetchone()
        return row["id"] if row else None


def run(db_path: str | None = None) -> int:
    """Entry point for the TUI app used by main.py.

    Args:
        db_path: Path to the SQLite database.
    Returns:
        Exit code (0 for success).
    """
    PlannerApp(db_path).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
