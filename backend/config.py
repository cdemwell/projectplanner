"""Configuration loading for the planner.

The configuration file is a flat YAML document (see ``planner.example.yaml`` for
a fully-commented reference). Every setting the CLI exposes as a flag has a
matching field here, so the file doubles as documentation. Fields that are
omitted from the file fall back to built-in defaults; command-line flags always
override config values (CLI > config > built-in defaults).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

import yaml


@dataclass
class Config:
    """Default values for every configurable CLI/TUI option.

    Field names match the CLI ``--flag`` destinations and the keys written to
    the YAML config file. ``db_path``, ``limit``, and ``offset`` are optional
    (``None`` means "use the default"), so a config file can leave them unset.
    """

    default_project: str = "backend"
    default_owner: str = "cdemwell"
    default_story_type: str = "feature"
    default_state: str = ""
    default_iteration: str = ""
    default_group: str = ""
    default_epic: str = ""
    default_label: str = ""
    format: str = "text"  # text | json | csv | id-only
    auto_refresh_seconds: int = 5
    rotate_backup: int = 0
    keep: int = 0
    db_path: str | None = None
    limit: int | None = None
    offset: int | None = None
    include_completed: bool = True


def load_config(path: str | Path) -> Config:
    """Load configuration from a YAML ``path``.

    Returns a fully-defaulted :class:`Config` when the file is missing, is not a
    mapping, or fails to parse. Present keys override the dataclass defaults;
    unknown keys are ignored.
    """
    path = Path(path)
    if not path.exists():
        return Config()

    try:
        with path.open("r") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError:
        return Config()

    if not isinstance(data, dict):
        return Config()

    fields = Config.__dataclass_fields__
    vals = {key: data[key] for key in fields if key in data}
    return Config(**vals)


def save_config(config: Config, path: str | Path) -> None:
    """Write ``config`` to a YAML file at ``path`` (creating parent dirs)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        yaml.safe_dump(asdict(config), f, sort_keys=False)
