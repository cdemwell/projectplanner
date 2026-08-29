# CONTEXT.md — AI Contributor Briefing

> **Single point of contact for an AI agent about to contribute code to
> `projectplanner`.** Read this file first; it tells you what the project is,
> what is decided, what is in scope, and how to work on it. For depth — the
> architecture diagrams, the full walkthroughs for adding entities/commands/TUI
> features, the testing methodology, and operating the software — read
> [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md). This file is the briefing;
> DEVELOPMENT.md is the reference.

---

## 1. What this project is

A **local, personal project-planning tool** that uses **Shortcut's (formerly
Clubhouse) v3 data model as inspiration — not a contract**. We do **not** call
the Shortcut REST API, we are **not** a faithful mock of Shortcut, and we do not
reproduce its JSON shapes or endpoints. We borrow the *entity vocabulary*
(story, epic, iteration, milestone, project, workflow, label, member, group,
task, comment) and keep Shortcut-flavored *field names* for the fields we
carry; everything else is a trimmed local schema.

The tool is **meant to be shared with AI coding systems**: an AI agent (or a
human) reads, queries, and modifies the plan via a scriptable **CLI** or an
interactive **TUI**, both over a single local SQLite database (`planner.db`).
There is no server and no network.

The on-disk reference `Shortcut Rest API, V3.html` is the *model* reference
only — never a spec to implement against.

## 2. How to use this file

| You are… | Read |
|---|---|
| An AI about to contribute code | This file, then [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) for depth |
| An AI operating the CLI as a tool | [AGENTS.md](AGENTS.md) (the operational playbook) |
| A human learning the system | [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) (human-first dev guide) |
| A human using the tool | [README.md](README.md) (user & integrator guide) |
| An AI *user* of the tool (process) | [SDLC.md](SDLC.md) (the AI-orchestrated development process) |

## 3. Locked decisions (the contract)

These are settled. **Don't re-litigate them** unless the user explicitly asks.

1. **Purpose:** personal/local planning tool, shared with AI agents. Shortcut is
   just a starting model.
2. **Scope:** build a **core set** of resources now; defer the rest until a
   concrete need appears (see §5).
3. **Data fidelity:** sensible local schema (trimmed). Keep Shortcut's entity
   and field *names* for the fields we carry; drop everything else. No fidelity
   to Shortcut's nested JSON shapes.
4. **`main.py` modes:** no args → interactive **TUI**; args → one-shot **CLI**
   subcommands. Both share one backend service layer over SQLite.
5. **Storage:** single local SQLite file, `planner.db`, in the repo root.
6. **Concurrency:** multiple processes may write concurrently. Writers
   **block** (not error) via `busy_timeout` + `BEGIN IMMEDIATE`.
   Single-writer-at-a-time is accepted behavior. WAL is off (YAGNI).

## 4. Repository state (current)

**Backend + CLI + TUI are all built, tested, and linted.** Schema version is
**4**. The suite is **180 tests**, all passing.

- `main.py` — dispatch: no args → Textual TUI; resource name or CLI flag → CLI.
- `backend/` — `db.py` (connect, pragmas, migrations, seed, `tx_write`),
  `errors.py`, `models.py` (dataclasses), `_util.py` (shared SQL helpers),
  `config.py` (YAML config), `plan.py` (export/import), `search.py` (FTS5), and
  one module per entity: `members`, `groups`, `workflows`, `projects`,
  `labels`, `milestones`, `epics`, `iterations`, `stories`, `tasks`,
  `comments`, `story_links`.
- `cli/commands.py` — argparse subparsers for 15 resources (`story` `epic`
  `iteration` `milestone` `project` `label` `member` `group` `workflow` `task`
  `comment` `link` `search` `plan` `config`); `--format` text/json/csv/id-only
  (`--json` is a deprecated alias); `--db`, `--config`, `--dry-run`,
  `--rotate-backup`, `--limit`/`--offset`; name→id resolution; `$EDITOR` flow.
- `tui/` — `app.py` (Textual `PlannerApp`: three-pane Miller-columns browser,
  entity switching, drill-in/out, zoom, multi-select bulk ops, command palette,
  auto-refresh, help overlay), `chains.py` (declarative parent→child chain
  model), `detail.py` (generic `EntityDetailPane`).
- `tests/` — pytest suite (fresh temp DB per test; autouse fixture redirects
  `db.DEFAULT_DB_PATH` so tests can never touch the real `planner.db`).
- `docs/DEVELOPMENT.md` — the development guide (architecture, walkthroughs,
  testing, operating).
- `pyproject.toml` — `projectplanner` v0.1.0, `requires-python = ">=3.12"`,
  runtime dep `textual>=0.80` (TUI only; backend + CLI are stdlib), dev group
  `pytest`, `ruff`, `hypothesis`, `pyyaml`.
- `.github/workflows/ci.yml` — ruff + pytest on Python 3.12 for every push/PR.

## 5. Scope — build vs defer

### Built (the core)
Stories, Epics, Iterations, Milestones, Projects, Workflows (+ Workflow States),
Labels, Members, Groups, Comments, Tasks, Story Links, Search, Plan
Export/Import, Config, Backup.

### Deferred (stub nothing; don't build until needed)
Documents, Objectives + Key-Results, Custom-Fields, Entity-Templates, Files,
Linked-Files, Repositories, Integrations, Health, Story History, Reactions,
External-Link, Categories, Epic Workflow.

