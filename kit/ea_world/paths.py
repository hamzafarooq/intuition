"""Where the kit, the world data and the run directories live."""

import os
from pathlib import Path


def kit_root() -> Path:
    """The kit folder: `EA_KIT_ROOT`, or the parent of this package."""
    env = os.environ.get("EA_KIT_ROOT")
    if env:
        return Path(env).resolve()
    return Path(__file__).resolve().parent.parent


def world_dir() -> Path:
    return Path(os.environ.get("EA_WORLD_DIR") or kit_root() / "world")


def runs_dir() -> Path:
    return kit_root() / "runs"


def current_file() -> Path:
    """`runs/CURRENT` holds the absolute path of the most recent run directory."""
    return runs_dir() / "CURRENT"


def gold_dir() -> Path:
    return world_dir() / "gold"
