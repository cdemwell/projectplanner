"""Argparse-driven CLI over the backend.

Layout: ``python main.py <resource> <action> [flags]`` (or ``--json`` anywhere,
inherited via the common parent). Each action handler calls the matching
backend function and returns a value; :func:`run` formats it as text (default)
or JSON (``--json``). Human names are resolved to ids where a person would type
a name (projects, epics, iterations, milestones, groups, labels, members,
workflow states). Mutating commands print the resulting entity so an agent can
read back the assigned id.
"""

from __future__ import annotations

import argparse
import csv
import dataclasses
import json as _json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from io import StringIO
from typing import Any

from backend import (
    _util,
    comments,
    db,
    epics,
    errors,
    groups,
    iterations,
    labels,
    members,
    milestones,
    plan,
    projects,
    search,
    stories,
    story_links,
    tasks,
    workflows,
)

# --------------------------------------------------------------------------- #
# Output helpers
# --------------------------------------------------------------------------- #

def _jsonable(x: Any) -> Any:
    """Convert a backend return value into a JSON-serializable structure.

    Recurses through lists/tuples; dataclasses use ``to_dict`` when available
    (e.g. ``StoryDetail``), else ``dataclasses.asdict``.

    Args:
        x: A backend value (dataclass, list of dataclasses, dict, or scalar).
    Returns:
        A JSON-serializable representation of ``x``.
    """
    if isinstance(x, (list, tuple)):
        return [_jsonable(i) for i in x]
    if dataclasses.is_dataclass(x):
        if hasattr(x, "to_dict"):
            return x.to_dict()
        return dataclasses.asdict(x)
    return x


def _print_table(rows: list[dict], columns: list[str]) -> None:
    """Print a list of dicts as a left-justified text table (or ``(none)``).

    Args:
        rows: Row dicts (only ``columns`` keys are shown).
        columns: Column order to display.
    """
    if not rows:
        print("(none)")
        return
    str_rows = [{c: str(r.get(c, "")) for c in columns} for r in rows]
    widths = {c: max(len(c), max((len(sr[c]) for sr in str_rows), default=0))
              for c in columns}
    print("  ".join(c.ljust(widths[c]) for c in columns))
    print("  ".join("-" * widths[c] for c in columns))
    for sr in str_rows:
        print("  ".join(sr[c].ljust(widths[c]) for c in columns))


def _print_kv(d: dict) -> None:
    """Print a dict as ``key: value`` lines (used for single-entity detail)."""
    for k, v in d.items():
        print(f"{k}: {v}")


def _is_flat(value: Any) -> bool:
    """Check if a value is a flat dict/list (no nested dicts/lists)."""
    # Convert dataclasses to dict for checking
    if dataclasses.is_dataclass(value):
        value = value.to_dict() if hasattr(value, "to_dict") else dataclasses.asdict(value)
    if isinstance(value, list):
        for v in value:
            if dataclasses.is_dataclass(v):
                v = v.to_dict() if hasattr(v, "to_dict") else dataclasses.asdict(v)
            if not isinstance(v, dict):
                return False
            if any(isinstance(v2, (dict, list)) for v2 in v.values()):
                return False
        return True
    if isinstance(value, dict):
        return all(not isinstance(v, (dict, list)) for v in value.values())
    return False


def _fmt_csv(value: Any, *, include_headers: bool = True) -> None:
    """Render ``value`` as CSV to stdout.

    For lists of flat dicts: each dict is a row, headers are dict keys.
    For single flat dicts: single row with headers.
    For nested objects: prints a message and falls back to JSON.
    For other values: wrapped in a single column.
    """
    # Convert to jsonable for processing
    json_value = _jsonable(value)

    if isinstance(json_value, list):
        rows = json_value
        if not rows:
            return
        # Check if flat
        if _is_flat(value):
            fieldnames = list(rows[0].keys()) if rows else []
        else:
            print("CSV format not supported for nested data; use --format json", file=sys.stderr)
            print(_json.dumps(json_value, indent=2, default=str))
            return
    elif isinstance(json_value, dict):
        if _is_flat(value):
            rows = [json_value]
            fieldnames = list(json_value.keys())
        else:
            print("CSV format not supported for nested data; use --format json", file=sys.stderr)
            print(_json.dumps(json_value, indent=2, default=str))
            return
    else:
        rows = [{"value": json_value}]
        fieldnames = ["value"]

    buf = StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
    if include_headers:
        writer.writeheader()
    for row in rows:
        writer.writerow(row)
    print(buf.getvalue().rstrip("\n"))


def _fmt_id_only(value: Any) -> None:
    """Print just the ID(s) from the value, one per line."""
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict) and "id" in item:
                print(item["id"])
            elif hasattr(item, "id"):
                print(item.id)
    elif isinstance(value, dict):
        # Handle nested structures like StoryDetail
        if "story" in value and isinstance(value["story"], dict) and "id" in value["story"]:
            print(value["story"]["id"])
        elif "id" in value:
            print(value["id"])
    elif hasattr(value, "id"):
        print(value.id)
    elif hasattr(value, "story") and hasattr(value.story, "id"):
        print(value.story.id)


def emit(args: argparse.Namespace, value: Any, *, text_fn=None) -> None:
    """Render ``value`` according to ``--format`` (text/json/csv/id-only).

    Args:
        args: Namespace; ``args.format`` selects output format, ``args.json``
            is a deprecated alias for ``--format json``.
        value: The backend return value to render.
        text_fn: Optional ``(conn, value)`` text formatter for default text mode.
    """
    # Deprecated --json alias
    if getattr(args, "json", False):
        fmt = "json"
    else:
        fmt = getattr(args, "format", "text")

    if fmt == "json":
        print(_json.dumps(_jsonable(value), indent=2, default=str))
    elif fmt == "csv":
        _fmt_csv(value)
    elif fmt == "id-only":
        _fmt_id_only(value)
    elif fmt == "text":
        if text_fn is not None:
            text_fn(value)
        elif value is None:
            pass
        else:
            # Fallback: dump as key/value or table depending on shape.
            if isinstance(value, list) and value and isinstance(value[0], dict):
                _print_table(value, list(value[0]))
            else:
                print(_json.dumps(_jsonable(value), indent=2, default=str))
    else:
        # Should not happen due to argparse choices
        print(_json.dumps(_jsonable(value), indent=2, default=str))


# --------------------------------------------------------------------------- #
# Name -> id resolution
# --------------------------------------------------------------------------- #

def _looks_like_id(val: Any) -> bool:
    """True if ``val`` is a string that parses as an integer (a bare id)."""
    return isinstance(val, str) and val.strip().lstrip("-").isdigit()


