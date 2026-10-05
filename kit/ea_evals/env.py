"""Load keys from `.env` into the environment (never printed). Looks in the kit, then its parent folder."""

import os
from pathlib import Path

from ea_world import paths


def dotenv_paths() -> list[Path]:
    kit = paths.kit_root()
    explicit = os.environ.get("EA_DOTENV")
    return [Path(explicit)] if explicit else [kit / ".env", kit.parent / ".env"]


def load_dotenv(override: bool = False) -> Path | None:
    for p in dotenv_paths():
        if not p.is_file():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip().removeprefix("export ").strip()
            value = value.strip().strip('"').strip("'")
            if value and (override or not os.environ.get(key)):
                os.environ[key] = value
        return p
    return None
