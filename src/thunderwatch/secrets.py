"""SMTP password storage using the OS keyring or an explicitly enabled file."""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path
from typing import Any, cast

SERVICE = "ThunderWatch"


def _keyring() -> Any:
    return importlib.import_module("keyring")


def keyring_available() -> bool:
    try:
        fail_module = importlib.import_module("keyring.backends.fail")
        fail_backend = cast(Any, fail_module).Keyring
        return not isinstance(_keyring().get_keyring(), fail_backend)
    except Exception:
        return False


def store_password(
    username: str,
    host: str,
    password: str,
    fallback: Path | None = None,
    allow_file: bool = False,
) -> None:
    account = f"{username}@{host}"
    try:
        keyring = _keyring()
        if keyring_available():
            keyring.set_password(SERVICE, account, password)
            return
    except Exception:
        pass
    if not allow_file or fallback is None:
        raise RuntimeError("No usable keyring; opt in to the private password file")
    fallback.parent.mkdir(parents=True, exist_ok=True)
    fallback.write_text(password, encoding="utf-8")
    if sys.platform != "win32":
        os.chmod(fallback, 0o600)


def read_password(username: str, host: str, fallback: Path | None = None) -> str | None:
    try:
        keyring = _keyring()
    except Exception:
        keyring = None
    if keyring is not None and keyring_available():
        try:
            result = keyring.get_password(SERVICE, f"{username}@{host}")
            if result:
                return cast(str, result)
        except Exception:
            return None
    try:
        return fallback.read_text(encoding="utf-8") if fallback and fallback.exists() else None
    except OSError:
        return None
