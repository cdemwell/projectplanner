"""Tests for cli/commands.py: run(), --json output, name resolution, exit codes."""

from __future__ import annotations

import json

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