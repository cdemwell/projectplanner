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

from rich.style import Style
from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    Checkbox,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    OptionList,
    RichLog,
    Select,
    Static,
    TextArea,
)
from textual.widgets import Select as _Select
from textual.widgets.option_list import Option

from backend import (
    comments,
    db,
    epics,
    errors,
    groups,
    iterations,
    labels,
    members,
    milestones,
    projects,
    stories,
    story_links,
    tasks,
    workflows,
)
from backend.models import StoryComment

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


# Command palette entries: (display label, action method name).
# These mirror the app's actions so the palette can dispatch to them directly.
_PALETTE_COMMANDS: list[tuple[str, str]] = [
    ("New story", "new_story"),
    ("Update story", "edit_story"),
    ("Move state", "move_state"),
    ("Add comment", "add_comment"),
    ("Comment action", "comment_action"),
    ("Add task", "add_task"),
    ("Task action", "task_action"),
    ("Manage owners", "manage_owners"),
    ("Manage labels", "manage_labels"),
    ("Manage links", "manage_links"),
    ("Filter", "filter"),
    ("Browse", "browse"),
    ("Search", "search"),
    ("Manage workflows", "manage_workflows"),
    ("Manage epics", "manage_epics"),
    ("Manage iterations", "manage_iterations"),
    ("Manage milestones", "manage_milestones"),
    ("Manage projects", "manage_projects"),
    ("Manage label catalog", "manage_label_catalog"),
    ("Manage member roster", "manage_member_catalog"),
    ("Manage groups", "manage_group_catalog"),
    ("Toggle complete", "toggle_complete"),
    ("Delete story", "delete_story"),
    ("Refresh", "refresh"),
    ("Move down", "move_down"),
    ("Move up", "move_up"),
    ("Toggle auto-refresh", "toggle_auto_refresh"),
    ("Quit", "quit"),
]


# --------------------------------------------------------------------------- #
# Modal screens
# --------------------------------------------------------------------------- #

class FilterScreen(ModalScreen[tuple]):
    """Collects project, state-type, owner, and label filters.

    Dismisses with:
        tuple: (project_id|None, state_types|list[str], owner_id|None, label_id|None),
               or None on cancel.
    """

    def __init__(self, conn: sqlite3.Connection, current: tuple) -> None:
        super().__init__()
        self.conn = conn
        self.current = current  # (project_id|None, state_types|list[str], owner_id|None, label_id|None)

    def compose(self) -> ComposeResult:
        proj_opts = [("(all projects)", _NONE_INT)]
        for p in projects.list_projects(self.conn, include_archived=True):
            proj_opts.append((p.name, p.id))
        proj_opts.append(("(archived-only view: use CLI)", _NONE_INT))  # noqa

        owner_opts = [("(any owner)", _NONE_INT)]
        for m in members.list_members(self.conn):
            owner_opts.append((m.name, m.id))

        label_opts = [("(any label)", _NONE_INT)]
        for lb in labels.list_labels(self.conn):
            label_opts.append((lb.name, lb.id))

        cur_proj, cur_types, cur_owner, cur_label = self.current

        yield VerticalScroll(
            Static("Filter stories", classes="modal-title"),
            Label("Project:"),
            Select(proj_opts, value=cur_proj if cur_proj is not None else _NONE_INT, id="f-proj"),
            Label("Owner:"),
            Select(owner_opts, value=cur_owner if cur_owner is not None else _NONE_INT, id="f-owner"),
            Label("Label:"),
            Select(label_opts, value=cur_label if cur_label is not None else _NONE_INT, id="f-label"),
            Label("State types:"),
            Checkbox("Unstarted", value=("unstarted" in cur_types), id="f-unstarted"),
            Checkbox("Started", value=("started" in cur_types), id="f-started"),
            Checkbox("Done", value=("done" in cur_types), id="f-done"),
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
        owner = _sel(self.query_one("#f-owner", Select).value)
        label = _sel(self.query_one("#f-label", Select).value)
        stypes = []
        if self.query_one("#f-unstarted", Checkbox).value: stypes.append("unstarted")
        if self.query_one("#f-started", Checkbox).value: stypes.append("started")
        if self.query_one("#f-done", Checkbox).value: stypes.append("done")
        self.dismiss((proj, stypes, owner, label))

    @on(Button.Pressed, "#clear")
    def _clear(self) -> None:
        self.dismiss((None, [], None, None))

    @on(Button.Pressed, "#cancel")
    def _cancel(self) -> None:
        self.dismiss(None)


class CommandPalette(ModalScreen[str]):
    """Command palette opened with Ctrl+P.

    An Input at the top fuzzy/substring-filters a list of all app commands
    below. Enter or click selects the highlighted command.

    Dismisses with:
        str: The action method name to run (e.g. 'new_story'), or None on cancel.
    """

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static("Commands", classes="modal-title"),
            Input(id="pal-input", placeholder="Type to filter commands…"),
            OptionList(*[Option(name, id=action)
                         for name, action in _PALETTE_COMMANDS],
                       id="pal-options"),
            classes="palette-box",
        )

    def on_mount(self) -> None:
        opts = self.query_one("#pal-options", OptionList)
        if opts.option_count:
            opts.highlighted = 0
        self.query_one("#pal-input", Input).focus()

    def _filtered(self, query: str) -> list[tuple[str, str]]:
        """Return palette commands whose label contains the query (case-insensitive)."""
        q = query.strip().lower()
        if not q:
            return _PALETTE_COMMANDS
        return [(name, action) for name, action in _PALETTE_COMMANDS
                if q in name.lower()]

    def _rebuild(self, query: str) -> None:
        opts = self.query_one("#pal-options", OptionList)
        opts.clear_options()
        opts.add_options([Option(name, id=action)
                          for name, action in self._filtered(query)])
        if opts.option_count:
            opts.highlighted = 0

    def _dispatch_highlighted(self) -> None:
        opts = self.query_one("#pal-options", OptionList)
        if opts.option_count == 0:
            return
        self.dismiss(opts.get_option_at_index(opts.highlighted).id)

    @on(Input.Changed, "#pal-input")
    def _on_changed(self, event: Input.Changed) -> None:
        self._rebuild(event.value)

    @on(Input.Submitted, "#pal-input")
    def _on_submitted(self, event: Input.Submitted) -> None:
        self._dispatch_highlighted()

    @on(OptionList.OptionSelected, "#pal-options")
    def _on_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)




class EditStoryPane(Vertical):
    """Edit an existing story's fields in the right detail pane.

    Mounted into the ``#detail`` container when 'u' is pressed, replacing the
    read-only detail view. Fields: name, description, type, state, project,
    epic, iteration, group, deadline. Nullable parents are cleared via the
    ``(no …)`` option (maps to None). State changes go through
    ``move_story_state`` so ``completed_at`` stays consistent with the done-state
    rule. Uses the full pane height, so it fits without a scrollbar.

    Args:
        conn: sqlite3.Connection.
        story_id: The story being edited.
        on_saved: Called with the story id after a successful save.
        on_cancelled: Called (with no args) when editing is cancelled.
    """

    def __init__(self, conn: sqlite3.Connection, story_id: int, *,
                 on_saved, on_cancelled) -> None:
        super().__init__()
        self.conn = conn
        self.story_id = story_id
        self.on_saved = on_saved
        self.on_cancelled = on_cancelled
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
        # State: pre-select the current one; the sentinel means "leave as is".
        state_opts = [("(leave as is)", _NONE_INT)]
        for wf in workflows.list_workflows(self.conn):
            for st in workflows.list_workflow_states(self.conn, wf.id):
                state_opts.append((f"{st.name} ({st.type})", st.id))
        cur_state = s.workflow_state_id if s.workflow_state_id is not None else _NONE_INT
        yield Static(f"Edit story #{s.id}", classes="detail-title")
        yield Label("Name:")
        yield Input(value=s.name, id="e-name")
        yield Label("Description:")
        yield TextArea(id="e-desc")
        yield Label("Type:")
        yield Select([("feature", "feature"), ("bug", "bug"), ("chore", "chore")],
                     value=s.story_type, id="e-type")
        yield Label("State:")
        yield Select(state_opts, value=cur_state, id="e-state")
        yield Label("Project:")
        yield Select(proj_opts, value=s.project_id or _NONE_INT, id="e-proj")
        yield Label("Epic:")
        yield Select(epic_opts, value=s.epic_id or _NONE_INT, id="e-epic")
        yield Label("Iteration:")
        yield Select(iter_opts, value=s.iteration_id or _NONE_INT, id="e-iter")
        yield Label("Group:")
        yield Select(group_opts, value=s.group_id or _NONE_INT, id="e-group")
        yield Label("Deadline:")
        yield Input(value=s.deadline or "", id="e-deadline")
        yield Horizontal(Button("Save", id="e-save", variant="primary"),
                         Button("Cancel", id="e-cancel"))
        yield Label("", id="e-err", classes="err")

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
        self.on_saved(self.story_id)

    @on(Button.Pressed, "#e-save")
    def _ok(self) -> None:
        self._save()

    @on(Button.Pressed, "#e-cancel")
    def _cancel(self) -> None:
        self.on_cancelled()


class CreateStoryPane(Vertical):
    """Collects name, description, type, project, owner, state, and labels for a new story.

    Mounted into the ``#detail`` container when 'n' is pressed.
    """

    def __init__(self, conn: sqlite3.Connection, *, on_saved, on_cancelled) -> None:
        super().__init__()
        self.conn = conn
        self.on_saved = on_saved
        self.on_cancelled = on_cancelled

    def compose(self) -> ComposeResult:
        proj_opts = [("(no project)", _NONE_INT)]
        for p in projects.list_projects(self.conn, include_archived=True):
            proj_opts.append((p.name, p.id))
        owner_opts = [("(no owner)", _NONE_INT)]
        for m in members.list_members(self.conn):
            owner_opts.append((m.name, m.id))
        wfs = workflows.list_workflows(self.conn)
        state_opts = [("(workflow default)", _NONE_INT)]
        if wfs:
            for s in workflows.list_workflow_states(self.conn, wfs[0].id):
                state_opts.append((f"{s.name} ({s.type})", s.id))

        yield Static("New story", classes="detail-title")
        yield Label("Name:")
        yield Input(id="c-name")
        yield Label("Description:")
        yield TextArea(id="c-desc")
        yield Label("Type:")
        yield Select([("feature", "feature"), ("bug", "bug"), ("chore", "chore")],
                     value="feature", id="c-type")
        yield Label("Project:")
        yield Select(proj_opts, value=_NONE_INT, id="c-proj")
        yield Label("Owner:")
        yield Select(owner_opts, value=_NONE_INT, id="c-owner")
        yield Label("State:")
        yield Select(state_opts, value=_NONE_INT, id="c-state")
        yield Label("Labels (comma-separated):")
        yield Input(id="c-labels")
        yield Horizontal(Button("Create", id="ok", variant="primary"),
                         Button("Cancel", id="c-cancel"))
        yield Label("", id="c-err", classes="err")

    def on_mount(self) -> None:
        self.query_one("#c-name", Input).focus()

    def _save(self) -> None:
        name = self.query_one("#c-name", Input).value.strip()
        if not name:
            self.query_one("#c-err", Label).update("Name is required.")
            return
        desc = self.query_one("#c-desc", TextArea).text
        stype = self.query_one("#c-type", Select).value
        proj = _sel(self.query_one("#c-proj", Select).value)
        owner = _sel(self.query_one("#c-owner", Select).value)
        state = _sel(self.query_one("#c-state", Select).value)
        labels_str = self.query_one("#c-labels", Input).value.strip()

        owner_ids = [owner] if owner is not None else None
        try:
            sid = stories.create_story(
                self.conn, name, description=desc, story_type=stype,
                workflow_state_id=state, project_id=proj,
                owner_ids=owner_ids)

            if labels_str:
                for lb_name in labels_str.split(','):
                    lb_name = lb_name.strip()
                    if lb_name:
                        # Find or create label
                        res = self.conn.execute('SELECT id FROM label WHERE name = ?', (lb_name,)).fetchone()
                        lid = res["id"] if res else labels.create_label(self.conn, lb_name).id
                        stories.add_label(self.conn, sid, lid)

        except errors.PlannerError as e:
            self.query_one("#c-err", Label).update(f"error: {e}")
            return
        self.on_saved(sid)

    @on(Button.Pressed, "#ok")
    def _ok(self) -> None:
        self._save()

    @on(Button.Pressed, "#c-cancel")
    def _cancel(self) -> None:
        self.on_cancelled()


