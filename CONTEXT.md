# Project Planner — Context & Briefing

> Self-contained briefing. Everything needed to continue this project is in this file.
> Read this first; you should not need other context to pick up the work.

## 1. What this project is

A **local, personal project-planning tool** that uses **Shortcut's (formerly Clubhouse) v3
data model as inspiration** — not a contract. Shortcut is chosen only because its model
is simple, well-understood, and a good basis for development. **We do not care about
Shortcut per se**, we do not call the Shortcut REST API, and we are not building a
faithful mock of Shortcut for testing real Shortcut client code.

The tool is **meant to be shared with AI coding systems** — i.e. it should be easy for an
AI agent (or a human) to read, query, and modify the plan via either an interactive TUI or
scriptable CLI commands, all operating on a single local SQLite database.

There is an on-disk reference of the real Shortcut v3 API in the repo root:
`Shortcut Rest API, V3.html`. It is a reference for the *model* only. We borrow entity names
and relationships from it; we do not reproduce its exact field shapes, endpoints, or
behaviors.

## 2. Locked decisions (from the design conversation)

1. **Purpose:** personal/local planning tool, shared with AI agents. Shortcut is just a
   starting model.
2. **Scope:** build a **core set** of resources now. Defer the rest (build later only if
   needed) — see §4.
3. **Data fidelity:** use a **sensible local schema** (trimmed). Keep Shortcut's entity and
   field *names* for the fields we carry; drop everything else. No requirement to mirror
   Shortcut's nested JSON shapes.
4. **`main.py` modes:** no args → interactive **TUI**; args → one-shot **CLI** subcommands.
   Both share one backend service layer over SQLite.
5. **Storage:** single local SQLite file, `planner.db`, in the repo root next to `main.py`.
6. **Concurrency:** multiple AI agents / processes may write concurrently. SQLite
   serializes writers via its file lock; we make writers **block** (not error) until the
   lock is free, using `busy_timeout` + `BEGIN IMMEDIATE`. Single-writer-at-a-time is the
   accepted behavior. WAL mode is *not* enabled (YAGNI); revisit only if read-during-write
   contention becomes real.

Anything not listed here as "open" is decided. Don't re-litigate the locked decisions
unless the user explicitly asks.

## 3. Repository state

**Backend + CLI + TUI are all built and working.**

- `main.py` — dispatch: no args → Textual TUI; args → CLI.
- `backend/` — `db.py`, `errors.py`, `models.py`, `_util.py`, `plan.py`, and one module per
  entity (`members`, `groups`, `workflows`, `projects`, `labels`, `milestones`,
  `epics`, `iterations`, `stories`, `tasks`, `comments`, `story_links`,
  `search.py`). Schema is at version 3 (v1 = core tables + seed; v2 = FTS5
  over name+description for story/epic/project/milestone/iteration/label; v3 =
  FTS5 over comment text and task description). `plan.py` provides export/import
  of the entire plan as a portable JSON snapshot (remapping primary keys on import).
- `cli/commands.py` — argparse subparsers for every resource; `--json` flag;
  name-to-id resolution; `run(argv)` entry point.
- `tui/app.py` — full-screen Textual TUI: filterable story list + detail pane,
  modal screens for create/move/comment/task/filter/search, keyboard bindings.
- `pyproject.toml` — `projectplanner` v0.1.0, `requires-python = ">=3.12"`,
  dependencies: `textual>=0.80` (TUI only; backend + CLI are stdlib).
- `README.md` — user-facing docs.
- `.gitignore` — ignores `planner.db`, Python caches, `.venv/`.
- `.python-version` — `3.12` (use Python 3.12; stdlib `sqlite3` ships with FTS5).
- `.venv/` — local uv-managed venv (gitignored). Recreate with `uv sync`.
- `Shortcut Rest API, V3.html` — reference doc (model only).

## 4. Scope — build vs defer

### Build now (the core)
Stories, Epics, Iterations, Milestones, Projects, Workflows (+ Workflow States),
Labels, Members, Groups, Comments, Tasks (subtasks), Story Links, and Search.

