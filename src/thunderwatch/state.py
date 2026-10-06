"""Versioned atomic application state persistence."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, cast

STATE_VERSION = 1
FAMILIES = {"ipv4", "ipv6"}


def empty_state() -> dict[str, Any]:
    return {
        "version": STATE_VERSION,
        "last_reported": {},
        "last_observed": {},
        "last_check": {},
        "pending": {},
        "history": [],
    }


def _string_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _change_map(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    for family, change in value.items():
        if family not in FAMILIES or not isinstance(change, dict):
            return False
        if not isinstance(change.get("new"), str):
            return False
        if change.get("old") is not None and not isinstance(change.get("old"), str):
            return False
        if "providers" in change and not _string_list(change["providers"]):
            return False
    return True


def _family_records(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    for family, record in value.items():
        if family not in FAMILIES or not isinstance(record, dict):
            return False
        if not isinstance(record.get("value"), str) or not isinstance(record.get("time"), str):
            return False
        if "providers" in record and not _string_list(record["providers"]):
            return False
    return True


def _valid_history(value: Any) -> bool:
    if not isinstance(value, list):
        return False
    for event in value:
        if not isinstance(event, dict) or not isinstance(event.get("type"), str):
            return False
        if not isinstance(event.get("time"), str):
            return False
        kind = event["type"]
        invalid_changes = kind in {"change_detected", "email_sent"} and not _change_map(
            event.get("changes")
        )
        invalid_failure = kind == "lookups_failing" and (
            not isinstance(event.get("last_time"), str)
            or not isinstance(event.get("attempts"), int)
            or not isinstance(event.get("error"), str)
        )
        invalid_error = kind in {"email_failed", "check_failed"} and not isinstance(
            event.get("error"), str
        )
        if invalid_changes or invalid_failure or invalid_error:
            return False
    return True


def _valid_last_check(last_check: Any) -> bool:
    if not isinstance(last_check, dict):
        return False
    invalid_check_details = (
        ("time" in last_check and not isinstance(last_check["time"], str))
        or (
            "result" in last_check and last_check["result"] not in {"success", "partial", "failure"}
        )
        or ("error" in last_check and not isinstance(last_check["error"], str))
    )
    invalid_families = "families" in last_check and (
        not isinstance(last_check["families"], dict)
        or any(
            family not in FAMILIES or result not in {"success", "failure"}
            for family, result in last_check["families"].items()
        )
    )
    invalid_providers = "providers" in last_check and (
        not isinstance(last_check["providers"], dict)
        or any(
            family not in FAMILIES or not _string_list(providers)
            for family, providers in last_check["providers"].items()
        )
    )
    return not (invalid_check_details or invalid_families or invalid_providers)


def _valid_pending(pending: Any) -> bool:
    if not isinstance(pending, dict):
        return False
    invalid_pending = (
        ("changes" in pending and not _change_map(pending["changes"]))
        or (
            "last_error" in pending
            and pending["last_error"] is not None
            and not isinstance(pending["last_error"], str)
        )
        or any(
            key in pending and not isinstance(pending[key], str)
            for key in ("detected_at", "attempted_at")
        )
    )
    return not invalid_pending


def _valid_state(data: Any) -> bool:
    if not isinstance(data, dict) or data.get("version") != STATE_VERSION:
        return False
    if any(
        key not in data or not _family_records(data[key])
        for key in ("last_reported", "last_observed")
    ):
        return False
    if not _valid_last_check(data.get("last_check")):
        return False
    if not _valid_pending(data.get("pending")):
        return False
    if "ipv4_failure_streak" in data and (
        not isinstance(data["ipv4_failure_streak"], int)
        or isinstance(data["ipv4_failure_streak"], bool)
        or data["ipv4_failure_streak"] < 0
    ):
        return False
    return _valid_history(data.get("history"))


def read_state(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not _valid_state(data):
            raise ValueError("invalid state schema")
        return cast(dict[str, Any], data)
    except OSError, ValueError, TypeError, json.JSONDecodeError:
        return empty_state()


def write_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    state["version"] = STATE_VERSION
    state["history"] = state.get("history", [])[-50:]
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(state, stream, indent=2, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)
