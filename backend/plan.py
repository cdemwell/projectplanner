"""Export/import the whole plan as a portable JSON snapshot.

``export_plan`` dumps every entity (members, groups, workflows + states,
projects, labels, milestones, epics, iterations, stories, owners/labels, tasks,
comments, story links) into a JSON-serializable dict. ``import_plan`` restores
such a snapshot into a database: it wipes all existing content rows and re-inserts
the snapshot inside one ``BEGIN IMMEDIATE`` transaction, remapping primary keys so
foreign-key links survive even though ids change. This is how plan state is shared
with an agent in a new environment (portable, diff-friendly).

Import is a destructive replace-all over an *untrusted* file, so every snapshot
is fully validated before anything is wiped (:func:`_validate_snapshot`): the
``_meta`` header and its format version, column types against the live schema,
enum-bearing columns, foreign-key references *within* the snapshot, and overall
size/row caps. A snapshot that fails validation raises
:class:`~backend.errors.ValidationError` and leaves the database untouched.

The snapshot intentionally excludes ``schema_version`` and the FTS tables (the
target database creates its own schema and FTS indexes are maintained by triggers).
"""

from __future__ import annotations

import json
import math
import sqlite3
from pathlib import Path
from typing import Any

from . import _util, db, errors
from .epics import STATES as _EPIC_STATES
from .iterations import STATUSES as _ITERATION_STATUSES
from .milestones import STATES as _MILESTONE_STATES
from .stories import STORY_TYPES as _STORY_TYPES
from .story_links import VERBS as _LINK_VERBS
from .workflows import STATE_TYPES as _STATE_TYPES

# Version of the snapshot *format* written by export_plan and required by
# import_plan. Independent of the database schema version (db.CURRENT_SCHEMA_
# VERSION): bump this only when the snapshot layout itself changes, and bump
# both writers and readers together.
SNAPSHOT_FORMAT_VERSION = 1

# Caps on untrusted snapshots, so a crafted import can neither exhaust memory
# while decoding nor insert an unbounded number of rows. Generous for real
# plans; module-level so tests (and callers) can tighten them.
MAX_SNAPSHOT_BYTES = 128 * 1024 * 1024   # source file size, bytes
MAX_SNAPSHOT_ROWS = 250_000              # total rows across all tables
MAX_ROWS_PER_TABLE = 100_000             # rows in any single table

# Columns validated against a fixed value set (mirrors the DB CHECK constraints
# declared in db.py's migrations).
_ENUMS: dict[tuple[str, str], frozenset[str]] = {
    ("story", "story_type"): frozenset(_STORY_TYPES),
    ("workflow_state", "type"): frozenset(_STATE_TYPES),
    ("milestone", "state"): frozenset(_MILESTONE_STATES),
    ("epic", "state"): frozenset(_EPIC_STATES),
    ("iteration", "status"): frozenset(_ITERATION_STATUSES),
    ("story_link", "verb"): frozenset(_LINK_VERBS),
}

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


# --------------------------------------------------------------------------- #
# Snapshot validation — runs fully before anything is wiped
# --------------------------------------------------------------------------- #

def _invalid(where: str, msg: str) -> errors.ValidationError:
    """Build a ValidationError locating a problem inside the snapshot."""
    return errors.ValidationError(f"invalid snapshot: {where}: {msg}")


def _table_kinds(conn: sqlite3.Connection) -> dict[str, dict[str, tuple[str, bool]]]:
    """Declared column kinds per snapshot table, read from the live schema.

    Returns ``table -> column -> (kind, notnull)`` where ``kind`` is one of
    ``"text"``/``"int"``/``"real"`` (derived from the SQLite declared type).
    Reading this from the target database keeps validation in sync with the
    schema the import actually writes into.

    Args:
        conn: sqlite3.Connection from db.connect().
    Returns:
        dict mapping table -> column -> (kind, notnull) tuples.
    """
    kinds: dict[str, dict[str, tuple[str, bool]]] = {}
    for table in _TABLES:
        cols: dict[str, tuple[str, bool]] = {}
        for row in conn.execute(f"PRAGMA table_info({_util._q(table)})"):
            decl = (row["type"] or "").upper()
            if "INT" in decl:
                kind = "int"
            elif "REAL" in decl or "FLOA" in decl or "DOUB" in decl:
                kind = "real"
            else:
                kind = "text"
            cols[row["name"]] = (kind, bool(row["notnull"]))
        kinds[table] = cols
    return kinds


