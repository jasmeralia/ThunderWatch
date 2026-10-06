"""Network and SMTP work executed in a dedicated QThread."""

from __future__ import annotations

import logging
import socket
from datetime import datetime
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot

from .config import Config
from .ipcheck import lookup
from .monitor import apply_delivery, apply_lookup, discard_pending_families
from .notifier import compose_email, send_email
from .paths import password_path, state_path
from .secrets import read_password
from .state import empty_state, read_state, write_state
from .updater import installed_version

logger = logging.getLogger(__name__)


class CheckWorker(QObject):
    finished = pyqtSignal(dict, list)

    def __init__(self, config: Config) -> None:
        super().__init__()
        self.config = config

    @pyqtSlot()
    def check(self) -> None:
        state = empty_state()
        actions: list[dict[str, Any]] = [{"type": "retry"}]
        try:
            state = read_state(state_path())
            if not self.config.ipv6:
                state = discard_pending_families(state, {"ipv6"})
            results = {}
            for family in ("ipv4", "ipv6"):
                if family == "ipv6" and not self.config.ipv6:
                    continue
                baseline = state.get("last_reported", {}).get(family, {}).get("value")
                results[family] = lookup(family, baseline=baseline)
            state, actions = apply_lookup(state, results, datetime.now().astimezone())
            write_state(state_path(), state)
            action = next((item for item in actions if item["type"].startswith("send_")), None)
            if action:
                password = read_password(
                    self.config.smtp_username, self.config.smtp_host, password_path()
                )
                if not password:
                    ok, message = False, "Email password unavailable"
                else:
                    kind = "started" if action["type"] == "send_started" else "change"
                    current = {key: value.value for key, value in results.items() if value.value}
                    mail = compose_email(
                        kind,
                        self.config.location,
                        socket.gethostname(),
                        installed_version() or "0.0.0",
                        action.get("changes"),
                        current,
                    )
                    ok, message = send_email(
                        self.config.smtp_host,
                        self.config.smtp_port,
                        self.config.smtp_security,
                        self.config.smtp_username,
                        password,
                        self.config.smtp_from,
                        self.config.smtp_recipient,
                        mail,
                    )
                state, delivery_actions = apply_delivery(
                    state, ok, datetime.now().astimezone(), message, action.get("changes")
                )
                actions.extend(delivery_actions)
                write_state(state_path(), state)
        except Exception as exc:
            logger.exception("IP check worker failed")
            now = datetime.now().astimezone().isoformat()
            state["last_check"] = {
                "time": now,
                "result": "failure",
                "error": type(exc).__name__,
            }
            state.setdefault("history", []).append(
                {"type": "check_failed", "time": now, "error": type(exc).__name__}
            )
            actions = [{"type": "retry"}]
            try:
                write_state(state_path(), state)
            except Exception:
                logger.exception("Could not persist IP check failure")
        self.finished.emit(state, actions)


class TestEmailWorker(QObject):
    """Fetch current addresses and send a user-requested test off the GUI thread."""

    finished = pyqtSignal(bool, str)

    def __init__(
        self,
        config: Config,
        password_file: Path | None = None,
        password_override: str | None = None,
    ) -> None:
        super().__init__()
        self.config = config
        self.password_file = password_file or password_path()
        self.password_override = password_override
        self.current_addresses: dict[str, str] = {}

    @pyqtSlot()
    def run(self) -> None:
        password = self.password_override or read_password(
            self.config.smtp_username, self.config.smtp_host, self.password_file
        )
        if not password:
            self.finished.emit(False, "Email password unavailable")
            return
        addresses = {}
        for family in ("ipv4", "ipv6"):
            if family == "ipv6" and not self.config.ipv6:
                continue
            result = lookup(family)
            if result.ok and result.value:
                addresses[family] = result.value
        self.current_addresses = addresses
        message = compose_email(
            "test",
            self.config.location,
            socket.gethostname(),
            installed_version() or "0.0.0",
            current=addresses,
        )
        ok, detail = send_email(
            self.config.smtp_host,
            self.config.smtp_port,
            self.config.smtp_security,
            self.config.smtp_username,
            password,
            self.config.smtp_from,
            self.config.smtp_recipient,
            message,
        )
        self.finished.emit(ok, detail)