### Defer (stub nothing; just don't build until needed)
Documents, Objectives + Key-Results, Custom-Fields, Entity-Templates, Files,
Linked-Files, Repositories, Integrations, Health, Story History, Reactions,
External-Link, Categories, Epic Workflow.

When a deferred entity is needed, add it then. Do **not** create empty tables or stubs for
deferred entities now.

## 5. Architecture

```
projectplanner/
├── main.py            # entry: TUI (no args) or CLI (subcommands); thin dispatch
├── planner.db         # local SQLite DB (created on first run; gitignore it)
├── backend/
│   ├── __init__.py
│   ├── db.py          # connect(), pragmas, schema creation/migrations, busy_timeout
│   ├── errors.py      # NotFound, ValidationError, Conflict, etc.
│   ├── models.py      # plain dataclasses for each entity (local, trimmed shapes)
│   ├── members.py  groups.py  workflows.py  projects.py  labels.py
│   ├── milestones.py  epics.py  iterations.py  stories.py
│   ├── comments.py  tasks.py   story_links.py
│   └── search.py      # FTS5-backed search across the major entities
├── cli/
│   └── commands.py    # argparse subparsers -> calls backend functions, prints output
├── tui/
│   └── app.py         # full-screen interactive UI -> calls same backend functions
├── pyproject.toml
└── CONTEXT.md         # this file
```

Principles:
- **Function-call API, not REST.** Each REST-shaped operation becomes a plain Python
  function in a `backend/*.py` module. Example: `GET /stories/{id}` → `stories.get_story(conn, id)`.
  The CLI and TUI are both thin layers over these functions.
- **Connection is passed in, not global.** `backend/db.py` exposes a `connect(db_path)`
  factory returning a configured `sqlite3.Connection`. Each function takes `conn` as its
  first arg. This keeps concurrent-call boundaries explicit and testable.
- **Models are plain dataclasses** (stdlib `dataclasses`), not ORMs. Each entity has one
  dataclass. Functions return these (or raise). Lists return lists of these.
- **No ORM.** Raw SQL via `sqlite3`. Keep queries simple and readable.
- **Migrations are simple:** `db.py` creates all tables if missing (idempotent `CREATE TABLE
  IF NOT EXISTS`). A `schema_version` table tracks a single integer version; on connect,
  apply any newer migrations. Don't over-engineer — a versioned list of `CREATE`/`ALTER`
  statements is fine.

## 6. Concurrency model (§2.6 in detail)

- On every connection, set `PRAGMA busy_timeout = 5000` (5s) so a writer blocks instead of
  getting `SQLITE_BUSY` immediately. Raise it if agents are slow.
- **Write transactions use `BEGIN IMMEDIATE`** to acquire the write lock up front. This
  serializes writers deterministically: the second writer blocks until the first commits,
  then proceeds.
- Read queries use the default (deferred) mode.
- Provide a small helper `db.tx_write(conn)` context manager that does
  `conn.execute("BEGIN IMMEDIATE")` … `commit`/`rollback`, used by every mutating function.
- Don't enable WAL unless read/write contention is observed. If it is, enabling
  `PRAGMA journal_mode=WAL` + keeping `busy_timeout` is the one-line fix.

## 7. Schema (sensible local, Shortcut-flavored naming)

Integer autoincrement primary keys everywhere (`id INTEGER PRIMARY KEY AUTOINCREMENT`).
`created_at`/`updated_at` are TEXT (ISO-8601 UTC, stored via Python's `datetime`, since
`CURRENT_TIMESTAMP` gives naive local-ish strings). `position` columns are REAL for easy
insert-between reordering. ON DELETE behavior: **CASCADE** for children owned by a parent
(e.g. tasks/story), **SET NULL** for optional foreign links (e.g. story.epic_id).

Tables and key columns (abbreviated — `created_at`/`updated_at` implied on most):

- **member**: id, name, mention_name (unique), created_at. (The single local user is seeded
  as member 1 on first run.)
