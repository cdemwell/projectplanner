"""Projects — top-level containers for stories/epics. Soft-deleted via ``archived``."""

from __future__ import annotations

import sqlite3

from . import _util, db
from .models import Project, Story

EDITABLE = {"name", "description", "abbreviation", "color", "archived"}


def list_projects(conn: sqlite3.Connection, *, include_archived: bool = False) -> list[Project]:
    if include_archived:
        return _util.list_rows(conn, Project, "project", order="name")
    return _util.list_rows(conn, Project, "project", where="archived = 0", order="name")


def get_project(conn: sqlite3.Connection, id) -> Project:
    return _util.get(conn, Project, "project", id, resource="project")


def create_project(conn: sqlite3.Connection, name: str, *, description: str = "",
                   abbreviation: str = "", color: str = "") -> Project:
    with db.tx_write(conn):
        new_id = _util.insert(conn, "project", {
            "name": name, "description": description, "abbreviation": abbreviation,
            "color": color, "archived": 0, "created_at": db.now(),
        })
    return get_project(conn, new_id)


def update_project(conn: sqlite3.Connection, id, **fields) -> Project:
    get_project(conn, id)
    fields = {k: v for k, v in fields.items() if k in EDITABLE}
    if fields:
        with db.tx_write(conn):
            _util.update(conn, "project", id, fields)
    return get_project(conn, id)


def archive_project(conn: sqlite3.Connection, id, archived: bool = True) -> Project:
    return update_project(conn, id, archived=1 if archived else 0)


def delete_project(conn: sqlite3.Connection, id) -> None:
    with db.tx_write(conn):
        _util.delete(conn, "project", id, resource="project")


def list_project_stories(conn: sqlite3.Connection, project_id) -> list[Story]:
    rows = conn.execute("SELECT * FROM story WHERE project_id = ? ORDER BY position, id",
                       (project_id,))
    return [Story.from_row(r) for r in rows]