When a deferred entity is needed, add it then. **Do not** create empty tables
or stubs for deferred entities now. The walkthrough for adding a new entity is
in [docs/DEVELOPMENT.md §4.4](docs/DEVELOPMENT.md#44-how-to-add-a-new-entity-walkthrough).

## 6. Architecture in brief

The full picture (with mermaid diagrams of increasing detail) is in
[docs/DEVELOPMENT.md §3](docs/DEVELOPMENT.md#3-architecture). The essentials an
AI contributor must internalize:

- **Function-call API, not REST.** Each REST-shaped operation is a plain Python
  function in a `backend/*.py` module, e.g. `GET /stories/{id}` →
  `stories.get_story(conn, id)`. The CLI and TUI are thin layers over these.
- **Connection is passed, not global.** `db.connect(db_path)` returns a
  configured connection; every function takes `conn` as its first argument.
- **No ORM.** Plain dataclasses (`backend/models.py`) for return values; raw
  SQL via `sqlite3`. `_util.py` provides get/list/insert/update/delete and maps
  `sqlite3.IntegrityError` → `Conflict`/`ValidationError`.
- **Errors** (`backend/errors.py`): `NotFound(resource, id)`,
  `ValidationError(msg)`, `Conflict(msg)` — all subclass `PlannerError`.
- **Concurrency.** Every connection sets `PRAGMA busy_timeout = 5000` and
  `PRAGMA foreign_keys = ON`. Writes go through `db.tx_write(conn)` (a context
  manager doing `BEGIN IMMEDIATE` … commit/rollback). Never write outside it.
- **Migrations.** A `schema_version` table tracks an integer version; on
  connect, pending versioned statements are applied idempotently inside
  `BEGIN IMMEDIATE`. Current version: **4** (v1 core+seed; v2 FTS5 over
  name+description; v3 FTS5 over comment text + task description; v4
  `workflow_state.description` column).
- **Search.** FTS5 external-content tables kept in sync by triggers (created in
  `db.py`); `backend/search.py` only queries them. `search.search(conn, query,
  entity=None, limit=, offset=)` covers story/epic/project/milestone/iteration/
  label/comment/task, ranked by bm25.
- **Config.** `backend/config.py` loads a flat YAML file. Precedence: **CLI
  flags > config file > built-in defaults**.
- **Plan.** `backend/plan.py` exports/imports the whole plan as a JSON snapshot,
  remapping primary keys on import so FKs survive.

## 7. Conventions an AI contributor must follow

These are the rules the review gate checks. Violating them gets a story sent
back.

- **`conn` first, always.** Every backend function takes the connection as its
  first argument. No globals, no module-level connections.
- **Writes go through `db.tx_write`.** Never `conn.execute("INSERT …")` outside
  a `tx_write` block. This is what makes concurrent writers serialize.
- **`completed_at` is automated.** Moving a story/epic/milestone into a
  `done`-typed state stamps it; moving out clears it. Never set it manually.
- **The TUI is a thin layer.** Fetch and mutate only through backend
  `get_*`/`list_*`/`create_*`/`update_*`/`delete_*` functions. No business
  logic, no parallel query logic, no second source of truth in the TUI.
- **Guard entity-context actions.** A story-only action must no-op/bell when
  the TUI is browsing a different entity kind.
- **Scope transient TUI state.** Search, selection, and filters must be scoped
  to the navigation context and cleared on switch/drill/zoom.
- **Never name a method after a framework internal** (e.g. `_context`,
  `_compose`) — it silently overrides Textual internals.
- **Quote SQL identifiers.** `group` is a SQLite keyword; use `_util._q()` in
  generated SQL.
- **Name resolution is case-insensitive**; ambiguous names error with the
  matching ids. Stories are referenced by **id** (names aren't unique).
- **`--format` over `--json`.** `--json` is a deprecated alias for
  `--format json`. Use `--format json` in new code and tests.
- **Keep field/entity names Shortcut-flavored** for the fields we carry.
- **Tests never touch the real `planner.db`.** Use `tmp_path` (the autouse
  conftest fixture guards this). For manual smoke tests, use `--db /tmp/…`.
- **`ruff`-clean and `pytest`-green before commit.** Run both; don't merge red.

## 8. How to contribute

The development *process* (roles, phases, review gates) is documented in
[SDLC.md](SDLC.md). The practical mechanics:

1. **Work is tracked in the planner itself** — as stories under an epic, with
   tasks and acceptance-criteria comments. Pick up a story, don't invent work.
2. **Work in an isolated git worktree**, never the main checkout. Edit only in
   the worktree; stage only your source files; never `git add -A` (it sweeps in
   worktree dirs and untracked docs).
3. **Implement against the story's acceptance criteria.** Each story is atomic:
   a single responsibility, a 2–3 sentence description, and a verifiable
   definition of done.
4. **Verify before handing off:** `uv run ruff check backend cli tui main.py
   tests` and `uv run python -m pytest -q` must both pass.
5. **Record the outcome on the story** — a comment with the commit id when
   merged, or a fix-list when sent back.

## 9. Verification

```bash
uv sync                          # install runtime + dev deps from uv.lock
uv run python -m pytest -q       # 180 tests, ~1 min
uv run ruff check backend cli tui main.py tests
```

CI (`.github/workflows/ci.yml`) runs `uv sync --frozen` (fails if `uv.lock` is
out of date), then ruff + pytest on Python 3.12 for every push/PR.

## 10. Where work stands

Feature-complete on `main` (see §4). The most recent work added TUI bulk
operations, search/filter scoping, and the help overlay (stories 80–82). Nothing
is pending. Possible future work (only if a concrete need appears): pagination
if lists grow, TUI polish, export/import refinements, a remote/PR.

For the authoritative design history and the original build record, see
`CONTEXT.md.backup` (the previous CONTEXT.md, kept for reference).