- **group**: id, name, description, archived (INTEGER 0/1), created_at.
- **workflow**: id, name, default_state_id (-> workflow_state, nullable), created_at.
  One default workflow + standard states seeded on first run.
- **workflow_state**: id, workflow_id (-> workflow, CASCADE), name, type
  ('unstarted'|'started'|'done'), position, created_at.
- **project**: id, name, description, abbreviation, color (TEXT e.g. '#65c8c8'),
  archived, created_at.
- **label**: id, name, color, description, created_at.
- **milestone**: id, name, description, state ('planned'|'in_progress'|'done'),
  created_at, completed_at (nullable).
- **epic**: id, name, description, state ('planned'|'in_progress'|'done'), milestone_id
  (-> milestone, SET NULL), project_id (-> project, SET NULL), created_at, completed_at.
- **iteration**: id, name, description, status ('planned'|'active'|'done'), start_date
  (TEXT, nullable), end_date (TEXT, nullable), created_at.
- **story**: id, name, description, story_type ('bug'|'feature'|'chore'),
  workflow_state_id (-> workflow_state, SET NULL), epic_id (-> epic, SET NULL),
  iteration_id (-> iteration, SET NULL), project_id (-> project, SET NULL),
  group_id (-> group, SET NULL), requested_by_id (-> member, SET NULL),
  deadline (TEXT, nullable), position (REAL), created_at, updated_at, completed_at.
- **story_owner**: story_id (-> story, CASCADE), member_id (-> member, CASCADE).
  PRIMARY KEY(story_id, member_id). (Stories can have multiple owners.)
- **story_label**: story_id (-> story, CASCADE), label_id (-> label, CASCADE).
  PRIMARY KEY(story_id, label_id).
- **task**: id, story_id (-> story, CASCADE), description, complete (0/1), position,
  created_at, completed_at.
- **story_comment**: id, story_id (-> story, CASCADE), author_id (-> member, SET NULL),
  text, parent_id (-> story_comment, CASCADE, nullable for threading), created_at,
  updated_at.
- **story_link**: id, subject_story_id (-> story, CASCADE), verb
  ('blocks'|'blocks_by'|'duplicates'|'duplicated_by'|'relates_to'),
  object_story_id (-> story, CASCADE), created_at. Uniqueness:
  `UNIQUE(subject_story_id, verb, object_story_id)`.

### Search
Use **FTS5** external-content virtual tables kept in sync by triggers: `name` +
`description` for stories, epics, projects, milestones, iterations, and labels;
`text` for comments; `description` for tasks. `search.search(conn, query,
entity=None)` returns ranked results across all eight entity types (or filtered
to one — `entity` ∈ story/epic/project/milestone/iteration/label/comment/task).
For comment/task results the `name` field holds the indexed text. FTS5 is
compiled into the stdlib `sqlite3` on CPython 3.12.

## 8. Function-call API surface (REST → function)

Naming: `list_*`, `get_*`, `create_*`, `update_*`, `delete_*`, plus `search_*`. Each takes
`conn` first. Representative subset:

- stories: `list_stories(conn, *, project_id, epic_id, iteration_id, state_type, ...)`,
  `get_story(conn, id)`, `create_story(conn, name, **fields)`, `update_story(conn, id, **fields)`,
  `delete_story(conn, id)`, `move_story_state(conn, id, new_state_id)`,
  `assign_owner(conn, story_id, member_id)`, `remove_owner(...)`.
- epics: `list_epics`, `get_epic`, `create_epic`, `update_epic`, `delete_epic`,
  `list_epic_stories(conn, epic_id)`.
- iterations / milestones / projects / labels / members / groups / workflows: analogous
  CRUD + a `list_*_stories(conn, <parent>_id)` where it makes sense.
- tasks: `list_tasks(conn, story_id)`, `create_task`, `update_task`, `delete_task`,
  `complete_task`.
- comments: `list_comments(conn, story_id)`, `create_comment`, `update_comment`,
  `delete_comment`.