def _resolve_named(conn, table: str, val: Any, *, entity: str,
                   cols=("name",)) -> int:
    """Resolve a human name (case-insensitive) or a bare id to a row id.

    Args:
        conn: sqlite3.Connection.
        table: Table to search (identifier is quoted via ``_util._q``).
        val: A name to look up, or a numeric id string.
        entity: Entity label for error messages.
        cols: Columns to match against (e.g. ``("name", "mention_name")``).
    Returns:
        The resolved integer id.
    Raises:
        NotFound: if no row matches the name.
        ValidationError: if the name matches more than one row (ambiguous).
    """
    if _looks_like_id(val):
        return int(val)
    cond = " OR ".join(f"LOWER({c}) = LOWER(?)" for c in cols)
    rows = conn.execute(f"SELECT id FROM {_util._q(table)} WHERE {cond}",
                        tuple([val] * len(cols))).fetchall()
    if not rows:
        raise errors.NotFound(entity, val)
    if len(rows) > 1:
        raise errors.ValidationError(
            f"ambiguous {entity} name {val!r}; use an id (matches: {[r[0] for r in rows]})")
    return rows[0][0]


def resolve_project(conn, v):   # noqa: E701
    """Resolve a project by name (case-insensitive) or id."""
    return _resolve_named(conn, "project", v, entity="project")
def resolve_epic(conn, v):
    """Resolve an epic by name (case-insensitive) or id."""
    return _resolve_named(conn, "epic", v, entity="epic")
def resolve_iteration(conn, v):
    """Resolve an iteration by name (case-insensitive) or id."""
    return _resolve_named(conn, "iteration", v, entity="iteration")
def resolve_milestone(conn, v):
    """Resolve a milestone by name (case-insensitive) or id."""
    return _resolve_named(conn, "milestone", v, entity="milestone")
def resolve_group(conn, v):
    """Resolve a group by name (case-insensitive) or id."""
    return _resolve_named(conn, "group", v, entity="group")
def resolve_label(conn, v):
    """Resolve a label by name (case-insensitive) or id."""
    return _resolve_named(conn, "label", v, entity="label")
def resolve_member(conn, v):
    """Resolve a member by name or mention_name (case-insensitive) or id."""
    return _resolve_named(conn, "member", v, entity="member",
                          cols=("name", "mention_name"))


def resolve_workflow_state(conn, val: Any) -> int:
    """Resolve a state by id, by name, or by type ('unstarted'/'started'/'done').

    Resolution order: numeric id → unique state name (case-insensitive) →
    state type (first state of that type). By-type lets ``--state done`` work
    against the seeded default workflow.

    Args:
        conn: sqlite3.Connection.
        val: An id, a state name, or a state type.
    Returns:
        The resolved state id.
    Raises:
        NotFound: if nothing matches.
        ValidationError: if the name matches more than one state.
    """
    if _looks_like_id(val):
        return int(val)
    # By name (case-insensitive).
    rows = conn.execute("SELECT id FROM workflow_state WHERE LOWER(name) = LOWER(?)",
                        (val,)).fetchall()
    if len(rows) == 1:
        return rows[0][0]
    if len(rows) > 1:
        raise errors.ValidationError(
            f"ambiguous state name {val!r}; use an id (matches: {[r[0] for r in rows]})")
    # By type.
    if val in workflows.STATE_TYPES:
        row = conn.execute("SELECT id FROM workflow_state WHERE type = ? ORDER BY id LIMIT 1",
                           (val,)).fetchone()
        if row is not None:
            return row[0]
    raise errors.NotFound("workflow_state", val)


def _split_csv(val: str | None) -> list[str] | None:
    """Split a comma-separated string into trimmed, non-empty parts (or None)."""
    if val is None:
        return None
    return [p.strip() for p in val.split(",") if p.strip()]


def _opt_id(conn, val, resolver):
    """Resolve ``val`` via ``resolver`` only when it is not None; else return None.

    Used for optional ``--project``/``--owner``/etc. flags.
    """
    return resolver(conn, val) if val is not None else None


# --------------------------------------------------------------------------- #
# $EDITOR flow for long-form text
# --------------------------------------------------------------------------- #
def _editor_command() -> list[str]:
    """Return the editor command (from $VISUAL, $EDITOR, or `vi`) as a list."""
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vi"
    return shlex.split(editor)


def edit_text(initial: str = "", suffix: str = ".md") -> str | None:
    """Open $EDITOR on a temp file pre-filled with ``initial``; return the result.

    Returns the edited file content, or None if the editor exited non-zero or
    no editor is available. The temp file is removed afterwards.

    Args:
        initial: Text to pre-fill the editor with.
        suffix: Temp-file suffix (affects editor syntax highlighting).
    """
    cmd = _editor_command()
    fd, path = tempfile.mkstemp(suffix=suffix)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(initial)
        try:
            rc = subprocess.call(cmd + [path])
        except FileNotFoundError:
            print(f"error: editor not found: {' '.join(cmd)}", file=sys.stderr)
            return None
        if rc != 0:
            return None
        with open(path) as f:
            return f.read()
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def parse_story_edit_template(content: str) -> tuple[str, str]:
    """Parse the ``story edit`` template: line 1 = name, blank line, rest = desc.

    Args:
        content: The full text returned from the editor.
    Returns:
        (name, description) — description may be "" and may span multiple lines.
    Raises:
        ValidationError: if the name (first non-empty line) is empty.
    """
    lines = content.splitlines()
    # Drop a single leading blank line so the user can leave line 1 empty safely.
    while lines and not lines[0].strip():
        lines.pop(0)
    if not lines or not lines[0].strip():
        raise errors.ValidationError("name is required (first line of the edit buffer)")
    name = lines[0].strip()
    rest = lines[1:]
    # Drop one separating blank line between name and description.
    if rest and not rest[0].strip():
        rest = rest[1:]
    description = "\n".join(rest).strip()
    return name, description


# --------------------------------------------------------------------------- #
# Text formatters
# --------------------------------------------------------------------------- #

def _story_rows(conn, items) -> list[dict]:
    """Build text-table row dicts for a list of stories, resolving names.

    State, project, and owners are looked up by id so the table is human-readable
    while stories themselves are still referenced by id.

    Args:
        conn: sqlite3.Connection.
        items: list[Story] to render.
    Returns:
        list[dict] with keys id, name, type, state, project, owners, done.
    """
    rows = []
    for s in items:
        state = ""
        if s.workflow_state_id is not None:
            r = conn.execute("SELECT name FROM workflow_state WHERE id = ?",
                             (s.workflow_state_id,)).fetchone()
            state = r["name"] if r else ""
        proj = ""
        if s.project_id is not None:
            r = conn.execute("SELECT name FROM project WHERE id = ?",
                             (s.project_id,)).fetchone()
            proj = r["name"] if r else ""
        owners = ",".join(m["mention_name"] for m in conn.execute(
            "SELECT m.mention_name AS mention_name FROM member m "
            "JOIN story_owner so ON so.member_id = m.id WHERE so.story_id = ?", (s.id,)))
        rows.append({"id": s.id, "name": s.name, "type": s.story_type,
                      "state": state, "project": proj, "owners": owners,
                      "done": "✓" if s.completed_at else ""})
    return rows


