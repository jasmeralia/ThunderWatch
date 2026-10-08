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


def _merge_pending_changes(
    state: dict[str, Any],
    results: dict[str, LookupResult],
    candidates: dict[str, dict[str, Any]],
    now: datetime,
) -> dict[str, Any]:
    pending = state.get("pending") or {}
    changes = deepcopy(pending.get("changes", {}))
    for family, result in results.items():
        if not result.ok:
            continue
        baseline = state.get("last_reported", {}).get(family, {}).get("value")
        if baseline == result.value:
            changes.pop(family, None)
        elif family in candidates:
            changes[family] = candidates[family]
    if not changes:
        return {}
    updated = deepcopy(pending)
    updated["changes"] = changes
    updated.setdefault("detected_at", _stamp(now))
    if candidates:
        updated["last_error"] = None
        updated.pop("attempted_at", None)
    return updated


def apply_lookup(
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
        "families": {
            family: "success" if result.ok else "failure" for family, result in results.items()
        },
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
        has_any_baseline = any(new.get("last_reported", {}).values())
        kind = "send_change" if has_any_baseline else "send_started"
        actions.append({"type": kind, "changes": candidates})
    new["pending"] = _merge_pending_changes(new, results, candidates, now)
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
    changes = deepcopy(sent_changes if sent_changes is not None else pending.get("changes", {}))
    if not changes:
        return new, []
    if success:
        reported = new.setdefault("last_reported", {})
        for family, change in changes.items():
            reported[family] = {"value": change["new"], "time": _stamp(now)}
        current_changes = pending.get("changes", {})
        for family, delivered in changes.items():
            current = current_changes.get(family)
            if not current:
                continue
            if current == delivered:
                del current_changes[family]
            elif current.get("old") == delivered.get("old"):
                current["old"] = delivered["new"]
        if pending:
            if current_changes:
                pending["changes"] = current_changes
            else:
                new["pending"] = {}
        new.setdefault("history", []).append(
            {"type": "email_sent", "time": _stamp(now), "changes": changes}
        )
        actions = [{"type": "delivered"}]
    else:
        current_changes = pending.get("changes", {})
        if any(current_changes.get(family) == change for family, change in changes.items()):
            pending["last_error"] = error
            pending["attempted_at"] = _stamp(now)
        new.setdefault("history", []).append(
            {"type": "email_failed", "time": _stamp(now), "error": error}
        )
        actions = [{"type": "delivery_failed", "error": error}]
    new["history"] = new.get("history", [])[-50:]
    return new, actions