class MoveStateScreen(ModalScreen[int]):
    """Collects a new workflow state for the selected story/stories.

    Applies the chosen state to every story in ``story_ids``. Dismisses with:
        int: The new state id, or None on cancel.
    """

    def __init__(self, conn: sqlite3.Connection, story_ids: list[int]) -> None:
        super().__init__()
        self.conn = conn
        self.story_ids = story_ids

    def compose(self) -> ComposeResult:
        wfs = workflows.list_workflows(self.conn)
        opts: list[tuple[str, int]] = []
        for wf in wfs:
            for s in workflows.list_workflow_states(self.conn, wf.id):
                opts.append((f"{s.name} ({s.type})", s.id))
        count = len(self.story_ids)
        title = "Move story to state" if count == 1 else f"Move {count} stories to state"
        yield VerticalScroll(
            Static(title, classes="modal-title"),
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
        for story_id in self.story_ids:
            stories.move_story_state(self.conn, story_id, sid)
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
        yield VerticalScroll(
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
        yield VerticalScroll(
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
        yield VerticalScroll(
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
    """Toggle completion, edit, or delete a task on a story.

    The task is chosen from a Select of the story's tasks. "Toggle" flips
    ``complete`` (stamping/clearing ``completed_at``); "Save Desc" updates the
    description from the TextArea; "Delete" removes the task after a
    confirmation via :class:`ConfirmScreen`. Backend errors are shown in the
    ``#ta-err`` label.

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
        yield VerticalScroll(
            Static("Task actions", classes="modal-title"),
            Label("Task:"), Select(opts, value=opts[0][1], id="ta-task"),
            Label("New description:"), TextArea(id="ta-desc"),
            Horizontal(Button("Toggle", id="toggle", variant="primary"),
                       Button("Save Desc", id="save"), Button("Delete", id="delete", variant="error"),
                       Button("Cancel", id="cancel")),
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

    @on(Button.Pressed, "#delete")
    def _delete(self) -> None:
        tid = self._task_id()
        if tid is None:
            self.query_one("#ta-err", Label).update("No task to delete.")
            return
        t = tasks.get_task(self.conn, tid)
        self.app.push_screen(ConfirmScreen(f"Delete task '#{t.id} {t.description}'?"),
                         lambda ok: self._do_delete(tid, ok))

    def _do_delete(self, tid: int, ok: bool | None) -> None:
        if not ok:
            return
        try:
            tasks.delete_task(self.conn, tid)
        except errors.PlannerError as e:
            self.query_one("#ta-err", Label).update(f"error: {e}")
            return
        self.dismiss(True)

    @on(Button.Pressed, "#cancel")
    def _cancel(self) -> None:
        self.dismiss(None)


class CommentActionScreen(ModalScreen[bool]):
    """Edit or delete a comment on a story.

    The comment is chosen from a Select of the story's comments. "Save Text"
    updates the comment text from the TextArea via the backend ``comments``
    module; "Delete" removes the comment after a confirmation via
    :class:`ConfirmScreen`. Backend errors are shown in the ``#ca-err`` label.

    Dismisses with:
        bool: True if a change was made, None on cancel.
    """

    def __init__(self, conn: sqlite3.Connection, story_id: int) -> None:
        super().__init__()
        self.conn = conn
        self.story_id = story_id

    def compose(self) -> ComposeResult:
        opts = [(f"#{cm.id} {cm.text}", cm.id)
                for cm in comments.list_comments(self.conn, self.story_id)]
        if not opts:
            opts = [("(no comments)", _NONE_INT)]
        yield VerticalScroll(
            Static("Comment actions", classes="modal-title"),
            Label("Comment:"), Select(opts, value=opts[0][1], id="ca-comment"),
            Label("New text:"), TextArea(id="ca-text"),
            Horizontal(Button("Save Text", id="save", variant="primary"),
                       Button("Delete", id="delete", variant="error"),
                       Button("Cancel", id="cancel")),
            Label("", id="ca-err", classes="err"),
            classes="modal-box",
        )

    def on_mount(self) -> None:
        self.query_one("#ca-comment", Select).focus()
        self._prefill()

    def _comment_id(self) -> int | None:
        return _sel(self.query_one("#ca-comment", Select).value)

    def _comment(self) -> StoryComment | None:
        cid = self._comment_id()
        if cid is None:
            return None
        try:
            return comments.get_comment(self.conn, cid)
        except errors.NotFound:
            return None

    def _prefill(self) -> None:
        """Preload the editor with the selected comment's text."""
        cm = self._comment()
        if cm is not None:
            self.query_one("#ca-text", TextArea).text = cm.text

    @on(Select.Changed, "#ca-comment")
    def _on_pick(self) -> None:
        self._prefill()

    @on(Button.Pressed, "#save")
    def _save(self) -> None:
        cm = self._comment()
        if cm is None:
            self.query_one("#ca-err", Label).update("No comment to edit.")
            return
        text = self.query_one("#ca-text", TextArea).text.strip()
        if not text:
            self.query_one("#ca-err", Label).update("Comment text is required.")
            return
        try:
            comments.update_comment(self.conn, cm.id, text=text)
        except errors.PlannerError as e:
            self.query_one("#ca-err", Label).update(f"error: {e}")
            return
        self.dismiss(True)

    @on(Button.Pressed, "#delete")
    def _delete(self) -> None:
        cm = self._comment()
        if cm is None:
            self.query_one("#ca-err", Label).update("No comment to delete.")
            return
        self.app.push_screen(ConfirmScreen(f"Delete comment '#{cm.id}'?"),
                             lambda ok: self._do_delete(cm.id, ok))

    def _do_delete(self, cid: int, ok: bool | None) -> None:
        if not ok:
            return
        try:
            comments.delete_comment(self.conn, cid)
        except errors.PlannerError as e:
            self.query_one("#ca-err", Label).update(f"error: {e}")
            return
        self.dismiss(True)

    @on(Button.Pressed, "#cancel")
    def _cancel(self) -> None:
        self.dismiss(None)


class LinkAddScreen(ModalScreen[tuple]):
    """Collect a target story and verb to link from the current story.

    The current story is the link's *subject*; the chosen story becomes the
    *object* of a directed ``subject --verb--> object`` link. Every other
    story is offered as a target. Dismisses with a ``(object_story_id, verb)``
    tuple, or None on cancel.
    """

    def __init__(self, conn: sqlite3.Connection, subject_id: int) -> None:
        super().__init__()
        self.conn = conn
        self.subject_id = subject_id

    def compose(self) -> ComposeResult:
        story_opts = [
            (f"#{s.id} {s.name}", s.id)
            for s in stories.list_stories(self.conn)
            if s.id != self.subject_id
        ]
        if not story_opts:
            story_opts = [("(no other stories)", _NONE_INT)]
        verb_opts = [(v, v) for v in story_links.VERBS]
        yield VerticalScroll(
            Static("Add story link", classes="modal-title"),
            Label("To story:"),
            Select(story_opts, value=story_opts[0][1], id="la-object"),
            Label("Verb:"),
            Select(verb_opts, value="blocks", id="la-verb"),
            Horizontal(Button("Add", id="ok", variant="primary"),
                       Button("Cancel", id="cancel")),
            Label("", id="la-err", classes="err"),
            classes="modal-box",
        )

    def on_mount(self) -> None:
        self.query_one("#la-object", Select).focus()

    @on(Button.Pressed, "#ok")
    def _add(self) -> None:
        obj = _sel(self.query_one("#la-object", Select).value)
        if obj is None:
            self.query_one("#la-err", Label).update("No target story.")
            return
        self.dismiss((obj, self.query_one("#la-verb", Select).value))

    @on(Button.Pressed, "#cancel")
    def _cancel(self) -> None:
        self.dismiss(None)


class StoryLinkActionScreen(ModalScreen[bool]):
    """Add or delete a directed link involving the selected story.

    Lists the story's links (as subject or object) in a Select. "Add" opens
    :class:`LinkAddScreen` to create a link *from* this story to another with a
    verb; "Delete" removes the selected link after a confirmation via
    :class:`ConfirmScreen`. All operations go through the backend
    ``story_links`` module; backend errors surface in the ``#sl-err`` label.

    Dismisses with:
        bool: True if a change was made (caller refreshes), else None.
    """

    def __init__(self, conn: sqlite3.Connection, story_id: int) -> None:
        super().__init__()
        self.conn = conn
        self.story_id = story_id

    def _link_opts(self) -> list[tuple[str, int]]:
        links = story_links.list_links(self.conn, self.story_id)
        return [(self._render_link(lk), lk.id) for lk in links]

    def _render_link(self, lk) -> str:
        subj = self._story_name(lk.subject_story_id)
        obj = self._story_name(lk.object_story_id)
        return f"#{lk.id} {subj} --{lk.verb}--> {obj}"

    def _story_name(self, sid: int) -> str:
        row = self.conn.execute(
            'SELECT name FROM "story" WHERE id = ?', (sid,)).fetchone()
        return row["name"] if row else str(sid)

    def compose(self) -> ComposeResult:
        opts = self._link_opts()
        if not opts:
            opts = [("(no links)", _NONE_INT)]
        yield VerticalScroll(
            Static("Story links", classes="modal-title"),
            Label("Link:"), Select(opts, value=opts[0][1], id="sl-link"),
            Horizontal(Button("Add", id="add", variant="primary"),
                       Button("Delete", id="delete", variant="error"),
                       Button("Done", id="cancel")),
            Label("", id="sl-err", classes="err"),
            classes="modal-box",
        )

    def on_mount(self) -> None:
        self.query_one("#sl-link", Select).focus()

    def _link_id(self) -> int | None:
        return _sel(self.query_one("#sl-link", Select).value)

    def _add(self) -> None:
        self.app.push_screen(LinkAddScreen(self.conn, self.story_id),
                             self._do_add)

    def _do_add(self, res: tuple | None) -> None:
        if res is None:
            return
        obj, verb = res
        try:
            story_links.create_link(self.conn, self.story_id, verb, obj)
        except errors.PlannerError as e:
            self.query_one("#sl-err", Label).update(f"error: {e}")
            return
        self.dismiss(True)

    @on(Button.Pressed, "#delete")
    def _delete(self) -> None:
        lid = self._link_id()
        if lid is None:
            self.query_one("#sl-err", Label).update("No link to delete.")
            return
        self.app.push_screen(ConfirmScreen(f"Delete story link #{lid}?"),
                             lambda ok: self._do_delete(lid, ok))

    def _do_delete(self, lid: int, ok: bool | None) -> None:
        if not ok:
            return
        try:
            story_links.delete_link(self.conn, lid)
        except errors.PlannerError as e:
            self.query_one("#sl-err", Label).update(f"error: {e}")
            return
        self.dismiss(True)

    @on(Button.Pressed, "#add")
    def _b_add(self) -> None:
        self._add()

    @on(Button.Pressed, "#cancel")
    def _done(self) -> None:
        self.dismiss(None)


class OwnerScreen(ModalScreen[bool]):
    """Toggle a member's ownership of the selected story.

    Lists every member, marking current owners. "Toggle" adds the selected
    member if they aren't an owner, or removes them if they are.

    Dismisses with:
        bool: True if a change was made, None on cancel/Done.
    """

    def __init__(self, conn: sqlite3.Connection, story_id: int) -> None:
        super().__init__()
        self.conn = conn
        self.story_id = story_id

    def _owner_ids(self) -> set[int]:
        return {o.id for o in stories.list_owners(self.conn, self.story_id)}

    def compose(self) -> ComposeResult:
        ids = self._owner_ids()
        opts = [(f"{m.name}{' (owner)' if m.id in ids else ''}", m.id)
                for m in members.list_members(self.conn)]
        if not opts:
            opts = [("(no members)", _NONE_INT)]
        yield VerticalScroll(
            Static("Manage owners", classes="modal-title"),
            Select(opts, value=opts[0][1], id="o-member"),
            Horizontal(Button("Toggle", id="toggle", variant="primary"), Button("Done", id="cancel")),
            Label("", id="o-status", classes="err"),
            classes="modal-box",
        )

    def on_mount(self) -> None:
        self.query_one("#o-member", Select).focus()

    @on(Button.Pressed, "#toggle")
    def _toggle(self) -> None:
        mid = _sel(self.query_one("#o-member", Select).value)
        if mid is None:
            return
        name = self.name_of_member(mid)
        if mid in self._owner_ids():
            stories.remove_owner(self.conn, self.story_id, mid)
            self.query_one("#o-status", Label).update(f"Removed owner: {name}")
        else:
            stories.assign_owner(self.conn, self.story_id, mid)
            self.query_one("#o-status", Label).update(f"Added owner: {name}")
        self.dismiss(True)

    def name_of_member(self, mid: int) -> str:
        row = self.conn.execute('SELECT name FROM "member" WHERE id = ?', (mid,)).fetchone()
        return row["name"] if row else str(mid)

    @on(Button.Pressed, "#cancel")
    def _done(self) -> None:
        self.dismiss(None)


class LabelScreen(ModalScreen[bool]):
    """Toggle a label on the selected story.

    Lists every label, marking those already applied. "Toggle" adds or removes
    the selected label.

    Dismisses with:
        bool: True if a change was made, None on cancel/Done.
    """

    def __init__(self, conn: sqlite3.Connection, story_id: int) -> None:
        super().__init__()
        self.conn = conn
        self.story_id = story_id

    def _label_ids(self) -> set[int]:
        return {lb.id for lb in stories.list_story_labels(self.conn, self.story_id)}

    def compose(self) -> ComposeResult:
        ids = self._label_ids()
        opts = [(f"{lb.name}{' (on)' if lb.id in ids else ''}", lb.id)
                for lb in labels.list_labels(self.conn)]
        if not opts:
            opts = [("(no labels)", _NONE_INT)]
        yield VerticalScroll(
            Static("Manage labels", classes="modal-title"),
            Select(opts, value=opts[0][1], id="lb-label"),
            Horizontal(Button("Toggle", id="toggle", variant="primary"), Button("Done", id="cancel")),
            Label("", id="lb-status", classes="err"),
            classes="modal-box",
        )

    def on_mount(self) -> None:
        self.query_one("#lb-label", Select).focus()

    @on(Button.Pressed, "#toggle")
    def _toggle(self) -> None:
        lid = _sel(self.query_one("#lb-label", Select).value)
        if lid is None:
            return
        name = self.name_of_label(lid)
        if lid in self._label_ids():
            stories.remove_label(self.conn, self.story_id, lid)
            self.query_one("#lb-status", Label).update(f"Removed label: {name}")
        else:
            stories.add_label(self.conn, self.story_id, lid)
            self.query_one("#lb-status", Label).update(f"Added label: {name}")
        self.dismiss(True)

    def name_of_label(self, lid: int) -> str:
        row = self.conn.execute('SELECT name FROM "label" WHERE id = ?', (lid,)).fetchone()
        return row["name"] if row else str(lid)

    @on(Button.Pressed, "#cancel")
    def _done(self) -> None:
        self.dismiss(None)


class AssignOwnerScreen(ModalScreen[int]):
    """Pick a member to assign as owner of every selected story.

    Dismisses with:
        int: The member id to assign, or None on cancel.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        super().__init__()
        self.conn = conn

    def compose(self) -> ComposeResult:
        opts = [(m.name, m.id) for m in members.list_members(self.conn)]
        if not opts:
            opts = [("(no members)", _NONE_INT)]
        yield VerticalScroll(
            Static("Assign owner to selected", classes="modal-title"),
            Select(opts, value=opts[0][1], id="a-member"),
            Horizontal(Button("Assign", id="ok", variant="primary"),
                       Button("Cancel", id="cancel")),
            classes="modal-box",
        )

    @on(Button.Pressed, "#ok")
    def _assign(self) -> None:
        mid = _sel(self.query_one("#a-member", Select).value)
        if mid is None:
            self.dismiss(None)
            return
        self.dismiss(mid)


class AssignLabelScreen(ModalScreen[int]):
    """Pick a label to add to every selected story.

    Dismisses with:
        int: The label id to add, or None on cancel.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        super().__init__()
        self.conn = conn

    def compose(self) -> ComposeResult:
        opts = [(lb.name, lb.id) for lb in labels.list_labels(self.conn)]
        if not opts:
            opts = [("(no labels)", _NONE_INT)]
        yield VerticalScroll(
            Static("Add label to selected", classes="modal-title"),
            Select(opts, value=opts[0][1], id="a-label"),
            Horizontal(Button("Add", id="ok", variant="primary"),
                       Button("Cancel", id="cancel")),
            classes="modal-box",
        )

    @on(Button.Pressed, "#ok")
    def _add(self) -> None:
        lid = _sel(self.query_one("#a-label", Select).value)
        if lid is None:
            self.dismiss(None)
            return
        self.dismiss(lid)


class BrowseMenuScreen(ModalScreen[str]):
    """Choose which container entity to browse.

    Dismisses with one of 'project'/'epic'/'iteration'/'milestone', or None.
    """

    def compose(self) -> ComposeResult:
        yield VerticalScroll(
            Static("Browse", classes="modal-title"),
            Horizontal(
                Button("Projects", id="project", variant="primary"),
                Button("Epics", id="epic"),
                Button("Iterations", id="iteration"),
                Button("Milestones", id="milestone"),
            ),
            Button("Cancel", id="cancel"),
            classes="modal-box",
        )

    @on(Button.Pressed)
    def _pick(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
        elif event.button.id in ("project", "epic", "iteration", "milestone"):
            self.dismiss(event.button.id)


class EntityBrowserScreen(ModalScreen[tuple]):
    """Browse a container entity and pick one to filter the story list by.

    Dismisses with (filter_key, id) — e.g. ('project', 3) — or None on cancel.
    """

    def __init__(self, conn: sqlite3.Connection, kind: str) -> None:
        super().__init__()
        self.conn = conn
        self.kind = kind

    def compose(self) -> ComposeResult:
        title = {"project": "Projects", "epic": "Epics",
                 "iteration": "Iterations", "milestone": "Milestones"}[self.kind]
        yield VerticalScroll(
            Static(f"{title} — pick to filter stories", classes="modal-title"),
            DataTable(id="browser-table", cursor_type="row"),
            Horizontal(Button("Select", id="ok", variant="primary"),
                       Button("Cancel", id="cancel")),
            classes="modal-box",
        )

    def on_mount(self) -> None:
        table = self.query_one("#browser-table", DataTable)
        rows = self._rows()
        cols = self._columns()
        table.add_columns(*cols)
        for r in rows:
            table.add_row(*[str(c) for c in r], key=str(r[0]))

    def _columns(self) -> list[str]:
        if self.kind == "project":
            return ["ID", "Name", "Archived"]
        if self.kind == "epic":
            return ["ID", "Name", "State"]
        if self.kind == "iteration":
            return ["ID", "Name", "Status"]
        return ["ID", "Name", "State"]  # milestone

    def _rows(self) -> list[tuple]:
        c = self.conn
        if self.kind == "project":
            return [(p.id, p.name, "yes" if p.archived else "")
                    for p in projects.list_projects(c, include_archived=True)]
        if self.kind == "epic":
            return [(e.id, e.name, e.state) for e in epics.list_epics(c)]
        if self.kind == "iteration":
            return [(it.id, it.name, it.status) for it in iterations.list_iterations(c)]
        return [(m.id, m.name, m.state) for m in milestones.list_milestones(c)]

    def _selected_id(self) -> int | None:
        table = self.query_one("#browser-table", DataTable)
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

    @on(Button.Pressed, "#ok")
    def _select(self) -> None:
        sid = self._selected_id()
        if sid is None:
            self.bell()
            return
        self.dismiss((self.kind, sid))

    @on(Button.Pressed, "#cancel")
    def _cancel(self) -> None:
        self.dismiss(None)


class PromptScreen(ModalScreen[str]):
    """Collects a single line of text (a name).

    Dismisses with:
        str: The entered text, or None on cancel.
    """

    def __init__(self, title: str, value: str = "") -> None:
        super().__init__()
        self._title = title
        self._value = value

    def compose(self) -> ComposeResult:
        yield VerticalScroll(
            Static(self._title, classes="modal-title"),
            Input(value=self._value, id="p-value"),
            Horizontal(Button("OK", id="ok", variant="primary"),
                       Button("Cancel", id="cancel")),
            classes="modal-box",
        )

    def on_mount(self) -> None:
        self.query_one("#p-value", Input).focus()

    @on(Button.Pressed, "#ok")
    def _ok(self) -> None:
        self.dismiss(self.query_one("#p-value", Input).value)

    @on(Button.Pressed, "#cancel")
    def _cancel(self) -> None:
        self.dismiss(None)

    @on(Input.Submitted, "#p-value")
    def _submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)


class AddStateScreen(ModalScreen[tuple]):
    """Collects a name and type for a new workflow state.

    Dismisses with:
        tuple: (name, type), or None on cancel.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        super().__init__()
        self.conn = conn

    def compose(self) -> ComposeResult:
        yield VerticalScroll(
            Static("Add workflow state", classes="modal-title"),
            Label("Name:"),
            Input(id="as-name"),
            Label("Type:"),
            Select([(t, t) for t in workflows.STATE_TYPES],
                   value="unstarted", id="as-type"),
            Horizontal(Button("Add", id="ok", variant="primary"),
                       Button("Cancel", id="cancel")),
            Label("", id="as-err", classes="err"),
            classes="modal-box",
        )

    def on_mount(self) -> None:
        self.query_one("#as-name", Input).focus()

    @on(Button.Pressed, "#ok")
    def _ok(self) -> None:
        name = self.query_one("#as-name", Input).value.strip()
        if not name:
            self.query_one("#as-err", Label).update("Name is required.")
            return
        self.dismiss((name, self.query_one("#as-type", Select).value))

    @on(Button.Pressed, "#cancel")
    def _cancel(self) -> None:
        self.dismiss(None)


class WorkflowManagerScreen(ModalScreen[bool]):
    """Manage workflows and their states.

    A workflow selector (left) lists every workflow; a state selector (right)
    lists the selected workflow's states in order. Buttons create/rename/delete
    workflows and add/rename/delete/reorder states. Every operation goes
    through the backend ``workflows`` module; destructive deletes confirm first
    via :class:`ConfirmScreen`. Backend errors are shown in a status label.

    Dismisses with:
        bool: True if any change was made (caller refreshes), else None.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        super().__init__()
        self.conn = conn
        self._dirty = False
        self._wf_ids: list[int] = []
        self._state_ids: list[int] = []

    def compose(self) -> ComposeResult:
        yield VerticalScroll(
            Static("Workflows & States", classes="modal-title"),
            Horizontal(
                Vertical(Static("Workflows", classes="modal-subtitle"),
                         OptionList(id="wm-workflows")),
                Vertical(Static("States", classes="modal-subtitle"),
                         OptionList(id="wm-states")),
            ),
            Horizontal(
                Button("New WF", id="wf-new"),
                Button("Rename WF", id="wf-rename"),
                Button("Delete WF", id="wf-delete", variant="error"),
            ),
            Horizontal(
                Button("Add State", id="st-add"),
                Button("Rename State", id="st-rename"),
                Button("Delete State", id="st-delete", variant="error"),
                Button("↑", id="st-up"),
                Button("↓", id="st-down"),
            ),
            Button("Done", id="done", variant="primary"),
            Label("", id="wm-status", classes="err"),
            classes="workflow-box",
        )

    # --- helpers ---------------------------------------------------------- #
    @staticmethod
    def _option_id(opts: OptionList) -> str | None:
        """Return the id string of the highlighted option, or None."""
        if opts.option_count == 0:
            return None
        return opts.get_option_at_index(opts.highlighted).id

    def _selected_wf(self) -> int | None:
        idx = self.query_one("#wm-workflows", OptionList).highlighted
        if idx is None or not (0 <= idx < len(self._wf_ids)):
            return None
        return self._wf_ids[idx]

    def _selected_state(self) -> int | None:
        idx = self.query_one("#wm-states", OptionList).highlighted
        if idx is None or not (0 <= idx < len(self._state_ids)):
            return None
        return self._state_ids[idx]

    def _status(self, msg: str) -> None:
        self.query_one("#wm-status", Label).update(msg)

    # --- population ------------------------------------------------------- #
    def on_mount(self) -> None:
        self._refresh_workflows()

    def _refresh_workflows(self) -> None:
        opts = self.query_one("#wm-workflows", OptionList)
        prev = self._option_id(opts)
        wfs = workflows.list_workflows(self.conn)
        self._wf_ids = [w.id for w in wfs]
        opts.clear_options()
        for w in wfs:
            opts.add_option(Option(w.name, id=str(w.id)))
        if self._wf_ids:
            idx = 0
            if prev:
                for i, wid in enumerate(self._wf_ids):
                    if str(wid) == prev:
                        idx = i
                        break
            opts.highlighted = idx
        self._refresh_states()

    def _refresh_states(self) -> None:
        opts = self.query_one("#wm-states", OptionList)
        wf_id = self._selected_wf()
        states = (workflows.list_workflow_states(self.conn, wf_id)
                  if wf_id is not None else [])
        self._state_ids = [s.id for s in states]
        opts.clear_options()
        for s in states:
            opts.add_option(Option(f"{s.name} ({s.type})", id=str(s.id)))
        if self._state_ids:
            opts.highlighted = 0

    @on(OptionList.OptionHighlighted, "#wm-workflows")
    def _on_wf_highlight(self) -> None:
        self._refresh_states()

    # --- workflow actions ------------------------------------------------- #
    def _new_workflow(self) -> None:
        self.app.push_screen(PromptScreen("New workflow name"), self._create_workflow)

    def _create_workflow(self, name: str | None) -> None:
        if not name or not name.strip():
            return
        try:
            workflows.create_workflow(self.conn, name.strip())
        except errors.PlannerError as e:
            self._status(f"error: {e}")
            return
        self._dirty = True
        self._refresh_workflows()
        self._status(f"Created workflow '{name.strip()}'")

    def _rename_workflow(self) -> None:
        wf_id = self._selected_wf()
        if wf_id is None:
            self._status("Select a workflow to rename.")
            return
        name = workflows.get_workflow(self.conn, wf_id).name
        self.app.push_screen(PromptScreen("Rename workflow", value=name),
                         lambda new: self._do_rename_workflow(wf_id, new))

    def _do_rename_workflow(self, wf_id: int, new: str | None) -> None:
        if not new or not new.strip():
            return
        try:
            workflows.update_workflow(self.conn, wf_id, name=new.strip())
        except errors.PlannerError as e:
            self._status(f"error: {e}")
            return
        self._dirty = True
        self._refresh_workflows()
        self._status(f"Renamed workflow to '{new.strip()}'")

    def _delete_workflow(self) -> None:
        wf_id = self._selected_wf()
        if wf_id is None:
            self._status("Select a workflow to delete.")
            return
        wf = workflows.get_workflow(self.conn, wf_id)
        self.app.push_screen(ConfirmScreen(f"Delete workflow '{wf.name}' and its states?"),
                         lambda ok: self._do_delete_workflow(wf_id, ok))

    def _do_delete_workflow(self, wf_id: int, ok: bool | None) -> None:
        if not ok:
            return
        try:
            workflows.delete_workflow(self.conn, wf_id)
        except errors.PlannerError as e:
            self._status(f"error: {e}")
            return
        self._dirty = True
        self._refresh_workflows()
        self._status("Deleted workflow.")

    # --- state actions ---------------------------------------------------- #
    def _add_state(self) -> None:
        wf_id = self._selected_wf()
        if wf_id is None:
            self._status("Select a workflow first.")
            return
        self.app.push_screen(AddStateScreen(self.conn),
                         lambda res: self._do_add_state(wf_id, res))

    def _do_add_state(self, wf_id: int, res: tuple | None) -> None:
        if res is None:
            return
        name, stype = res
        try:
            workflows.create_workflow_state(self.conn, wf_id, name, stype)
        except errors.PlannerError as e:
            self._status(f"error: {e}")
            return
        self._dirty = True
        self._refresh_states()
        self._status(f"Added state '{name}'.")

    def _rename_state(self) -> None:
        wf_id = self._selected_wf()
        sid = self._selected_state()
        if wf_id is None or sid is None:
            self._status("Select a workflow and state to rename.")
            return
        st = workflows.get_workflow_state(self.conn, sid)
        self.app.push_screen(PromptScreen("Rename state", value=st.name),
                         lambda new: self._do_rename_state(sid, new))

    def _do_rename_state(self, sid: int, new: str | None) -> None:
        if not new or not new.strip():
            return
        try:
            workflows.update_workflow_state(self.conn, sid, name=new.strip())
        except errors.PlannerError as e:
            self._status(f"error: {e}")
            return
        self._dirty = True
        self._refresh_states()
        self._status(f"Renamed state to '{new.strip()}'")

    def _delete_state(self) -> None:
        wf_id = self._selected_wf()
        sid = self._selected_state()
        if wf_id is None or sid is None:
            self._status("Select a workflow and state to delete.")
            return
        st = workflows.get_workflow_state(self.conn, sid)
        self.app.push_screen(ConfirmScreen(f"Delete state '{st.name}'?"),
                         lambda ok: self._do_delete_state(sid, ok))

    def _do_delete_state(self, sid: int, ok: bool | None) -> None:
        if not ok:
            return
        try:
            workflows.delete_workflow_state(self.conn, sid)
        except errors.PlannerError as e:
            self._status(f"error: {e}")
            return
        self._dirty = True
        self._refresh_states()
        self._status("Deleted state.")

    def _move_state(self, delta: int) -> None:
        wf_id = self._selected_wf()
        sid = self._selected_state()
        if wf_id is None or sid is None:
            self._status("Select a state to move.")
            return
        states = workflows.list_workflow_states(self.conn, wf_id)
        idx = next((i for i, s in enumerate(states) if s.id == sid), None)
        if idx is None:
            return
        target = idx + delta
        if target < 0 or target >= len(states):
            self._status("Already at the edge.")
            return
        a, b = states[idx], states[target]
        try:
            pa, pb = a.position, b.position
            workflows.update_workflow_state(self.conn, a.id, position=pb)
            workflows.update_workflow_state(self.conn, b.id, position=pa)
        except errors.PlannerError as e:
            self._status(f"error: {e}")
            return
        self._dirty = True
        self._refresh_states()
        opts = self.query_one("#wm-states", OptionList)
        opts.highlighted = target
        self._status(f"Moved state '{a.name}'.")

    # --- button dispatch -------------------------------------------------- #
    @on(Button.Pressed, "#wf-new")
    def _b_wf_new(self) -> None:
        self._new_workflow()

    @on(Button.Pressed, "#wf-rename")
    def _b_wf_rename(self) -> None:
        self._rename_workflow()

    @on(Button.Pressed, "#wf-delete")
    def _b_wf_delete(self) -> None:
        self._delete_workflow()

    @on(Button.Pressed, "#st-add")
    def _b_st_add(self) -> None:
        self._add_state()

    @on(Button.Pressed, "#st-rename")
    def _b_st_rename(self) -> None:
        self._rename_state()

    @on(Button.Pressed, "#st-delete")
    def _b_st_delete(self) -> None:
        self._delete_state()

    @on(Button.Pressed, "#st-up")
    def _b_st_up(self) -> None:
        self._move_state(-1)

    @on(Button.Pressed, "#st-down")
    def _b_st_down(self) -> None:
        self._move_state(1)

    @on(Button.Pressed, "#done")
    def _done(self) -> None:
        self.dismiss(self._dirty)


class EpicFormScreen(ModalScreen[dict]):
    """Collect name/description/state/project/milestone for an epic.

    Used both for creating a new epic (``epic=None``) and for editing an
    existing one (pre-populated from the given :class:`~backend.models.Epic`).
    All operations call the backend ``epics`` module. Dismisses with a dict of
    field values, or None on cancel.
    """

    def __init__(self, conn: sqlite3.Connection, epic=None) -> None:
        super().__init__()
        self.conn = conn
        self.epic = epic  # Epic | None

    def compose(self) -> ComposeResult:
        e = self.epic
        proj_opts = [("(no project)", _NONE_INT)]
        for p in projects.list_projects(self.conn, include_archived=True):
            proj_opts.append((p.name, p.id))
        ms_opts = [("(no milestone)", _NONE_INT)]
        for ms in milestones.list_milestones(self.conn):
            ms_opts.append((ms.name, ms.id))
        state_opts = [(s, s) for s in epics.STATES]
        title = f"Edit epic #{e.id}" if e else "New epic"
        yield VerticalScroll(
            Static(title, classes="modal-title"),
            Label("Name:"),
            Input(value=e.name if e else "", id="ef-name"),
            Label("Description:"),
            TextArea(id="ef-desc"),
            Label("State:"),
            Select(state_opts, value=(e.state if e else "planned"), id="ef-state"),
            Label("Project:"),
            Select(proj_opts,
                   value=(e.project_id or _NONE_INT) if e else _NONE_INT, id="ef-proj"),
            Label("Milestone:"),
            Select(ms_opts,
                   value=(e.milestone_id or _NONE_INT) if e else _NONE_INT, id="ef-ms"),
            Horizontal(Button("Save", id="ok", variant="primary"),
                       Button("Cancel", id="cancel")),
            Label("", id="ef-err", classes="err"),
            classes="modal-box",
        )

    def on_mount(self) -> None:
        if self.epic and self.epic.description:
            self.query_one("#ef-desc", TextArea).text = self.epic.description
        self.query_one("#ef-name", Input).focus()

    def _submit(self) -> None:
        name = self.query_one("#ef-name", Input).value.strip()
        if not name:
            self.query_one("#ef-err", Label).update("Name is required.")
            return
        state = self.query_one("#ef-state", Select).value
        if state not in epics.STATES:
            self.query_one("#ef-err", Label).update("Invalid state.")
            return
        self.dismiss({
            "name": name,
            "description": self.query_one("#ef-desc", TextArea).text,
            "state": state,
            "project_id": _sel(self.query_one("#ef-proj", Select).value),
            "milestone_id": _sel(self.query_one("#ef-ms", Select).value),
        })

    @on(Button.Pressed, "#ok")
    def _ok(self) -> None:
        self._submit()

    @on(Button.Pressed, "#cancel")
    def _cancel(self) -> None:
        self.dismiss(None)


class EpicManagerScreen(ModalScreen[bool]):
    """Manage epics: create, edit, and delete.

    An OptionList lists every epic; New/Edit/Delete/Done buttons drive the
    flow. Create and edit open :class:`EpicFormScreen`; delete confirms via
    :class:`ConfirmScreen`. Every operation goes through the backend ``epics``
    module; backend errors surface in a status label.

    Dismisses with:
        bool: True if any change was made (caller refreshes), else None.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        super().__init__()
        self.conn = conn
        self._dirty = False
        self._epic_ids: list[int] = []

    def compose(self) -> ComposeResult:
        yield VerticalScroll(
            Static("Manage Epics", classes="modal-title"),
            OptionList(id="em-epics"),
            Horizontal(
                Button("New", id="em-new", variant="primary"),
                Button("Edit", id="em-edit"),
                Button("Delete", id="em-delete", variant="error"),
                Button("Done", id="em-done"),
            ),
            Label("", id="em-status", classes="err"),
            classes="modal-box",
        )

    def _status(self, msg: str) -> None:
        self.query_one("#em-status", Label).update(msg)

    def _selected_id(self) -> int | None:
        idx = self.query_one("#em-epics", OptionList).highlighted
        if idx is None or not (0 <= idx < len(self._epic_ids)):
            return None
        return self._epic_ids[idx]

    def on_mount(self) -> None:
        self._refresh()

    def _refresh(self) -> None:
        opts = self.query_one("#em-epics", OptionList)
        prev = opts.get_option_at_index(opts.highlighted).id if opts.option_count else None
        es = epics.list_epics(self.conn)
        self._epic_ids = [e.id for e in es]
        opts.clear_options()
        for e in es:
            opts.add_option(Option(f"#{e.id} {e.name} ({e.state})", id=str(e.id)))
        if self._epic_ids:
            idx = 0
            if prev:
                for i, eid in enumerate(self._epic_ids):
                    if str(eid) == prev:
                        idx = i
                        break
            opts.highlighted = idx

    def _new(self) -> None:
        self.app.push_screen(EpicFormScreen(self.conn), self._do_create)

    def _do_create(self, fields: dict | None) -> None:
        if fields is None:
            return
        try:
            epics.create_epic(self.conn, fields["name"],
                              description=fields["description"],
                              state=fields["state"],
                              milestone_id=fields["milestone_id"],
                              project_id=fields["project_id"])
        except errors.PlannerError as e:
            self._status(f"error: {e}")
            return
        self._dirty = True
        self._refresh()
        self._status(f"Created epic '{fields['name']}'.")

    def _edit(self) -> None:
        eid = self._selected_id()
        if eid is None:
            self._status("Select an epic to edit.")
            return
        self.app.push_screen(EpicFormScreen(self.conn, epic=epics.get_epic(self.conn, eid)),
                             lambda fields: self._do_update(eid, fields))

    def _do_update(self, eid: int, fields: dict | None) -> None:
        if fields is None:
            return
        try:
            epics.update_epic(self.conn, eid, name=fields["name"],
                              description=fields["description"],
                              state=fields["state"],
                              milestone_id=fields["milestone_id"],
                              project_id=fields["project_id"])
        except errors.PlannerError as e:
            self._status(f"error: {e}")
            return
        self._dirty = True
        self._refresh()
        self._status(f"Updated epic '{fields['name']}'.")

    def _delete(self) -> None:
        eid = self._selected_id()
        if eid is None:
            self._status("Select an epic to delete.")
            return
        e = epics.get_epic(self.conn, eid)
        self.app.push_screen(ConfirmScreen(f"Delete epic '#{e.id} {e.name}'?"),
                             lambda ok: self._do_delete(eid, ok))

    def _do_delete(self, eid: int, ok: bool | None) -> None:
        if not ok:
            return
        try:
            epics.delete_epic(self.conn, eid)
        except errors.PlannerError as e:
            self._status(f"error: {e}")
            return
        self._dirty = True
        self._refresh()
        self._status("Deleted epic.")

    @on(Button.Pressed, "#em-new")
    def _b_new(self) -> None:
        self._new()

    @on(Button.Pressed, "#em-edit")
    def _b_edit(self) -> None:
        self._edit()

    @on(Button.Pressed, "#em-delete")
    def _b_delete(self) -> None:
        self._delete()

    @on(Button.Pressed, "#em-done")
    def _b_done(self) -> None:
        self.dismiss(self._dirty)


class IterationFormScreen(ModalScreen[dict]):
    """Collect name/description/status/start/end for an iteration.

    Used both for creating a new iteration (``iteration=None``) and for editing
    an existing one (pre-populated from the given
    :class:`~backend.models.Iteration`). All operations call the backend
    ``iterations`` module. Dismisses with a dict of field values, or None on
    cancel.
    """

    def __init__(self, conn: sqlite3.Connection, iteration=None) -> None:
        super().__init__()
        self.conn = conn
        self.iteration = iteration  # Iteration | None

    def compose(self) -> ComposeResult:
        it = self.iteration
        status_opts = [(s, s) for s in iterations.STATUSES]
        title = f"Edit iteration #{it.id}" if it else "New iteration"
        yield VerticalScroll(
            Static(title, classes="modal-title"),
            Label("Name:"),
            Input(value=it.name if it else "", id="if-name"),
            Label("Description:"),
            TextArea(id="if-desc"),
            Label("Status:"),
            Select(status_opts, value=(it.status if it else "planned"), id="if-status"),
            Label("Start date (YYYY-MM-DD):"),
            Input(value=(it.start_date or "") if it else "", placeholder="YYYY-MM-DD", id="if-start"),
            Label("End date (YYYY-MM-DD):"),
            Input(value=(it.end_date or "") if it else "", placeholder="YYYY-MM-DD", id="if-end"),
            Horizontal(Button("Save", id="ok", variant="primary"),
                       Button("Cancel", id="cancel")),
            Label("", id="if-err", classes="err"),
            classes="modal-box",
        )

    def on_mount(self) -> None:
        if self.iteration and self.iteration.description:
            self.query_one("#if-desc", TextArea).text = self.iteration.description
        self.query_one("#if-name", Input).focus()

    def _submit(self) -> None:
        name = self.query_one("#if-name", Input).value.strip()
        if not name:
            self.query_one("#if-err", Label).update("Name is required.")
            return
        status = self.query_one("#if-status", Select).value
        if status not in iterations.STATUSES:
            self.query_one("#if-err", Label).update("Invalid status.")
            return
        start = self.query_one("#if-start", Input).value.strip() or None
        end = self.query_one("#if-end", Input).value.strip() or None
        self.dismiss({
            "name": name,
            "description": self.query_one("#if-desc", TextArea).text,
            "status": status,
            "start_date": start,
            "end_date": end,
        })

    @on(Button.Pressed, "#ok")
    def _ok(self) -> None:
        self._submit()

    @on(Button.Pressed, "#cancel")
    def _cancel(self) -> None:
        self.dismiss(None)


class IterationManagerScreen(ModalScreen[bool]):
    """Manage iterations: create, edit, and delete.

    An OptionList lists every iteration; New/Edit/Delete/Done buttons drive the
    flow. Create and edit open :class:`IterationFormScreen`; delete confirms via
    :class:`ConfirmScreen`. Every operation goes through the backend
    ``iterations`` module; backend errors surface in a status label.

    Dismisses with:
        bool: True if any change was made (caller refreshes), else None.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        super().__init__()
        self.conn = conn
        self._dirty = False
        self._iteration_ids: list[int] = []

    def compose(self) -> ComposeResult:
        yield VerticalScroll(
            Static("Manage Iterations", classes="modal-title"),
            OptionList(id="im-iterations"),
            Horizontal(
                Button("New", id="im-new", variant="primary"),
                Button("Edit", id="im-edit"),
                Button("Delete", id="im-delete", variant="error"),
                Button("Done", id="im-done"),
            ),
            Label("", id="im-status", classes="err"),
            classes="modal-box",
        )

    def _status(self, msg: str) -> None:
        self.query_one("#im-status", Label).update(msg)

    def _selected_id(self) -> int | None:
        idx = self.query_one("#im-iterations", OptionList).highlighted
        if idx is None or not (0 <= idx < len(self._iteration_ids)):
            return None
        return self._iteration_ids[idx]

    def on_mount(self) -> None:
        self._refresh()

    def _refresh(self) -> None:
        opts = self.query_one("#im-iterations", OptionList)
        prev = opts.get_option_at_index(opts.highlighted).id if opts.option_count else None
        its = iterations.list_iterations(self.conn)
        self._iteration_ids = [it.id for it in its]
        opts.clear_options()
        for it in its:
            opts.add_option(Option(f"#{it.id} {it.name} ({it.status})", id=str(it.id)))
        if self._iteration_ids:
            idx = 0
            if prev:
                for i, iid in enumerate(self._iteration_ids):
                    if str(iid) == prev:
                        idx = i
                        break
            opts.highlighted = idx

    def _new(self) -> None:
        self.app.push_screen(IterationFormScreen(self.conn), self._do_create)

    def _do_create(self, fields: dict | None) -> None:
        if fields is None:
            return
        try:
            iterations.create_iteration(self.conn, fields["name"],
                                        description=fields["description"],
                                        status=fields["status"],
                                        start_date=fields["start_date"],
                                        end_date=fields["end_date"])
        except errors.PlannerError as e:
            self._status(f"error: {e}")
            return
        self._dirty = True
        self._refresh()
        self._status(f"Created iteration '{fields['name']}'.")

    def _edit(self) -> None:
        iid = self._selected_id()
        if iid is None:
            self._status("Select an iteration to edit.")
            return
        self.app.push_screen(IterationFormScreen(self.conn, iteration=iterations.get_iteration(self.conn, iid)),
                             lambda fields: self._do_update(iid, fields))

    def _do_update(self, iid: int, fields: dict | None) -> None:
        if fields is None:
            return
        try:
            iterations.update_iteration(self.conn, iid, name=fields["name"],
                                        description=fields["description"],
                                        status=fields["status"],
                                        start_date=fields["start_date"],
                                        end_date=fields["end_date"])
        except errors.PlannerError as e:
            self._status(f"error: {e}")
            return
        self._dirty = True
        self._refresh()
        self._status(f"Updated iteration '{fields['name']}'.")

    def _delete(self) -> None:
        iid = self._selected_id()
        if iid is None:
            self._status("Select an iteration to delete.")
            return
        it = iterations.get_iteration(self.conn, iid)
        self.app.push_screen(ConfirmScreen(f"Delete iteration '#{it.id} {it.name}'?"),
                             lambda ok: self._do_delete(iid, ok))

    def _do_delete(self, iid: int, ok: bool | None) -> None:
        if not ok:
            return
        try:
            iterations.delete_iteration(self.conn, iid)
        except errors.PlannerError as e:
            self._status(f"error: {e}")
            return
        self._dirty = True
        self._refresh()
        self._status("Deleted iteration.")

    @on(Button.Pressed, "#im-new")
    def _b_new(self) -> None:
        self._new()

    @on(Button.Pressed, "#im-edit")
    def _b_edit(self) -> None:
        self._edit()

    @on(Button.Pressed, "#im-delete")
    def _b_delete(self) -> None:
        self._delete()

    @on(Button.Pressed, "#im-done")
    def _b_done(self) -> None:
        self.dismiss(self._dirty)


class MilestoneFormScreen(ModalScreen[dict]):
    """Collect name/description/state for a milestone.

    Used both for creating a new milestone (``milestone=None``) and for editing
    an existing one (pre-populated from the given
    :class:`~backend.models.Milestone`). All operations call the backend
    ``milestones`` module. Dismisses with a dict of field values, or None on
    cancel.
    """

    def __init__(self, conn: sqlite3.Connection, milestone=None) -> None:
        super().__init__()
        self.conn = conn
        self.milestone = milestone  # Milestone | None

    def compose(self) -> ComposeResult:
        ms = self.milestone
        state_opts = [(s, s) for s in milestones.STATES]
        title = f"Edit milestone #{ms.id}" if ms else "New milestone"
        yield VerticalScroll(
            Static(title, classes="modal-title"),
            Label("Name:"),
            Input(value=ms.name if ms else "", id="mf-name"),
            Label("Description:"),
            TextArea(id="mf-desc"),
            Label("State:"),
            Select(state_opts, value=(ms.state if ms else "planned"), id="mf-state"),
            Horizontal(Button("Save", id="ok", variant="primary"),
                       Button("Cancel", id="cancel")),
            Label("", id="mf-err", classes="err"),
            classes="modal-box",
        )

    def on_mount(self) -> None:
        if self.milestone and self.milestone.description:
            self.query_one("#mf-desc", TextArea).text = self.milestone.description
        self.query_one("#mf-name", Input).focus()

    def _submit(self) -> None:
        name = self.query_one("#mf-name", Input).value.strip()
        if not name:
            self.query_one("#mf-err", Label).update("Name is required.")
            return
        state = self.query_one("#mf-state", Select).value
        if state not in milestones.STATES:
            self.query_one("#mf-err", Label).update("Invalid state.")
            return
        self.dismiss({
            "name": name,
            "description": self.query_one("#mf-desc", TextArea).text,
            "state": state,
        })

    @on(Button.Pressed, "#ok")
    def _ok(self) -> None:
        self._submit()

    @on(Button.Pressed, "#cancel")
    def _cancel(self) -> None:
        self.dismiss(None)


class MilestoneManagerScreen(ModalScreen[bool]):
    """Manage milestones: create, edit, and delete.

    An OptionList lists every milestone; New/Edit/Delete/Done buttons drive the
    flow. Create and edit open :class:`MilestoneFormScreen`; delete confirms via
    :class:`ConfirmScreen`. Every operation goes through the backend
    ``milestones`` module; backend errors surface in a status label.

    Dismisses with:
        bool: True if any change was made (caller refreshes), else None.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        super().__init__()
        self.conn = conn
        self._dirty = False
        self._milestone_ids: list[int] = []

    def compose(self) -> ComposeResult:
        yield VerticalScroll(
            Static("Manage Milestones", classes="modal-title"),
            OptionList(id="mm-milestones"),
            Horizontal(
                Button("New", id="mm-new", variant="primary"),
                Button("Edit", id="mm-edit"),
                Button("Delete", id="mm-delete", variant="error"),
                Button("Done", id="mm-done"),
            ),
            Label("", id="mm-status", classes="err"),
            classes="modal-box",
        )

    def _status(self, msg: str) -> None:
        self.query_one("#mm-status", Label).update(msg)

    def _selected_id(self) -> int | None:
        idx = self.query_one("#mm-milestones", OptionList).highlighted
        if idx is None or not (0 <= idx < len(self._milestone_ids)):
            return None
        return self._milestone_ids[idx]

    def on_mount(self) -> None:
        self._refresh()

    def _refresh(self) -> None:
        opts = self.query_one("#mm-milestones", OptionList)
        prev = opts.get_option_at_index(opts.highlighted).id if opts.option_count else None
        mss = milestones.list_milestones(self.conn)
        self._milestone_ids = [m.id for m in mss]
        opts.clear_options()
        for ms in mss:
            opts.add_option(Option(f"#{ms.id} {ms.name} ({ms.state})", id=str(ms.id)))
        if self._milestone_ids:
            idx = 0
            if prev:
                for i, mid in enumerate(self._milestone_ids):
                    if str(mid) == prev:
                        idx = i
                        break
            opts.highlighted = idx

    def _new(self) -> None:
        self.app.push_screen(MilestoneFormScreen(self.conn), self._do_create)

    def _do_create(self, fields: dict | None) -> None:
        if fields is None:
            return
        try:
            milestones.create_milestone(self.conn, fields["name"],
                                        description=fields["description"],
                                        state=fields["state"])
        except errors.PlannerError as e:
            self._status(f"error: {e}")
            return
        self._dirty = True
        self._refresh()
        self._status(f"Created milestone '{fields['name']}'.")

    def _edit(self) -> None:
        mid = self._selected_id()
        if mid is None:
            self._status("Select a milestone to edit.")
            return
        self.app.push_screen(MilestoneFormScreen(self.conn, milestone=milestones.get_milestone(self.conn, mid)),
                             lambda fields: self._do_update(mid, fields))

    def _do_update(self, mid: int, fields: dict | None) -> None:
        if fields is None:
            return
        try:
            milestones.update_milestone(self.conn, mid, name=fields["name"],
                                        description=fields["description"],
                                        state=fields["state"])
        except errors.PlannerError as e:
            self._status(f"error: {e}")
            return
        self._dirty = True
        self._refresh()
        self._status(f"Updated milestone '{fields['name']}'.")

    def _delete(self) -> None:
        mid = self._selected_id()
        if mid is None:
            self._status("Select a milestone to delete.")
            return
        ms = milestones.get_milestone(self.conn, mid)
        self.app.push_screen(ConfirmScreen(f"Delete milestone '#{ms.id} {ms.name}'?"),
                             lambda ok: self._do_delete(mid, ok))

    def _do_delete(self, mid: int, ok: bool | None) -> None:
        if not ok:
            return
        try:
            milestones.delete_milestone(self.conn, mid)
        except errors.PlannerError as e:
            self._status(f"error: {e}")
            return
        self._dirty = True
        self._refresh()
        self._status("Deleted milestone.")

    @on(Button.Pressed, "#mm-new")
    def _b_new(self) -> None:
        self._new()

    @on(Button.Pressed, "#mm-edit")
    def _b_edit(self) -> None:
        self._edit()

    @on(Button.Pressed, "#mm-delete")
    def _b_delete(self) -> None:
        self._delete()

    @on(Button.Pressed, "#mm-done")
    def _b_done(self) -> None:
        self.dismiss(self._dirty)


class ProjectFormScreen(ModalScreen[dict]):
    """Collect name/description/abbreviation/color for a project.

    Used both for creating a new project (``project=None``) and for editing an
    existing one (pre-populated from the given :class:`~backend.models.Project`).
    All operations call the backend ``projects`` module. Dismisses with a dict
    of field values, or None on cancel.
    """

    def __init__(self, conn: sqlite3.Connection, project=None) -> None:
        super().__init__()
        self.conn = conn
        self.project = project  # Project | None

    def compose(self) -> ComposeResult:
        p = self.project
        title = f"Edit project #{p.id}" if p else "New project"
        yield VerticalScroll(
            Static(title, classes="modal-title"),
            Label("Name:"),
            Input(value=p.name if p else "", id="pf-name"),
            Label("Description:"),
            TextArea(id="pf-desc"),
            Label("Abbreviation:"),
            Input(value=p.abbreviation if p else "", id="pf-abbr"),
            Label("Color:"),
            Input(value=p.color if p else "", id="pf-color"),
            Horizontal(Button("Save", id="ok", variant="primary"),
                       Button("Cancel", id="cancel")),
            Label("", id="pf-err", classes="err"),
            classes="modal-box",
        )

    def on_mount(self) -> None:
        if self.project and self.project.description:
            self.query_one("#pf-desc", TextArea).text = self.project.description
        self.query_one("#pf-name", Input).focus()

    def _submit(self) -> None:
        name = self.query_one("#pf-name", Input).value.strip()
        if not name:
            self.query_one("#pf-err", Label).update("Name is required.")
            return
        self.dismiss({
            "name": name,
            "description": self.query_one("#pf-desc", TextArea).text,
            "abbreviation": self.query_one("#pf-abbr", Input).value.strip(),
            "color": self.query_one("#pf-color", Input).value.strip(),
        })

    @on(Button.Pressed, "#ok")
    def _ok(self) -> None:
        self._submit()

    @on(Button.Pressed, "#cancel")
    def _cancel(self) -> None:
        self.dismiss(None)


class ProjectManagerScreen(ModalScreen[bool]):
    """Manage projects: create, edit, archive/unarchive, and delete.

    An OptionList lists every project (including archived ones); New/Edit/
    Archive/Done buttons drive the flow. Create and edit open
    :class:`ProjectFormScreen`; delete confirms via :class:`ConfirmScreen`.
    Archive/unarchive is reversible and runs without confirmation. Every
    operation goes through the backend ``projects`` module; backend errors
    surface in a status label.

    Dismisses with:
        bool: True if any change was made (caller refreshes), else None.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        super().__init__()
        self.conn = conn
        self._dirty = False
        self._project_ids: list[int] = []

    def compose(self) -> ComposeResult:
        yield VerticalScroll(
            Static("Manage Projects", classes="modal-title"),
            OptionList(id="pm-projects"),
            Horizontal(
                Button("New", id="pm-new", variant="primary"),
                Button("Edit", id="pm-edit"),
                Button("Archive/Unarchive", id="pm-archive"),
                Button("Delete", id="pm-delete", variant="error"),
                Button("Done", id="pm-done"),
            ),
            Label("", id="pm-status", classes="err"),
            classes="modal-box",
        )

    def _status(self, msg: str) -> None:
        self.query_one("#pm-status", Label).update(msg)

    def _selected_id(self) -> int | None:
        idx = self.query_one("#pm-projects", OptionList).highlighted
        if idx is None or not (0 <= idx < len(self._project_ids)):
            return None
        return self._project_ids[idx]

    def on_mount(self) -> None:
        self._refresh()

    def _refresh(self) -> None:
        opts = self.query_one("#pm-projects", OptionList)
        prev = opts.get_option_at_index(opts.highlighted).id if opts.option_count else None
        ps = projects.list_projects(self.conn, include_archived=True)
        self._project_ids = [p.id for p in ps]
        opts.clear_options()
        for p in ps:
            label = f"#{p.id} {p.name}"
            if p.archived:
                label += " (archived)"
            opts.add_option(Option(label, id=str(p.id)))
        if self._project_ids:
            idx = 0
            if prev:
                for i, pid in enumerate(self._project_ids):
                    if str(pid) == prev:
                        idx = i
                        break
            opts.highlighted = idx

    def _new(self) -> None:
        self.app.push_screen(ProjectFormScreen(self.conn), self._do_create)

    def _do_create(self, fields: dict | None) -> None:
        if fields is None:
            return
        try:
            projects.create_project(self.conn, fields["name"],
                                    description=fields["description"],
                                    abbreviation=fields["abbreviation"],
                                    color=fields["color"])
        except errors.PlannerError as e:
            self._status(f"error: {e}")
            return
        self._dirty = True
        self._refresh()
        self._status(f"Created project '{fields['name']}'.")

    def _edit(self) -> None:
        pid = self._selected_id()
        if pid is None:
            self._status("Select a project to edit.")
            return
        self.app.push_screen(ProjectFormScreen(self.conn,
                                               project=projects.get_project(self.conn, pid)),
                             lambda fields: self._do_update(pid, fields))

    def _do_update(self, pid: int, fields: dict | None) -> None:
        if fields is None:
            return
        try:
            projects.update_project(self.conn, pid, name=fields["name"],
                                    description=fields["description"],
                                    abbreviation=fields["abbreviation"],
                                    color=fields["color"])
        except errors.PlannerError as e:
            self._status(f"error: {e}")
            return
        self._dirty = True
        self._refresh()
        self._status(f"Updated project '{fields['name']}'.")

    def _archive(self) -> None:
        pid = self._selected_id()
        if pid is None:
            self._status("Select a project to archive.")
            return
        p = projects.get_project(self.conn, pid)
        try:
            projects.archive_project(self.conn, pid, archived=not bool(p.archived))
        except errors.PlannerError as e:
            self._status(f"error: {e}")
            return
        self._dirty = True
        self._refresh()
        self._status("Unarchived project." if p.archived else "Archived project.")

    def _delete(self) -> None:
        pid = self._selected_id()
        if pid is None:
            self._status("Select a project to delete.")
            return
        p = projects.get_project(self.conn, pid)
        self.app.push_screen(ConfirmScreen(f"Delete project '#{p.id} {p.name}'?"),
                             lambda ok: self._do_delete(pid, ok))

    def _do_delete(self, pid: int, ok: bool | None) -> None:
        if not ok:
            return
        try:
            projects.delete_project(self.conn, pid)
        except errors.PlannerError as e:
            self._status(f"error: {e}")
            return
        self._dirty = True
        self._refresh()
        self._status("Deleted project.")

    @on(Button.Pressed, "#pm-new")
    def _b_new(self) -> None:
        self._new()

    @on(Button.Pressed, "#pm-edit")
    def _b_edit(self) -> None:
        self._edit()

    @on(Button.Pressed, "#pm-archive")
    def _b_archive(self) -> None:
        self._archive()

    @on(Button.Pressed, "#pm-delete")
    def _b_delete(self) -> None:
        self._delete()

    @on(Button.Pressed, "#pm-done")
    def _b_done(self) -> None:
        self.dismiss(self._dirty)


class LabelFormScreen(ModalScreen[dict]):
    """Collect name/color/description for a label.

    Used both for creating a new label (``label=None``) and for editing an
    existing one (pre-populated from the given :class:`~backend.models.Label`).
    All operations call the backend ``labels`` module. Dismisses with a dict
    of field values, or None on cancel.
    """

    def __init__(self, conn: sqlite3.Connection, label=None) -> None:
        super().__init__()
        self.conn = conn
        self.label = label  # Label | None

    def compose(self) -> ComposeResult:
        label = self.label
        title = f"Edit label #{label.id}" if label else "New label"
        yield VerticalScroll(
            Static(title, classes="modal-title"),
            Label("Name:"),
            Input(value=label.name if label else "", id="lf-name"),
            Label("Color:"),
            Input(value=label.color if label else "", id="lf-color"),
            Label("Description:"),
            TextArea(id="lf-desc"),
            Horizontal(Button("Save", id="ok", variant="primary"),
                       Button("Cancel", id="cancel")),
            Label("", id="lf-err", classes="err"),
            classes="modal-box",
        )

    def on_mount(self) -> None:
        if self.label and self.label.description:
            self.query_one("#lf-desc", TextArea).text = self.label.description
        self.query_one("#lf-name", Input).focus()

    def _submit(self) -> None:
        name = self.query_one("#lf-name", Input).value.strip()
        if not name:
            self.query_one("#lf-err", Label).update("Name is required.")
            return
        self.dismiss({
            "name": name,
            "color": self.query_one("#lf-color", Input).value.strip(),
            "description": self.query_one("#lf-desc", TextArea).text,
        })

    @on(Button.Pressed, "#ok")
    def _ok(self) -> None:
        self._submit()

    @on(Button.Pressed, "#cancel")
    def _cancel(self) -> None:
        self.dismiss(None)


class LabelManagerScreen(ModalScreen[bool]):
    """Manage labels: create, rename/change-color, and delete.

    An OptionList lists every label; New/Edit/Done buttons drive the flow.
    Create and edit open :class:`LabelFormScreen`; delete confirms via
    :class:`ConfirmScreen`. Every operation goes through the backend ``labels``
    module; backend errors surface in a status label.

    Dismisses with:
        bool: True if any change was made (caller refreshes), else None.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        super().__init__()
        self.conn = conn
        self._dirty = False
        self._label_ids: list[int] = []

    def compose(self) -> ComposeResult:
        yield VerticalScroll(
            Static("Manage Labels", classes="modal-title"),
            OptionList(id="lm-labels"),
            Horizontal(
                Button("New", id="lm-new", variant="primary"),
                Button("Edit", id="lm-edit"),
                Button("Delete", id="lm-delete", variant="error"),
                Button("Done", id="lm-done"),
            ),
            Label("", id="lm-status", classes="err"),
            classes="modal-box",
        )

    def _status(self, msg: str) -> None:
        self.query_one("#lm-status", Label).update(msg)

    def _selected_id(self) -> int | None:
        idx = self.query_one("#lm-labels", OptionList).highlighted
        if idx is None or not (0 <= idx < len(self._label_ids)):
            return None
        return self._label_ids[idx]

    def on_mount(self) -> None:
        self._refresh()

    def _refresh(self) -> None:
        opts = self.query_one("#lm-labels", OptionList)
        prev = opts.get_option_at_index(opts.highlighted).id if opts.option_count else None
        ls = labels.list_labels(self.conn)
        self._label_ids = [label.id for label in ls]
        opts.clear_options()
        for label in ls:
            opts.add_option(Option(f"#{label.id} {label.name}", id=str(label.id)))
        if self._label_ids:
            idx = 0
            if prev:
                for i, lid in enumerate(self._label_ids):
                    if str(lid) == prev:
                        idx = i
                        break
            opts.highlighted = idx

    def _new(self) -> None:
        self.app.push_screen(LabelFormScreen(self.conn), self._do_create)

    def _do_create(self, fields: dict | None) -> None:
        if fields is None:
            return
        try:
            labels.create_label(self.conn, fields["name"],
                                color=fields["color"],
                                description=fields["description"])
        except errors.PlannerError as e:
            self._status(f"error: {e}")
            return
        self._dirty = True
        self._refresh()
        self._status(f"Created label '{fields['name']}'.")

    def _edit(self) -> None:
        lid = self._selected_id()
        if lid is None:
            self._status("Select a label to edit.")
            return
        self.app.push_screen(LabelFormScreen(self.conn,
                                             label=labels.get_label(self.conn, lid)),
                             lambda fields: self._do_update(lid, fields))

    def _do_update(self, lid: int, fields: dict | None) -> None:
        if fields is None:
            return
        try:
            labels.update_label(self.conn, lid, name=fields["name"],
                                color=fields["color"],
                                description=fields["description"])
        except errors.PlannerError as e:
            self._status(f"error: {e}")
            return
        self._dirty = True
        self._refresh()
        self._status(f"Updated label '{fields['name']}'.")

    def _delete(self) -> None:
        lid = self._selected_id()
        if lid is None:
            self._status("Select a label to delete.")
            return
        label = labels.get_label(self.conn, lid)
        self.app.push_screen(ConfirmScreen(f"Delete label '#{label.id} {label.name}'?"),
                             lambda ok: self._do_delete(lid, ok))

    def _do_delete(self, lid: int, ok: bool | None) -> None:
        if not ok:
            return
        try:
            labels.delete_label(self.conn, lid)
        except errors.PlannerError as e:
            self._status(f"error: {e}")
            return
        self._dirty = True
        self._refresh()
        self._status("Deleted label.")

    @on(Button.Pressed, "#lm-new")
    def _b_new(self) -> None:
        self._new()

    @on(Button.Pressed, "#lm-edit")
    def _b_edit(self) -> None:
        self._edit()

    @on(Button.Pressed, "#lm-delete")
    def _b_delete(self) -> None:
        self._delete()

    @on(Button.Pressed, "#lm-done")
    def _b_done(self) -> None:
        self.dismiss(self._dirty)


class MemberFormScreen(ModalScreen[dict]):
    """Collect name/mention_name for a member.

    Used both for creating a new member (``member=None``) and for editing an
    existing one (pre-populated from the given
    :class:`~backend.models.Member`). Dismisses with a dict of field values,
    or None on cancel.
    """

    def __init__(self, conn: sqlite3.Connection, member=None) -> None:
        super().__init__()
        self.conn = conn
        self.member = member  # Member | None

    def compose(self) -> ComposeResult:
        member = self.member
        title = f"Edit member #{member.id}" if member else "New member"
        yield VerticalScroll(
            Static(title, classes="modal-title"),
            Label("Name:"),
            Input(value=member.name if member else "", id="mf-name"),
            Label("Mention name:"),
            Input(value=member.mention_name if member else "",
                  id="mf-mention", placeholder="(derived from name if blank)"),
            Horizontal(Button("Save", id="ok", variant="primary"),
                       Button("Cancel", id="cancel")),
            Label("", id="mf-err", classes="err"),
            classes="modal-box",
        )

    def on_mount(self) -> None:
        self.query_one("#mf-name", Input).focus()

    def _submit(self) -> None:
        name = self.query_one("#mf-name", Input).value.strip()
        if not name:
            self.query_one("#mf-err", Label).update("Name is required.")
            return
        self.dismiss({
            "name": name,
            "mention_name": self.query_one("#mf-mention", Input).value.strip(),
        })

    @on(Button.Pressed, "#ok")
    def _ok(self) -> None:
        self._submit()

    @on(Button.Pressed, "#cancel")
    def _cancel(self) -> None:
        self.dismiss(None)


class MemberManagerScreen(ModalScreen[bool]):
    """Manage members: create, rename, and delete.

    An OptionList lists every member; New/Edit/Delete/Done buttons drive the
    flow. Create and edit open :class:`MemberFormScreen`; delete confirms via
    :class:`ConfirmScreen`. Every operation goes through the backend ``members``
    module; backend errors surface in a status label.

    Dismisses with:
        bool: True if any change was made (caller refreshes), else None.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        super().__init__()
        self.conn = conn
        self._dirty = False
        self._member_ids: list[int] = []

    def compose(self) -> ComposeResult:
        yield VerticalScroll(
            Static("Manage Members", classes="modal-title"),
            OptionList(id="mm-members"),
            Horizontal(
                Button("New", id="mm-new", variant="primary"),
                Button("Edit", id="mm-edit"),
                Button("Delete", id="mm-delete", variant="error"),
                Button("Done", id="mm-done"),
            ),
            Label("", id="mm-status", classes="err"),
            classes="modal-box",
        )

    def _status(self, msg: str) -> None:
        self.query_one("#mm-status", Label).update(msg)

    def _selected_id(self) -> int | None:
        idx = self.query_one("#mm-members", OptionList).highlighted
        if idx is None or not (0 <= idx < len(self._member_ids)):
            return None
        return self._member_ids[idx]

    def on_mount(self) -> None:
        self._refresh()

    def _refresh(self) -> None:
        opts = self.query_one("#mm-members", OptionList)
        prev = opts.get_option_at_index(opts.highlighted).id if opts.option_count else None
        ms = members.list_members(self.conn)
        self._member_ids = [m.id for m in ms]
        opts.clear_options()
        for m in ms:
            opts.add_option(Option(f"#{m.id} {m.name} (@{m.mention_name})",
                                   id=str(m.id)))
        if self._member_ids:
            idx = 0
            if prev:
                for i, mid in enumerate(self._member_ids):
                    if str(mid) == prev:
                        idx = i
                        break
            opts.highlighted = idx

    def _new(self) -> None:
        self.app.push_screen(MemberFormScreen(self.conn), self._do_create)

    def _do_create(self, fields: dict | None) -> None:
        if fields is None:
            return
        try:
            members.create_member(self.conn, fields["name"],
                                  mention_name=fields["mention_name"] or None)
        except errors.PlannerError as e:
            self._status(f"error: {e}")
            return
        self._dirty = True
        self._refresh()
        self._status(f"Created member '{fields['name']}'.")

    def _edit(self) -> None:
        mid = self._selected_id()
        if mid is None:
            self._status("Select a member to edit.")
            return
        self.app.push_screen(MemberFormScreen(self.conn,
                                              member=members.get_member(self.conn, mid)),
                             lambda fields: self._do_update(mid, fields))

    def _do_update(self, mid: int, fields: dict | None) -> None:
        if fields is None:
            return
        try:
            members.update_member(self.conn, mid,
                                  name=fields["name"],
                                  mention_name=fields["mention_name"])
        except errors.PlannerError as e:
            self._status(f"error: {e}")
            return
        self._dirty = True
        self._refresh()
        self._status(f"Updated member '{fields['name']}'.")

    def _delete(self) -> None:
        mid = self._selected_id()
        if mid is None:
            self._status("Select a member to delete.")
            return
        member = members.get_member(self.conn, mid)
        self.app.push_screen(
            ConfirmScreen(f"Delete member '#{member.id} {member.name}'?"),
            lambda ok: self._do_delete(mid, ok))

    def _do_delete(self, mid: int, ok: bool | None) -> None:
        if not ok:
            return
        try:
            members.delete_member(self.conn, mid)
        except errors.PlannerError as e:
            self._status(f"error: {e}")
            return
        self._dirty = True
        self._refresh()
        self._status("Deleted member.")

    @on(Button.Pressed, "#mm-new")
    def _b_new(self) -> None:
        self._new()

    @on(Button.Pressed, "#mm-edit")
    def _b_edit(self) -> None:
        self._edit()

    @on(Button.Pressed, "#mm-delete")
    def _b_delete(self) -> None:
        self._delete()

    @on(Button.Pressed, "#mm-done")
    def _b_done(self) -> None:
        self.dismiss(self._dirty)


class GroupFormScreen(ModalScreen[dict]):
    """Collect name/description for a group.

    Used both for creating a new group (``group=None``) and for editing an
    existing one (pre-populated from the given
    :class:`~backend.models.Group`). Dismisses with a dict of field values, or
    None on cancel.
    """

    def __init__(self, conn: sqlite3.Connection, group=None) -> None:
        super().__init__()
        self.conn = conn
        self.group = group  # Group | None

    def compose(self) -> ComposeResult:
        g = self.group
        title = f"Edit group #{g.id}" if g else "New group"
        yield VerticalScroll(
            Static(title, classes="modal-title"),
            Label("Name:"),
            Input(value=g.name if g else "", id="gf-name"),
            Label("Description:"),
            TextArea(id="gf-desc"),
            Horizontal(Button("Save", id="ok", variant="primary"),
                       Button("Cancel", id="cancel")),
            Label("", id="gf-err", classes="err"),
            classes="modal-box",
        )

    def on_mount(self) -> None:
        if self.group and self.group.description:
            self.query_one("#gf-desc", TextArea).text = self.group.description
        self.query_one("#gf-name", Input).focus()

    def _submit(self) -> None:
        name = self.query_one("#gf-name", Input).value.strip()
        if not name:
            self.query_one("#gf-err", Label).update("Name is required.")
            return
        self.dismiss({
            "name": name,
            "description": self.query_one("#gf-desc", TextArea).text,
        })

    @on(Button.Pressed, "#ok")
    def _ok(self) -> None:
        self._submit()

    @on(Button.Pressed, "#cancel")
    def _cancel(self) -> None:
        self.dismiss(None)


class GroupManagerScreen(ModalScreen[bool]):
    """Manage groups: create, edit, archive/unarchive, and delete.

    An OptionList lists every group; New/Edit/Archive/Delete/Done buttons drive
    the flow. Create and edit open :class:`GroupFormScreen`; delete confirms via
    :class:`ConfirmScreen`. Every operation goes through the backend ``groups``
    module; backend errors surface in a status label.

    Dismisses with:
        bool: True if any change was made (caller refreshes), else None.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        super().__init__()
        self.conn = conn
        self._dirty = False
        self._group_ids: list[int] = []

    def compose(self) -> ComposeResult:
        yield VerticalScroll(
            Static("Manage Groups", classes="modal-title"),
            OptionList(id="gm-groups"),
            Horizontal(
                Button("New", id="gm-new", variant="primary"),
                Button("Edit", id="gm-edit"),
                Button("Archive", id="gm-archive"),
                Button("Delete", id="gm-delete", variant="error"),
                Button("Done", id="gm-done"),
            ),
            Label("", id="gm-status", classes="err"),
            classes="modal-box",
        )

    def _status(self, msg: str) -> None:
        self.query_one("#gm-status", Label).update(msg)

    def _selected_id(self) -> int | None:
        idx = self.query_one("#gm-groups", OptionList).highlighted
        if idx is None or not (0 <= idx < len(self._group_ids)):
            return None
        return self._group_ids[idx]

    def on_mount(self) -> None:
        self._refresh()

    def _refresh(self) -> None:
        opts = self.query_one("#gm-groups", OptionList)
        prev = opts.get_option_at_index(opts.highlighted).id if opts.option_count else None
        gs = groups.list_groups(self.conn, include_archived=True)
        self._group_ids = [g.id for g in gs]
        opts.clear_options()
        for g in gs:
            suffix = " (archived)" if g.archived else ""
            opts.add_option(Option(f"#{g.id} {g.name}{suffix}", id=str(g.id)))
        if self._group_ids:
            idx = 0
            if prev:
                for i, gid in enumerate(self._group_ids):
                    if str(gid) == prev:
                        idx = i
                        break
            opts.highlighted = idx

    def _new(self) -> None:
        self.app.push_screen(GroupFormScreen(self.conn), self._do_create)

    def _do_create(self, fields: dict | None) -> None:
        if fields is None:
            return
        try:
            groups.create_group(self.conn, fields["name"],
                                description=fields["description"])
        except errors.PlannerError as e:
            self._status(f"error: {e}")
            return
        self._dirty = True
        self._refresh()
        self._status(f"Created group '{fields['name']}'.")

    def _edit(self) -> None:
        gid = self._selected_id()
        if gid is None:
            self._status("Select a group to edit.")
            return
        self.app.push_screen(GroupFormScreen(self.conn,
                                              group=groups.get_group(self.conn, gid)),
                             lambda fields: self._do_update(gid, fields))

    def _do_update(self, gid: int, fields: dict | None) -> None:
        if fields is None:
            return
        try:
            groups.update_group(self.conn, gid,
                                name=fields["name"],
                                description=fields["description"])
        except errors.PlannerError as e:
            self._status(f"error: {e}")
            return
        self._dirty = True
        self._refresh()
        self._status(f"Updated group '{fields['name']}'.")

    def _archive(self) -> None:
        gid = self._selected_id()
        if gid is None:
            self._status("Select a group to archive/unarchive.")
            return
        g = groups.get_group(self.conn, gid)
        try:
            groups.archive_group(self.conn, gid, archived=not g.archived)
        except errors.PlannerError as e:
            self._status(f"error: {e}")
            return
        self._dirty = True
        self._refresh()
        verb = "Unarchived" if g.archived else "Archived"
        self._status(f"{verb} group '{g.name}'.")

    def _delete(self) -> None:
        gid = self._selected_id()
        if gid is None:
            self._status("Select a group to delete.")
            return
        g = groups.get_group(self.conn, gid)
        self.app.push_screen(
            ConfirmScreen(f"Delete group '#{g.id} {g.name}'?"),
            lambda ok: self._do_delete(gid, ok))

    def _do_delete(self, gid: int, ok: bool | None) -> None:
        if not ok:
            return
        try:
            groups.delete_group(self.conn, gid)
        except errors.PlannerError as e:
            self._status(f"error: {e}")
            return
        self._dirty = True
        self._refresh()
        self._status("Deleted group.")

    @on(Button.Pressed, "#gm-new")
    def _b_new(self) -> None:
        self._new()

    @on(Button.Pressed, "#gm-edit")
    def _b_edit(self) -> None:
        self._edit()

    @on(Button.Pressed, "#gm-archive")
    def _b_archive(self) -> None:
        self._archive()

    @on(Button.Pressed, "#gm-delete")
    def _b_delete(self) -> None:
        self._delete()

    @on(Button.Pressed, "#gm-done")
    def _b_done(self) -> None:
        self.dismiss(self._dirty)


# --------------------------------------------------------------------------- #
# Main app
# --------------------------------------------------------------------------- #

class PlannerApp(App):
    """Full-screen project planner TUI."""

    # Replace Textual's built-in command palette with our own Ctrl+P palette.
    ENABLE_COMMAND_PALETTE = False

    CSS = """
    #filter-bar { background: $panel; height: 1; padding: 0 1; color: $text-muted; }
    #stories { width: 1fr; border: solid $primary; }
    #detail { width: 1fr; border: solid $accent; }
    .modal-box {
        width: 64; height: auto; max-height: 80%;
        background: $panel; border: solid $primary; padding: 1 2;
    }
    .palette-box {
        width: 60; height: auto; max-height: 60%;
        background: $panel; border: solid $primary; padding: 1 2;
    }
    .workflow-box {
        width: 96; height: auto; max-height: 85%;
        background: $panel; border: solid $primary; padding: 1 2;
    }
    .modal-subtitle { text-style: bold; margin-bottom: 1; }
    #wm-workflows { height: 14; }
    #wm-states { height: 14; }
    #pal-options { height: auto; max-height: 30; }
    .modal-title { text-style: bold; margin-bottom: 1; }
    .err { color: $error; }
    TextArea { height: 6; }
    """

    BINDINGS = [
        Binding("ctrl+p", "open_palette", "Palette"),
        Binding("q", "quit", "Quit"),
        Binding("n", "new_story", "New"),
        Binding("u", "edit_story", "Update"),
        Binding("m", "move_state", "Move"),
        Binding("c", "add_comment", "Comment"),
        Binding("C", "comment_action", "Comment⇄"),
        Binding("t", "add_task", "Task"),
        Binding("x", "task_action", "Task⇄"),
        Binding("o", "manage_owners", "Owners"),
        Binding("l", "manage_labels", "Labels"),
        Binding("h", "manage_links", "Links"),
        Binding("f", "filter", "Filter"),
        Binding("b", "browse", "Browse"),
        Binding("slash", "search", "Search"),  # '/'
        Binding("w", "manage_workflows", "Workflows"),
        Binding("E", "manage_epics", "Epics"),
        Binding("I", "manage_iterations", "Iterations"),
        Binding("M", "manage_milestones", "Milestones"),
        Binding("P", "manage_projects", "Projects"),
        Binding("L", "manage_label_catalog", "Labels"),
        Binding("R", "manage_member_catalog", "Members"),
        Binding("G", "manage_group_catalog", "Groups"),
        Binding("r", "refresh", "Refresh"),
        Binding("a", "toggle_auto_refresh", "Auto↻"),
        Binding("J", "move_down", "Down"),
        Binding("K", "move_up", "Up"),
        Binding("d", "delete_story", "Delete"),
        Binding("e", "toggle_complete", "Complete"),
        Binding("v", "toggle_multiselect", "Multi"),
        Binding("space", "toggle_select", "Toggle", show=False),
        Binding("escape", "exit_multiselect", "Exit", show=False),
    ]

    def __init__(self, db_path: str | None = None,
                 auto_refresh: float | None = None) -> None:
        super().__init__()
        self.db_path = db_path
        self.conn: sqlite3.Connection | None = None
        # Active story-list filters. Keys map to stories.list_stories params.
        self.filters = {"project": None, "state_type": [], "q": None,
                        "epic": None, "iteration": None, "milestone": None,
                        "owner": None, "label": None}
        self._edit_pane: EditStoryPane | None = None
        self._create_pane: CreateStoryPane | None = None
        # Off until the 'a' hotkey (or an explicit --auto-refresh N>0).
        self._auto_refresh_enabled = bool(auto_refresh)
        self._auto_refresh_interval = float(auto_refresh) if auto_refresh else 1.0
        self._auto_refresh_timer = None
        # Multi-select (visual) mode: a set of selected story ids, toggled by Space.
        self._multi_select = False
        self._selected: set[int] = set()

    # --- lifecycle --------------------------------------------------------- #
    def on_mount(self) -> None:
        self.conn = db.connect(self.db_path)
        self.title = "Project Planner"
        self.refresh_stories()
        if self._auto_refresh_enabled:
            self._auto_refresh_timer = self.set_interval(
                self._auto_refresh_interval, self.refresh_stories, name="auto-refresh")

    def on_unmount(self) -> None:
        self._stop_auto_refresh_timer()
        if self.conn is not None:
            self.conn.close()

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("(all stories)", id="filter-bar")
        with Horizontal():
            with Vertical(id="list-pane"):
                yield DataTable(id="stories", cursor_type="row")
            # Right pane: read-only detail view, or an EditStoryPane when editing.
            with VerticalScroll(id="detail"):
                yield RichLog(id="detail-view", wrap=True, markup=True)
        yield Footer()

    # --- story list -------------------------------------------------------- #
    def refresh_stories(self) -> None:
        """Query stories based on active filters and populate the DataTable.

        ``self.filters`` is a dict with keys project/state_type/q/epic/iteration/
        milestone (each id|str|None), mapped to ``stories.list_stories`` params.
        """
        assert self.conn is not None
        f = self.filters
        items = stories.list_stories(
            self.conn, project_id=f["project"], state_type=f["state_type"],
            epic_id=f["epic"], iteration_id=f["iteration"],
            milestone_id=f["milestone"], q=f["q"],
            owner_id=f["owner"], label_id=f["label"])
        table = self.query_one("#stories", DataTable)
        # Drop selections that no longer match the filter.
        self._selected &= {s.id for s in items}
        # Remember where the cursor was so the rebuild doesn't yank it to the top.
        prev_row = prev_col = None
        prev_key: str | None = None
        if table.row_count:
            coord = table.cursor_coordinate
            prev_row, prev_col = coord.row, coord.column
            try:
                prev_key = table.coordinate_to_cell_key(coord).row_key.value
            except Exception:
                prev_key = None
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
            cells = [str(s.id), s.name, s.story_type, state, projname,
                     owners, "✓" if s.completed_at else ""]
            if s.id in self._selected:
                # Highlight selected rows in multi-select (visual) mode.
                table.add_row(*[Text(c, style=Style(reverse=True)) for c in cells],
                              key=str(s.id))
            else:
                table.add_row(*cells, key=str(s.id))
        # Filter-bar caption.
        parts = []
        parts.append(f"project={'any' if f['project'] is None else self.name_of('project', f['project'])}")
        parts.append(f"owner={'any' if f['owner'] is None else self.name_of('member', f['owner'])}")
        parts.append(f"label={'any' if f['label'] is None else self.name_of('label', f['label'])}")
        stypes = f['state_type']
        st_str = 'any' if not stypes else ','.join(stypes)
        parts.append(f"state={st_str}")
        if f["epic"] is not None:
            parts.append(f"epic={self.name_of('epic', f['epic'])}")
        if f["iteration"] is not None:
            parts.append(f"iter={self.name_of('iteration', f['iteration'])}")
        if f["milestone"] is not None:
            parts.append(f"milestone={self.name_of('milestone', f['milestone'])}")
        parts.append(f"q={'-' if f['q'] is None else f['q']!r}")
        parts.append(f"  ({len(items)} stories)")
        if self._multi_select:
            parts.append(f"  [MULTI] {len(self._selected)} selected")
        self.query_one("#filter-bar", Static).update("  ".join(parts))
        # Restore the cursor to the story it was on before the rebuild.
        keys = [str(s.id) for s in items]
        if items:
            if prev_key is not None and prev_key in keys:
                new_row = keys.index(prev_key)
            elif prev_row is not None:
                new_row = max(0, min(prev_row, len(keys) - 1))
            else:
                new_row = 0
            table.cursor_coordinate = (new_row, prev_col or 0)  # type: ignore[assignment]
            self.show_current_detail()
        else:
            log = self.query_one("#detail-view", RichLog)
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

    def _detail_story_id(self) -> int | None:
        """Story id to render in the detail pane.

        In multi-select mode this is the first selected story; otherwise it is
        the story under the cursor.
        """
        if self._multi_select and self._selected:
            return sorted(self._selected)[0]
        return self._current_story_id()

    def show_current_detail(self) -> None:
        """Render the highlighted story's full details into the RichLog.
        """
        sid = self._detail_story_id()
        if sid is None:
            return
        assert self.conn is not None
        try:
            detail = stories.get_story_detail(self.conn, sid)
        except errors.NotFound:
            return
        log = self.query_one("#detail-view", RichLog)
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
        links = story_links.list_links(self.conn, sid)
        if links:
            log.write("links:")
            for lk in links:
                subj = self.name_of("story", lk.subject_story_id)
                obj = self.name_of("story", lk.object_story_id)
                log.write(f"  #{lk.id} {subj} --{lk.verb}--> {obj}")
        if s.completed_at:
            log.write(f"[green]completed: {s.completed_at}[/green]")

    @on(DataTable.RowHighlighted)
    def _on_highlight(self, event: DataTable.RowHighlighted) -> None:
        if event.data_table.id == "stories":
            # Moving the selection while editing abandons the edit and shows
            # the newly selected story's detail instead.
            if self._edit_pane is not None:
                self._close_edit()
            self.show_current_detail()

    # --- actions ----------------------------------------------------------- #
    def action_open_palette(self) -> None:
        """Open the command palette; run the chosen command on dismiss."""
        self.push_screen(CommandPalette(), self._run_palette_command)

    def _run_palette_command(self, action_name: str | None) -> None:
        """Dispatch a palette selection to the matching action method."""
        if action_name is None:
            return
        method = getattr(self, f"action_{action_name}", None)
        if method is not None:
            method()
        else:
            self.bell()

    def _selected_ids(self) -> list[int]:
        """IDs of the current bulk selection, or [] outside multi-select mode."""
        if not self._multi_select:
            return []
        return sorted(self._selected)

    def action_toggle_multiselect(self) -> None:
        """Enter or exit multi-select (visual) mode."""
        if self._multi_select:
            self._multi_select = False
            self._selected.clear()
        else:
            self._multi_select = True
            self._selected.clear()
        self.refresh_stories()

    def action_exit_multiselect(self) -> None:
        """Leave multi-select mode and clear the selection."""
        if not self._multi_select:
            return
        self._multi_select = False
        self._selected.clear()
        self.refresh_stories()

    def action_toggle_select(self) -> None:
        """Toggle the current row's selection (Space) in multi-select mode."""
        if not self._multi_select:
            return
        sid = self._current_story_id()
        if sid is None:
            self.bell()
            return
        if sid in self._selected:
            self._selected.discard(sid)
        else:
            self._selected.add(sid)
        self.refresh_stories()

    def action_refresh(self) -> None:
        """Refresh the story list based on current filters."""
        self.refresh_stories()

    def action_toggle_auto_refresh(self) -> None:
        """Toggle automatic polling for external changes."""
        if self._auto_refresh_enabled:
            self._auto_refresh_enabled = False
            self._stop_auto_refresh_timer()
            self.notify("Auto-refresh disabled", title="🔴")
        else:
            self._auto_refresh_enabled = True
            self._auto_refresh_timer = self.set_interval(
                self._auto_refresh_interval, self.refresh_stories, name="auto-refresh")
            self.notify(f"Auto-refresh enabled (every {self._auto_refresh_interval:.0f}s)", title="🟢")

    def _stop_auto_refresh_timer(self) -> None:
        """Cancel the auto-refresh timer if it's running."""
        if self._auto_refresh_timer is not None:
            self._auto_refresh_timer.stop()
            self._auto_refresh_timer = None

    def _filtered_neighbors(self) -> list:
        """Return the stories currently shown (ordered by position, id)."""
        assert self.conn is not None
        f = self.filters
        return stories.list_stories(
            self.conn, project_id=f["project"], state_type=f["state_type"],
            epic_id=f["epic"], iteration_id=f["iteration"],
            milestone_id=f["milestone"], q=f["q"])

    def _swap_positions(self, a, b) -> None:
        """Swap the position columns of two stories."""
        assert self.conn is not None
        pa, pb = a.position, b.position
        stories.update_story(self.conn, a.id, position=pb)
        stories.update_story(self.conn, b.id, position=pa)

    def action_move_down(self) -> None:
        """Move the selected story down the list (swap with the next story)."""
        sid = self._current_story_id()
        if sid is None or self.conn is None:
            self.bell()
            return
        nbrs = self._filtered_neighbors()
        idx = next((i for i, s in enumerate(nbrs) if s.id == sid), None)
        if idx is None or idx >= len(nbrs) - 1:
            self.bell()  # already last
            return
        self._swap_positions(nbrs[idx], nbrs[idx + 1])
        self.refresh_stories()
        table = self.query_one("#stories", DataTable)
        try:
            table.move_cursor(row=idx + 1)
        except Exception:
            pass
        self.show_current_detail()

    def action_move_up(self) -> None:
        """Move the selected story up the list (swap with the previous story)."""
        sid = self._current_story_id()
        if sid is None or self.conn is None:
            self.bell()
            return
        nbrs = self._filtered_neighbors()
        idx = next((i for i, s in enumerate(nbrs) if s.id == sid), None)
        if idx is None or idx <= 0:
            self.bell()  # already first
            return
        self._swap_positions(nbrs[idx - 1], nbrs[idx])
        self.refresh_stories()
        table = self.query_one("#stories", DataTable)
        try:
            table.move_cursor(row=idx - 1)
        except Exception:
            pass
        self.show_current_detail()

    def action_new_story(self) -> None:
        """Create a new story in the right detail pane."""
        assert self.conn is not None
        self.query_one("#detail-view", RichLog).display = False
        pane = CreateStoryPane(self.conn,
                               on_saved=self._create_saved,
                               on_cancelled=self._create_cancelled)
        self._create_pane = pane
        self.query_one("#detail", VerticalScroll).mount(pane)


    def _create_saved(self, sid: int) -> None:
        self._close_create()
        self.refresh_stories()
        # Select the newly created row.
        table = self.query_one("#stories", DataTable)
        try:
            table.move_cursor(row=table.get_row_index(str(sid)))
        except Exception:
            pass
        self.show_current_detail()

    def _create_cancelled(self) -> None:
        self._close_create()
        self.show_current_detail()

    def _close_create(self) -> None:
        """Remove the create pane and restore the read-only detail view."""
        if self._create_pane is not None:
            try:
                self._create_pane.remove()
            except Exception:
                pass
            self._create_pane = None
        self.query_one("#detail-view", RichLog).display = True

    def action_edit_story(self) -> None:
        """Edit the selected story in-place in the right detail pane."""
        assert self.conn is not None
        sid = self._current_story_id()
        if sid is None:
            self.bell()
            return
        if self._edit_pane is not None:
            self.bell()  # already editing
            return
        # Hide the read-only view and mount the edit form in its place.
        self.query_one("#detail-view", RichLog).display = False
        pane = EditStoryPane(self.conn, sid,
                             on_saved=self._edit_saved,
                             on_cancelled=self._edit_cancelled)
        self._edit_pane = pane
        self.query_one("#detail", VerticalScroll).mount(pane)

    def _edit_saved(self, sid: int) -> None:
        self._close_edit()
        self.refresh_stories()
        # Keep the cursor on the edited story.
        table = self.query_one("#stories", DataTable)
        try:
            table.move_cursor(row=table.get_row_index(str(sid)))
        except Exception:
            pass
        self.show_current_detail()

    def _edit_cancelled(self) -> None:
        self._close_edit()
        self.show_current_detail()

    def _close_edit(self) -> None:
        """Remove the edit pane and restore the read-only detail view."""
        if self._edit_pane is not None:
            try:
                self._edit_pane.remove()
            except Exception:
                pass
            self._edit_pane = None
        self.query_one("#detail-view", RichLog).display = True

    def action_move_state(self) -> None:
        """Open modal to change the workflow state of the selected story/selection."""
        assert self.conn is not None
        ids = self._selected_ids()
        if self._multi_select and ids:
            self.push_screen(MoveStateScreen(self.conn, ids),
                             lambda _: self.refresh_stories())
            return
        sid = self._current_story_id()
        if sid is None:
            self.bell()
            return
        self.push_screen(MoveStateScreen(self.conn, [sid]),
                         lambda _: self.refresh_stories())

    def action_add_comment(self) -> None:
        """Open modal to add a comment to the selected story."""
        sid = self._current_story_id()
        if sid is None or self.conn is None:
            self.bell()
            return
        self.push_screen(TextScreen("Add comment"),
                         lambda text: self._do_comment(sid, text))

    def action_comment_action(self) -> None:
        """Open modal to edit or delete a comment on the selected story."""
        sid = self._current_story_id()
        if sid is None or self.conn is None:
            self.bell()
            return
        self.push_screen(CommentActionScreen(self.conn, sid),
                         lambda changed: self.show_current_detail() if changed else None)

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

    def action_manage_owners(self) -> None:
        """Open modal to add/remove owners on the selected story, or assign to all selected."""
        ids = self._selected_ids()
        if self._multi_select and ids:
            assert self.conn is not None
            self.push_screen(AssignOwnerScreen(self.conn),
                             lambda mid: self._assign_owner_to_all(mid, ids))
            return
        sid = self._current_story_id()
        if sid is None or self.conn is None:
            self.bell()
            return
        self.push_screen(OwnerScreen(self.conn, sid),
                         lambda changed: self.show_current_detail() if changed else None)

    def _assign_owner_to_all(self, mid: int | None, ids: list[int]) -> None:
        if mid is None or self.conn is None:
            return
        for sid in ids:
            stories.assign_owner(self.conn, sid, mid)
        self.show_current_detail()

    def action_manage_labels(self) -> None:
        """Open modal to add/remove labels on the selected story, or add a label
        to every selected story."""
        ids = self._selected_ids()
        if self._multi_select and ids:
            assert self.conn is not None
            self.push_screen(AssignLabelScreen(self.conn),
                             lambda lid: self._add_label_to_all(lid, ids))
            return
        sid = self._current_story_id()
        if sid is None or self.conn is None:
            self.bell()
            return
        self.push_screen(LabelScreen(self.conn, sid),
                         lambda changed: self.show_current_detail() if changed else None)

    def _add_label_to_all(self, lid: int | None, ids: list[int]) -> None:
        if lid is None or self.conn is None:
            return
        for sid in ids:
            stories.add_label(self.conn, sid, lid)
        self.show_current_detail()

    def action_manage_links(self) -> None:
        """Open modal to add or delete a link on the selected story."""
        sid = self._current_story_id()
        if sid is None or self.conn is None:
            self.bell()
            return
        self.push_screen(StoryLinkActionScreen(self.conn, sid),
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
        self.push_screen(FilterScreen(self.conn, (self.filters["project"],
                                                 self.filters["state_type"],
                                                 self.filters["owner"],
                                                 self.filters["label"])),
                         self._after_filter)

    def _after_filter(self, result: tuple | None) -> None:
        if result is None:
            return
        proj, stypes, owner, label = result
        self.filters["project"] = proj
        self.filters["state_type"] = stypes
        self.filters["owner"] = owner
        self.filters["label"] = label
        self.refresh_stories()

    def action_search(self) -> None:
        """Open modal to search stories by keyword."""
        self.push_screen(SearchInputScreen(), self._after_search)

    def action_manage_workflows(self) -> None:
        """Open the workflow & states management screen."""
        assert self.conn is not None
        self.push_screen(WorkflowManagerScreen(self.conn),
                         self._after_workflow_manage)

    def _after_workflow_manage(self, changed: bool | None) -> None:
        if changed:
            self.refresh_stories()

    def action_manage_epics(self) -> None:
        """Open the epic management screen."""
        assert self.conn is not None
        self.push_screen(EpicManagerScreen(self.conn), self._after_epic_manage)

    def _after_epic_manage(self, changed: bool | None) -> None:
        if changed:
            self.refresh_stories()

    def action_manage_iterations(self) -> None:
        """Open the iteration management screen."""
        assert self.conn is not None
        self.push_screen(IterationManagerScreen(self.conn),
                         self._after_iteration_manage)

    def _after_iteration_manage(self, changed: bool | None) -> None:
        if changed:
            self.refresh_stories()

    def action_manage_milestones(self) -> None:
        """Open the milestone management screen."""
        assert self.conn is not None
        self.push_screen(MilestoneManagerScreen(self.conn),
                         self._after_milestone_manage)

    def _after_milestone_manage(self, changed: bool | None) -> None:
        if changed:
            self.refresh_stories()

    def action_manage_projects(self) -> None:
        """Open the project management screen."""
        assert self.conn is not None
        self.push_screen(ProjectManagerScreen(self.conn),
                         self._after_project_manage)

    def _after_project_manage(self, changed: bool | None) -> None:
        if changed:
            self.refresh_stories()

    def action_manage_label_catalog(self) -> None:
        """Open the label management screen."""
        assert self.conn is not None
        self.push_screen(LabelManagerScreen(self.conn),
                         self._after_label_manage)

    def _after_label_manage(self, changed: bool | None) -> None:
        if changed:
            self.refresh_stories()

    def action_manage_member_catalog(self) -> None:
        """Open the member management screen."""
        assert self.conn is not None
        self.push_screen(MemberManagerScreen(self.conn),
                         self._after_member_manage)

    def _after_member_manage(self, changed: bool | None) -> None:
        if changed:
            self.refresh_stories()

    def action_manage_group_catalog(self) -> None:
        """Open the group management screen."""
        assert self.conn is not None
        self.push_screen(GroupManagerScreen(self.conn),
                         self._after_group_manage)

    def _after_group_manage(self, changed: bool | None) -> None:
        if changed:
            self.refresh_stories()

    def action_browse(self) -> None:
        """Open a menu to browse a container entity, then filter stories by it."""
        assert self.conn is not None
        self.push_screen(BrowseMenuScreen(), self._after_browse_menu)

    def _after_browse_menu(self, kind: str | None) -> None:
        if kind is None or self.conn is None:
            return
        self.push_screen(EntityBrowserScreen(self.conn, kind), self._after_browse)

    def _after_browse(self, result: tuple | None) -> None:
        if result is None:
            return
        kind, entity_id = result
        self.filters[kind] = entity_id
        self.refresh_stories()

    def _after_search(self, q: str | None) -> None:
        if q is None:
            return
        self.filters["q"] = q or None
        self.refresh_stories()

    def action_delete_story(self) -> None:
        """Open confirmation modal to delete the selected story or selection."""
        ids = self._selected_ids()
        if self._multi_select and ids:
            n = len(ids)
            self.push_screen(ConfirmScreen(f"Delete {n} selected stories?"),
                             lambda ok: self._after_bulk_delete(ok))
            return
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

    def _after_bulk_delete(self, ok: bool | None) -> None:
        if not ok:
            return
        assert self.conn is not None
        for sid in list(self._selected):
            stories.delete_story(self.conn, sid)
        self._selected.clear()
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


def run(db_path: str | None = None,
        auto_refresh: float | None = None) -> int:
    """Entry point for the TUI app used by main.py.

    Args:
        db_path: Path to the SQLite database.
        auto_refresh: Start auto-refreshing at this interval in seconds
            (default: off; the 'a' hotkey toggles it using a 1s interval).
    Returns:
        Exit code (0 for success).
    """
    PlannerApp(db_path, auto_refresh=auto_refresh).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