def _fmt_stories(conn, items):
    """Text formatter for ``story list`` / ``epic stories`` / etc."""
    _print_table(_story_rows(conn, items),
                 ["id", "name", "type", "state", "project", "owners", "done"])


def _fmt_one(conn, obj):
    """Text formatter for a single entity: prints ``key: value`` lines."""
    d = (obj.to_dict() if hasattr(obj, "to_dict")
         else dataclasses.asdict(obj) if dataclasses.is_dataclass(obj) else obj)
    _print_kv(d) if isinstance(d, dict) else print(d)


def _fmt_list_simple(items, columns):
    """Text formatter for a list of dataclasses: ``asdict`` each, then table."""
    rows = [dataclasses.asdict(i) if dataclasses.is_dataclass(i) else i for i in items]
    _print_table(rows, columns)


def _fmt_story_detail(conn, detail):
    """Text formatter for ``story detail`` (a :class:`StoryDetail`).

    Renders the story header, resolved state, parent ids, owners, labels,
    tasks (with completion marks), and ``completed_at`` if set.
    """
    s = detail.story
    state = detail.workflow_state
    state_str = f"{state.name} ({state.type})" if state else "(none)"
    print(f"#{s.id}  {s.name}  [{s.story_type}]")
    print(f"  state:      {state_str}")
    print(f"  project:    {s.project_id}    epic: {s.epic_id}    "
          f"iteration: {s.iteration_id}    group: {s.group_id}")
    print(f"  owners:     {', '.join(o.name for o in detail.owners) or '(none)'}")
    print(f"  labels:     {', '.join(lb.name for lb in detail.labels) or '(none)'}")
    if s.description:
        print(f"  description: {s.description}")
    print("  tasks:")
    if detail.tasks:
        for t in detail.tasks:
            mark = "x" if t.complete else " "
            print(f"    [{mark}] #{t.id} {t.description}")
    else:
        print("    (none)")
    if s.completed_at:
        print(f"  completed_at: {s.completed_at}")


# --------------------------------------------------------------------------- #
# Handlers — one per action. Each takes (conn, args: argparse.Namespace) and
# returns a backend value (entity, list, or a small status dict). They resolve
# human names to ids, call the matching backend function, and return the result
# for ``run`` to render.
# --------------------------------------------------------------------------- #

# -- stories -------------------------------------------------------------- #
def h_story_list(conn, a):
    """Handle ``story list``; resolve filter names and return matching stories."""
    return stories.list_stories(
        conn,
        project_id=resolve_project(conn, a.project) if a.project else None,
        epic_id=resolve_epic(conn, a.epic) if a.epic else None,
        iteration_id=resolve_iteration(conn, a.iteration) if a.iteration else None,
        state_type=a.state_type, group_id=resolve_group(conn, a.group) if a.group else None,
        owner_id=resolve_member(conn, a.owner) if a.owner else None,
        label_id=resolve_label(conn, a.label) if a.label else None,
        q=a.q, include_completed=a.include_completed,
        limit=a.limit, offset=a.offset)


def h_story_get(conn, a):
    """Handle ``story get``; return the story with the given id."""
    return stories.get_story(conn, int(a.id))


def h_story_detail(conn, a):
    """Handle ``story detail``; return a StoryDetail (story + relations)."""
    return stories.get_story_detail(conn, int(a.id))


def h_story_create(conn, a):
    """Handle ``story create``; resolve owners/labels/parents and return the new story."""
    owner_ids = [resolve_member(conn, o) for o in (_split_csv(a.owners) or [])]
    label_ids = [resolve_label(conn, lb) for lb in (_split_csv(a.labels) or [])]
    return stories.create_story(
        conn, a.name, description=a.desc or "", story_type=a.type,
        workflow_state_id=resolve_workflow_state(conn, a.state) if a.state else None,
        epic_id=_opt_id(conn, a.epic, resolve_epic),
        iteration_id=_opt_id(conn, a.iteration, resolve_iteration),
        project_id=_opt_id(conn, a.project, resolve_project),
        group_id=_opt_id(conn, a.group, resolve_group),
        requested_by_id=_opt_id(conn, a.requested_by, resolve_member),
        deadline=a.deadline, owner_ids=owner_ids, label_ids=label_ids)


def h_story_update(conn, a):
    """Handle ``story update``; map provided flags to editable fields and return the story."""
    fields = {}
    if a.name is not None: fields["name"] = a.name
    if a.desc is not None: fields["description"] = a.desc
    if a.type is not None: fields["story_type"] = a.type
    if a.project is not None: fields["project_id"] = resolve_project(conn, a.project)
    if a.epic is not None: fields["epic_id"] = resolve_epic(conn, a.epic)
    if a.iteration is not None: fields["iteration_id"] = resolve_iteration(conn, a.iteration)
    if a.group is not None: fields["group_id"] = resolve_group(conn, a.group)
    if a.deadline is not None: fields["deadline"] = a.deadline
    if a.position is not None: fields["position"] = a.position
    return stories.update_story(conn, int(a.id), **fields)


def h_story_edit(conn, a):
    """Handle ``story edit``; open $EDITOR on name+description and update them."""
    s = stories.get_story(conn, int(a.id))
    template = f"{s.name}\n\n{s.description}\n"
    content = edit_text(template, suffix=".md")
    if content is None:
        return {"aborted": "story edit", "id": int(a.id)}
    name, description = parse_story_edit_template(content)
    return stories.update_story(conn, int(a.id), name=name, description=description)


def h_story_move(conn, a):
    """Handle ``story move``; resolve the target state and move the story."""
    return stories.move_story_state(conn, int(a.id), resolve_workflow_state(conn, a.state))


def h_story_assign(conn, a):
    """Handle ``story assign``; add an owner and return the story."""
    stories.assign_owner(conn, int(a.id), resolve_member(conn, a.owner))
    return stories.get_story(conn, int(a.id))


def h_story_unassign(conn, a):
    """Handle ``story unassign``; remove an owner and return the story."""
    stories.remove_owner(conn, int(a.id), resolve_member(conn, a.owner))
    return stories.get_story(conn, int(a.id))


