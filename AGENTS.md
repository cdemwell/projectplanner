# AGENTS.md — Operating the `projectplanner` CLI as an AI agent

This file is the operational guide for an AI coding agent that uses
`projectplanner` to track the software work it is doing. It is a focused
distillation of the agent-relevant parts of [README.md](README.md). For the
full reference (every flag, the data model, architecture, schema), read the
README and [CONTEXT.md](CONTEXT.md) — this file links to them for depth.

---

## When to use this tool

Use `projectplanner` when you are doing a unit of work on a software project and
want a durable, queryable record of it — what's in flight, what's done, what
blocked what. It writes to a local SQLite file (`planner.db`); no server, no
network, no auth. There is one seeded local user (id 1).

**Do NOT use it for:**

- General note-taking or scratch files — use your editor.
- Issues that belong in a hosted tracker (Shortcut, GitHub Issues, Jira) — this
  is a local-only personal tool, not a sync client or mock.
- Anything requiring multi-user collaboration or remote access — there is no
  network and one local user.

---

## The command contract

The rules you can rely on when parsing output and chaining commands.

- **Invocation:** `python main.py <resource> <action> [flags]`.
- **Output format:** `--format` (`text`|`json`|`csv`|`id-only`, default `text`),
  placed anywhere in the command. `--json` still works as a deprecated alias for
  `--format json`. **Use `--format json` when you parse; `text` when you read.**
- **Name or id, your choice.** Anywhere a name is natural (`--project backend`,
  `--owner chris`, `--labels bug,auth`) it resolves to an id
  case-insensitively. Pass a bare number to use an id directly. Ambiguous names
  error with the matching ids. **Stories are referenced by id** (names are not
  unique).
- **`--state`** on `story move` accepts a state id, a state name (`"Done"`), or
  a state type (`unstarted`/`started`/`done`).
- **Mutating commands echo the resulting entity** — this is how you learn the
  id just assigned. Always read it back before referencing the new entity.
- **Errors** print `error: <message>` to stderr and exit non-zero
  (1 = backend error, recoverable; 2 = argparse/usage bug in *your* command).
- **Comma-separated lists** (`--owners`, `--labels`) accept mixed names and ids.
- **`$EDITOR` for long text.** `story edit <id>`, and `comment add` / `task add`
  with no `--text` / `--desc`, open the editor; a non-zero editor exit aborts
  cleanly. When scripting non-interactively, pass `--text` / `--desc` instead.

See [README §CLI Conventions](README.md#cli-conventions) and
[§CLI Reference](README.md#cli-reference) for the authoritative full contract
and every flag (or `python main.py <resource> <action> -h`).

---

## The operational loop

Treat the CLI as a small, reliable tool you call between editing steps.

1. **Create before you code.** When you pick up a unit of work, create a story
   first and capture the id. Cite that id in subsequent commands.
2. **Move it to reflect reality.** `started` when you begin, `done` when
   finished. State is the source of truth for "what's in flight".
3. **Record decisions and findings as comments.** Comments are the durable log;
   put reproduction steps, decisions, and blockers there.
4. **Break work into tasks.** `task add` for checklist items so progress is
   visible and resumable.
5. **Always read back the id** from a mutating command before reusing the
   entity.

### Start a session — see what's in flight

```bash
python main.py story list --state-type started --no-completed --format json
python main.py story detail 17        # relations, tasks, comments
```

### Pick up a new task and begin

```bash
SID=$(python main.py story create --name "Refactor auth middleware" \
      --project backend --type chore --owners "$(whoami)" --format json \
      | python -c "import sys,json; print(json.load(sys.stdin)['id'])")
python main.py story move "$SID" --state started
python main.py task add --story "$SID" --desc "Extract session check"
python main.py task add --story "$SID" --desc "Add tests for middleware"
```

### Log progress as you work

```bash
python main.py comment add --story 17 --text "Root cause: session cookie not set on redirect"
python main.py task complete 3
python main.py link add --subject 17 --verb blocks --object 22   # note a blocker
```

### Finish and verify

```bash
python main.py task complete 4
python main.py story move 17 --state done        # completed_at stamped automatically
python main.py story get 17 --format json
```

### Find related work before starting

```bash
python main.py search "auth login"               # FTS5 across all entities
python main.py search "auth" --entity story --format json
python main.py story list --project backend --iteration "Sprint 1" --format json
```

### Track a feature as an epic with stories

```bash
EID=$(python main.py epic create --name "Multi-factor auth" --project backend --format json \
      | python -c "import sys,json; print(json.load(sys.stdin)['id'])")
python main.py story create --name "TOTP enrollment"  --project backend --epic "$EID"
python main.py story create --name "TOTP verification" --project backend --epic "$EID"
python main.py epic stories "$EID"
```

---

## Anti-patterns to avoid

- **Don't set `completed_at` yourself** — it's automated by state moves.
- **Don't reference stories by name** in scripts (names aren't unique); capture
  the id from create/move output.
- **Don't poll in a tight loop** to wait for a writer — concurrent writers block
  up to 5s then proceed; just retry on a non-zero exit.
- **Don't parse plain-text tables programmatically** — use `--format json`.
- **Don't ignore exit codes** — exit 2 means *you* built the command wrong
  (bad flag/choice), not that the data is missing.

---

## Error recovery

| Exit | Meaning | What to do |
|------|---------|------------|
| `0`  | success | proceed |
| `1`  | backend error (`NotFound` / `ValidationError` / `Conflict`) | read `error:`, fix the input, retry |
| `2`  | argparse usage error | your flags/choices are wrong — re-read `-h`, rebuild the command |

---

## One-line mental model

A story is a unit of work; it lives in a workflow state, may belong to a
project/epic/iteration/group, and carries owners, labels, tasks, comments, and
links. You create stories, move them through states, and search them — all from
the command line, against a local file.

---

## Where to go deeper

- [README §CLI Reference](README.md#cli-reference) — every resource, action,
  and flag (or `python main.py <resource> <action> -h`).
- [README §Data Model](README.md#data-model) — entities and relationships.
- [CONTEXT.md](CONTEXT.md) — engineering source of truth: locked decisions,
  full schema DDL, concurrency model, deferred scope.