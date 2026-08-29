"""Connection management, schema creation/migrations, and first-run seeding.

The single source of storage is ``planner.db`` in the repo root. Connections are
created via :func:`connect`, which configures pragmas (foreign keys on, a 5s busy
timeout so concurrent writers block rather than error) and runs any pending
migrations. Writers go through :func:`tx_write`, which takes the write lock up
front with ``BEGIN IMMEDIATE`` to ensure deterministic serialization.
"""

from __future__ import annotations

import contextlib
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from . import errors

# Schema version this code understands. Bump when adding a migration in MIGRATIONS.
CURRENT_SCHEMA_VERSION = 5

# Default DB location: next to main.py (repo root).
DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "planner.db"

# 0/1 integer flags stored in TEXT-less form; constants keep call sites readable.
ARCHIVED_FALSE = 0
ARCHIVED_TRUE = 1


def now() -> str:
    """Return the current UTC timestamp as an ISO-8601 string.

    Stored in TEXT columns to avoid relying on SQLite's ``CURRENT_TIMESTAMP``,
    which can yield naive local-ish strings.

    Returns:
        str: The ISO-8601 UTC timestamp (e.g. ``2026-08-20T14:03:11+00:00``).
    """
    return datetime.now(UTC).isoformat()


def _read_db_state(path: Path) -> tuple[set[str], int | None]:
    """Read-only census of the SQLite file at ``path``.

    The file is opened read-only via a file URI, so this never creates it or
    writes anything — a missing file reports no tables (treated as a fresh
    database by the caller).

    Args:
        path: Candidate database file path.
    Returns:
        tuple of (table names excluding SQLite internals, stored schema
        version — None when there is no readable version row yet).
    Raises:
        errors.ValidationError: if the file exists but cannot be opened as a
            SQLite database (arbitrary bytes, a directory, an unreadable file).
    """
    if not path.exists():
        return set(), None
    uri = Path(path).resolve().as_uri() + "?mode=ro"  # read-only: never create
    probe = None
    try:
        probe = sqlite3.connect(uri, uri=True)
        rows = probe.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        tables = {r[0] for r in rows}
        version: int | None = None
        if "schema_version" in tables:
            # MAX() of NULL returns None. Anything unreadable or non-integer
            # here means a name collision with some other app's convention —
            # the caller refuses rather than guessing.
            try:
                row = probe.execute("SELECT MAX(version) FROM schema_version").fetchone()
            except sqlite3.DatabaseError as e:
                raise errors.ValidationError(
                    f"refusing to open {path}: its schema_version table is not "
                    f"ours (unreadable as a schema version: {e})") from e
            version = row[0]
    except sqlite3.DatabaseError as e:
        # Covers "cannot be opened at all" (a directory, bad permissions) and
        # "opened but not a database" (arbitrary bytes) — same refusal either way.
        raise errors.ValidationError(
            f"refusing to open {path}: cannot be opened as a SQLite database "
            f"({e})") from e
    finally:
        if probe is not None:
            probe.close()
    return tables, version


def restrict_to_owner(path: os.PathLike[str] | str) -> None:
    """Set owner-only (0600) permissions on a planner file.

    Planner files hold the whole plan in plaintext, so this is applied on
    every write the tool makes: `connect` for a newly created database (an
    existing database's mode is left alone, so a deliberately widened mode
    survives), `backup_db_file` for every backup copy, and `export_to_file`
    for every export write — including re-exports over an existing snapshot,
    where tightening is the safer default. Best-effort: exotic filesystems
    that cannot chmod keep their mount default rather than failing the run.

    Args:
        path: File to restrict.
    """
    try:
        os.chmod(path, 0o600)
    except OSError:
        # Exotic filesystems may not support chmod; the tool works regardless.
        pass