def h_story_label(conn, a):
    """Handle ``story label``; add a label and return the story."""
    stories.add_label(conn, int(a.id), resolve_label(conn, a.label))
    return stories.get_story(conn, int(a.id))


def h_story_unlabel(conn, a):
    """Handle ``story unlabel``; remove a label and return the story."""
    stories.remove_label(conn, int(a.id), resolve_label(conn, a.label))
    return stories.get_story(conn, int(a.id))


def h_story_delete(conn, a):
    """Handle ``story delete``; delete and return a ``{deleted, id}`` status."""
    stories.delete_story(conn, int(a.id))
    return {"deleted": "story", "id": int(a.id)}


# -- epics ---------------------------------------------------------------- #
def h_epic_list(conn, a):
    """Handle ``epic list``; resolve project/milestone filters and return epics."""
    return epics.list_epics(
        conn,
        project_id=resolve_project(conn, a.project) if a.project else None,
        milestone_id=resolve_milestone(conn, a.milestone) if a.milestone else None,
        limit=a.limit, offset=a.offset)


def h_epic_create(conn, a):
    """Handle ``epic create``; resolve project/milestone and return the new epic."""
    return epics.create_epic(conn, a.name, description=a.desc or "", state=a.state,
                             milestone_id=_opt_id(conn, a.milestone, resolve_milestone),
                             project_id=_opt_id(conn, a.project, resolve_project))


def h_epic_update(conn, a):
    """Handle ``epic update``; map provided flags to fields and return the epic."""
    fields = {k: v for k, v in dict(name=a.name, description=a.desc, state=a.state,
             project_id=resolve_project(conn, a.project) if a.project else None,
             milestone_id=resolve_milestone(conn, a.milestone) if a.milestone else None).items()
             if v is not None}
    return epics.update_epic(conn, int(a.id), **fields)


# -- iterations ----------------------------------------------------------- #
def h_iteration_list(conn, a):
    """Handle ``iteration list``; optionally filter by status."""
    return iterations.list_iterations(conn, status=a.status, limit=a.limit, offset=a.offset)
def h_iteration_create(conn, a):
    """Handle ``iteration create``; return the new iteration."""
    return iterations.create_iteration(conn, a.name, description=a.desc or "",
                                        status=a.status, start_date=a.start,
                                        end_date=a.end)
def h_iteration_update(conn, a):
    """Handle ``iteration update``; map provided flags to fields and return it."""
    fields = {k: v for k, v in dict(name=a.name, description=a.desc, status=a.status,
             start_date=a.start, end_date=a.end).items() if v is not None}
    return iterations.update_iteration(conn, int(a.id), **fields)


# -- milestones ------------------------------------------------------------ #
def h_milestone_list(conn, a):
    """Handle ``milestone list``; optionally filter by state."""
    return milestones.list_milestones(conn, state=a.state, limit=a.limit, offset=a.offset)
def h_milestone_create(conn, a):
    """Handle ``milestone create``; return the new milestone."""
    return milestones.create_milestone(conn, a.name, description=a.desc or "", state=a.state)
def h_milestone_update(conn, a):
    """Handle ``milestone update``; map provided flags to fields and return it."""
    fields = {k: v for k, v in dict(name=a.name, description=a.desc, state=a.state).items()
             if v is not None}
    return milestones.update_milestone(conn, int(a.id), **fields)


# -- projects -------------------------------------------------------------- #
def h_project_list(conn, a):
    """Handle ``project list``; include archived only if ``--archived``."""
    return projects.list_projects(conn, include_archived=a.archived,
                                  limit=a.limit, offset=a.offset)
def h_project_create(conn, a):
    """Handle ``project create``; return the new project."""
    return projects.create_project(conn, a.name, description=a.desc or "",
                                   abbreviation=a.abbr or "", color=a.color or "")
def h_project_update(conn, a):
    """Handle ``project update``; map provided flags (incl. archive) and return it."""
    fields = {k: v for k, v in dict(name=a.name, description=a.desc, abbreviation=a.abbr,
             color=a.color, archived=1 if a.archive else 0 if a.archive is False else None).items()
             if v is not None}
    return projects.update_project(conn, int(a.id), **fields)


# -- labels ---------------------------------------------------------------- #
def h_label_list(conn, a):
    """Handle ``label list``; return all labels."""
    return labels.list_labels(conn, limit=a.limit, offset=a.offset)
def h_label_create(conn, a):
    """Handle ``label create``; return the new label."""
    return labels.create_label(conn, a.name, color=a.color or "", description=a.desc or "")
def h_label_update(conn, a):
    """Handle ``label update``; map provided flags to fields and return it."""
    fields = {k: v for k, v in dict(name=a.name, color=a.color, description=a.desc).items()
             if v is not None}
    return labels.update_label(conn, int(a.id), **fields)


# -- members --------------------------------------------------------------- #
def h_member_list(conn, a):
    """Handle ``member list``; return all members."""
    return members.list_members(conn, limit=a.limit, offset=a.offset)
def h_member_create(conn, a):
    """Handle ``member create``; return the new member."""
    return members.create_member(conn, a.name, mention_name=a.mention)
def h_member_update(conn, a):
    """Handle ``member update``; map provided flags to fields and return it."""
    fields = {k: v for k, v in dict(name=a.name, mention_name=a.mention).items() if v is not None}
    return members.update_member(conn, int(a.id), **fields)


# -- groups ---------------------------------------------------------------- #
def h_group_list(conn, a):
    """Handle ``group list``; include archived only if ``--archived``."""
    return groups.list_groups(conn, include_archived=a.archived,
                              limit=a.limit, offset=a.offset)
def h_group_create(conn, a):
    """Handle ``group create``; return the new group."""
    return groups.create_group(conn, a.name, description=a.desc or "")
def h_group_update(conn, a):
    """Handle ``group update``; map provided flags (incl. archive) and return it."""
    fields = {k: v for k, v in dict(name=a.name, description=a.desc,
             archived=1 if a.archive else 0 if a.archive is False else None).items() if v is not None}
    return groups.update_group(conn, int(a.id), **fields)


# -- workflows ------------------------------------------------------------- #
def h_workflow_list(conn, a):
    """Handle ``workflow list``; return all workflows."""
    return workflows.list_workflows(conn)
def h_workflow_create(conn, a):
    """Handle ``workflow create``; parse ``--states name:type,...`` and return the workflow."""
    states = []
    for item in (_split_csv(a.states) or []):
        name, _, stype = item.partition(":")
        states.append({"name": name, "type": stype or "unstarted"})
    return workflows.create_workflow(conn, a.name, states=states or None)
