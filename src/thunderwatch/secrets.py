"""SMTP password storage using the OS keyring or an explicitly enabled file."""

from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path
from typing import Any, cast

SERVICE = "ThunderWatch"


def _account_metadata_path(fallback: Path) -> Path:
    return fallback.with_name(f"{fallback.name}.account")


def _write_fallback_account(fallback: Path, username: str, host: str) -> None:
    metadata = _account_metadata_path(fallback)
    fd = os.open(metadata, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", closefd=False) as account_file:
            json.dump({"username": username, "host": host}, account_file)
    finally:
        os.close(fd)


def _fallback_matches_account(fallback: Path, username: str, host: str) -> bool:
    try:
        metadata = json.loads(_account_metadata_path(fallback).read_text(encoding="utf-8"))
    except OSError, ValueError, TypeError:
        return False
    if not isinstance(metadata, dict):
        return False
    return bool(metadata.get("username") == username and metadata.get("host") == host)


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
            if fallback is not None:
                _account_metadata_path(fallback).unlink(missing_ok=True)
                fallback.unlink(missing_ok=True)
            return
    except Exception:
        pass
    if not allow_file or fallback is None:
        raise RuntimeError("No usable keyring; opt in to the private password file")
    fallback.parent.mkdir(parents=True, exist_ok=True)
    _account_metadata_path(fallback).unlink(missing_ok=True)
    if sys.platform == "win32":
        fallback.write_text(password, encoding="utf-8")
        _write_fallback_account(fallback, username, host)
        return
    fd = os.open(fallback, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", closefd=False) as password_file:
            password_file.write(password)
    finally:
        os.close(fd)
    _write_fallback_account(fallback, username, host)


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
            pass
    try:
        if fallback and fallback.exists() and _fallback_matches_account(fallback, username, host):
            return fallback.read_text(encoding="utf-8")
        return None
    except OSError:
        return None