def connect(db_path: str | os.PathLike[str] | None = None) -> sqlite3.Connection:
    """Open (and, if needed, create + migrate + seed) the planner database.

    Configures the connection with ``sqlite3.Row``, enables foreign keys,
    and sets a 5-second busy timeout to handle concurrent writers.

    The target is classified before anything is written: a missing/empty file
    is a fresh planner database (created, migrated, seeded); a file carrying
    a readable ``schema_version`` in the supported range is one of ours
    (migrated if needed); a file whose member table is absent self-heads a
    first-run seed (the v1-crash window leaves exactly that shape), while a
    present-but-empty member table stays empty. Any other existing SQLite
    file — a foreign database, or one claiming an unsupported schema version
    — is refused rather than silently given planner tables and a seed row.

    Args:
        db_path: Optional path to the database file. Defaults to ``DEFAULT_DB_PATH``.

    Returns:
        sqlite3.Connection: A configured SQLite connection.

    Raises:
        errors.ValidationError: if ``db_path`` exists but is not a planner
            database (a foreign SQLite file, a non-SQLite file, or a planner
            schema version newer than this build understands).
    """
    path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
    try:
        # A missing file is created; an existing 0-byte file (e.g. an aborted
        # first create, or a touch'd blank) is equally "brand new" to us.
        created = not path.exists() or path.stat().st_size == 0
    except OSError:
        created = False
    # Classify before mutating: read-only, so nothing is created by the probe.
    tables, stored_version = _read_db_state(path)
    if tables and "schema_version" not in tables:
        raise errors.ValidationError(
            f"refusing to open {path}: it is not a planner database "
            f"(no schema_version table; contains tables: {sorted(tables)[:8]})")
    if stored_version is not None and not isinstance(stored_version, int):
        raise errors.ValidationError(
            f"refusing to open {path}: its schema_version table holds "
            f"{stored_version!r}, which is not a schema version")
    if stored_version is not None and stored_version > CURRENT_SCHEMA_VERSION:
        raise errors.ValidationError(
            f"refusing to open {path}: schema version {stored_version} is "
            f"newer than this build supports ({CURRENT_SCHEMA_VERSION}); "
            "upgrade projectplanner first")
    if tables and (stored_version is None or stored_version <= 0) \
            and tables != {"schema_version"}:
        # A real planner DB always stores a version >= 1 (the first migration
        # stamps it in the same transaction that creates the tables), and the
        # only legal no-version-rows shape is the v1-crash window: exactly
        # schema_version and nothing else. A name-collision with other tables
        # is someone else's database — refuse it. (Non-int version rows were
        # refused above, so the <= here is int-safe.)
        raise errors.ValidationError(
            f"refusing to open {path}: its schema_version table has no usable "
            "version row, but other tables exist — not a planner database")

    conn = sqlite3.connect(str(path))  # check_same_thread=True is fine: callers
    # own the connection and pass it explicitly.
    conn.row_factory = sqlite3.Row
    # Enforce FK constraints (off by default in SQLite) and make a second writer
    # block for up to 5s instead of raising SQLITE_BUSY immediately.
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    if created:
        # A brand-new database holds the whole plan in plaintext; created
        # files are owner-only. Existing files keep their set mode.
        restrict_to_owner(path)
    # Seed when the member table is absent: it exists in every migrated
    # planner DB (v1 creates it with schema_version), so its absence means
    # first-run or a v1-crash window — both of which want seeding. A present
    # but empty member table (e.g. after a memberless plan import) must NOT
    # resurrect a seed member and a second Default workflow.
    _migrate(conn, seed="member" not in tables)
    return conn


