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
    labels,
    members,
    milestones,
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
        yield VerticalScroll(
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
        Binding("o", "manage_owners", "Owners"),
        Binding("l", "manage_labels", "Labels"),
        Binding("f", "filter", "Filter"),
        Binding("b", "browse", "Browse"),
        Binding("slash", "search", "Search"),  # '/'
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
        self._auto_refresh_interval = auto_refresh if auto_refresh else 1
        # Multi-select (visual) mode: a set of selected story ids, toggled by Space.
        self._multi_select = False
        self._selected: set[int] = set()

    # --- lifecycle --------------------------------------------------------- #
    def on_mount(self) -> None:
        self.conn = db.connect(self.db_path)
        self.title = "Project Planner"
        self.refresh_stories()
        if self._auto_refresh_enabled:
            self.set_interval(self._auto_refresh_interval, self.refresh_stories, name="auto-refresh")

    def on_unmount(self) -> None:
        if self._auto_refresh_enabled:
            self.set_timer("auto-refresh", None)  # type: ignore[arg-type]
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
            self.set_timer("auto-refresh", None)  # type: ignore[arg-type]
            self.notify("Auto-refresh disabled", title="🔴")
        else:
            self._auto_refresh_enabled = True
            self.set_interval(self._auto_refresh_interval, self.refresh_stories, name="auto-refresh")
            self.notify(f"Auto-refresh enabled (every {self._auto_refresh_interval:.0f}s)", title="🟢")

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
