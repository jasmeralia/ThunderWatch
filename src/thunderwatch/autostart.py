"""Per-user sign-in registration for native Linux and Windows installs."""

from __future__ import annotations

import contextlib
import logging
import os
import subprocess
import sys
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol

from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot

logger = logging.getLogger(__name__)


class BackgroundPortal(Protocol):
    def request_background(self, options: dict[str, Any]) -> None: ...


def background_portal_error(
    response: int, results: dict[str, Any], requested_autostart: bool
) -> str | None:
    if response != 0:
        return "The desktop portal did not approve ThunderWatch background access."
    if requested_autostart and results.get("autostart") is False:
        return "The desktop portal denied ThunderWatch automatic startup."
    if requested_autostart and results.get("background") is False:
        return "The desktop portal denied ThunderWatch background access."
    return None


_PORTAL_CLIENTS: list[QtBackgroundPortal] = []


class QtBackgroundPortal(QObject):
    """Small QtDBus client for the XDG Background portal."""

    request_failed = pyqtSignal(str)

    def __init__(self) -> None:
        super().__init__()
        self._connection: Any = None
        self._request_path = ""
        self._requested_autostart = False

    def request_background(self, options: dict[str, Any]) -> None:
        from PyQt6.QtDBus import (  # noqa: PLC0415
            QDBusConnection,
            QDBusInterface,
            QDBusMessage,
        )

        self._connection = QDBusConnection.sessionBus()
        self._requested_autostart = bool(options.get("autostart", False))
        token = uuid.uuid4().hex
        sender = self._connection.baseService().removeprefix(":").replace(".", "_")
        expected_path = f"/org/freedesktop/portal/desktop/request/{sender}/{token}"
        request_options = dict(options)
        request_options["handle_token"] = token
        service = "org.freedesktop.portal.Desktop"
        request_interface = "org.freedesktop.portal.Request"
        if not self._connection.connect(
            service,
            expected_path,
            request_interface,
            "Response",
            self,
            self._on_response,
        ):
            raise RuntimeError("Could not subscribe to the XDG Background portal response")
        interface = QDBusInterface(
            service,
            "/org/freedesktop/portal/desktop",
            "org.freedesktop.portal.Background",
            self._connection,
        )
        if not interface.isValid():
            self._disconnect_response(expected_path)
            raise RuntimeError("XDG Background portal is unavailable")
        reply = interface.call("RequestBackground", "", request_options)
        if reply.type() == QDBusMessage.MessageType.ErrorMessage:
            self._disconnect_response(expected_path)
            raise RuntimeError(reply.errorMessage())
        arguments = reply.arguments()
        if not arguments:
            self._disconnect_response(expected_path)
            raise RuntimeError("XDG Background portal returned no response handle")
        self._request_path = str(arguments[0])
        if self._request_path != expected_path:
            self._disconnect_response(expected_path)
            if not self._connection.connect(
                service,
                self._request_path,
                request_interface,
                "Response",
                self,
                self._on_response,
            ):
                raise RuntimeError("Could not subscribe to the XDG Background portal response")
        if self not in _PORTAL_CLIENTS:
            _PORTAL_CLIENTS.append(self)

    @pyqtSlot("uint", "QVariantMap")
    def _on_response(self, response: int, results: dict[str, Any]) -> None:
        error = background_portal_error(response, results, self._requested_autostart)
        if error:
            logger.warning("Background portal request failed: %s", error)
            self.request_failed.emit(error)
        self._disconnect_response(self._request_path)
        with contextlib.suppress(ValueError):
            _PORTAL_CLIENTS.remove(self)

    def _disconnect_response(self, path: str) -> None:
        if self._connection and path:
            self._connection.disconnect(
                "org.freedesktop.portal.Desktop",
                path,
                "org.freedesktop.portal.Request",
                "Response",
                self._on_response,
            )


def desktop_file(
    executable: str,
    appimage: str | None = None,
    arguments: Sequence[str] = ("--autostart",),
) -> str:
    target = appimage or executable
    args = ("--autostart",) if appimage else arguments
    command = " ".join(
        [_desktop_argument(target, force_quotes=True), *(_desktop_argument(arg) for arg in args)]
    )
    return (
        "[Desktop Entry]\nType=Application\nName=ThunderWatch\n"
        f"Exec={command}\nX-GNOME-Autostart-enabled=true\n"
    )


def _desktop_argument(value: str, force_quotes: bool = False) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    escaped = escaped.replace("$", "\\$").replace("`", "\\`").replace("%", "%%")
    if force_quotes or any(character.isspace() or character in '"\\$`' for character in value):
        return f'"{escaped}"'
    return escaped


def set_autostart(
    enabled: bool,
    executable: str | None = None,
    portal_client: BackgroundPortal | None = None,
    arguments: Sequence[str] | None = None,
) -> None:
    if executable is None:
        executable = sys.executable
        arguments = arguments or (
            ("--autostart",)
            if getattr(sys, "frozen", False)
            else ("-m", "thunderwatch", "--autostart")
        )
    else:
        arguments = arguments or ("--autostart",)
    if sys.platform == "win32":
        import winreg  # noqa: PLC0415

        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0,
            winreg.KEY_SET_VALUE,
        )
        try:
            if enabled:
                winreg.SetValueEx(
                    key,
                    "ThunderWatch",
                    0,
                    winreg.REG_SZ,
                    f'"{executable}" {subprocess.list2cmdline(list(arguments))}',
                )
            else:
                with contextlib.suppress(FileNotFoundError):
                    winreg.DeleteValue(key, "ThunderWatch")
        finally:
            winreg.CloseKey(key)
        return
    if os.environ.get("FLATPAK_ID"):
        portal = portal_client or QtBackgroundPortal()
        portal.request_background(
            {"autostart": enabled, "commandline": ["thunderwatch", "--autostart"]}
        )
        return
    if os.environ.get("SNAP"):
        base = Path(os.environ.get("SNAP_USER_DATA", str(Path.home()))) / ".config/autostart"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "autostart"
    target = base / "thunderwatch.desktop"
    if enabled:
        base.mkdir(parents=True, exist_ok=True)
        target.write_text(
            desktop_file(executable, os.environ.get("APPIMAGE"), arguments), encoding="utf-8"
        )
        target.chmod(0o644)
    else:
        target.unlink(missing_ok=True)