@contextlib.contextmanager
def tx_write(conn: sqlite3.Connection):
    """Context manager for a write transaction.

    Acquires the write lock up front using ``BEGIN IMMEDIATE`` so concurrent
    writers serialize deterministically. The second writer blocks until the
    first commits or rolls back.

    Args:
        conn: sqlite3.Connection from db.connect().

    Yields:
        sqlite3.Connection: The connection within the transaction.

    Invariants:
        Commits on clean exit, rolls back on any exception.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except Exception:
        conn.rollback()
        raise
    else:
        conn.commit()


# --------------------------------------------------------------------------- #
# Migrations
# --------------------------------------------------------------------------- #
# Each migration is a list of SQL statements applied inside one BEGIN IMMEDIATE
# transaction when the stored schema_version is below the migration's version.
# Statements should be idempotent (CREATE TABLE IF NOT EXISTS, etc.) where the
# migration could conceivably re-run after a partial failure.

_SCHEMA_V1 = [
    """
    CREATE TABLE IF NOT EXISTS schema_version (
        version INTEGER PRIMARY KEY
    )
    """,
    # People ---------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS member (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        name          TEXT NOT NULL,
        mention_name  TEXT NOT NULL UNIQUE,
        created_at    TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS "group" (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        name         TEXT NOT NULL,
        description  TEXT NOT NULL DEFAULT '',
        archived     INTEGER NOT NULL DEFAULT 0,
        created_at   TEXT NOT NULL
    )
    """,
    # Workflows ------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS workflow (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        name             TEXT NOT NULL,
        default_state_id INTEGER,
        created_at       TEXT NOT NULL,
        FOREIGN KEY (default_state_id) REFERENCES workflow_state(id) ON DELETE SET NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS workflow_state (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        workflow_id INTEGER NOT NULL,
        name        TEXT NOT NULL,
        type        TEXT NOT NULL CHECK (type IN ('unstarted', 'started', 'done')),
        position    REAL NOT NULL DEFAULT 0,
        created_at  TEXT NOT NULL,
        FOREIGN KEY (workflow_id) REFERENCES workflow(id) ON DELETE CASCADE
    )
    """,
    # Planning containers --------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS project (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        name         TEXT NOT NULL,
        description  TEXT NOT NULL DEFAULT '',
        abbreviation TEXT NOT NULL DEFAULT '',
        color        TEXT NOT NULL DEFAULT '',
        archived     INTEGER NOT NULL DEFAULT 0,
        created_at   TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS label (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        name        TEXT NOT NULL,
        color       TEXT NOT NULL DEFAULT '',
        description TEXT NOT NULL DEFAULT '',
        created_at  TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS milestone (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        name         TEXT NOT NULL,
        description  TEXT NOT NULL DEFAULT '',
        state        TEXT NOT NULL DEFAULT 'planned'
                     CHECK (state IN ('planned', 'in_progress', 'done')),
        created_at   TEXT NOT NULL,
        completed_at TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS epic (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        name         TEXT NOT NULL,
        description  TEXT NOT NULL DEFAULT '',
        state        TEXT NOT NULL DEFAULT 'planned'
                     CHECK (state IN ('planned', 'in_progress', 'done')),
        milestone_id INTEGER,
        project_id   INTEGER,
        created_at   TEXT NOT NULL,
        completed_at TEXT,
        FOREIGN KEY (milestone_id) REFERENCES milestone(id) ON DELETE SET NULL,
        FOREIGN KEY (project_id)   REFERENCES project(id)   ON DELETE SET NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS iteration (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        name        TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        status      TEXT NOT NULL DEFAULT 'planned'
                    CHECK (status IN ('planned', 'active', 'done')),
        start_date  TEXT,
        end_date    TEXT,
        created_at  TEXT NOT NULL
    )
    """,
    # Stories --------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS story (
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        name              TEXT NOT NULL,
        description       TEXT NOT NULL DEFAULT '',
        story_type        TEXT NOT NULL DEFAULT 'feature'
                          CHECK (story_type IN ('bug', 'feature', 'chore')),
        workflow_state_id INTEGER,
        epic_id           INTEGER,
        iteration_id      INTEGER,
        project_id        INTEGER,
        group_id          INTEGER,
        requested_by_id   INTEGER,
        deadline          TEXT,
        position           REAL NOT NULL DEFAULT 0,
        created_at        TEXT NOT NULL,
        updated_at        TEXT NOT NULL,
        completed_at      TEXT,
        FOREIGN KEY (workflow_state_id) REFERENCES workflow_state(id) ON DELETE SET NULL,
        FOREIGN KEY (epic_id)           REFERENCES epic(id)           ON DELETE SET NULL,
        FOREIGN KEY (iteration_id)      REFERENCES iteration(id)     ON DELETE SET NULL,
        FOREIGN KEY (project_id)        REFERENCES project(id)        ON DELETE SET NULL,
        FOREIGN KEY (group_id)          REFERENCES "group"(id)        ON DELETE SET NULL,
        FOREIGN KEY (requested_by_id)   REFERENCES member(id)         ON DELETE SET NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS story_owner (
        story_id  INTEGER NOT NULL,
        member_id INTEGER NOT NULL,
        PRIMARY KEY (story_id, member_id),
        FOREIGN KEY (story_id)  REFERENCES story(id)  ON DELETE CASCADE,
        FOREIGN KEY (member_id) REFERENCES member(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS story_label (
        story_id INTEGER NOT NULL,
        label_id INTEGER NOT NULL,
        PRIMARY KEY (story_id, label_id),
        FOREIGN KEY (story_id) REFERENCES story(id) ON DELETE CASCADE,
        FOREIGN KEY (label_id) REFERENCES label(id) ON DELETE CASCADE
    )
    """,
    # Tasks, comments, links ------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS task (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        story_id    INTEGER NOT NULL,
        description TEXT NOT NULL,
        complete    INTEGER NOT NULL DEFAULT 0,
        position    REAL NOT NULL DEFAULT 0,
        created_at  TEXT NOT NULL,
        completed_at TEXT,
        FOREIGN KEY (story_id) REFERENCES story(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS story_comment (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        story_id   INTEGER NOT NULL,
        author_id  INTEGER,
        text       TEXT NOT NULL,
        parent_id  INTEGER,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY (story_id)  REFERENCES story(id)          ON DELETE CASCADE,
        FOREIGN KEY (author_id) REFERENCES member(id)         ON DELETE SET NULL,
        FOREIGN KEY (parent_id) REFERENCES story_comment(id)  ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS story_link (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        subject_story_id INTEGER NOT NULL,
        verb             TEXT NOT NULL
                         CHECK (verb IN ('blocks', 'blocks_by', 'duplicates',
                                         'duplicated_by', 'relates_to')),
        object_story_id  INTEGER NOT NULL,
        created_at       TEXT NOT NULL,
        UNIQUE (subject_story_id, verb, object_story_id),
        FOREIGN KEY (subject_story_id) REFERENCES story(id) ON DELETE CASCADE,
        FOREIGN KEY (object_story_id)  REFERENCES story(id) ON DELETE CASCADE,
        CHECK (subject_story_id <> object_story_id)
    )
    """,
]

def _fts_trigger(table: str, cols=("name", "description")) -> list[str]:
    """Return the trigger SQL to keep an FTS5 external-content table in sync.

    The FTS5 table ``{table}_fts`` mirrors the given ``cols`` of the source
    table. Triggers update the index on every insert, update, or delete.

    Args:
        table: The name of the source table.
        cols: The columns to mirror (e.g. ``("name", "description")`` for
            stories, ``("text",)`` for comments, ``("description",)`` for tasks).

    Returns:
        list[str]: Three SQL statements for the AI, AD, and AU triggers.
    """
    fts = f"{table}_fts"
    cols = list(cols)
    new_vals = ", ".join(f"NEW.{c}" for c in cols)
    old_vals = ", ".join(f"OLD.{c}" for c in cols)
    col_list = ", ".join(cols)
    return [
        f"""CREATE TRIGGER IF NOT EXISTS {table}_fts_ai AFTER INSERT ON {table} BEGIN
            INSERT INTO {fts}(rowid, {col_list}) VALUES (NEW.id, {new_vals});
        END""",
        f"""CREATE TRIGGER IF NOT EXISTS {table}_fts_ad AFTER DELETE ON {table} BEGIN
            INSERT INTO {fts}({fts}, rowid, {col_list}) VALUES ('delete', OLD.id, {old_vals});
        END""",
        f"""CREATE TRIGGER IF NOT EXISTS {table}_fts_au AFTER UPDATE ON {table} BEGIN
            INSERT INTO {fts}({fts}, rowid, {col_list}) VALUES ('delete', OLD.id, {old_vals});
            INSERT INTO {fts}(rowid, {col_list}) VALUES (NEW.id, {new_vals});
        END""",
    ]


# v2: full-text search over stories, epics, projects, milestones, iterations,
# labels. External-content FTS5 tables kept in sync by triggers.
_SCHEMA_V2 = [
    """
    CREATE VIRTUAL TABLE IF NOT EXISTS story_fts USING fts5(
        name, description, content='story', content_rowid='id'
    )
    """,
    """
    CREATE VIRTUAL TABLE IF NOT EXISTS epic_fts USING fts5(
        name, description, content='epic', content_rowid='id'
    )
    """,
    """
    CREATE VIRTUAL TABLE IF NOT EXISTS project_fts USING fts5(
        name, description, content='project', content_rowid='id'
    )
    """,
    """
    CREATE VIRTUAL TABLE IF NOT EXISTS milestone_fts USING fts5(
        name, description, content='milestone', content_rowid='id'
    )
    """,
    """
    CREATE VIRTUAL TABLE IF NOT EXISTS iteration_fts USING fts5(
        name, description, content='iteration', content_rowid='id'
    )
    """,
    """
    CREATE VIRTUAL TABLE IF NOT EXISTS label_fts USING fts5(
        name, description, content='label', content_rowid='id'
    )
    """,
    # Keep each FTS table in sync with its source via after-insert/update/delete
    # triggers. (The owning backend modules still call these indirectly; the
    # triggers are the single source of truth so a direct SQL edit also stays
    # indexed.)
    _fts_trigger("story"),
    _fts_trigger("epic"),
    _fts_trigger("project"),
    _fts_trigger("milestone"),
    _fts_trigger("iteration"),
    _fts_trigger("label"),
]

# v3: full-text search over comments (text) and tasks (description). External-
# content FTS5 tables kept in sync by triggers; existing rows are indexed via a
# rebuild so the migration works on databases that already have data.
_SCHEMA_V3 = [
    """
    CREATE VIRTUAL TABLE IF NOT EXISTS story_comment_fts USING fts5(
        text, content='story_comment', content_rowid='id'
    )
    """,
    """
    CREATE VIRTUAL TABLE IF NOT EXISTS task_fts USING fts5(
        description, content='task', content_rowid='id'
    )
    """,
    # Backfill indexes from any pre-existing rows.
    "INSERT INTO story_comment_fts(story_comment_fts) VALUES ('rebuild')",
    "INSERT INTO task_fts(task_fts) VALUES ('rebuild')",
    _fts_trigger("story_comment", ("text",)),
    _fts_trigger("task", ("description",)),
]

# v4: add an optional human-readable note to workflow states.
_SCHEMA_V4 = [
    "ALTER TABLE workflow_state ADD COLUMN description TEXT NOT NULL DEFAULT ''",
]

# v5: enforce case-insensitive name uniqueness at the database level.
#
# The name resolvers (CLI `resolve_*`) match case-insensitively, so a DB that
# holds 'Bug' and 'bug' as separate labels (or two states named 'todo' in one
# workflow) contains rows the resolver can never disambiguate. Both tables get
# a collation-scoped UNIQUE index rather than a column-level COLLATE NOCASE,
# which enforces the same write-time guarantee without rebuilding either table
# (a DROP-based rebuild would fire ON DELETE SET NULL on story.workflow_state_id
# and workflow.default_state_id under foreign_keys=ON).
#
# Any pre-existing case-variant collisions are merged first (deterministic
# survivor: lowest id — its spelling wins; references are repointed), so the
# index creation cannot fail on a dirty database. On a database created by v1
# the dedup statements are no-ops.
_SCHEMA_V5 = [
    # --- label: merge case-variant duplicate names -------------------------
    # Repoint story_label rows onto the survivor id (ignoring ones that would
    # duplicate that (story, label) pair).
    """
    UPDATE OR IGNORE story_label
    SET label_id = (
        SELECT MIN(k.id) FROM label k
        WHERE lower(k.name) = lower(
            (SELECT cur.name FROM label cur WHERE cur.id = story_label.label_id))
    )
    WHERE label_id <> (
        SELECT MIN(k.id) FROM label k
        WHERE lower(k.name) = lower(
            (SELECT cur.name FROM label cur WHERE cur.id = story_label.label_id))
    )
    """,
    "DELETE FROM story_label "
    "WHERE label_id NOT IN (SELECT MIN(id) FROM label GROUP BY lower(name))",
    "DELETE FROM label "
    "WHERE id NOT IN (SELECT MIN(id) FROM label GROUP BY lower(name))",
    # --- workflow_state: merge case-variant duplicate names per workflow ----
    """
    UPDATE story
    SET workflow_state_id = (
        SELECT MIN(k.id) FROM workflow_state k
        WHERE k.workflow_id = (SELECT ws.workflow_id FROM workflow_state ws
                               WHERE ws.id = story.workflow_state_id)
          AND lower(k.name) = lower(
              (SELECT ws2.name FROM workflow_state ws2 WHERE ws2.id = story.workflow_state_id))
    )
    WHERE workflow_state_id IS NOT NULL
      AND workflow_state_id <> (
        SELECT MIN(k.id) FROM workflow_state k
        WHERE k.workflow_id = (SELECT ws.workflow_id FROM workflow_state ws
                               WHERE ws.id = story.workflow_state_id)
          AND lower(k.name) = lower(
              (SELECT ws2.name FROM workflow_state ws2 WHERE ws2.id = story.workflow_state_id))
      )
    """,
    """
    UPDATE workflow
    SET default_state_id = (
        SELECT MIN(k.id) FROM workflow_state k
        WHERE k.workflow_id = workflow.id
          AND lower(k.name) = lower(
              (SELECT ws.name FROM workflow_state ws WHERE ws.id = workflow.default_state_id))
    )
    WHERE default_state_id IS NOT NULL
      AND default_state_id <> (
        SELECT MIN(k.id) FROM workflow_state k
        WHERE k.workflow_id = workflow.id
          AND lower(k.name) = lower(
              (SELECT ws.name FROM workflow_state ws WHERE ws.id = workflow.default_state_id))
      )
    """,
    "DELETE FROM workflow_state "
    "WHERE id NOT IN (SELECT MIN(id) FROM workflow_state "
    "GROUP BY workflow_id, lower(name))",
    # --- the unique constraints --------------------------------------------
    "CREATE UNIQUE INDEX IF NOT EXISTS label_name_ci ON label (name COLLATE NOCASE)",
    "CREATE UNIQUE INDEX IF NOT EXISTS workflow_state_wf_name_ci "
    "ON workflow_state (workflow_id, name COLLATE NOCASE)",
    # Re-sync the label full-text index after the merge (triggers keep it
    # current, this is belt-and-braces for hand-edited databases).
    "INSERT INTO label_fts(label_fts) VALUES ('rebuild')",
]

_MIGRATIONS = [
    (1, _SCHEMA_V1),
    (2, _SCHEMA_V2),
    (3, _SCHEMA_V3),
    (4, _SCHEMA_V4),
    (5, _SCHEMA_V5),
]


def _schema_version(conn: sqlite3.Connection) -> int:
    """Return the stored schema version, or 0 if the table is absent or empty.

    Args:
        conn: sqlite3.Connection from db.connect().

    Returns:
        int: The current schema version stored in the database.
    """
    row = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
    if row is None or row["v"] is None:
        return 0
    return int(row["v"])


def _migrate(conn: sqlite3.Connection, seed: bool) -> None:
    """Apply any pending migrations up to CURRENT_SCHEMA_VERSION.

    Iterates through ``_MIGRATIONS`` and applies any that are newer than the
    stored version. Each migration is wrapped in a ``tx_write`` transaction.

    Args:
        conn: sqlite3.Connection from db.connect().
        seed: Whether to run first-run seeding. True only for a genuinely
            fresh database (no tables before migrating) — seeding used to be
            keyed on an empty ``member`` table, which resurrected a seed
            member plus a duplicate Default workflow after any plan import
            that carried no members.

    Invariants:
        Seeding runs at most once, on a genuinely fresh database.
    """
    # schema_version table is created by v1; make sure it exists before reading.
    conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY)")
    current = _schema_version(conn)
    for version, statements in _MIGRATIONS:
        if current >= version:
            continue
        with tx_write(conn):
            for stmt in statements:
                # An entry may itself be a list of single statements
                # (e.g. several trigger definitions) — flatten them.
                stmts = stmt if isinstance(stmt, list) else [stmt]
                for s in stmts:
                    conn.execute(s)
            conn.execute("INSERT INTO schema_version(version) VALUES (?)", (version,))
        current = version

    if seed:
        _seed(conn)