- story_links: `list_links`, `create_link`, `delete_link`.
- search: `search(conn, query, *, entity=None)`.
- plan: `export_plan(conn) -> dict`, `export_to_file(conn, path)`, `import_plan(conn, data) -> dict`, `import_from_file(conn, path)`.

### Errors (`backend/errors.py`)
- `NotFound(resource, id)` — raised when a referenced entity doesn't exist.
- `ValidationError(msg)` — invalid args / constraint violation.
- `Conflict(msg)` — e.g. duplicate unique link.

## 9. CLI conventions (`main.py` + `cli/commands.py`)

- `python main.py` with no args → launch the TUI (`tui/app.py`).
- `python main.py <resource> <action> [flags]` → CLI. Examples:
  - `main.py story list [--project P] [--iteration I] [--state done]`
  - `main.py story get <id>`
  - `main.py story create --name "Fix login bug" --project backend --type bug --labels bug,auth`
  - `main.py story move <id> --state "In Progress"`
  - `main.py story assign <id> --owner chris`
  - `main.py epic list`, `main.py epic create --name "Auth" --project backend`
  - `main.py task add --story 42 --desc "Write tests"`
  - `main.py comment add --story 42 --text "..."`
  - `main.py search "login bug"`
- `$EDITOR` flow for long-form text: `story edit <id>` opens `$VISUAL`/`$EDITOR`
  (fallback `vi`) on a buffer of `name`/blank/`description` and updates them;
  `comment add` and `task add` with no `--text`/`--desc` open the editor for the
  body. A non-zero editor exit aborts with no change. `--text`/`--desc` still
  work for the non-editor path.
- Use `argparse` subparsers. Resource = subparser group, action = nested subparser.
- Output is plain text / simple tables (stdlib only — no rich dependency required unless
  chosen). Keep it greppable and agent-friendly (stable, parseable output).
- Mutating commands print the resulting entity (so an AI can read back the assigned id).
- IDs and human names both accepted where a human would type a name: resolve names to ids
  via a small `resolve_*` helper (e.g. `--project backend` resolves the project named
  "backend"). Ambiguous names → error with options.

## 10. TUI conventions (`tui/app.py`)

- Full-screen interactive UI, built with **Textual** (library now decided — see
  §15). Shares the **same** backend functions as the CLI.
- Layout: `Header`, a `Static` filter-bar, a `Horizontal` of story `DataTable`
  (left) + `RichLog` detail pane (right), `Footer` with the keybindings.
- Modal screens (`ModalScreen`): `CreateStoryScreen`, `MoveStateScreen`,
  `TextScreen` (comments/tasks), `SearchInputScreen`, `FilterScreen`,
  `ConfirmScreen`. Each dismisses with a result; the app installs a callback.
