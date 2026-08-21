"""Small shared helpers for backend modules.

These keep the per-entity modules short and consistent: row <-> dataclass
mapping, plus insert/update/delete that translate ``sqlite3.IntegrityError`` into
the project's own error types (NotFound/ValidationError/Conflict).
"""

from __future__ import annotations

import sqlite3

from . import errors


def _q(ident: str) -> str:
    """Quote a SQL identifier (double quotes, escaped). Needed because ``group``
    is a SQLite keyword and is one of our table names."""
    return '"' + ident.replace('"', '""') + '"'


def get(conn: sqlite3.Connection, model, table: str, id, *, resource: str | None = None):
    """Fetch one row by id as a dataclass, or raise :class:`NotFound`."""
    resource = resource or table
    row = conn.execute(f"SELECT * FROM {_q(table)} WHERE id = ?", (id,)).fetchone()
    if row is None:
        raise errors.NotFound(resource, id)
    return model.from_row(row)


def list_rows(conn, model, table: str, *, where: str | None = None,
              params=(), order: str = "id"):
    """Fetch rows (optionally filtered) as a list of dataclasses."""
    sql = f"SELECT * FROM {_q(table)}"
    if where:
        sql += f" WHERE {where}"
    sql += f" ORDER BY {order}"
    return [model.from_row(r) for r in conn.execute(sql, params)]


def insert(conn: sqlite3.Connection, table: str, fields: dict) -> int:
    """Insert ``fields`` (col -> value) and return the new rowid."""
    cols = list(fields)
    placeholders = ", ".join("?" for _ in cols)
    sql = f"INSERT INTO {_q(table)}({', '.join(cols)}) VALUES ({placeholders})"
    try:
        cur = conn.execute(sql, tuple(fields.values()))
    except sqlite3.IntegrityError as e:
        raise _classify_integrity(e)
    return cur.lastrowid


def update(conn: sqlite3.Connection, table: str, id, fields: dict) -> bool:
    """Set the given ``fields`` on the row with ``id``. Returns True if a row matched."""
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
    """Delete the row with ``id``; raise :class:`NotFound` if it didn't exist."""
    resource = resource or table
    cur = conn.execute(f"DELETE FROM {_q(table)} WHERE id = ?", (id,))
    if cur.rowcount == 0:
        raise errors.NotFound(resource, id)


def _classify_integrity(err: sqlite3.IntegrityError) -> errors.PlannerError:
    """Turn a raw IntegrityError into a PlannerError (Conflict vs ValidationError)."""
    msg = str(err)
    if "UNIQUE" in msg.upper():
        return errors.Conflict(msg)
    return errors.ValidationError(msg)