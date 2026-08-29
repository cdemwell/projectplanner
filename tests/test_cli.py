"""Tests for cli/commands.py: run(), --json output, name resolution, exit codes."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta

import pytest

from cli.commands import run


@pytest.fixture
def run_cli(db_path, capsys):
    """Return a callable that invokes the CLI against a fresh temp db."""
    def _invoke(*args):
        rc = run(["--db", db_path, *args])
        out, err = capsys.readouterr()
        return rc, out, err
    return _invoke


@pytest.fixture
def fake_editor(monkeypatch, tmp_path):
    """Provide a fake $EDITOR that writes $EDITOR_CONTENT to the temp file.

    Returns a callable ``set_content(text)``. ``VISUAL`` is cleared so ``EDITOR``
    is used.
    """
    monkeypatch.delenv("VISUAL", raising=False)
    script = tmp_path / "fake_editor.py"
    script.write_text(
        'import os, sys\nopen(sys.argv[1], "w").write(os.environ.get("EDITOR_CONTENT", ""))\n')
    monkeypatch.setenv("EDITOR", f"{sys.executable} {script}")

    def set_content(content: str) -> None:
        monkeypatch.setenv("EDITOR_CONTENT", content)
    return set_content


def test_text_and_json_create(run_cli):
    rc, out, err = run_cli("project", "create", "--name", "backend")
    assert rc == 0
    assert "backend" in out  # text output echoes the entity
    rc, out, err = run_cli("--json", "project", "create", "--name", "docs")
    assert rc == 0
    obj = json.loads(out)
    assert obj["name"] == "docs"
    assert isinstance(obj["id"], int)


def test_name_resolution_in_create(run_cli):
    run_cli("project", "create", "--name", "backend")
    run_cli("label", "create", "--name", "auth")
    run_cli("epic", "create", "--name", "Auth", "--project", "backend")
    rc, out, err = run_cli("--json", "story", "create", "--name", "Fix login",
                           "--project", "backend", "--type", "bug",
                           "--epic", "Auth", "--labels", "auth",
                           "--owners", os.environ.get("USER", "me"))
    assert rc == 0, err
    s = json.loads(out)
    assert s["name"] == "Fix login"
    assert s["story_type"] == "bug"
    assert s["epic_id"] is not None


def test_json_list_shape(run_cli):
    run_cli("project", "create", "--name", "backend")
    run_cli("story", "create", "--name", "a", "--project", "backend")
    run_cli("story", "create", "--name", "b", "--project", "backend")
    rc, out, err = run_cli("--json", "story", "list", "--project", "backend")
    assert rc == 0
    arr = json.loads(out)
    assert isinstance(arr, list)
    assert {x["name"] for x in arr} == {"a", "b"}


def test_story_detail_json_shape(run_cli):
    run_cli("story", "create", "--name", "x")
    rc, out, err = run_cli("--json", "story", "detail", "1")
    assert rc == 0
    d = json.loads(out)
    assert set(d.keys()) == {"story", "owners", "labels", "tasks", "workflow_state"}
    assert d["story"]["id"] == 1


def test_search_json(run_cli):
    run_cli("story", "create", "--name", "login bug")
    rc, out, err = run_cli("--json", "search", "login")
    assert rc == 0
    arr = json.loads(out)
    assert any(r["entity"] == "story" and r["name"] == "login bug" for r in arr)


def test_state_resolution_by_name_and_type(run_cli):
    rc, out, err = run_cli("--json", "story", "create", "--name", "x")
    sid = json.loads(out)["id"]
    # by type
    rc, out, err = run_cli("--json", "story", "move", str(sid), "--state", "done")
    assert rc == 0, err
    assert json.loads(out)[0]["completed_at"] is not None
    # by name
    rc, out, err = run_cli("--json", "story", "move", str(sid), "--state", "Unstarted")
    assert rc == 0
    assert json.loads(out)[0]["completed_at"] is None


def test_error_exit_code_one(run_cli):
    rc, out, err = run_cli("story", "get", "999")
    assert rc == 1
    assert "not found" in err
    assert out == ""


def test_conflict_exit_code_one(run_cli):
    run_cli("story", "create", "--name", "a")
    run_cli("story", "create", "--name", "b")
    rc, out, err = run_cli("link", "add", "--subject", "1", "--verb", "blocks", "--object", "2")
    assert rc == 0
    rc, out, err = run_cli("link", "add", "--subject", "1", "--verb", "blocks", "--object", "2")
    assert rc == 1
    assert "already exists" in err


def test_argparse_error_exits_two(run_cli):
    # argparse rejects an invalid --type choice by raising SystemExit(2).
    with pytest.raises(SystemExit) as exc:
        run_cli("story", "create", "--name", "x", "--type", "bogus")
    assert exc.value.code == 2


def test_delete_status_json(run_cli):
    run_cli("story", "create", "--name", "x")
    rc, out, err = run_cli("--json", "story", "delete", "1")
    assert rc == 0
    assert json.loads(out) == [{"deleted": "story", "id": 1}]


def test_duplicate_label_name_conflicts(run_cli):
    # Migration v5 enforces case-insensitive unique label names at the DB, so a
    # duplicate is rejected at creation (exit 1, Conflict) instead of leaving a
    # name the case-insensitive resolver could never disambiguate.
    run_cli("label", "create", "--name", "auth")
    rc, out, err = run_cli("label", "create", "--name", "auth")
    assert rc == 1
    assert "conflict" in err.lower() or "unique" in err.lower()
    # A case-variant is also rejected.
    rc, out, err = run_cli("label", "create", "--name", "AUTH")
    assert rc == 1


# --- $EDITOR flow --------------------------------------------------------- #
def test_comment_add_via_editor(run_cli, fake_editor):
    run_cli("story", "create", "--name", "x")
    fake_editor("editor body")
    rc, out, err = run_cli("--json", "comment", "add", "--story", "1")
    assert rc == 0, err
    rc, out, err = run_cli("--json", "comment", "list", "--story", "1")
    arr = json.loads(out)
    assert arr[0]["text"] == "editor body"


def test_task_add_via_editor(run_cli, fake_editor):
    run_cli("story", "create", "--name", "x")
    fake_editor("do the thing")
    rc, out, err = run_cli("--json", "task", "add", "--story", "1")
    assert rc == 0, err
    rc, out, err = run_cli("--json", "task", "list", "--story", "1")
    arr = json.loads(out)
    assert arr[0]["description"] == "do the thing"


def test_story_edit_via_editor(run_cli, fake_editor):
    run_cli("story", "create", "--name", "old", "--desc", "olddesc")
    fake_editor("new name\n\nnew desc")
    rc, out, err = run_cli("--json", "story", "edit", "1")
    assert rc == 0, err
    s = json.loads(out)
    assert s["name"] == "new name"
    assert s["description"] == "new desc"


def test_story_edit_abort(run_cli, monkeypatch):
    """A non-zero editor exit aborts the edit (no change), exit 0."""
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.setenv("EDITOR", f'{sys.executable} -c "import sys; sys.exit(1)"')
    run_cli("story", "create", "--name", "x")
    rc, out, err = run_cli("--json", "story", "edit", "1")
    assert rc == 0
    assert json.loads(out) == {"aborted": "story edit", "id": 1}


def test_cli_pagination(run_cli):
    for n in ["a", "b", "c", "d"]:
        run_cli("story", "create", "--name", n)
    rc, out, err = run_cli("--json", "story", "list", "--limit", "2")
    assert [s["name"] for s in json.loads(out)] == ["a", "b"]
    rc, out, err = run_cli("--json", "story", "list", "--offset", "2")
    assert [s["name"] for s in json.loads(out)] == ["c", "d"]
    rc, out, err = run_cli("--json", "story", "list", "--limit", "2", "--offset", "1")
    assert [s["name"] for s in json.loads(out)] == ["b", "c"]
    # default unchanged
    rc, out, err = run_cli("--json", "story", "list")
    assert len(json.loads(out)) == 4


def test_story_list_mine(run_cli):
    # The DB is seeded with one member automatically.
    # Create another member for comparison.
    run_cli("member", "create", "--name", "Bob")

    # Identify the first member (the seeded one).
    rc, out, err = run_cli("--json", "member", "list")
    members = json.loads(out)
    me = members[0]  # The seeded member is always first (id=1)
    bob = members[1]

    # Create stories owned by 'me' and 'Bob'.
    run_cli("story", "create", "--name", "My Story 1", "--owners", me["name"])
    run_cli("story", "create", "--name", "My Story 2", "--owners", me["name"])
    run_cli("story", "create", "--name", "Bob Story 1", "--owners", bob["name"])

    # Test --mine (should only show 'me's stories)
    rc, out, err = run_cli("--json", "story", "list", "--mine")
    assert rc == 0
    stories = json.loads(out)
    assert len(stories) == 2
    assert {s["name"] for s in stories} == {"My Story 1", "My Story 2"}

    # Test --owner Bob (should only show Bob's story)
    rc, out, err = run_cli("--json", "story", "list", "--owner", bob["name"])
    assert rc == 0
    stories = json.loads(out)
    assert len(stories) == 1
    assert stories[0]["name"] == "Bob Story 1"


def test_cli_search_pagination(run_cli):
    for n in ["login a", "login b", "login c"]:
        run_cli("story", "create", "--name", n)
    rc, out, err = run_cli("--json", "search", "login", "--limit", "2")
    assert len(json.loads(out)) == 2
    rc, out, err = run_cli("--json", "search", "login", "--offset", "2")
    assert len(json.loads(out)) == 1


def test_dry_run(run_cli):
    """Verify --dry-run does not modify the DB but reports success."""
    # 1. Create a story with --dry-run
    rc, out, err = run_cli("--dry-run", "--json", "story", "create", "--name", "dry run story")
    assert rc == 0
    assert "[dry-run]" in out
    s = json.loads(out.replace("[dry-run]\n", "", 1))
    assert s["name"] == "dry run story"

    # 2. Verify it does NOT exist in the real DB
    rc, out, err = run_cli("--json", "story", "list")
    stories = json.loads(out)
    assert not any(st["name"] == "dry run story" for st in stories)


def test_backup_rotation(run_cli, db_path):
    """Verify --rotate-backup N creates backups and prunes old ones."""
    import pathlib
    import time
    db_p = pathlib.Path(db_path)

    # 1. Run commands with --rotate-backup 3
    # Note: run_cli already passes --db db_path
    for i in range(5):
        rc, out, err = run_cli("--rotate-backup", "3", "story", "create", "--name", f"story {i}")
        assert rc == 0
        time.sleep(1.1)

    # 2. Verify backup files are created
    # Backups are named planner.db.<timestamp> but since we use --db db_path,
    # they will be <db_path>.<timestamp>
    backups = sorted(db_p.parent.glob(f"{db_p.name}.*"))
    assert len(backups) == 3
    # Verify they are indeed files
    for b in backups:
        assert b.is_file()


def test_story_deadlines(run_cli):
    from datetime import UTC
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    yesterday = (datetime.now(UTC) - timedelta(days=1)).strftime("%Y-%m-%d")
    tomorrow = (datetime.now(UTC) + timedelta(days=1)).strftime("%Y-%m-%d")

    run_cli("story", "create", "--name", "Past Story", "--deadline", yesterday)
    run_cli("story", "create", "--name", "Future Story", "--deadline", tomorrow)
    run_cli("story", "create", "--name", "Today Story", "--deadline", today)
    run_cli("story", "create", "--name", "No Deadline Story")

    rc, out, err = run_cli("story", "deadlines")
    assert rc == 0

    lines = [line for line in out.splitlines() if line.strip() and not line.startswith("-")]
    # Header is lines[0], data are lines[1:]
    data = lines[1:]

    assert len(data) == 3
    # Should be sorted: Past, Today, Future
    assert "Past Story" in data[0]
    assert "OVERDUE" in data[0]
    assert "Today Story" in data[1]
    assert "DUE" in data[1]
    assert "Future Story" in data[2]
    # status column is last, should be empty for future
    assert "OVERDUE" not in data[2]
    assert "DUE" not in data[2]


def test_story_update_clears_nullable_fks(run_cli, db_path):
    """Bug 103: --no-project/--no-epic/--no-iteration/--no-group clear the
    association (the CLI counterpart of the TUI's '(no …)' option)."""
    from backend import db, epics, groups, iterations, projects, stories
    c = db.connect(db_path)
    p = projects.create_project(c, "P")
    e = epics.create_epic(c, "E")
    it = iterations.create_iteration(c, "I")
    g = groups.create_group(c, "G")
    out = run_cli("--json", "story", "create", "--name", "x",
                  "--project", str(p.id), "--epic", str(e.id),
                  "--iteration", str(it.id), "--group", str(g.id))[1]
    sid = json.loads(out)["id"]
    c.close()

    rc, out, err = run_cli("--json", "story", "update", str(sid),
                           "--no-project", "--no-epic", "--no-iteration", "--no-group")
    assert rc == 0, err
    c = db.connect(db_path)
    s = stories.get_story(c, sid)
    assert (s.project_id, s.epic_id, s.iteration_id, s.group_id) == (None, None, None, None)
    c.close()


def test_epic_update_clears_project_and_milestone(run_cli, db_path):
    from backend import db, epics, milestones, projects
    c = db.connect(db_path)
    p = projects.create_project(c, "P")
    m = milestones.create_milestone(c, "M")
    out = run_cli("--json", "epic", "create", "--name", "E",
                  "--project", str(p.id), "--milestone", str(m.id))[1]
    eid = json.loads(out)["id"]
    c.close()
    rc, out, err = run_cli("--json", "epic", "update", str(eid),
                           "--no-project", "--no-milestone")
    assert rc == 0, err
    c = db.connect(db_path)
    e = epics.get_epic(c, eid)
    assert (e.project_id, e.milestone_id) == (None, None)
    c.close()