- Bindings: `n` new, `u` update/edit, `m` move, `c` comment, `t` task, `x`
  task toggle/edit, `o` owners, `l` labels, `f` filter, `/` search, `e` toggle
  complete, `d` delete, `r` refresh, `J`/`K` move down/up (reorder by swapping
  `position`), `q` quit. `e` toggles between a `done` workflow state and
  `unstarted`. `u` opens `EditStoryScreen` (name/desc/type/state/project/epic/
  iteration/group/deadline; state changes go through `move_story_state` so
  `completed_at` stays consistent). `x` opens `TaskActionScreen` (toggle a
  task's completion or edit its description). `o` and `l` open
  `OwnerScreen`/`LabelScreen` to toggle a member's ownership or a label on the
  selected story.
- The detail pane renders the story's tasks and comments (threaded, with author).
- The connection is opened in `on_mount` and closed in `on_unmount`.
- `main.py` (no args) imports `tui.app.run`; if `textual` isn't installed it
  prints an install hint and exits 1 (the CLI still works).

## 11. Dependencies

- **Stdlib only for the backend + CLI:** `sqlite3`, `argparse`, `dataclasses`, `datetime`.
- TUI: `textual>=0.80` (declared in `pyproject.toml` `dependencies`).
- Tests: `pytest>=8`, `hypothesis`; lint: `ruff>=0.6`; config: `pyyaml>=6`
  (all in `[dependency-groups] dev`, installed by a plain `uv sync`). Run
  tests with `uv run python -m pytest -q` (130 tests; fresh temp DB per test;
  TUI tests use Textual's headless `App.run_test()` pilot and are skipped if
  `textual` isn't installed). Lint with
  `uv run ruff check backend cli tui main.py tests` (config in `[tool.ruff]`;
  E501/E701/E702 ignored to permit long DDL lines and compact one-liners).
- CI: `.github/workflows/ci.yml` runs ruff + pytest on Python 3.12 for every
  push/PR.

## 12. First-run / seeding (`backend/db.py`)

On first connect (db missing or `schema_version` empty / at 0):
1. Create all tables.
2. Create **schema_version** row = current version.
3. Seed: one `member` (the local user, e.g. name from env `USER` or "me", mention_name
   derived), one default `workflow` with the three standard `workflow_state`s
   (Unstarted / Started / Done, types unstarted/started/done).

Seeding must be idempotent and run inside a single `BEGIN IMMEDIATE` transaction.

## 13. Non-goals / YAGNI guardrails

- No real Shortcut API calls, no network, no auth system beyond the implicit single user.
- No multi-workspace; one local DB, one member.
- No pagination protocol to mirror Shortcut — just return full lists (add limits only if a
  list actually grows large).
- No webhooks, no integrations, no file uploads, no history tracking, no custom fields.
- Don't build anything in the "Defer" list (§4) until a concrete need appears.
- Don't add caching, async, or a server. This is an in-process library + two front-ends.

## 14. Build order & status

1. ✅ `backend/db.py` — connect, pragmas, busy_timeout, `tx_write`, schema create +
   seed, `schema_version` (now at v3; v2 FTS5 over name+description, v3 FTS5 over
   comment text + task description).
2. ✅ `backend/errors.py` + `backend/models.py` — error types + dataclasses.
3. ✅ `backend/stories.py` + parents (`projects.py`, `workflows.py`, `members.py`,
   `labels.py`) — full CRUD + `move_story_state` (auto `completed_at`).
4. ✅ `cli/commands.py` — full CLI wired for all resources.
5. ✅ Remaining `backend` modules (epics, iterations, milestones, groups, tasks,
   comments, story_links) + their CLI subcommands.
6. ✅ `backend/search.py` (FTS5) + `main.py search`.
7. ✅ `backend/plan.py` (export/import) + `main.py plan`.
8. ✅ TUI (`tui/app.py`) — built with **Textual**; headless smoke test passes
   (create / search / comment / move / toggle-complete / delete).
9. ✅ README + `.gitignore`; committed.

## 15. Open items

- None. The TUI library decision is resolved: **Textual**. Everything in the
  original build plan (backend, CLI, search, TUI) is done.
- If something seems ambiguous, prefer the simplest reading that satisfies the
  locked decisions, and keep field/entity names Shortcut-flavored.

## 16. Implementation notes (decisions made while building)

- `completed_at` is automated: `move_story_state` stamps it on entering a
  `done` workflow state and clears it on leaving; `epic`/`milestone` `update_*`
  does the same on a `done` `state` value.
- Story position defaults to max+1 within the same project (or globally if no
  project); new stories sort to the end.
- `create_story` with no explicit state falls back to the default workflow's
  `default_state_id` (seeded = the Started state).
- `--state` on the CLI resolves by id, by name, or by type (`unstarted`/
  `started`/`done`).
- Name resolution is case-insensitive; ambiguous names error with the matching
  ids. Stories are referenced by id (names aren't unique). `member` resolves by
  `name` or `mention_name`.
- Soft delete (the `archived` flag) applies to `project` and `group`; everything
  else hard-deletes (with CASCADE for owned children, SET NULL for optional FKs).
- The `group` table is a reserved word, so `_util` double-quotes all table
  identifiers.
- Story-link inserts use a raw `execute` (not `_util.insert`) so a UNIQUE
  violation surfaces as a friendly `Conflict`.