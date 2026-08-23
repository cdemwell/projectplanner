"""Configuration loading for the planner.

The configuration file is a JSON document (see ``planner.example.json`` for a
fully-commented reference). It is organised into named sections so each area of
the CLI keeps its settings together:

* ``defaults``   — default values applied when creating new stories.
* ``display``    — how results are rendered and how the TUI auto-refreshes.
* ``backup``     — automatic backup rotation settings.
* ``database``   — path to the SQLite database.

Every section (and the document itself) may carry ``_comment`` keys that are
ignored on load, so the file doubles as self-documentation.
"""

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Config:
    # defaults section
    default_project: str = "backend"
    default_owner: str = "cdemwell"
    default_story_type: str = "feature"
    default_state: str = ""
    default_iteration: str = ""
    default_group: str = ""
    default_epic: str = ""

    # display section
    display_format: str = "text"  # text | json | csv | id-only
    auto_refresh_seconds: int = 5

    # backup section
    rotate_backup: int = 3
    keep: int = 7

    # database section
    db_path: str | None = None  # resolved relative to the config file


def _resolve_section(data: dict, name: str) -> dict:
    """Return a named section dict, tolerating a missing/None section."""
    section = data.get(name)
    return section if isinstance(section, dict) else {}


def _resolve_db_path(config_path: Path, raw: str | None) -> str | None:
    """Resolve ``raw`` against the config file's directory.

    Absolute paths are returned unchanged; relative paths are made absolute
    relative to the directory holding the config file. Empty values stay as-is
    so the caller's default can win.
    """
    if not raw:
        return None
    p = Path(raw)
    if p.is_absolute():
        return str(p)
    return str((config_path.parent / p).resolve())


def load_config(path: str | Path) -> Config:
    """Load configuration from ``path``.

    Returns a fully-defaulted ``Config`` when the file is missing or not valid
    JSON. Present keys override the dataclass defaults; unknown keys and
    ``_comment`` fields are ignored.
    """
    path = Path(path)
    if not path.exists():
        return Config()

    try:
        with path.open("r") as f:
            data = json.load(f)
    except (json.JSONDecodeError, TypeError):
        return Config()

    vals: dict[str, object] = {}

    defaults = _resolve_section(data, "defaults")
    for key in (
        "default_project",
        "default_owner",
        "default_story_type",
        "default_state",
        "default_iteration",
        "default_group",
        "default_epic",
    ):
        if key in defaults:
            vals[key] = defaults[key]

    display = _resolve_section(data, "display")
    if "format" in display:
        vals["display_format"] = display["format"]
    if "auto_refresh_seconds" in display:
        vals["auto_refresh_seconds"] = display["auto_refresh_seconds"]

    backup = _resolve_section(data, "backup")
    if "rotate_backup" in backup:
        vals["rotate_backup"] = backup["rotate_backup"]
    if "keep" in backup:
        vals["keep"] = backup["keep"]

    database = _resolve_section(data, "database")
    if "path" in database:
        vals["db_path"] = _resolve_db_path(path, database["path"])

    return Config(**{k: v for k, v in vals.items() if k in Config.__dataclass_fields__})
