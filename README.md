# projectplanner

A local, personal project-planning tool. It uses [Shortcut]'s (formerly
Clubhouse) v3 data model as **inspiration only** — not a contract — and stores
everything in a single local SQLite database (`planner.db`). There is no server
and no network; you talk to it via a scriptable CLI (and, eventually, a TUI).

It's designed to be shared with AI coding systems: the CLI is stable and
parseable, and every mutating command prints back the resulting entity so an
agent can read the assigned id. Add `--json` to any command for
machine-readable output.

[Shortcut]: https://shortcut.com

## Requirements

- Python 3.12+ (stdlib `sqlite3` ships with FTS5, used for search).
- No third-party dependencies for the backend or CLI.

## Quick start

```bash
python main.py project create --name backend --desc "core api" --abbr bck --color "#65c8c8"
python main.py label create --name auth --color "#f00" --desc "authentication"
python main.py epic create --name Auth --project backend
python main.py iteration create --name "Sprint 1" --status active --start 2026-09-01 --end 2026-09-14
python main.py story create --name "Fix login bug" --desc "oauth redirect fails" \
    --project backend --type bug --epic Auth --iteration "Sprint 1" \
    --owners "$(whoami)" --labels auth
python main.py task add --story 1 --desc "write tests"
python main.py story move 1 --state done          # or --state "Done" / --state done
python main.py story list --project backend
python main.py story detail 1
python main.py search "login OR auth"
```

The database (`planner.db`) is created on first run and seeded with one member
(from `$USER`) and a default workflow with **Unstarted / Started / Done**
states.

## Running it

- `python main.py <resource> <action> [flags]` — one-shot CLI.
- `python main.py` (no args) — interactive TUI (**not yet built**; see
  [CONTEXT.md §10/§15](CONTEXT.md) for the open library decision).

## CLI reference

Resources: `story`, `epic`, `iteration`, `milestone`, `project`, `label`,
`member`, `group`, `workflow`, `task`, `comment`, `link`, `search`.

Each resource has sub-actions (`list`, `get`, `create`, `update`, `delete`,
plus resource-specific ones like `story move`, `story detail`, `epic stories`,
`task complete`, etc.). Run `python main.py <resource> -h` and
`python main.py <resource> <action> -h` for the full flag list.

### Conventions

- **Name or id, your choice.** Where you'd type a human name (`--project
  backend`, `--owner chris`, `--labels bug,auth`), it's resolved to an id
  case-insensitively; pass a number to use an id directly. Ambiguous names
  error with the matching ids. Stories are referenced by id (names aren't
  unique).
- **`--state`** accepts a state id, a state name, or a state type
  (`unstarted`/`started`/`done`).
- **`--json`** (anywhere in the command) prints structured JSON.
- **Errors** print to stderr and exit non-zero: `error: story 999 not found`.
- Mutating commands print the resulting entity (text by default; JSON with
  `--json`).

## Data model (core)

Stories are the central unit of work. A story belongs to a workflow *state*
(typed unstarted/started/done), and optionally to a project, epic, iteration,
and group; it has owners (members), labels, tasks, comments, and links to other
stories. Epics group stories and may belong to a project and a milestone;
iterations are time-boxes; milestones group epics. Moving a story/epic/milestone
to a `done` state stamps `completed_at` (and clears it when moved back out).

Entities built now: **Stories, Epics, Iterations, Milestones, Projects,
Workflows (+States), Labels, Members, Groups, Comments, Tasks, Story Links,
Search**. Others from the Shortcut model (Documents, Objectives, Custom Fields,
History, etc.) are deferred — see [CONTEXT.md §4](CONTEXT.md).

## Architecture

```
backend/   function-call API over SQLite (each REST-shaped op = one Python fn)
cli/       argparse subparsers → calls backend, prints text/JSON
tui/       full-screen UI (pending) → same backend
main.py    dispatches: no args → TUI, args → CLI
```

- Every backend function takes the `sqlite3.Connection` as its first arg; the
  CLI/TUI are thin layers over them.
- Writers serialize via `PRAGMA busy_timeout = 5000` + `BEGIN IMMEDIATE`: a
  second writer blocks (up to 5s) until the first commits, rather than erroring.
  No WAL (YAGNI). See [CONTEXT.md §6](CONTEXT.md).
- Plain dataclasses, raw SQL, no ORM. Simple versioned migrations.

Full design, schema, and build status live in [CONTEXT.md](CONTEXT.md).