"""Small shared helpers for backend modules.

These keep the per-entity modules short and consistent: row <-> dataclass
mapping, plus insert/update/delete that translate ``sqlite3.IntegrityError`` into
the project's own error types (NotFound/ValidationError/Conflict).
"""

from __future__ import annotations

import sqlite3

from . import errors


def _q(ident: str) -> str:
    """Quote a SQL identifier using double quotes.

    Prevents conflicts with SQLite keywords (e.g. ``group``).

    Args:
        ident: The identifier to quote.

    Returns:
        str: The quoted identifier with internal double-quotes escaped.
    """
    return '"' + ident.replace('"', '""') + '"'


def get(conn: sqlite3.Connection, model, table: str, id, *, resource: str | None = None):
    """Fetch one row by id as a dataclass, or raise :class:`NotFound`.

    Args:
        conn: sqlite3.Connection from db.connect().
        model: The dataclass to use for mapping (must inherit from Model).
        table: The name of the table to query.
        id: The primary key id.
        resource: Optional display name for the resource if different from table.

    Returns:
        Model: The mapped dataclass instance.

    Raises:
        NotFound: if the row with the given id is not found.
    """
    resource = resource or table
    row = conn.execute(f"SELECT * FROM {_q(table)} WHERE id = ?", (id,)).fetchone()
    if row is None:
        raise errors.NotFound(resource, id)
    return model.from_row(row)


def list_rows(conn, model, table: str, *, where: str | None = None,
              params=(), order: str = "id", limit: int | None = None,
              offset: int | None = None):
    """Fetch rows as a list of dataclasses.

    Args:
        conn: sqlite3.Connection from db.connect().
        model: The dataclass to use for mapping.
        table: The name of the table to query.
        where: Optional SQL WHERE clause (without 'WHERE' keyword).
        params: Parameters for the WHERE clause.
        order: SQL ORDER BY clause (without 'ORDER BY' keyword).
        limit: Optional maximum number of rows (None = no limit).
        offset: Optional number of rows to skip (None = 0). Applied with or
            without a limit (SQLite ``LIMIT -1 OFFSET n`` skips n then returns
            the rest).

    Returns:
        list: A list of mapped dataclass instances.
    """
    from . import _validate
    _validate.check_limit_offset(limit, offset, resource=table)
    sql = f"SELECT * FROM {_q(table)}"
    if where:
        sql += f" WHERE {where}"
    sql += f" ORDER BY {order}"
    if limit is not None or offset is not None:
        sql += " LIMIT ? OFFSET ?"
        params = tuple(params) + (limit if limit is not None else -1,
                                  offset if offset is not None else 0)
    return [model.from_row(r) for r in conn.execute(sql, params)]


def insert(conn: sqlite3.Connection, table: str, fields: dict) -> int:
    """Insert a row and return the new rowid.

    Args:
        conn: sqlite3.Connection from db.connect().
        table: The name of the table.
        fields: A dictionary of column names to values.

    Returns:
        int: The last inserted rowid.

    Raises:
        Conflict: on uniqueness violations.
        ValidationError: on other integrity constraints.
    """
    cols = list(fields)
    placeholders = ", ".join("?" for _ in cols)
    sql = f"INSERT INTO {_q(table)}({', '.join(cols)}) VALUES ({placeholders})"
    try:
        cur = conn.execute(sql, tuple(fields.values()))
    except sqlite3.IntegrityError as e:
        raise _classify_integrity(e)
    return cur.lastrowid


def update(conn: sqlite3.Connection, table: str, id, fields: dict) -> bool:
    """Update the row with the given id.

    Args:
        conn: sqlite3.Connection from db.connect().
        table: The name of the table.
        id: The primary key id of the row to update.
        fields: A dictionary of column names to new values.

    Returns:
        bool: True if a row was actually updated, False otherwise.

    Raises:
        Conflict: on uniqueness violations.
        ValidationError: on other integrity constraints.
    """
    if not fields:
        return True
    sets = ", ".join(f"{k} = ?" for k in fields)
    sql = f"UPDATE {_q(table)} SET {sets} WHERE id = ?"
    try:
        cur = conn.execute(sql, tuple(fields.values()) + (id,))
    except sqlite3.IntegrityError as e:
        raise _classify_integrity(e)
    return cur.rowcount == 1


def delete(conn: sqlite3.Connection, table: str, id, *, resource: str | None = None) -> None:
    """Delete the row with the given id.

    Args:
        conn: sqlite3.Connection from db.connect().
        table: The name of the table.
        id: The primary key id.
        resource: Optional display name for the resource.

    Raises:
        NotFound: if the row with the given id did not exist.
    """
    resource = resource or table
    cur = conn.execute(f"DELETE FROM {_q(table)} WHERE id = ?", (id,))
    if cur.rowcount == 0:
        raise errors.NotFound(resource, id)


def _classify_integrity(err: sqlite3.IntegrityError) -> errors.PlannerError:
    """Map a sqlite3.IntegrityError to a project-specific PlannerError.

    Args:
        err: The raw IntegrityError from sqlite3.

    Returns:
        PlannerError: Either a Conflict (for UNIQUE violations) or a ValidationError.
    """
    msg = str(err)
    if "UNIQUE" in msg.upper():
        return errors.Conflict(msg)
    return errors.ValidationError(msg)
