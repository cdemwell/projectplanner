"""Projects — top-level containers for stories/epics. Soft-deleted via ``archived``."""

from __future__ import annotations

import sqlite3

from . import _util, db
from .models import Project, Story

EDITABLE = {"name", "description", "abbreviation", "color", "archived"}


def list_projects(conn: sqlite3.Connection, *, include_archived: bool = False) -> list[Project]:
    """List projects.

    Args:
        conn: sqlite3.Connection from db.connect().
        include_archived: Whether to include archived projects.
    Returns:
        A list of Project dataclasses.
    """
    if include_archived:
        return _util.list_rows(conn, Project, "project", order="name")
    return _util.list_rows(conn, Project, "project", where="archived = 0", order="name")


def get_project(conn: sqlite3.Connection, id) -> Project:
    """Get a project by ID.

    Args:
        conn: sqlite3.Connection from db.connect().
        id: Project ID.
    Returns:
        The Project dataclass.
    Raises:
        NotFound: if the project does not exist.
    """
    return _util.get(conn, Project, "project", id, resource="project")


def create_project(conn: sqlite3.Connection, name: str, *, description: str = "",
                   abbreviation: str = "", color: str = "") -> Project:
    """Create a new project.

    Args:
        conn: sqlite3.Connection from db.connect().
        name: str — display name.
        description: Optional description.
        abbreviation: Optional short name.
        color: Optional color hex code.
    Returns:
        The created Project.
    Invariants:
        archived is initialized to 0.
    """
    with db.tx_write(conn):
        new_id = _util.insert(conn, "project", {
            "name": name, "description": description, "abbreviation": abbreviation,
            "color": color, "archived": 0, "created_at": db.now(),
        })
    return get_project(conn, new_id)


def update_project(conn: sqlite3.Connection, id, **fields) -> Project:
    """Update project fields.

    Args:
        conn: sqlite3.Connection from db.connect().
        id: Project ID.
        fields: Fields to update (name, description, abbreviation, color, archived).
    Returns:
        The updated Project.
    Raises:
        NotFound: if the project does not exist.
    """
    get_project(conn, id)
    fields = {k: v for k, v in fields.items() if k in EDITABLE}
    if fields:
        with db.tx_write(conn):
            _util.update(conn, "project", id, fields)
    return get_project(conn, id)


def archive_project(conn: sqlite3.Connection, id, archived: bool = True) -> Project:
    """Archive or unarchive a project.

    Args:
        conn: sqlite3.Connection from db.connect().
        id: Project ID.
        archived: Whether to archive (True) or unarchive (False).
    Returns:
        The updated Project.
    Invariants:
        archived is stored as int 0/1.
    """
    return update_project(conn, id, archived=1 if archived else 0)


def delete_project(conn: sqlite3.Connection, id) -> None:
    """Delete a project.

    Args:
        conn: sqlite3.Connection from db.connect().
        id: Project ID.
    """
    with db.tx_write(conn):
        _util.delete(conn, "project", id, resource="project")


def list_project_stories(conn: sqlite3.Connection, project_id) -> list[Story]:
    """List stories associated with a project.

    Args:
        conn: sqlite3.Connection from db.connect().
        project_id: Project ID.
    Returns:
        A list of Story dataclasses.
    """
    rows = conn.execute("SELECT * FROM story WHERE project_id = ? ORDER BY position, id",
                       (project_id,))
    return [Story.from_row(r) for r in rows]