def h_workflow_states(conn, a):
    """Handle ``workflow states``; list the states of a workflow."""
    return workflows.list_workflow_states(conn, int(a.id))
def h_workflow_add_state(conn, a):
    """Handle ``workflow add-state``; add a state to a workflow and return it."""
    return workflows.create_workflow_state(conn, int(a.id), a.name, a.type, position=a.position)


# -- tasks ----------------------------------------------------------------- #
def h_task_list(conn, a):
    """Handle ``task list``; return tasks for a story."""
    return tasks.list_tasks(conn, int(a.story))
def h_task_add(conn, a):
    """Handle ``task add``; create a task on a story and return it.

    With no ``--desc``, opens $EDITOR for the task description.
    """
    desc = a.desc
    if desc is None:
        desc = edit_text("", suffix=".md")
        if desc is None:
            return {"aborted": "task add", "story": int(a.story)}
        desc = desc.strip()
    if not desc:
        raise errors.ValidationError("task description is required")
    return tasks.create_task(conn, int(a.story), desc, complete=a.complete)
def h_task_update(conn, a):
    """Handle ``task update``; map provided flags to fields and return the task."""
    fields = {k: v for k, v in dict(description=a.desc, complete=1 if a.complete else 0 if a.complete is False else None,
             position=a.position).items() if v is not None}
    return tasks.update_task(conn, int(a.id), **fields)
def h_task_complete(conn, a):
    """Handle ``task complete``; mark a task complete."""
    return tasks.complete_task(conn, int(a.id), True)
def h_task_uncomplete(conn, a):
    """Handle ``task uncomplete``; mark a task incomplete."""
    return tasks.complete_task(conn, int(a.id), False)


# -- comments -------------------------------------------------------------- #
def h_comment_list(conn, a):
    """Handle ``comment list``; return comments for a story."""
    return comments.list_comments(conn, int(a.story))
def h_comment_add(conn, a):
    """Handle ``comment add``; create a comment (optionally threaded) and return it.

    With no ``--text``, opens $EDITOR for the comment body.
    """
    text = a.text
    if text is None:
        text = edit_text("", suffix=".md")
        if text is None:
            return {"aborted": "comment add", "story": int(a.story)}
        text = text.strip()
    if not text:
        raise errors.ValidationError("comment text is required")
    return comments.create_comment(conn, int(a.story), text,
                                    author_id=_opt_id(conn, a.author, resolve_member),
                                    parent_id=int(a.parent) if a.parent else None)
def h_comment_update(conn, a):
    """Handle ``comment update``; update comment text and return it."""
    return comments.update_comment(conn, int(a.id), text=a.text)


# -- links ----------------------------------------------------------------- #
def h_link_list(conn, a):
    """Handle ``link list``; return links, optionally filtered to one story."""
    return story_links.list_links(conn, int(a.story) if a.story else None)
def h_link_add(conn, a):
    """Handle ``link add``; create a directed story link and return it."""
    return story_links.create_link(conn, int(a.subject), a.verb, int(a.object))


# -- search --------------------------------------------------------------- #
def h_search(conn, a):
    """Handle ``search``; join query terms and return ranked SearchResults."""
    return search.search(conn, " ".join(a.query), entity=a.entity,
                         limit=a.limit, offset=a.offset)


# -- plan ----------------------------------------------------------------- #
def h_plan_export(conn, a):
    """Handle ``plan export``; write a JSON snapshot to a file and return counts."""
    data = plan.export_to_file(conn, a.file)
    total = sum(len(v) for k, v in data.items() if k != "_meta")
    return {"file": a.file, "exported": total}


def h_plan_import(conn, a):
    """Handle ``plan import``; restore a JSON snapshot and return counts."""
    counts = plan.import_from_file(conn, a.file)
    return {"file": a.file, "imported": counts}


# --------------------------------------------------------------------------- #
# Parser construction
# --------------------------------------------------------------------------- #

COMMON = argparse.ArgumentParser(add_help=False)
# SUPPRESS default so a subparser doesn't clobber a --json given on the top
# parser (e.g. `main.py --json story list`); the flag is only set when present.
# This is what makes `--json` work both before and after the subcommand.
COMMON.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                    help="emit JSON (machine-readable) [deprecated: use --format json]")
COMMON.add_argument("--format", choices=["text", "json", "csv", "id-only"], default="text",
                    help="output format (default: text)")
COMMON.add_argument("--dry-run", action="store_true", default=argparse.SUPPRESS,
                    help="run without modifying the database")


def _sp(parent, name, **kw):
    """Add an action subparser that inherits the common ``--json`` flag."""
    return parent.add_parser(name, parents=[COMMON], **kw)


def _id_arg(p):
    """Add the standard positional ``id`` argument to an action subparser."""
    p.add_argument("id", help="entity id")


def _paging(p):
    """Add ``--limit``/``--offset`` paging flags to a list/search subparser."""
    p.add_argument("--limit", type=int, help="max rows to return")
    p.add_argument("--offset", type=int, help="rows to skip before returning")


