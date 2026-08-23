import json
from pathlib import Path

import pytest

from backend.config import Config, load_config


def test_load_config_defaults(tmp_path):
    # Test that a non-existent file returns defaults
    non_existent = tmp_path / "missing.json"
    cfg = load_config(non_existent)
    assert isinstance(cfg, Config)
    assert cfg.default_project == "backend"
    assert cfg.default_owner == "cdemwell"

def test_load_config_from_file(tmp_path):
    # Test that a valid JSON file is loaded correctly
    cfg_file = tmp_path / "planner.json"
    data = {
        "default_project": "frontend",
        "default_owner": "alice",
        "auto_refresh_seconds": 10,
        "rotate_backup": 5,
        "default_story_type": "bug"
    }
    cfg_file.write_text(json.dumps(data))

    cfg = load_config(cfg_file)
    assert cfg.default_project == "frontend"
    assert cfg.default_owner == "alice"
    assert cfg.auto_refresh_seconds == 10
    assert cfg.rotate_backup == 5
    assert cfg.default_story_type == "bug"

def test_load_config_partial_file(tmp_path):
    # Test that missing fields are filled by defaults
    cfg_file = tmp_path / "partial.json"
    data = {"default_project": "docs"}
    cfg_file.write_text(json.dumps(data))

    cfg = load_config(cfg_file)
    assert cfg.default_project == "docs"
    assert cfg.default_owner == "cdemwell"  # default

def test_load_config_invalid_json(tmp_path):
    # Test that invalid JSON returns defaults
    cfg_file = tmp_path / "invalid.json"
    cfg_file.write_text("not json")

    cfg = load_config(cfg_file)
    assert cfg.default_project == "backend"
