"""Pure state transitions for lookup and notification delivery."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

from .ipcheck import LookupResult


def _stamp(now: datetime) -> str:
    return now.astimezone(UTC).isoformat()


def discard_pending_families(state: dict[str, Any], disabled_families: set[str]) -> dict[str, Any]:
    """Return state without pending notifications for disabled address families."""
    new = deepcopy(state)
    pending = new.get("pending") or {}
    changes = pending.get("changes", {})
    for family in disabled_families:
        changes.pop(family, None)
    if pending and not changes:
        new["pending"] = {}
    return new


def apply_lookup(  # noqa: PLR0912
    state: dict[str, Any], results: dict[str, LookupResult], now: datetime
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    new = deepcopy(state)
    actions: list[dict[str, Any]] = []
    observed = new.setdefault("last_observed", {})
    history = new.setdefault("history", [])
    prior_result = new.get("last_check", {}).get("result")
    candidates: dict[str, dict[str, Any]] = {}
    for family, result in results.items():
        if result.ok:
            observed[family] = {
                "value": result.value,
                "time": _stamp(now),
                "providers": result.providers,
            }
        if not result.ok:
            continue
        baseline = new.get("last_reported", {}).get(family, {}).get("value")
        if baseline == result.value:
            continue
        # lookup() marks an unconfirmed candidate as an error; never schedule delivery.
        candidates[family] = {"old": baseline, "new": result.value, "providers": result.providers}
    successes = [result for result in results.values() if result.ok]
    failures = [result for result in results.values() if not result.ok]
    if "ipv4" in results:
        streak = new.get("ipv4_failure_streak", 0)
        new["ipv4_failure_streak"] = streak + 1 if not results["ipv4"].ok else 0
    new["last_check"] = {
        "time": _stamp(now),
        "result": "failure" if failures and not successes else "partial" if failures else "success",
        "providers": {key: list(value.providers) for key, value in results.items()},
    }
    if failures:
        if history and history[-1].get("type") == "lookups_failing":
            history[-1]["attempts"] = history[-1].get("attempts", 1) + 1
            history[-1]["last_time"] = _stamp(now)
            history[-1]["error"] = "; ".join(result.error or "lookup failed" for result in failures)
        else:
            history.append(
                {
                    "type": "lookups_failing",
                    "time": _stamp(now),
                    "last_time": _stamp(now),
                    "attempts": 1,
                    "error": "; ".join(result.error or "lookup failed" for result in failures),
                }
            )
    elif prior_result in {"failure", "partial"}:
        history.append({"type": "lookups_recovered", "time": _stamp(now)})
    if candidates:
        history.append({"type": "change_detected", "time": _stamp(now), "changes": candidates})
        new["pending"] = {"changes": candidates, "detected_at": _stamp(now), "last_error": None}
        kind = (
            "send_started"
            if all(change["old"] is None for change in candidates.values())
            else "send_change"
        )
        actions.append({"type": kind, "changes": candidates})
    elif new.get("pending"):
        pending = new["pending"]
        for family, change in list(pending.get("changes", {}).items()):
            family_result = results.get(family)
            if family_result and family_result.ok and family_result.value == change.get("old"):
                del pending["changes"][family]
        if not pending.get("changes"):
            new["pending"] = {}
    if failures:
        actions.append({"type": "retry"})
    new["history"] = history[-50:]
    return new, actions


def apply_delivery(
    state: dict[str, Any],
    success: bool,
    now: datetime,
    error: str = "",
    sent_changes: dict[str, dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    new = deepcopy(state)
    pending = new.get("pending") or {}
    changes = sent_changes if sent_changes is not None else pending.get("changes", {})
    if not changes:
        return new, []
    if success:
        reported = new.setdefault("last_reported", {})
        for family, change in changes.items():
            reported[family] = {"value": change["new"], "time": _stamp(now)}
        current_changes = pending.get("changes", {})
        if current_changes == changes:
            new["pending"] = {}
        elif pending:
            for family, change in current_changes.items():
                if family in changes and change.get("old") == changes[family].get("new"):
                    change["old"] = changes[family]["new"]
        new.setdefault("history", []).append(
            {"type": "email_sent", "time": _stamp(now), "changes": changes}
        )
        actions = [{"type": "delivered"}]
    else:
        if pending.get("changes") == changes:
            pending["last_error"] = error
            pending["attempted_at"] = _stamp(now)
        new.setdefault("history", []).append(
            {"type": "email_failed", "time": _stamp(now), "error": error}
        )
        actions = [{"type": "delivery_failed", "error": error}]
    new["history"] = new.get("history", [])[-50:]
    return new, actions
