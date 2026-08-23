"""Tests for the YAML config file: parsing, CLI-overrides, and config subcommands."""

from __future__ import annotations

import json

import pytest

from backend import config as config_mod
from backend.config import Config, load_config, save_config
from cli.commands import run


def _write(cfg_file, text: str) -> None:
    cfg_file.write_text(text)


def test_load_config_missing_file_returns_defaults(tmp_path):
    # A non-existent file returns a fully-defaulted Config.
    cfg = load_config(tmp_path / "missing.yaml")
    assert isinstance(cfg, Config)
    assert cfg.default_project == "backend"
    assert cfg.default_owner == "cdemwell"
    assert cfg.default_story_type == "feature"
    assert cfg.default_state == ""
    assert cfg.default_iteration == ""
    assert cfg.default_group == ""
    assert cfg.default_epic == ""
    assert cfg.default_label == ""
    assert cfg.format == "text"
    assert cfg.auto_refresh_seconds == 5
    assert cfg.rotate_backup == 0
    assert cfg.keep == 0
    assert cfg.db_path is None
    assert cfg.limit is None
    assert cfg.offset is None
    assert cfg.include_completed is True


def test_load_config_parses_all_fields(tmp_path):
    # A YAML file with every field set is parsed correctly.
    cfg_file = tmp_path / "planner.yaml"
    cfg_file.write_text(
        "default_project: frontend\n"
        "default_owner: alice\n"
        "default_story_type: bug\n"
        "default_state: in-progress\n"
        "default_iteration: sprint-42\n"
        "default_group: eng\n"
        "default_epic: payments\n"
        "default_label: auth\n"
        "format: json\n"
        "auto_refresh_seconds: 10\n"
        "rotate_backup: 5\n"
        "keep: 14\n"
        "db_path: data/planner.db\n"
        "limit: 25\n"
        "offset: 10\n"
        "include_completed: false\n"
    )
    cfg = load_config(cfg_file)
    assert cfg.default_project == "frontend"
    assert cfg.default_owner == "alice"
    assert cfg.default_story_type == "bug"
    assert cfg.default_state == "in-progress"
    assert cfg.default_iteration == "sprint-42"
    assert cfg.default_group == "eng"
    assert cfg.default_epic == "payments"
    assert cfg.default_label == "auth"
    assert cfg.format == "json"
    assert cfg.auto_refresh_seconds == 10
    assert cfg.rotate_backup == 5
    assert cfg.keep == 14
    assert cfg.db_path == "data/planner.db"
    assert cfg.limit == 25
    assert cfg.offset == 10
    assert cfg.include_completed is False


def test_load_config_partial_file_keeps_defaults(tmp_path):
    # Omitted fields fall back to defaults.
    cfg_file = tmp_path / "partial.yaml"
    cfg_file.write_text("default_project: docs\nformat: csv\n")
    cfg = load_config(cfg_file)
    assert cfg.default_project == "docs"
    assert cfg.format == "csv"
    assert cfg.default_owner == "cdemwell"  # default
    assert cfg.include_completed is True  # default


def test_load_config_invalid_yaml_returns_defaults(tmp_path):
    # A malformed file returns defaults rather than raising.
    cfg_file = tmp_path / "bad.yaml"
    cfg_file.write_text("default_project: [unclosed\n")
    cfg = load_config(cfg_file)
    assert cfg.default_project == "backend"


def test_save_config_roundtrip(tmp_path):
    # save_config writes a file that load_config reads back identically.
    path = tmp_path / "nested" / "planner.yaml"
    cfg = Config(default_project="web", format="json", limit=50, keep=3)
    save_config(cfg, path)
    loaded = load_config(path)
    assert loaded.default_project == "web"
    assert loaded.format == "json"
    assert loaded.limit == 50
    assert loaded.keep == 3


@pytest.fixture
def run_cli(db_path, capsys):
    """Invoke the CLI against a fresh temp db."""
    def _invoke(*args):
        rc = run(["--db", db_path, *args])
        out, err = capsys.readouterr()
        return rc, out, err
    return _invoke


def test_cli_flags_override_config_values(tmp_path, run_cli):
    # A config sets format=csv; an explicit --format json on the CLI wins.
    cfg_file = tmp_path / "cfg.yaml"
    cfg_file.write_text("default_project: backend\nformat: csv\n")

    run_cli("project", "create", "--name", "backend")
    run_cli("story", "create", "--name", "a", "--project", "backend")

    rc, out, err = run_cli("--config", str(cfg_file), "story", "list",
                           "--project", "backend", "--format", "json")
    assert rc == 0, err
    # JSON output (not CSV) proves the CLI flag overrode the config value.
    data = json.loads(out)
    assert [s["name"] for s in data] == ["a"]


def test_cli_config_applies_default_format(tmp_path, run_cli):
    # With no explicit --format, the config value is used as the default.
    cfg_file = tmp_path / "cfg.yaml"
    cfg_file.write_text("format: json\n")

    run_cli("project", "create", "--name", "backend")
    run_cli("story", "create", "--name", "a", "--project", "backend")

    rc, out, err = run_cli("--config", str(cfg_file), "story", "list", "--project", "backend")
    assert rc == 0, err
    json.loads(out)  # JSON by default (from config), not text


def test_config_init_creates_file(tmp_path, run_cli):
    # `config init` writes a default YAML config file.
    cfg_file = tmp_path / "planner.yaml"
    rc, out, err = run_cli("config", "init", "--file", str(cfg_file))
    assert rc == 0, err
    assert cfg_file.exists()
    loaded = load_config(cfg_file)
    assert isinstance(loaded, Config)
    assert loaded.default_project == "backend"
    assert loaded.format == "text"
    assert loaded.auto_refresh_seconds == 5


def test_config_show_prints_config(tmp_path, run_cli):
    # `config show` prints the current config (from a file, when given).
    cfg_file = tmp_path / "planner.yaml"
    cfg_file.write_text("default_project: frontend\nformat: json\n")
    rc, out, err = run_cli("--config", str(cfg_file), "config", "show")
    assert rc == 0, err
    # The config file sets format=json, so show renders as JSON.
    cfg = json.loads(out)
    assert cfg["default_project"] == "frontend"
    assert cfg["format"] == "json"


def test_config_show_defaults_when_no_file(tmp_path, run_cli):
    # Without a config file, `config show` prints built-in defaults.
    rc, out, err = run_cli("config", "show")
    assert rc == 0, err
    assert "default_project" in out
    assert "backend" in out
    assert "text" in out
