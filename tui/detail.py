"""Reusable read-only entity detail pane for the TUI.

Refactors the story-detail rendering (formerly written directly into a
``RichLog`` in :mod:`tui.app`) into a generic :class:`EntityDetailPane` that can
render *any* entity kind. Each kind has a small, data-driven layout describing
which fields to render and which related-entity links to show. Related links are
rendered as keyboard-addressable :class:`RelatedLink` widgets that carry the
target entity kind + id, so a later story can wire real navigation to them.

All data is fetched through the existing backend ``get_*``/``list_*`` functions;
the TUI adds no parallel query logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from textual.containers import Vertical
from textual.widgets import Static

from backend import (
    comments,
    epics,
    groups,
    iterations,
    labels,
    members,
    milestones,
    projects,
    stories,
    story_links,
    workflows,
)


@dataclass
class Field:
    """A single ``label: value`` row in a detail layout."""

    label: str
    value: str


@dataclass
class Link:
    """A link to a related entity, carrying the target kind + id.

    ``label`` is the display text (usually the related entity's name). ``entity``
    and ``id`` are the navigation target so a later story can jump to it.
    """

    entity: str
    id: int
    label: str


@dataclass
class Section:
    """A titled block of plain text lines (e.g. tasks, comments)."""

    title: str
    lines: list[str]


@dataclass
class DetailModel:
    """Structured rendering model for one entity's detail view."""

    title: str
    fields: list[Field] = field(default_factory=list)
    links: list[Link] = field(default_factory=list)
    sections: list[Section] = field(default_factory=list)


# Backend getter per entity kind — used to resolve related-entity names without
# duplicating query logic in the UI.
_GETTERS: dict[str, object] = {
    "story": stories.get_story,
    "epic": epics.get_epic,
    "iteration": iterations.get_iteration,
    "milestone": milestones.get_milestone,
    "project": projects.get_project,
    "group": groups.get_group,
    "label": labels.get_label,
    "member": members.get_member,
    "workflow": workflows.get_workflow,
    "workflow_state": workflows.get_workflow_state,
}


def _entity_name(conn, kind: str, id: int | None) -> str | None:
    """Resolve an entity's display name via its backend getter (None if missing)."""
    if id is None:
        return None
    getter = _GETTERS.get(kind)
    if getter is None:
        return str(id)
    try:
        obj = getter(conn, id)
    except Exception:
        return None
    return str(getattr(obj, "name", id))


# --- Layout builders ------------------------------------------------------ #
def _story_model(conn, id: int) -> DetailModel:
    detail = stories.get_story_detail(conn, id)
    s = detail.story
    title = f"#{s.id}  {s.name}  [{s.story_type}]"
    st = detail.workflow_state
    fields = [Field("state", f"{st.name} ({st.type})" if st else "(none)")]
    if s.description:
        fields.append(Field("desc", s.description))
    fields.append(Field("owners", ", ".join(o.name for o in detail.owners) or "-"))
    fields.append(Field("labels", ", ".join(lb.name for lb in detail.labels) or "-"))

    links = []
    for kind, key in (("project", s.project_id), ("epic", s.epic_id),
                      ("iteration", s.iteration_id), ("group", s.group_id),
                      ("member", s.requested_by_id)):
        if key is not None:
            links.append(Link(kind, key, _entity_name(conn, kind, key) or "-"))
    if s.workflow_state_id is not None:
        links.append(Link("workflow_state", s.workflow_state_id,
                          _entity_name(conn, "workflow_state", s.workflow_state_id)))

    task_lines = [f"  [{'x' if t.complete else ' '}] #{t.id} {t.description}"
                  for t in detail.tasks] or ["  (none)"]
    comment_lines = []
    for cm in comments.list_comments(conn, id):
        author = _entity_name(conn, "member", cm.author_id) or "-"
        indent = "    " if cm.parent_id else "  "
        comment_lines.append(f"{indent}#{cm.id} {author}: {cm.text}")
    if not comment_lines:
        comment_lines = ["  (none)"]
    sections = [Section("tasks", task_lines), Section("comments", comment_lines)]

    link_lines = []
    for lk in story_links.list_links(conn, id):
        subj = _entity_name(conn, "story", lk.subject_story_id) or str(lk.subject_story_id)
        obj = _entity_name(conn, "story", lk.object_story_id) or str(lk.object_story_id)
        link_lines.append(f"  #{lk.id} {subj} --{lk.verb}--> {obj}")
    if link_lines:
        sections.append(Section("links", link_lines))
    if s.completed_at:
        fields.append(Field("completed", s.completed_at))
    return DetailModel(title=title, fields=fields, links=links, sections=sections)


def _epic_model(conn, id: int) -> DetailModel:
    e = epics.get_epic(conn, id)
    title = f"#{e.id}  {e.name}  [{e.state}]"
    fields = [Field("desc", e.description)] if e.description else []
    if e.completed_at:
        fields.append(Field("completed", e.completed_at))
    links = []
    if e.milestone_id is not None:
        links.append(Link("milestone", e.milestone_id,
                          _entity_name(conn, "milestone", e.milestone_id) or "-"))
    if e.project_id is not None:
        links.append(Link("project", e.project_id,
                          _entity_name(conn, "project", e.project_id) or "-"))
    child_lines = [f"  #{st.id} {st.name}" for st in epics.list_epic_stories(conn, id)]
    sections = [Section("stories", child_lines or ["  (none)"])]
    return DetailModel(title=title, fields=fields, links=links, sections=sections)


def _iteration_model(conn, id: int) -> DetailModel:
    it = iterations.get_iteration(conn, id)
    title = f"#{it.id}  {it.name}  [{it.status}]"
    fields = [Field("desc", it.description)] if it.description else []
    if it.start_date:
        fields.append(Field("start", it.start_date))
    if it.end_date:
        fields.append(Field("end", it.end_date))
    return DetailModel(title=title, fields=fields)


def _milestone_model(conn, id: int) -> DetailModel:
    m = milestones.get_milestone(conn, id)
    title = f"#{m.id}  {m.name}  [{m.state}]"
    fields = [Field("desc", m.description)] if m.description else []
    if m.completed_at:
        fields.append(Field("completed", m.completed_at))
    return DetailModel(title=title, fields=fields)


def _project_model(conn, id: int) -> DetailModel:
    p = projects.get_project(conn, id)
    title = f"#{p.id}  {p.name}  [{p.abbreviation}]"
    fields = []
    if p.description:
        fields.append(Field("desc", p.description))
    fields.append(Field("color", p.color))
    return DetailModel(title=title, fields=fields)


def _group_model(conn, id: int) -> DetailModel:
    g = groups.get_group(conn, id)
    title = f"#{g.id}  {g.name}"
    fields = [Field("desc", g.description)] if g.description else []
    fields.append(Field("archived", "yes" if g.archived else "no"))
    return DetailModel(title=title, fields=fields)


def _label_model(conn, id: int) -> DetailModel:
    lb = labels.get_label(conn, id)
    title = f"#{lb.id}  {lb.name}"
    fields = [Field("desc", lb.description)] if lb.description else []
    fields.append(Field("color", lb.color))
    return DetailModel(title=title, fields=fields)


def _member_model(conn, id: int) -> DetailModel:
    m = members.get_member(conn, id)
    title = f"#{m.id}  {m.name}  (@{m.mention_name})"
    return DetailModel(title=title, fields=[Field("created", m.created_at)])


def _workflow_model(conn, id: int) -> DetailModel:
    w = workflows.get_workflow(conn, id)
    title = f"#{w.id}  {w.name}"
    default = _entity_name(conn, "workflow_state", w.default_state_id)
    fields = [Field("default state", default or "(none)")]
    return DetailModel(title=title, fields=fields)


def _workflow_state_model(conn, id: int) -> DetailModel:
    ws = workflows.get_workflow_state(conn, id)
    title = f"#{ws.id}  {ws.name}  [{ws.type}]"
    fields = [Field("workflow", _entity_name(conn, "workflow", ws.workflow_id) or "-")]
    if ws.description:
        fields.append(Field("desc", ws.description))
    return DetailModel(title=title, fields=fields)


def _task_model(conn, id: int) -> DetailModel:
    from backend import tasks
    t = tasks.get_task(conn, id)
    title = f"#{t.id}  {t.description[:40]}  [{'x' if t.complete else ' '}]"
    fields = [
        Field("desc", t.description),
        Field("complete", "yes" if t.complete else "no"),
        Field("position", str(t.position)),
    ]
    if t.completed_at:
        fields.append(Field("completed", t.completed_at))
    links = [Link("story", t.story_id, _entity_name(conn, "story", t.story_id) or str(t.story_id))]
    return DetailModel(title=title, fields=fields, links=links)


_BUILDERS: dict[str, object] = {
    "story": _story_model,
    "epic": _epic_model,
    "iteration": _iteration_model,
    "milestone": _milestone_model,
    "project": _project_model,
    "group": _group_model,
    "label": _label_model,
    "member": _member_model,
    "workflow": _workflow_model,
    "workflow_state": _workflow_state_model,
    "task": _task_model,
}


def build_model(conn, kind: str, id: int) -> DetailModel:
    """Build the detail rendering model for an entity.

    Args:
        conn: sqlite3.Connection from db.connect().
        kind: The entity kind, e.g. ``"story"``.
        id: The entity id.

    Returns:
        DetailModel: the structured rendering content.

    Raises:
        KeyError: if the kind has no layout builder.
    """
    builder = _BUILDERS[kind]
    return builder(conn, id)


# --- Widgets -------------------------------------------------------------- #
class RelatedLink(Static):
    """A keyboard-addressable link to a related entity.

    Carries the navigation target (``entity`` kind + ``id``) so a later story can
    drill into it. Focusable so the user can Tab to and activate it.
    """

    can_focus = True

    def __init__(self, entity: str, target_id: int, label: str) -> None:
        super().__init__(f"→ {label}  ({entity} #{target_id})", classes="detail-link")
        self.entity = entity
        self.target_id = target_id


class EntityDetailPane(Vertical):
    """Read-only detail pane that renders any entity kind.

    Replaces the old story-only ``RichLog`` detail. Call :meth:`show` to render an
    entity (read-only) or :meth:`show_message` for a one-off message.
    """

    DEFAULT_CSS = """
    EntityDetailPane { padding: 0 1; }
    .detail-title { text-style: bold; margin-bottom: 1; }
    .detail-field { margin-bottom: 0; }
    .detail-section { text-style: bold; color: $text-muted; margin-top: 1; }
    .detail-link {
        color: $accent; text-style: bold;
        padding: 0 1;
    }
    .detail-link:focus { background: $primary; color: $text; }
    """

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)
        # (kind, id) currently rendered, or None for a message view. Used to
        # assert which entity the pane tracks (e.g. bug 90's focus sync).
        self._current: tuple[str, int] | None = None

    def show(self, conn, kind: str, id: int) -> None:
        """Render an entity's details read-only into this pane.

        Args:
            conn: sqlite3.Connection from db.connect().
            kind: The entity kind, e.g. ``"story"``.
            id: The entity id.
        """
        model = build_model(conn, kind, id)
        self._current = (kind, id)
        self._populate(model)

    def show_message(self, message: str) -> None:
        """Render a plain message in place of a detail view."""
        self._current = None
        self.remove_children()
        self.mount(Static(message, classes="detail-field"))

    def _populate(self, model: DetailModel) -> None:
        self.remove_children()
        widgets = [Static(model.title, classes="detail-title")]
        widgets += [Static(f"{f.label}: {f.value}", classes="detail-field")
                    for f in model.fields]
        if model.links:
            widgets.append(Static("related", classes="detail-section"))
            widgets += [RelatedLink(lk.entity, lk.id, lk.label) for lk in model.links]
        for sec in model.sections:
            widgets.append(Static(sec.title, classes="detail-section"))
            widgets += [Static(line, classes="detail-field") for line in sec.lines]
        self.mount(*widgets)

    def related_links(self) -> list[RelatedLink]:
        """The related-entity links currently rendered in the pane."""
        return list(self.query(RelatedLink))