def _seed(conn: sqlite3.Connection) -> None:
    """Seed the database with a local member and a default workflow.

    Creates one member based on the current system user and a 'Default' workflow
    containing Unstarted, Started, and Done states.

    Args:
        conn: sqlite3.Connection from db.connect().

    Invariants:
        Runs in a single ``BEGIN IMMEDIATE`` transaction.
    """
    name = os.environ.get("USER") or "me"
    mention_name = name.lower().replace(" ", "_")
    ts = now()
    with tx_write(conn):
        conn.execute(
            "INSERT INTO member(name, mention_name, created_at) VALUES (?, ?, ?)",
            (name, mention_name, ts),
        )
        cur = conn.execute(
            "INSERT INTO workflow(name, default_state_id, created_at) VALUES (?, NULL, ?)",
            ("Default", ts),
        )
        workflow_id = cur.lastrowid
        states = [("Unstarted", "unstarted", 0.0),
                  ("Started", "started", 1.0),
                  ("Done", "done", 2.0)]
        started_id = None
        for sname, stype, pos in states:
            cur = conn.execute(
                "INSERT INTO workflow_state(workflow_id, name, type, position, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (workflow_id, sname, stype, pos, ts),
            )
            if stype == "started":
                started_id = cur.lastrowid
        # Point the workflow at its default (Started) state.
        conn.execute(
            "UPDATE workflow SET default_state_id = ? WHERE id = ?",
            (started_id, workflow_id),
        )