def _check_value(table: str, col: str, kind: str, v: Any) -> None:
    """Reject a snapshot value that cannot be stored in the declared column.

    JSON ``true``/``false`` are accepted for int/real columns (SQLite stores
    them as 1/0), matching its type affinity. NaN/infinite floats are rejected.
    Date-ish text columns are NOT format-checked on purpose: the free-text
    forms users already have in their databases (e.g. hand-set deadlines) must
    survive a round-trip.

    Args:
        table: The snapshot table the value came from.
        col: The column the value is bound for.
        kind: One of "text", "int", "real".
        v: The value read from the (untrusted) JSON.
    Raises:
        errors.ValidationError: if the value does not fit the column.
    """
    if kind == "text":
        ok = isinstance(v, str)
    elif kind == "int":
        ok = isinstance(v, int)
    else:  # real
        ok = isinstance(v, (int, float)) and not (
            isinstance(v, float) and (math.isnan(v) or math.isinf(v)))
    if not ok:
        raise _invalid(f"{table}.{col}",
                       f"expected {kind}, got {type(v).__name__} ({v!r})")


def _check_fk(table: str, col: str, where: str, v: Any,
              ref_table: str, id_sets: dict[str, set[int]]) -> None:
    """Check that a snapshot FK value points at a row inside the snapshot.

    Args:
        table: The table the referencing row came from.
        col: The referencing column.
        where: Human-readable row locator for error messages.
        v: The FK value (may be None, which is always allowed).
        ref_table: The table the FK references.
        id_sets: Map of table -> set of ids present in the snapshot.
    Raises:
        errors.ValidationError: if the reference cannot be remapped on import.
    """
    if v is None:
        return
    if not isinstance(v, int) or isinstance(v, bool):
        raise _invalid(f"{where}.{col}",
                       f"FK must be an int, got {type(v).__name__} ({v!r})")
    if not id_sets[ref_table]:
        raise _invalid(f"{where}.{col}",
                       f"references {ref_table}, whose rows carry no ids to remap")
    if v not in id_sets[ref_table]:
        raise _invalid(f"{where}.{col}",
                       f"references {ref_table} id {v}, which is not in the snapshot")


def _validate_snapshot(conn: sqlite3.Connection, data: dict[str, Any]) -> None:
    """Validate an untrusted snapshot before any destructive step.

    Checks, in order: the ``_meta`` header and its ``schema_version``; that
    every required table is present and is a list; per-table and total row
    caps; that every row is an object whose values fit the target schema
    (types, NOT NULL, enum columns); and that every foreign key — including
    ``workflow.default_state_id``, which is resolved late — points at a row
    inside the snapshot. Nothing here mutates the database.

    Args:
        conn: sqlite3.Connection from db.connect().
        data: The parsed snapshot (a dict keyed by table name).
    Raises:
        errors.ValidationError: on the first problem found, with a message
            locating the offending table/row/column.
    """
    meta = data.get("_meta")
    if not isinstance(meta, dict):
        raise errors.ValidationError(
            "invalid snapshot: missing or malformed _meta header "
            f"(this tool writes format version {SNAPSHOT_FORMAT_VERSION})")
    version = meta.get("schema_version")
    if version is None:
        raise errors.ValidationError(
            "invalid snapshot: _meta.schema_version is missing "
            f"(expected {SNAPSHOT_FORMAT_VERSION})")
    if version != SNAPSHOT_FORMAT_VERSION:
        raise errors.ValidationError(
            f"unsupported snapshot schema_version {version!r}; expected "
            f"{SNAPSHOT_FORMAT_VERSION} — re-export with a compatible version")

    missing = [t for t in _TABLES if t not in data]
    if missing:
        raise errors.ValidationError(f"snapshot is missing tables: {missing}")

    # Size caps first: fail fast on a snapshot that is too big to be sane.
    total = 0
    for table in _TABLES:
        rows = data[table]
        if not isinstance(rows, list):
            raise _invalid(table, f"expected a list of rows, got {type(rows).__name__}")
        if len(rows) > MAX_ROWS_PER_TABLE:
            raise _invalid(table,
                           f"{len(rows)} rows exceeds the {MAX_ROWS_PER_TABLE} row cap")
        total += len(rows)
    if total > MAX_SNAPSHOT_ROWS:
        raise errors.ValidationError(
            f"invalid snapshot: {total} rows exceeds the {MAX_SNAPSHOT_ROWS} row cap")

    # Snapshot ids, used to verify that every FK points inside the snapshot.
    id_sets: dict[str, set[int]] = {}
    for table in _TABLES:
        ids: set[int] = set()
        for i, row in enumerate(data[table]):
            if not isinstance(row, dict):
                raise _invalid(f"{table}[{i}]", "row must be an object")
            rid = row.get("id")
            if rid is None:
                continue
            if not isinstance(rid, int) or isinstance(rid, bool):
                raise _invalid(f"{table}[{i}].id",
                               f"expected int, got {type(rid).__name__} ({rid!r})")
            ids.add(rid)
        id_sets[table] = ids

    kinds = _table_kinds(conn)
    for table in _TABLES:
        cols, fk_map = _TABLES[table]
        decls = kinds[table]
        for i, row in enumerate(data[table]):
            where = f"{table}[{i}]"
            for c in cols:
                kind, notnull = decls[c]
                if c not in row:
                    if notnull:
                        raise _invalid(f"{where}.{c}",
                                       "missing value for a NOT NULL column")
                    continue
                v = row[c]
                if v is None:
                    if notnull:
                        raise _invalid(f"{where}.{c}", "NULL in a NOT NULL column")
                    continue
                _check_value(table, c, kind, v)
                enumeration = _ENUMS.get((table, c))
                if enumeration is not None and v not in enumeration:
                    raise _invalid(f"{where}.{c}",
                                   f"{v!r} is not one of {sorted(enumeration)}")
            for c, ref_table in fk_map.items():
                _check_fk(table, c, where, row.get(c), ref_table, id_sets)
            if table == "workflow":
                # Resolved after the states are inserted; validated like any FK.
                _check_fk(table, "default_state_id", where,
                          row.get("default_state_id"), "workflow_state", id_sets)


