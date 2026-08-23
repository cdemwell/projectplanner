import json
from pathlib import Path

from backend.config import Config, load_config


def test_load_config_defaults(tmp_path):
    # A non-existent file returns fully-defaulted Config.
    non_existent = tmp_path / "missing.json"
    cfg = load_config(non_existent)
    assert isinstance(cfg, Config)
    assert cfg.default_project == "backend"
    assert cfg.default_owner == "cdemwell"
    assert cfg.default_story_type == "feature"
    assert cfg.default_state == ""
    assert cfg.default_iteration == ""
    assert cfg.default_group == ""
    assert cfg.default_epic == ""
    assert cfg.display_format == "text"
    assert cfg.auto_refresh_seconds == 5
    assert cfg.rotate_backup == 3
    assert cfg.keep == 7
    assert cfg.db_path is None


def test_load_config_from_file(tmp_path):
    # All sections load correctly from a valid JSON file.
    cfg_file = tmp_path / "planner.json"
    data = {
        "defaults": {
            "default_project": "frontend",
            "default_owner": "alice",
            "default_story_type": "bug",
            "default_state": "in-progress",
            "default_iteration": "sprint-42",
            "default_group": "eng",
            "default_epic": "payments",
        },
        "display": {
            "format": "json",
            "auto_refresh_seconds": 10,
        },
        "backup": {
            "rotate_backup": 5,
            "keep": 14,
        },
        "database": {
            "path": "data/planner.db",
        },
    }
    cfg_file.write_text(json.dumps(data))

    cfg = load_config(cfg_file)
    assert cfg.default_project == "frontend"
    assert cfg.default_owner == "alice"
    assert cfg.default_story_type == "bug"
    assert cfg.default_state == "in-progress"
    assert cfg.default_iteration == "sprint-42"
    assert cfg.default_group == "eng"
    assert cfg.default_epic == "payments"
    assert cfg.display_format == "json"
    assert cfg.auto_refresh_seconds == 10
    assert cfg.rotate_backup == 5
    assert cfg.keep == 14
    assert cfg.db_path == str((tmp_path / "data" / "planner.db").resolve())


def test_load_config_absolute_db_path(tmp_path):
    # Absolute database paths are used unchanged.
    cfg_file = tmp_path / "planner.json"
    cfg_file.write_text(json.dumps({"database": {"path": "/tmp/other.db"}}))

    cfg = load_config(cfg_file)
    assert cfg.db_path == "/tmp/other.db"


def test_load_config_ignores_comments(tmp_path):
    # _comment keys are documentation and must not leak into the Config.
    cfg_file = tmp_path / "planner.json"
    cfg_file.write_text(json.dumps({
        "_comment": "top-level doc",
        "defaults": {"_comment": "doc", "default_project": "docs"},
        "display": {"_comment": "doc", "format": "csv"},
        "backup": {"_comment": "doc", "keep": 3},
        "database": {"_comment": "doc"},
    }))

    cfg = load_config(cfg_file)
    assert cfg.default_project == "docs"
    assert cfg.display_format == "csv"
    assert cfg.keep == 3
    assert cfg.db_path is None


def test_load_config_partial_file(tmp_path):
    # Missing sections/fields are filled by defaults.
    cfg_file = tmp_path / "partial.json"
    cfg_file.write_text(json.dumps({"defaults": {"default_project": "docs"}}))

    cfg = load_config(cfg_file)
    assert cfg.default_project == "docs"
    assert cfg.default_owner == "cdemwell"  # default
    assert cfg.display_format == "text"  # default
    assert cfg.keep == 7  # default


def test_load_config_invalid_json(tmp_path):
    # Invalid JSON returns defaults.
    cfg_file = tmp_path / "invalid.json"
    cfg_file.write_text("not json")

    cfg = load_config(cfg_file)
    assert cfg.default_project == "backend"
