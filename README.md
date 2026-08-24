# projectplanner

A local, personal project-planning tool that uses [Shortcut]'s (formerly
Clubhouse) v3 data model as **inspiration only** — not a contract — and stores
everything in a single local SQLite database (`planner.db`). There is no server
and no network. You talk to it through a scriptable **CLI** (the primary
interface for automation and AI agents) and an optional full-screen **TUI**.

It is built to be shared with AI coding systems: the CLI is stable, parseable,
and agent-friendly — every mutating command prints the resulting entity so an
agent can read back the assigned id, and `--json` gives structured output for
reliable parsing.

[Shortcut]: https://shortcut.com

---

## Executive Summary

This document is the complete user and integrator guide for `projectplanner`.
It is written so that a human **or** an AI agent can pick up the tool with no
other context.

**How to use this document to gather context quickly:**

- If you are an **AI agent about to use the CLI as a tool**, read
  [AGENTS.md](AGENTS.md) — the focused operational guide distilled for agents
  (when to use, the command contract, the work loop, anti-patterns, error
  recovery). This README remains the full reference; AGENTS.md points back to it
  for depth.
- If you need to know **what entities exist and how they relate**, read
  [§ Data Model](#data-model).
- If you need to know **how the tool is built and how concurrency works**
  (e.g. before modifying it), read [§ Architecture & Concurrency](#architecture--concurrency)
  and the per-resource [§ CLI Reference](#cli-reference).
- For the authoritative design/schema/build record (including the full SQL DDL
  and deferred-scope list), see [CONTEXT.md](CONTEXT.md) — this README is the
  interface guide; CONTEXT.md is the engineering source of truth.

**One-line mental model:** a story is a unit of work; it lives in a workflow
state, may belong to a project/epic/iteration/group, and carries owners, labels,
tasks, comments, and links. You create stories, move them through states, and
search them — all from the command line, against a local file.

---

## Table of Contents

| # | Section |
|---|---------|
| 1 | [Executive Summary](#executive-summary) |
| 2 | [Table of Contents](#table-of-contents) |
| 3 | [Intent & Design Goals](#intent--design-goals) |
| 4 | [Requirements & Setup](#requirements--setup) |
| 5 | [Quick Start](#quick-start) |
| 6 | [Running the Tool: CLI vs TUI](#running-the-tool-cli-vs-tui) |
| 7 | [CLI Conventions](#cli-conventions) |
| 8 | [CLI Reference](#cli-reference) |
| 9 | [AI Agent Guide (see AGENTS.md)](#ai-agent-guide-using-the-cli-during-development) |
| 10 | [Data Model](#data-model) |
| 11 | [Architecture & Concurrency](#architecture--concurrency) |
| 12 | [Storage & Schema](#storage--schema) |
| 13 | [Exit Codes & Errors](#exit-codes--errors) |
| 14 | [Examples & Recipes](#examples--recipes) |
| 15 | [Non-goals & Differences from Shortcut](#non-goals--differences-from-shortcut) |
| 16 | [Tests](#tests) |
| 17 | [Further Reading](#further-reading) |

---

## Intent & Design Goals

`projectplanner` exists to give a single person (and the AI agents that person
works with) a fast, durable, queryable place to track software work **without**
depending on a hosted service, a network, or an account.

**Design goals:**

- **Local-first, zero-dependency-by-default.** One SQLite file. The backend and
  CLI use only the Python 3.12 standard library (`sqlite3`, `argparse`,
  `dataclasses`, `datetime`). The TUI adds [Textual](https://textual.textualize.io).
- **Scriptable and agent-friendly.** The CLI is the automation surface. Output
  is stable and greppable by default; `--json` makes it reliably parseable.
  Mutating commands echo the resulting entity so the caller learns the new id.
- **Simple, understandable model.** Borrow Shortcut's entity vocabulary
  (story, epic, iteration, milestone, project, workflow, label, …) because it is
  familiar and well-shaped, but keep a trimmed local schema — no fidelity to
  Shortcut's JSON shapes or endpoints.
- **Concurrency-safe enough for multiple agents.** Several processes may write
  at once; writers serialize via SQLite's file lock with a 5s busy timeout, so a
  second writer blocks (rather than errors) until the first commits.
- **No server, no async, no cache.** It is an in-process library plus two thin
  front-ends (CLI, TUI) over the same backend functions.

---

## Requirements & Setup

- **Python 3.12+** (stdlib `sqlite3` ships with FTS5, used for search).
- **Backend + CLI:** stdlib only — no install step beyond having Python 3.12.
- **TUI / dev / tests (optional):** requires [uv](https://docs.astral.sh/uv/)
  (which uses `.python-version`). One command creates a gitignored `.venv` and
  installs everything — runtime (`textual`) and dev (`pytest`, `ruff`, …) —
  from the locked `uv.lock`:

  ```bash
  uv sync
  ```

- **Database:** `planner.db` is created in the repo root on first run and seeded
  with one member (name from `$USER`, mention name derived from it) and a
  default workflow named **Default** with three states — **Unstarted**
  (`unstarted`), **Started** (`started`), **Done** (`done`). The **Started**
  state is the workflow default, so new stories start there unless you say
  otherwise.

> No `.env`, config file, or auth is required. There is a single implicit local
> user (the seeded member, id 1).

---

## Quick Start

```bash
# Containers
python main.py project create --name backend --desc "core api" --abbr bck --color "#65c8c8"
python main.py label    create --name auth --color "#f00" --desc "authentication"
python main.py epic     create --name Auth --project backend
python main.py iteration create --name "Sprint 1" --status active --start 2026-09-01 --end 2026-09-14

# A unit of work, with relations, owners, and labels (names resolve to ids)
python main.py story create --name "Fix login bug" --desc "oauth redirect fails" \
    --project backend --type bug --epic Auth --iteration "Sprint 1" \
    --owners "$(whoami)" --labels auth

# Break it down, comment, and advance it
python main.py task    add --story 1 --desc "write tests"
python main.py comment add --story 1 --text "reproduced on staging"
python main.py story move 1 --state done      # accepts id, name, or type

# Bulk actions, deadlines, and search
python main.py story move 1 2 3 --state done  # bulk move multiple stories
python main.py story deadlines                # urgency-sorted, overdue flagged
python main.py search "login OR auth"

# Inspect
python main.py story list --project backend
python main.py story detail 1

# Config, backup, and export
python main.py config init                   # create planner.yaml from example
python main.py plan backup                    # timestamped backup of planner.db
python main.py plan export --file plan.json   # portable JSON snapshot
python main.py --help                         # full CLI overview with examples
```

---

## Running the Tool: CLI vs TUI

- `python main.py <resource> <action> [flags]` — one-shot **CLI** (primary
  interface for agents and scripts).
- `python main.py` (no args) — full-screen **TUI** (Textual). Needs `textual`
  installed; if it is missing, `main.py` prints an install hint and exits 1
  (the CLI is unaffected).

**TUI keybindings** (shown in the footer):

| Key | Action | | Key | Action |
|-----|--------|---|-----|--------|
| `n` | new story | | `f` | filter (project/state/owner/label) |
| `u` | update story | | `/` | search |
| `m` | move state | | `e` | toggle complete |
| `c` | add comment | | `x` | task toggle/edit |
| `t` | add task | | `o` | owners |
| `r` | refresh | | `l` | labels |
| `h` | links | | `S` | plan (export/import/backup) |
| `d` | delete story | | `J` | move down |
| `K` | move up | | `q` | quit |
| `Ctrl+P` | command palette | | `v` | multi-select |
| `a` | auto-refresh | | `b` | browse (epics/iterations/projects/milestones) |

Story editing happens **in the right detail pane** (not a modal): pressing
`u` or `n` swaps the read-only view for an in-pane edit form. Multi-select
(`v`) allows bulk delete/move/assign/label. The command palette (`Ctrl+P`)
fuzzy-searches all actions. Auto-refresh (`a`) polls for external changes.

The TUI shares the exact same backend functions as the CLI — there is no
separate data layer.

---

## CLI Conventions

These are the rules an agent can rely on when parsing output and chaining
commands.

- **Invocation:** `python main.py <resource> <action> [flags]`.
- **`--format` (anywhere in the command)** selects output format:
  `text` (default), `json`, `csv`, or `id-only`. Place it before or after
  the subcommand — both work. The deprecated `--json` flag still works as an
  alias for `--format json`.
- **Name or id, your choice.** Anywhere a human would type a name
  (`--project backend`, `--owner chris`, `--labels bug,auth`), it is resolved to
  an id **case-insensitively**. Pass a bare number to use an id directly.
  Ambiguous names error with the matching ids. **Stories are referenced by id**
  (names are not unique).
- **`--state`** on `story move` accepts a state **id**, a state **name**
  (`"Done"`), or a state **type** (`unstarted`/`started`/`done`). By-type is
  convenient against the seeded workflow.
- **Mutating commands echo the resulting entity** — text by default, structured
  with `--format`. This is how you learn the id that was just assigned.
- **Errors** print `error: <message>` to **stderr** and the process exits
  non-zero (see [§ Exit Codes & Errors](#exit-codes--errors)).
- **Comma-separated lists** (`--owners`, `--labels`) accept mixed names and ids,
  separated by commas.
- **Archived soft-delete:** `project` and `group` have an `archived` flag
  (`project archive <id>`, `group archive <id>`); `list` hides archived items
  unless `--archived` is passed. Everything else hard-deletes (with cascade —
  see [§ Data Model](#data-model)).
- **`completed_at` is automated:** moving a story/epic/milestone into a `done`
  state stamps `completed_at`; moving it back out clears it. You do not set it
  manually.
- **`$EDITOR` for long-form text.** `story edit <id>` opens `$VISUAL`/`$EDITOR`
  (fallback `vi`) on a buffer of the story's name and description and updates
  them (line 1 = name, blank line, rest = description). `comment add` and
  `task add` with no `--text`/`--desc` open the editor for the body. A non-zero
  editor exit aborts with no change. The `--text`/`--desc` flags still work for
  the non-editor path.

### The `--format` contract

- **`text`** (default): human-readable tables or key/value lines.
- **`json`**: structured JSON — single object, array of objects, or
  `story detail` shape with nested `story`, `owners`, `labels`, `tasks`,
  `workflow_state`.
- **`csv`**: CSV with headers. Works for flat lists (story/epic/project list,
  search). For nested objects (e.g. `story detail`) prints a warning and
  falls back to JSON.
- **`id-only`**: prints just the numeric id(s), one per line. Useful for
  piping to `xargs` or loops. Works for lists and single entities
  (including `story detail` → prints the story id).
- Delete/status → a small object like `{"deleted": "story", "id": 7}`.
- Timestamps are ISO-8601 UTC strings; nullable columns are `null`.

---

## CLI Reference

Resources: `story`, `epic`, `iteration`, `milestone`, `project`, `label`,
`member`, `group`, `workflow`, `task`, `comment`, `link`, `search`, `plan`.

Every `<resource> <action>` accepts `-h` for the exact flag list
(`python main.py story create -h`). Below is the compact surface; flags shown
are the commonly used ones.

**All commands accept `--format`** (`text`|`json`|`csv`|`id-only`, default `text`)
and the deprecated `--json` alias.

### story
| Action | Flags (selected) | Notes |
|--------|------------------|-------|
| `list` | `--project --epic --iteration --state-type --group --owner --label --q --no-completed` | filters; `--state-type` is unstarted/started/done |
| `get <id>` | — | one story |
| `detail <id>` | — | story + owners/labels/tasks/state |
| `create` | `--name --desc --type --state --project --epic --iteration --group --requested-by --deadline --owners --labels` | `--type` bug/feature/chore |
| `update <id>` | `--name --desc --type --project --epic --iteration --group --deadline --position` | pass a nullable FK as a number or `None`? *(clear via update API)* |
| `edit <id>` | — | open `$EDITOR` on name+description and update them |
| `move <id>` | `--state` | id/name/type; stamps/clears `completed_at` |
| `assign <id>` / `unassign <id>` | `--owner` | add/remove a member owner |
| `label <id>` / `unlabel <id>` | `--label` | add/remove a label |
| `delete <id>` | — | cascades to tasks/comments/links/owners/labels |

### epic
`list [--project --milestone]` · `get` · `create --name [--desc --state --project --milestone]`
· `update` · `delete` · `stories <id>` (stories in the epic). State:
`planned`/`in_progress`/`done` (entering `done` stamps `completed_at`).

### iteration
`list [--status]` · `get` · `create --name [--desc --status --start --end]`
· `update` · `delete` · `stories <id>`. Status: `planned`/`active`/`done`.

### milestone
`list [--state]` · `get` · `create --name [--desc --state]` · `update` · `delete`
· `epics <id>`. State: `planned`/`in_progress`/`done`.

### project
`list [--archived]` · `get` · `create --name [--desc --abbr --color]` · `update`
· `archive <id>` · `delete` · `stories <id>`. Soft-deleted via `archived`.

### label
`list` · `get` · `create --name [--color --desc]` · `update` · `delete`.

### member
`list` · `get` · `create --name [--mention]` · `update` · `delete`. Resolved by
`name` or `mention_name`.

### group
`list [--archived]` · `get` · `create --name [--desc]` · `update` · `archive <id>`
· `delete` · `stories <id>`. Soft-deleted via `archived`.

### workflow
`list` · `get` · `create --name [--states "Todo:unstarted,Doing:started,Done:done"]`
· `delete` · `states <id>` (list states) · `add-state <id> --name --type [--position]`.
State `type` is `unstarted`/`started`/`done`.

### task
`list --story <id>` · `add --story <id> [--desc] [--complete]` · `update <id>`
· `complete <id>` / `uncomplete <id>` · `delete <id>`. With no `--desc`, `add`
opens `$EDITOR`. Owned by a story (cascade).

### comment
`list --story <id>` · `add --story <id> [--text] [--author --parent]`
· `update <id> --text` · `delete <id>`. With no `--text`, `add` opens `$EDITOR`.
`--parent` threads a reply.

### link
`list [--story <id>]` · `add --subject <id> --verb <v> --object <id>` · `delete <id>`.
Verbs: `blocks` `blocks_by` `duplicates` `duplicated_by` `relates_to`. A story
cannot link to itself; (subject, verb, object) is unique.

### plan
`export [--file planner-export.json]` · `import [--file planner-export.json]`.
Exports/imports the entire plan (all entities with relationships preserved) as a
portable JSON snapshot. Ids are remapped on import so foreign keys survive. Use
this to share plan state across environments or back it up.

### search
`search <terms...> [--entity story|epic|project|milestone|iteration|label|comment|task]`.
Terms are passed to FTS5 `MATCH`, so you can use `login OR auth`, `log*` (prefix),
`"exact phrase"`, and boolean operators. Results are ranked by relevance.
Search covers stories/epics/projects/milestones/iterations/labels (name +
description) and comments (`text`) and tasks (`description`).

---

## AI Agent Guide: Using the CLI During Development

The agent operational playbook now lives in **[AGENTS.md](AGENTS.md)** — a
focused guide distilled for AI coding agents: when to use the tool (and when
not to), the command contract, the create → move → comment → finish work loop
with copy-paste recipes, anti-patterns, and error recovery.

Start there. This README remains the full reference (CLI surface, data model,
architecture, schema); AGENTS.md links back to the relevant sections for
depth.

---

## Data Model

The **story** is the central entity. Everything else is a container or an
attachment.

```
project ─┐
epic ────┤ (epic may belong to a project + milestone)
iteration│
group ───┤
         ▼
       story ──┬── workflow_state (type: unstarted | started | done)
               ├── owners   (members, many)
               ├── labels   (many)
               ├── tasks    (checklist, cascade-deleted with the story)
               ├── comments (threaded via parent_id, cascade-deleted)
               └── links    (directed: subject --verb--> object story)

milestone groups epics. workflow contains workflow_states.
member is a person (one seeded local user). group is a team.
```

**Key fields on a story:** `id`, `name`, `description`, `story_type`
(`bug`/`feature`/`chore`), `workflow_state_id`, `epic_id`, `iteration_id`,
`project_id`, `group_id`, `requested_by_id`, `deadline`, `position`, `created_at`,
`updated_at`, `completed_at`.

**Behaviors / invariants:**

- **State typing.** Each workflow state has a `type` of `unstarted`, `started`,
  or `done`. `completed_at` is set only while in a `done`-typed state.
- **Position.** New stories get `position = max(position)+1` within their
  project (or globally if no project), so they sort to the end.
- **Default state.** `story create` with no `--state` uses the default workflow's
  `default_state_id` (seeded = Started).
- **Delete semantics.** Deleting a story cascades to its tasks, comments,
  story-links (as subject or object), owners, and labels. Deleting a project /
  epic / iteration / group / milestone / member **sets the story's FK to null**
  (the story survives). Projects and groups are *soft-deleted* via `archived`.
- **Story links.** A link is a directed `(subject, verb, object)` triple with
  `verb` ∈ {`blocks`, `blocks_by`, `duplicates`, `duplicated_by`, `relates_to`};
  the triple is unique and a story cannot link to itself.

Entities built: **Stories, Epics, Iterations, Milestones, Projects,
Workflows (+States), Labels, Members, Groups, Comments, Tasks, Story Links,
Search, Plan Export/Import**. Deferred (not built unless needed): Documents, Objectives + Key
Results, Custom Fields, Entity Templates, Files, Linked Files, Repositories,
Integrations, Health, History, Reactions, External Links, Categories, Epic
Workflow — see [CONTEXT.md §4](CONTEXT.md).

---

## Architecture & Concurrency

```
main.py    entry point: no args → TUI; args → CLI
backend/   function-call API over SQLite (each REST-shaped op = one Python fn)
cli/       argparse subparsers → calls backend, prints text/JSON
tui/       full-screen Textual UI → same backend functions
```

- **Function-call API, not REST.** Each operation is a plain function in
  `backend/*.py` taking the `sqlite3.Connection` as its first argument
  (e.g. `stories.get_story(conn, id)`). The CLI and TUI are thin layers.
- **Connection is passed, not global.** `backend/db.connect(db_path)` returns a
  configured connection; every function takes `conn` first.
- **No ORM.** Plain dataclasses (`backend/models.py`) for return values; raw SQL
  via `sqlite3`. A small shared helper (`backend/_util.py`) does get/list/
  insert/update/delete and maps `sqlite3.IntegrityError` to `NotFound` /
  `ValidationError` / `Conflict`.
- **Concurrency.** Every connection sets `PRAGMA busy_timeout = 5000` and
  `PRAGMA foreign_keys = ON`. Writes go through a `tx_write(conn)` helper that
  does `BEGIN IMMEDIATE` (acquires the write lock up front) and commits or
  rolls back. Result: concurrent writers serialize — a second writer blocks
  (up to 5s) until the first commits, then proceeds, rather than failing with
  `SQLITE_BUSY`. WAL is intentionally off (YAGNI); enable it only if
  read-during-write contention is observed.
- **Migrations.** A `schema_version` table tracks an integer version; on connect
  any pending versioned statements are applied idempotently inside
  `BEGIN IMMEDIATE`. Current schema version: **3** (v1 = core tables + seed;
  v2 = FTS5 over name+description; v3 = FTS5 over comment text + task description).
- **Search.** FTS5 external-content tables mirror `name` + `description` for
  stories, epics, projects, milestones, iterations, and labels, plus `text` for
  comments and `description` for tasks, kept in sync by after-insert/update/delete
  triggers.

---

## Storage & Schema

- **File:** `planner.db` in the repo root (gitignored). Created + seeded on first
  connect.
- **Ids:** integer autoincrement primary keys.
- **Timestamps:** TEXT ISO-8601 UTC (`created_at`, `updated_at`, `completed_at`).
- **Position:** REAL, for insert-between reordering.
- **Foreign keys:** enforced (`PRAGMA foreign_keys = ON`). CASCADE for owned
  children (tasks, comments, story_owner, story_label, story_link,
  workflow_state); SET NULL for optional parent links on stories.
- **The `group` table** is a SQLite keyword, so all table identifiers are
  double-quoted in the helper SQL.

The full DDL and column-by-column schema live in
[CONTEXT.md §7](CONTEXT.md). This README intentionally does not duplicate the
DDL.

---

## Exit Codes & Errors

| Exit | Meaning | Example stderr |
|------|---------|----------------|
| `0`  | success | — |
| `1`  | a backend error (`NotFound`, `ValidationError`, `Conflict`) | `error: story 999 not found` |
| `2`  | argparse usage error (bad flags / choices) | `argument --type: invalid choice: 'bogus'` |

Error classes (`backend/errors.py`, all subclass `PlannerError`):

- `NotFound(resource, id)` — referenced entity doesn't exist.
- `ValidationError(msg)` — invalid args or a CHECK/constraint violation.
- `Conflict(msg)` — a uniqueness conflict (e.g. a duplicate story link).

Agents should treat exit `1` as a recoverable, retryable-after-fix condition,
and exit `2` as a usage bug in the command they constructed.

---

## Examples & Recipes

**Show my open work, newest context first:**
```bash
python main.py story list --owner "$(whoami)" --no-completed --json
```

**Everything in a sprint that isn't done:**
```bash
python main.py story list --iteration "Sprint 1" --no-completed
```

**Add a labeled bug in one shot:**
```bash
python main.py story create --name "Crash on empty input" --type bug \
    --project backend --labels bug,crash --owners "$(whoami)"
```

**Move a whole project's stories?** There's no bulk move command (YAGNI); loop
in the caller:
```bash
for id in $(python main.py story list --project backend --state-type unstarted --json \
            | python -c "import sys,json; print(' '.join(str(s['id']) for s in json.load(sys.stdin)))"); do
  python main.py story move "$id" --state started
done
```

**Threaded reply to a comment:**
```bash
python main.py comment add --story 17 --text "agreed, see PR #42" --parent 5
```

---

## Non-goals & Differences from Shortcut

- **No Shortcut calls, no network, no auth.** This is not a Shortcut client or
  mock; the model is inspiration only.
- **One local workspace, one seeded user.** No multi-workspace, no login.
- **No pagination protocol.** Lists return in full (add limits only if a list
  actually grows large).
- **No webhooks, integrations, file uploads, history tracking, or custom
  fields.**
- **No caching, async, or server.** In-process library + two front-ends.
- **Trimmed shapes.** Field/entity *names* are Shortcut-flavored where carried,
  but JSON shapes are our own, not Shortcut's.

---

## Tests

The test suite uses [pytest](https://docs.pytest.org) with a fresh temp
database per test (via `tmp_path`), so tests are isolated and need no manual
setup.

```bash
uv sync
uv run python -m pytest -q
```

Linting uses [ruff](https://docs.astral.sh/ruff/) (config in `pyproject.toml`);
CI (`.github/workflows/ci.yml`) runs both on every push/PR:

```bash
uv run ruff check backend cli tui main.py tests
```

Coverage:

- `tests/test_db.py` — connect/migrate/seed idempotency, pragmas, FTS tables +
  triggers, `tx_write` commit/rollback.
- `tests/test_stories.py` — story CRUD, `completed_at` automation, position
  defaults, all `list_stories` filters, `StoryDetail` shape, owner/label
  helpers, delete cascade, SET NULL on parent delete.
- `tests/test_parents.py` — epics/milestones/iterations `completed_at` and
  filters; project/group archive; label/member/workflow CRUD + state rules.
- `tests/test_tasks.py`, `test_comments.py`, `test_story_links.py` — child
  entities, threading, verb/self-link/UNIQUE rules, cascade.
- `tests/test_search.py` — FTS5 insert/update/delete sync, entity filter,
  ranking, boolean/prefix, error cases, and comment/task search + sync.
- `tests/test_cli.py` — `run()` with `--json` shapes, name resolution +
  ambiguity, `--state` by id/name/type, exit codes (1 backend, 2 argparse), and
  the `$EDITOR` flow (comment/task add, story edit, abort).
- `tests/test_tui.py` — headless Textual pilot (create/edit/toggle/search/
  comment/owners/labels/reorder/delete); skipped if `textual` isn't installed.
- `tests/test_concurrency.py` — two writers serialize (the second blocks until
  the first commits).

96 tests.

---

## Further Reading

- **[CONTEXT.md](CONTEXT.md)** — the engineering source of truth: locked
  decisions, full schema DDL, the build-vs-defer scope, concurrency model, and
  build history.
- **In-code docs** — each source file has module and function docstrings
  describing inputs, outputs, and invariants. Start at `backend/db.py` and
  `backend/stories.py`.
- **`python main.py <resource> <action> -h`** — the authoritative flag list for
  any command.