"""Rotating application log setup."""

import logging
import os
import stat
import sys
from dataclasses import dataclass
from logging.handlers import RotatingFileHandler
from pathlib import Path
from tempfile import gettempdir

from .paths import app_data


@dataclass
class _LoggingState:
    active_directory: Path | None = None


_STATE = _LoggingState()


def active_log_directory() -> Path | None:
    """Return the directory holding the active file logs, if file logging is available."""
    return _STATE.active_directory


def _prepare_fallback_directory(path: Path) -> None:
    if not hasattr(os, "getuid"):
        path.mkdir(parents=True, exist_ok=True)
        return

    uid = os.getuid()
    root = path.parent
    for directory in (root, path):
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        details = directory.lstat()
        if stat.S_ISLNK(details.st_mode) or details.st_uid != uid:
            raise PermissionError(f"Unsafe fallback log directory: {directory}")
        directory.chmod(0o700)


def configure_logging() -> Path:
    primary = app_data() / "logs"
    fallback_name = f"ThunderWatch-{os.getuid()}" if hasattr(os, "getuid") else "ThunderWatch"
    fallback = Path(gettempdir()) / fallback_name / "logs"
    folder = primary
    handler: logging.Handler | None = None
    for candidate in (primary, fallback):
        try:
            if candidate == fallback:
                _prepare_fallback_directory(candidate)
            else:
                candidate.mkdir(parents=True, exist_ok=True)
            handler = RotatingFileHandler(
                candidate / "thunderwatch.log",
                maxBytes=1_000_000,
                backupCount=5,
                encoding="utf-8",
            )
            folder = candidate
            break
        except OSError:
            continue
    if handler is None:
        handler = logging.StreamHandler(sys.stderr)
    _STATE.active_directory = folder if isinstance(handler, RotatingFileHandler) else None
    logging.basicConfig(
        level=logging.INFO,
        handlers=[handler],
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,
    )
    return folder