def import_plan(conn: sqlite3.Connection, data: dict[str, Any]) -> dict[str, int]:
    """Restore a snapshot into the database, replacing existing content.

    Runs in one ``BEGIN IMMEDIATE`` transaction; on any error it rolls back so
    the database is left unchanged. Ids are remapped so relationships survive.
    The snapshot is validated *before* the wipe, so a malformed snapshot is
    rejected without touching existing rows.

    Args:
        conn: sqlite3.Connection from db.connect().
        data: A dict from :func:`export_plan` (or a JSON file with that shape).
    Returns:
        dict of table name -> number of rows imported.
    Raises:
        errors.ValidationError: if the snapshot is missing required tables or
            fields, has an unsupported ``_meta.schema_version``, is oversized,
            or contains malformed values or dangling references.
    """
    if not isinstance(data, dict):
        raise errors.ValidationError(
            "invalid snapshot: expected a JSON object keyed by table name, "
            f"got {type(data).__name__}")
    _validate_snapshot(conn, data)

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
            for row in data[table]:
                values: dict[str, Any] = {}
                for c in cols:
                    v = row.get(c)
                    if v is not None and c in fk_map:
                        ref_table = fk_map[c]
                        if v not in id_map[ref_table]:
                            raise KeyError(f"FK remap failed: table={table}, col={c}, ref_table={ref_table}, old_id={v}, available_ids={list(id_map[ref_table].keys())}")
                        v = id_map[ref_table][v]  # remap FK to new id
                    values[c] = v
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
                n += 1
            counts[table] = n
        # 3) Point workflows at their (remapped) default state.
        for new_wf_id, old_default in workflow_defaults:
            if old_default is not None:
                conn.execute('UPDATE "workflow" SET default_state_id = ? WHERE id = ?',
                             (id_map["workflow_state"][old_default], new_wf_id))
    return counts


def import_from_file(conn: sqlite3.Connection, path: str) -> dict[str, int]:
    """Load a JSON snapshot from ``path`` and import it.

    The file size is capped (:data:`MAX_SNAPSHOT_BYTES`) before decoding, so an
    oversized file is rejected before it can consume memory. JSON parse errors
    (including pathologically nested documents) surface as clear
    :class:`~backend.errors.ValidationError` messages rather than tracebacks.

    Args:
        conn: sqlite3.Connection from db.connect().
        path: Source JSON file path.
    Returns:
        The import counts (from :func:`import_plan`).
    Raises:
        errors.ValidationError: if the file is missing, oversized, not valid
            JSON, or fails snapshot validation.
    """
    snapshot = Path(path)
    if not snapshot.is_file():
        raise errors.ValidationError(f"snapshot file not found: {path}")
    size = snapshot.stat().st_size
    if size > MAX_SNAPSHOT_BYTES:
        raise errors.ValidationError(
            f"invalid snapshot: {path} is {size} bytes, over the "
            f"{MAX_SNAPSHOT_BYTES} byte cap")
    try:
        with snapshot.open() as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise errors.ValidationError(f"invalid JSON in snapshot {path}: {e}") from e
    except RecursionError as e:
        raise errors.ValidationError(
            f"invalid snapshot: {path} is nested too deeply to parse") from e
    return import_plan(conn, data)
