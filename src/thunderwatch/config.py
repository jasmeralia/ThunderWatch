"""Validated, immutable Qt-free application configuration."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Protocol

EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


class SettingsStore(Protocol):
    def value(self, key: str, default: Any = None) -> Any: ...


def _setting_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off"}:
            return False
    return default


def _setting_int(value: Any) -> int:
    try:
        return int(value)
    except OverflowError:
        return -1
    except TypeError:
        return -1
    except ValueError:
        return -1


@dataclass(frozen=True)
class Config:
    setup_complete: bool = False
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_security: str = "auto"
    smtp_username: str = ""
    smtp_from: str = ""
    smtp_recipient: str = ""
    location: str = ""
    interval_minutes: int = 10
    ipv6: bool = True
    autostart: bool = True
    automatic_updates: bool = True
    include_beta: bool = False

    @property
    def complete(self) -> bool:
        return (
            self.setup_complete
            and bool(self.smtp_host.strip() and self.smtp_username.strip())
            and 1 <= self.smtp_port <= 65535
            and self.smtp_security in {"auto", "ssl", "starttls"}
            and bool(EMAIL_RE.fullmatch(self.smtp_from.strip()))
            and bool(EMAIL_RE.fullmatch(self.smtp_recipient.strip()))
            and 5 <= self.interval_minutes <= 720
        )

    @classmethod
    def from_store(cls, store: SettingsStore) -> Config:
        return cls(
            setup_complete=_setting_bool(store.value("setup/complete", False), False),
            smtp_host=str(store.value("smtp/host", "")),
            smtp_port=_setting_int(store.value("smtp/port", 587)),
            smtp_security=str(store.value("smtp/security", "auto")),
            smtp_username=str(store.value("smtp/username", "")),
            smtp_from=str(store.value("smtp/from", "")),
            smtp_recipient=str(store.value("smtp/recipient", "")),
            location=str(store.value("monitor/location", "")),
            interval_minutes=_setting_int(store.value("monitor/interval_minutes", 10)),
            ipv6=_setting_bool(store.value("monitor/ipv6", True), True),
            autostart=_setting_bool(store.value("startup/autostart", True), True),
            automatic_updates=_setting_bool(store.value("updates/automatic", True), True),
            include_beta=_setting_bool(store.value("updates/include_beta", False), False),
        )


def smtp_identity_changed(previous: Config, updated: Config) -> bool:
    """Whether a saved SMTP password would be looked up under a new key."""
    return (
        previous.smtp_username.strip(),
        previous.smtp_host.strip(),
    ) != (updated.smtp_username.strip(), updated.smtp_host.strip())
