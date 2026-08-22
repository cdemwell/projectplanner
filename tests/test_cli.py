"""Tests for cli/commands.py: run(), --json output, name resolution, exit codes."""

from __future__ import annotations

import json
import sys

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
                           "--epic", "Auth", "--labels", "auth", "--owners", "cdemwell")
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
    assert json.loads(out)["completed_at"] is not None
    # by name
    rc, out, err = run_cli("--json", "story", "move", str(sid), "--state", "Unstarted")
    assert rc == 0
    assert json.loads(out)["completed_at"] is None


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
    assert json.loads(out) == {"deleted": "story", "id": 1}


def test_ambiguous_name_error(run_cli):
    run_cli("label", "create", "--name", "auth")
    run_cli("label", "create", "--name", "auth")  # duplicate name allowed for labels
    rc, out, err = run_cli("story", "create", "--name", "x", "--labels", "auth")
    assert rc == 1
    assert "ambiguous" in err


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


def test_cli_search_pagination(run_cli):
    for n in ["login a", "login b", "login c"]:
        run_cli("story", "create", "--name", n)
    rc, out, err = run_cli("--json", "search", "login", "--limit", "2")
    assert len(json.loads(out)) == 2
    rc, out, err = run_cli("--json", "search", "login", "--offset", "2")
    assert len(json.loads(out)) == 1
