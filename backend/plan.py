"""Export/import the whole plan as a portable JSON snapshot.

``export_plan`` dumps every entity (members, groups, workflows + states,
projects, labels, milestones, epics, iterations, stories, owners/labels, tasks,
comments, story links) into a JSON-serializable dict. ``import_plan`` restores
such a snapshot into a database: it wipes all existing content rows and re-inserts
the snapshot inside one ``BEGIN IMMEDIATE`` transaction, remapping primary keys so
foreign-key links survive even though ids change. This is how plan state is shared
with an agent in a new environment (portable, diff-friendly).

The snapshot intentionally excludes ``schema_version`` and the FTS tables (the
target database creates its own schema and FTS indexes are maintained by triggers).
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from . import _util, db

# Import order (parents before children so FK remapping always has a mapping).
# Each entry: table -> (columns to insert, {fk_col: source_table}).
# ``default_state_id`` on workflow is handled specially (needs states to exist).
_TABLES: dict[str, tuple[list[str], dict[str, str]]] = {
    "member": (["name", "mention_name", "created_at"], {}),
    "group": (["name", "description", "archived", "created_at"], {}),
    "workflow": (["name", "created_at"], {}),  # default_state_id set post-states
    "workflow_state": (["workflow_id", "name", "type", "position", "created_at"],
                       {"workflow_id": "workflow"}),
    "project": (["name", "description", "abbreviation", "color", "archived", "created_at"], {}),
    "label": (["name", "color", "description", "created_at"], {}),
    "milestone": (["name", "description", "state", "created_at", "completed_at"], {}),
    "iteration": (["name", "description", "status", "start_date", "end_date", "created_at"], {}),
    "epic": (["name", "description", "state", "milestone_id", "project_id",
              "created_at", "completed_at"],
             {"milestone_id": "milestone", "project_id": "project"}),
    "story": (["name", "description", "story_type", "workflow_state_id", "epic_id",
               "iteration_id", "project_id", "group_id", "requested_by_id",
               "deadline", "position", "created_at", "updated_at", "completed_at"],
              {"workflow_state_id": "workflow_state", "epic_id": "epic",
               "iteration_id": "iteration", "project_id": "project",
               "group_id": "group", "requested_by_id": "member"}),
    "story_owner": (["story_id", "member_id"], {"story_id": "story", "member_id": "member"}),
    "story_label": (["story_id", "label_id"], {"story_id": "story", "label_id": "label"}),
    "task": (["story_id", "description", "complete", "position", "created_at", "completed_at"],
             {"story_id": "story"}),
    "story_comment": (["story_id", "author_id", "text", "parent_id", "created_at", "updated_at"],
                      {"story_id": "story", "author_id": "member", "parent_id": "story_comment"}),
    "story_link": (["subject_story_id", "verb", "object_story_id", "created_at"],
                   {"subject_story_id": "story", "object_story_id": "story"}),
}

# Reverse order of _TABLES for a safe wipe (children before parents).
_WIPE_ORDER = ["story_link", "story_comment", "task", "story_label", "story_owner",
               "story", "epic", "iteration", "milestone", "label", "project",
               "workflow_state", "workflow", "group", "member"]


def export_plan(conn: sqlite3.Connection) -> dict[str, Any]:
    """Read every content table into a dict of row-dicts keyed by table name.

    Args:
        conn: sqlite3.Connection from db.connect().
    Returns:
        dict mapping table name -> list of row dicts (with ``id`` and all columns).
    """
    data: dict[str, Any] = {"_meta": {"schema_version": 1, "tables": list(_TABLES)}}
    for table in _TABLES:
        rows = conn.execute(f'SELECT * FROM "{table}"').fetchall()
        data[table] = [dict(r) for r in rows]
    return data


def export_to_file(conn: sqlite3.Connection, path: str) -> dict[str, Any]:
    """Export the plan to ``path`` as JSON and return the export dict.

    Args:
        conn: sqlite3.Connection from db.connect().
        path: Destination file path.
    Returns:
        The exported data dict.
    """
    data = export_plan(conn)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    return data


def import_plan(conn: sqlite3.Connection, data: dict[str, Any]) -> dict[str, int]:
    """Restore a snapshot into the database, replacing existing content.

    Runs in one ``BEGIN IMMEDIATE`` transaction; on any error it rolls back so
    the database is left unchanged. Ids are remapped so relationships survive.

    Args:
        conn: sqlite3.Connection from db.connect().
        data: A dict from :func:`export_plan` (or a JSON file with that shape).
    Returns:
        dict of table name -> number of rows imported.
    Raises:
        ValueError: if the snapshot is missing a required table.
    """
    missing = [t for t in _TABLES if t not in data]
    if missing:
        raise ValueError(f"snapshot is missing tables: {missing}")

    counts: dict[str, int] = {}
    with db.tx_write(conn):
        # 1) Wipe existing content (children before parents).
        for table in _WIPE_ORDER:
            conn.execute(f'DELETE FROM "{table}"')
        # 2) Insert in dependency order, remapping ids.
        id_map: dict[str, dict[int, int]] = {t: {} for t in _TABLES}
        workflow_defaults: list[tuple[int, int | None]] = []  # (new_id, old_default)
        for table, (cols, fk_map) in _TABLES.items():
            n = 0
            # A self-referential FK (e.g. story_comment.parent_id) cannot rely
            # on row order: a child row may list its parent ahead of it. Those
            # columns are deferred — the row is inserted with the FK unset, and
            # it is filled in once the whole table is staged.
            self_fk_cols = [c for c in cols if fk_map.get(c) == table]
            deferred_self_fk: list[tuple[int, str, int]] = []  # (new_id, col, old_val)
            for row in data[table]:
                values: dict[str, Any] = {}
                for c in cols:
                    v = row.get(c)
                    if v is not None and c in fk_map and c not in self_fk_cols:
                        ref_table = fk_map[c]
                        if v not in id_map[ref_table]:
                            raise KeyError(f"FK remap failed: table={table}, col={c}, ref_table={ref_table}, old_id={v}, available_ids={list(id_map[ref_table].keys())}")
                        v = id_map[ref_table][v]  # remap FK to new id
                    values[c] = v
                deferred_rows: dict[str, int | None] = {
                    c: values.pop(c) for c in self_fk_cols}
                if table == "workflow":
                    # default_state_id points at a state not created yet.
                    new_id = _util.insert(conn, table, values)
                    workflow_defaults.append((new_id, row.get("default_state_id")))
                    if "id" in row:
                        id_map[table][row["id"]] = new_id
                else:
                    new_id = _util.insert(conn, table, values)
                    if "id" in row:
                        id_map[table][row["id"]] = new_id
                for c, old in deferred_rows.items():
                    if old is not None:
                        deferred_self_fk.append((new_id, c, old))
                n += 1
            # Fill in the deferred self-referential links (parents now mapped).
            for new_id, col, old in deferred_self_fk:
                ref_table = fk_map[col]
                if old not in id_map[ref_table]:
                    raise KeyError(f"FK remap failed: table={table}, col={col}, "
                                   f"ref_table={ref_table}, old_id={old}")
                conn.execute(f'UPDATE "{table}" SET {col} = ? WHERE id = ?',
                             (id_map[ref_table][old], new_id))
            counts[table] = n
        # 3) Point workflows at their (remapped) default state.
        for new_wf_id, old_default in workflow_defaults:
            if old_default is not None:
                conn.execute('UPDATE "workflow" SET default_state_id = ? WHERE id = ?',
                             (id_map["workflow_state"][old_default], new_wf_id))
    return counts


def import_from_file(conn: sqlite3.Connection, path: str) -> dict[str, int]:
    """Load a JSON snapshot from ``path`` and import it.

    Args:
        conn: sqlite3.Connection from db.connect().
        path: Source JSON file path.
    Returns:
        The import counts (from :func:`import_plan`).
    """
    with open(path) as f:
        data = json.load(f)
    return import_plan(conn, data)