def build_parser() -> argparse.ArgumentParser:
    """Construct the full argparse CLI: one subparser per resource, one nested
    subparser per action, each wiring ``func`` (a handler) and ``fmt`` (a text
    formatter). The top parser also carries ``--json`` (default False) and
    ``--db``. Returns the parser."""
    parser = argparse.ArgumentParser(prog="projectplanner",
                                     description="Local project planner (Shortcut-model-based).")
    parser.add_argument("--json", action="store_true", default=False)
    parser.add_argument("--db", help="path to planner.db (default: ./planner.db)")
    parser.add_argument("--dry-run", action="store_true", default=False,
                        help="run without modifying the database")
    sub = parser.add_subparsers(dest="resource", required=True)

    # story ------------------------------------------------------------------
    sp = sub.add_parser("story", parents=[COMMON])
    asp = sp.add_subparsers(dest="action", required=True)
    p = _sp(asp, "list")
    p.add_argument("--project"); p.add_argument("--epic"); p.add_argument("--iteration")
    p.add_argument("--state-type", choices=list(workflows.STATE_TYPES))
    p.add_argument("--group"); p.add_argument("--owner"); p.add_argument("--label")
    p.add_argument("--q"); p.add_argument("--no-completed", dest="include_completed",
                                          action="store_false", default=True)
    _paging(p)
    p.set_defaults(func=h_story_list, fmt=lambda c, v: _fmt_stories(c, v))
    p = _sp(asp, "get"); _id_arg(p); p.set_defaults(func=h_story_get, fmt=_fmt_one)
    p = _sp(asp, "detail"); _id_arg(p); p.set_defaults(func=h_story_detail, fmt=_fmt_story_detail)
    p = _sp(asp, "create")
    p.add_argument("--name", required=True); p.add_argument("--desc"); p.add_argument("--type",
            choices=list(stories.STORY_TYPES), default="feature")
    p.add_argument("--state"); p.add_argument("--project"); p.add_argument("--epic")
    p.add_argument("--iteration"); p.add_argument("--group"); p.add_argument("--requested-by")
    p.add_argument("--deadline"); p.add_argument("--owners", help="comma-separated member names/ids")
    p.add_argument("--labels", help="comma-separated label names/ids")
    p.set_defaults(func=h_story_create, fmt=_fmt_one)
    p = _sp(asp, "update"); _id_arg(p)
    for f in ("--name", "--desc", "--project", "--epic", "--iteration", "--group", "--deadline"):
        p.add_argument(f)
    p.add_argument("--type", choices=list(stories.STORY_TYPES))
    p.add_argument("--position", type=float)
    p.set_defaults(func=h_story_update, fmt=_fmt_one)
    p = _sp(asp, "edit"); _id_arg(p)
    p.set_defaults(func=h_story_edit, fmt=_fmt_one)
    p = _sp(asp, "move"); _id_arg(p); p.add_argument("--state", required=True)
    p.set_defaults(func=h_story_move, fmt=_fmt_one)
    p = _sp(asp, "assign"); _id_arg(p); p.add_argument("--owner", required=True)
    p.set_defaults(func=h_story_assign, fmt=_fmt_one)
    p = _sp(asp, "unassign"); _id_arg(p); p.add_argument("--owner", required=True)
    p.set_defaults(func=h_story_unassign, fmt=_fmt_one)
    p = _sp(asp, "label"); _id_arg(p); p.add_argument("--label", required=True)
    p.set_defaults(func=h_story_label, fmt=_fmt_one)
    p = _sp(asp, "unlabel"); _id_arg(p); p.add_argument("--label", required=True)
    p.set_defaults(func=h_story_unlabel, fmt=_fmt_one)
    p = _sp(asp, "delete"); _id_arg(p); p.set_defaults(func=h_story_delete, fmt=_fmt_one)

    # epic -------------------------------------------------------------------
    sp = sub.add_parser("epic", parents=[COMMON])
    asp = sp.add_subparsers(dest="action", required=True)
    p = _sp(asp, "list"); p.add_argument("--project"); p.add_argument("--milestone"); _paging(p)
    p.set_defaults(func=h_epic_list, fmt=lambda c, v: _fmt_list_simple(v, ["id", "name", "state", "project_id", "milestone_id"]))
    p = _sp(asp, "get"); _id_arg(p); p.set_defaults(func=lambda c, a: epics.get_epic(c, int(a.id)), fmt=_fmt_one)
    p = _sp(asp, "create"); p.add_argument("--name", required=True); p.add_argument("--desc")
    p.add_argument("--state", default="planned"); p.add_argument("--project"); p.add_argument("--milestone")
    p.set_defaults(func=h_epic_create, fmt=_fmt_one)
    p = _sp(asp, "update"); _id_arg(p); p.add_argument("--name"); p.add_argument("--desc")
    p.add_argument("--state"); p.add_argument("--project"); p.add_argument("--milestone")
    p.set_defaults(func=h_epic_update, fmt=_fmt_one)
    p = _sp(asp, "delete"); _id_arg(p)
    p.set_defaults(func=lambda c, a: (epics.delete_epic(c, int(a.id)), {"deleted": "epic", "id": int(a.id)})[1], fmt=_fmt_one)
    p = _sp(asp, "stories"); _id_arg(p)
    p.set_defaults(func=lambda c, a: epics.list_epic_stories(c, int(a.id)), fmt=lambda c, v: _fmt_stories(c, v))

    # iteration --------------------------------------------------------------
    sp = sub.add_parser("iteration", parents=[COMMON])
    asp = sp.add_subparsers(dest="action", required=True)
    p = _sp(asp, "list"); p.add_argument("--status", choices=list(iterations.STATUSES)); _paging(p)
    p.set_defaults(func=h_iteration_list, fmt=lambda c, v: _fmt_list_simple(v, ["id", "name", "status", "start_date", "end_date"]))
    p = _sp(asp, "get"); _id_arg(p); p.set_defaults(func=lambda c, a: iterations.get_iteration(c, int(a.id)), fmt=_fmt_one)
    p = _sp(asp, "create"); p.add_argument("--name", required=True); p.add_argument("--desc")
    p.add_argument("--status", default="planned"); p.add_argument("--start"); p.add_argument("--end")
    p.set_defaults(func=h_iteration_create, fmt=_fmt_one)
    p = _sp(asp, "update"); _id_arg(p); p.add_argument("--name"); p.add_argument("--desc")
    p.add_argument("--status"); p.add_argument("--start"); p.add_argument("--end")
    p.set_defaults(func=h_iteration_update, fmt=_fmt_one)
    p = _sp(asp, "delete"); _id_arg(p)
    p.set_defaults(func=lambda c, a: (iterations.delete_iteration(c, int(a.id)), {"deleted": "iteration", "id": int(a.id)})[1], fmt=_fmt_one)
    p = _sp(asp, "stories"); _id_arg(p)
    p.set_defaults(func=lambda c, a: iterations.list_iteration_stories(c, int(a.id)), fmt=lambda c, v: _fmt_stories(c, v))

    # milestone --------------------------------------------------------------
    sp = sub.add_parser("milestone", parents=[COMMON])
    asp = sp.add_subparsers(dest="action", required=True)
    p = _sp(asp, "list"); p.add_argument("--state", choices=list(milestones.STATES)); _paging(p)
    p.set_defaults(func=h_milestone_list, fmt=lambda c, v: _fmt_list_simple(v, ["id", "name", "state", "completed_at"]))
    p = _sp(asp, "get"); _id_arg(p); p.set_defaults(func=lambda c, a: milestones.get_milestone(c, int(a.id)), fmt=_fmt_one)
    p = _sp(asp, "create"); p.add_argument("--name", required=True); p.add_argument("--desc")
    p.add_argument("--state", default="planned")
    p.set_defaults(func=h_milestone_create, fmt=_fmt_one)
    p = _sp(asp, "update"); _id_arg(p); p.add_argument("--name"); p.add_argument("--desc"); p.add_argument("--state")
    p.set_defaults(func=h_milestone_update, fmt=_fmt_one)
    p = _sp(asp, "delete"); _id_arg(p)
    p.set_defaults(func=lambda c, a: (milestones.delete_milestone(c, int(a.id)), {"deleted": "milestone", "id": int(a.id)})[1], fmt=_fmt_one)
    p = _sp(asp, "epics"); _id_arg(p)
    p.set_defaults(func=lambda c, a: milestones.list_milestone_epics(c, int(a.id)), fmt=lambda c, v: _fmt_list_simple(v, ["id", "name", "state", "project_id"]))

    # project ---------------------------------------------------------------
    sp = sub.add_parser("project", parents=[COMMON])
    asp = sp.add_subparsers(dest="action", required=True)
    p = _sp(asp, "list"); p.add_argument("--archived", action="store_true"); _paging(p)
    p.set_defaults(func=h_project_list, fmt=lambda c, v: _fmt_list_simple(v, ["id", "name", "abbreviation", "color", "archived"]))
    p = _sp(asp, "get"); _id_arg(p); p.set_defaults(func=lambda c, a: projects.get_project(c, int(a.id)), fmt=_fmt_one)
    p = _sp(asp, "create"); p.add_argument("--name", required=True); p.add_argument("--desc")
    p.add_argument("--abbr"); p.add_argument("--color")
    p.set_defaults(func=h_project_create, fmt=_fmt_one)
    p = _sp(asp, "update"); _id_arg(p); p.add_argument("--name"); p.add_argument("--desc")
    p.add_argument("--abbr"); p.add_argument("--color")
    p.add_argument("--archive", dest="archive", action="store_true", default=None)
    p.add_argument("--no-archive", dest="archive", action="store_false")
    p.set_defaults(func=h_project_update, fmt=_fmt_one)
    p = _sp(asp, "archive"); _id_arg(p)
    p.set_defaults(func=lambda c, a: projects.archive_project(c, int(a.id), True), fmt=_fmt_one)
    p = _sp(asp, "delete"); _id_arg(p)
    p.set_defaults(func=lambda c, a: (projects.delete_project(c, int(a.id)), {"deleted": "project", "id": int(a.id)})[1], fmt=_fmt_one)
    p = _sp(asp, "stories"); _id_arg(p)
    p.set_defaults(func=lambda c, a: projects.list_project_stories(c, int(a.id)), fmt=lambda c, v: _fmt_stories(c, v))

    # label -----------------------------------------------------------------
    sp = sub.add_parser("label", parents=[COMMON])
    asp = sp.add_subparsers(dest="action", required=True)
    p = _sp(asp, "list"); _paging(p); p.set_defaults(func=h_label_list, fmt=lambda c, v: _fmt_list_simple(v, ["id", "name", "color", "description"]))
    p = _sp(asp, "get"); _id_arg(p); p.set_defaults(func=lambda c, a: labels.get_label(c, int(a.id)), fmt=_fmt_one)
    p = _sp(asp, "create"); p.add_argument("--name", required=True); p.add_argument("--color"); p.add_argument("--desc")
    p.set_defaults(func=h_label_create, fmt=_fmt_one)
    p = _sp(asp, "update"); _id_arg(p); p.add_argument("--name"); p.add_argument("--color"); p.add_argument("--desc")
    p.set_defaults(func=h_label_update, fmt=_fmt_one)
    p = _sp(asp, "delete"); _id_arg(p)
    p.set_defaults(func=lambda c, a: (labels.delete_label(c, int(a.id)), {"deleted": "label", "id": int(a.id)})[1], fmt=_fmt_one)

    # member ----------------------------------------------------------------
    sp = sub.add_parser("member", parents=[COMMON])
    asp = sp.add_subparsers(dest="action", required=True)
    p = _sp(asp, "list"); _paging(p); p.set_defaults(func=h_member_list, fmt=lambda c, v: _fmt_list_simple(v, ["id", "name", "mention_name"]))
    p = _sp(asp, "get"); _id_arg(p); p.set_defaults(func=lambda c, a: members.get_member(c, int(a.id)), fmt=_fmt_one)
    p = _sp(asp, "create"); p.add_argument("--name", required=True); p.add_argument("--mention")
    p.set_defaults(func=h_member_create, fmt=_fmt_one)
    p = _sp(asp, "update"); _id_arg(p); p.add_argument("--name"); p.add_argument("--mention")
    p.set_defaults(func=h_member_update, fmt=_fmt_one)
    p = _sp(asp, "delete"); _id_arg(p)
    p.set_defaults(func=lambda c, a: (members.delete_member(c, int(a.id)), {"deleted": "member", "id": int(a.id)})[1], fmt=_fmt_one)

    # group -----------------------------------------------------------------
    sp = sub.add_parser("group", parents=[COMMON])
    asp = sp.add_subparsers(dest="action", required=True)
    p = _sp(asp, "list"); p.add_argument("--archived", action="store_true"); _paging(p)
    p.set_defaults(func=h_group_list, fmt=lambda c, v: _fmt_list_simple(v, ["id", "name", "description", "archived"]))
    p = _sp(asp, "get"); _id_arg(p); p.set_defaults(func=lambda c, a: groups.get_group(c, int(a.id)), fmt=_fmt_one)
    p = _sp(asp, "create"); p.add_argument("--name", required=True); p.add_argument("--desc")
    p.set_defaults(func=h_group_create, fmt=_fmt_one)
    p = _sp(asp, "update"); _id_arg(p); p.add_argument("--name"); p.add_argument("--desc")
    p.add_argument("--archive", dest="archive", action="store_true", default=None)
    p.add_argument("--no-archive", dest="archive", action="store_false")
    p.set_defaults(func=h_group_update, fmt=_fmt_one)
    p = _sp(asp, "archive"); _id_arg(p)
    p.set_defaults(func=lambda c, a: groups.archive_group(c, int(a.id), True), fmt=_fmt_one)
    p = _sp(asp, "delete"); _id_arg(p)
    p.set_defaults(func=lambda c, a: (groups.delete_group(c, int(a.id)), {"deleted": "group", "id": int(a.id)})[1], fmt=_fmt_one)
    p = _sp(asp, "stories"); _id_arg(p)
    p.set_defaults(func=lambda c, a: groups.list_group_stories(c, int(a.id)), fmt=lambda c, v: _fmt_stories(c, v))

    # workflow --------------------------------------------------------------
    sp = sub.add_parser("workflow", parents=[COMMON])
    asp = sp.add_subparsers(dest="action", required=True)
    p = _sp(asp, "list"); p.set_defaults(func=h_workflow_list, fmt=lambda c, v: _fmt_list_simple(v, ["id", "name", "default_state_id"]))
    p = _sp(asp, "get"); _id_arg(p); p.set_defaults(func=lambda c, a: workflows.get_workflow(c, int(a.id)), fmt=_fmt_one)
    p = _sp(asp, "create"); p.add_argument("--name", required=True)
    p.add_argument("--states", help="comma list of name:type (e.g. Todo:unstarted,Doing:started,Done:done)")
    p.set_defaults(func=h_workflow_create, fmt=_fmt_one)
    p = _sp(asp, "states"); _id_arg(p); p.set_defaults(func=h_workflow_states, fmt=lambda c, v: _fmt_list_simple(v, ["id", "name", "type", "position"]))
    p = _sp(asp, "add-state"); _id_arg(p); p.add_argument("--name", required=True)
    p.add_argument("--type", required=True, choices=list(workflows.STATE_TYPES)); p.add_argument("--position", type=float)
    p.set_defaults(func=h_workflow_add_state, fmt=_fmt_one)
    p = _sp(asp, "delete"); _id_arg(p)
    p.set_defaults(func=lambda c, a: (workflows.delete_workflow(c, int(a.id)), {"deleted": "workflow", "id": int(a.id)})[1], fmt=_fmt_one)

    # task ------------------------------------------------------------------
    sp = sub.add_parser("task", parents=[COMMON])
    asp = sp.add_subparsers(dest="action", required=True)
    p = _sp(asp, "list"); p.add_argument("--story", required=True); p.set_defaults(func=h_task_list, fmt=lambda c, v: _fmt_list_simple(v, ["id", "description", "complete", "position", "completed_at"]))
    p = _sp(asp, "add"); p.add_argument("--story", required=True); p.add_argument("--desc", help="task description (opens $EDITOR if omitted)")
    p.add_argument("--complete", action="store_true"); p.set_defaults(func=h_task_add, fmt=_fmt_one)
    p = _sp(asp, "update"); _id_arg(p); p.add_argument("--desc"); p.add_argument("--complete", dest="complete", action="store_true", default=None)
    p.add_argument("--no-complete", dest="complete", action="store_false"); p.add_argument("--position", type=float)
    p.set_defaults(func=h_task_update, fmt=_fmt_one)
    p = _sp(asp, "complete"); _id_arg(p); p.set_defaults(func=h_task_complete, fmt=_fmt_one)
    p = _sp(asp, "uncomplete"); _id_arg(p); p.set_defaults(func=h_task_uncomplete, fmt=_fmt_one)
    p = _sp(asp, "delete"); _id_arg(p)
    p.set_defaults(func=lambda c, a: (tasks.delete_task(c, int(a.id)), {"deleted": "task", "id": int(a.id)})[1], fmt=_fmt_one)

    # comment ---------------------------------------------------------------
    sp = sub.add_parser("comment", parents=[COMMON])
    asp = sp.add_subparsers(dest="action", required=True)
    p = _sp(asp, "list"); p.add_argument("--story", required=True); p.set_defaults(func=h_comment_list, fmt=lambda c, v: _fmt_list_simple(v, ["id", "story_id", "author_id", "text", "parent_id", "created_at"]))
    p = _sp(asp, "add"); p.add_argument("--story", required=True); p.add_argument("--text", help="comment text (opens $EDITOR if omitted)")
    p.add_argument("--author"); p.add_argument("--parent", type=int); p.set_defaults(func=h_comment_add, fmt=_fmt_one)
    p = _sp(asp, "update"); _id_arg(p); p.add_argument("--text", required=True); p.set_defaults(func=h_comment_update, fmt=_fmt_one)
    p = _sp(asp, "delete"); _id_arg(p)
    p.set_defaults(func=lambda c, a: (comments.delete_comment(c, int(a.id)), {"deleted": "comment", "id": int(a.id)})[1], fmt=_fmt_one)

    # link ------------------------------------------------------------------
    sp = sub.add_parser("link", parents=[COMMON])
    asp = sp.add_subparsers(dest="action", required=True)
    p = _sp(asp, "list"); p.add_argument("--story", type=int); p.set_defaults(func=h_link_list, fmt=lambda c, v: _fmt_list_simple(v, ["id", "subject_story_id", "verb", "object_story_id"]))
    p = _sp(asp, "add"); p.add_argument("--subject", required=True, type=int)
    p.add_argument("--verb", required=True, choices=list(story_links.VERBS)); p.add_argument("--object", required=True, type=int)
    p.set_defaults(func=h_link_add, fmt=_fmt_one)
    p = _sp(asp, "delete"); _id_arg(p)
    p.set_defaults(func=lambda c, a: (story_links.delete_link(c, int(a.id)), {"deleted": "link", "id": int(a.id)})[1], fmt=_fmt_one)

    # search ----------------------------------------------------------------
    sp = sub.add_parser("search", parents=[COMMON])
    sp.add_argument("query", nargs="+", help="search terms (FTS5 syntax, e.g. 'login bug')")
    sp.add_argument("--entity", choices=["story", "epic", "project", "milestone",
                                          "iteration", "label", "comment", "task"])
    _paging(sp)
    sp.set_defaults(func=h_search, fmt=lambda c, v: _fmt_list_simple(v, ["entity", "id", "name", "rank"]))

    # plan -------------------------------------------------------------------
    sp = sub.add_parser("plan", parents=[COMMON])
    asp = sp.add_subparsers(dest="action", required=True)
    p = _sp(asp, "export"); p.add_argument("--file", default="planner-export.json")
    p.set_defaults(func=h_plan_export, fmt=_fmt_one)
    p = _sp(asp, "import"); p.add_argument("--file", required=True)
    p.set_defaults(func=h_plan_import, fmt=_fmt_one)

    return parser


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #

