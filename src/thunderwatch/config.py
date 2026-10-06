"""Validated, immutable Qt-free application configuration."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Protocol

EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


class SettingsStore(Protocol):
    def value(self, key: str, default: Any = None) -> Any: ...


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
            setup_complete=bool(store.value("setup/complete", False)),
            smtp_host=str(store.value("smtp/host", "")),
            smtp_port=int(store.value("smtp/port", 587)),
            smtp_security=str(store.value("smtp/security", "auto")),
            smtp_username=str(store.value("smtp/username", "")),
            smtp_from=str(store.value("smtp/from", "")),
            smtp_recipient=str(store.value("smtp/recipient", "")),
            location=str(store.value("monitor/location", "")),
            interval_minutes=int(store.value("monitor/interval_minutes", 10)),
            ipv6=bool(store.value("monitor/ipv6", True)),
            autostart=bool(store.value("startup/autostart", True)),
            automatic_updates=bool(store.value("updates/automatic", True)),
            include_beta=bool(store.value("updates/include_beta", False)),
        )
