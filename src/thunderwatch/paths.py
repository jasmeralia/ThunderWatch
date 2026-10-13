"""Qt standard application storage paths."""

import sys
from pathlib import Path

from PyQt6.QtCore import QStandardPaths


def app_data() -> Path:
    return Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation))


def app_config() -> Path:
    return Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppConfigLocation))


def state_path() -> Path:
    return app_data() / "state.json"


def password_path() -> Path:
    return app_config() / "smtp-password"


def resource_path(name: str) -> Path:
    """Resolve an application asset from source or a PyInstaller bundle."""
    bundle_root = Path(getattr(sys, "_MEIPASS", Path(__file__).parents[2]))
    return bundle_root / "resources" / name