def run(argv: list[str] | None = None) -> int:
    """Parse ``argv``, open a connection, run the action, and render output.

    A ``PlannerError`` is caught and printed to stderr as ``error: <msg>`` with
    exit code 1; the connection is always closed.

    Args:
        argv: Argument list (defaults to ``sys.argv[1:]`` via parse_args).
    Returns:
        0 on success, 1 on a backend error.
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    db_path = args.db if getattr(args, "db", None) else db.DEFAULT_DB_PATH
    is_dry_run = getattr(args, "dry_run", False)

    tmp_path = None
    if is_dry_run:
        print("[dry-run]")
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            tmp_path = tmp.name
            if os.path.exists(db_path):
                shutil.copy2(db_path, tmp_path)

    conn_path = tmp_path if is_dry_run else db_path
    conn = db.connect(conn_path)
    try:
        value = args.func(conn, args)
        # Always use emit for consistent format handling (--json is deprecated alias for --format json)
        # The custom formatter is passed to emit for text mode
        if getattr(args, "fmt", None) is not None:
            emit(args, value, text_fn=lambda v: args.fmt(conn, v))
        else:
            emit(args, value)
    except errors.PlannerError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    finally:
        conn.close()
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    return 0


if __name__ == "__main__":
    sys.exit(run())
