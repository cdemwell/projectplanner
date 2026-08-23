import json
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Config:
    default_project: str = "backend"
    default_owner: str = "cdemwell"
    auto_refresh_seconds: int = 5
    rotate_backup: int = 3
    default_story_type: str = "feature"

def load_config(path: str | Path) -> Config:
    path = Path(path)
    if not path.exists():
        return Config()

    try:
        with path.open("r") as f:
            data = json.load(f)
        return Config(**{k: v for k, v in data.items() if k in Config.__dataclass_fields__})
    except (json.JSONDecodeError, TypeError):
        return Config()
