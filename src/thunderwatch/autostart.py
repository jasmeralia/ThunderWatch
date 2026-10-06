"""Per-user sign-in registration for native Linux and Windows installs."""

from __future__ import annotations

import contextlib
import os
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol


class BackgroundPortal(Protocol):
    def request_background(self, options: dict[str, Any]) -> None: ...


class QtBackgroundPortal:
    """Small QtDBus client for the XDG Background portal."""

    def request_background(self, options: dict[str, Any]) -> None:
        from PyQt6.QtDBus import QDBusConnection, QDBusInterface, QDBusMessage  # noqa: PLC0415

        interface = QDBusInterface(
            "org.freedesktop.portal.Desktop",
            "/org/freedesktop/portal/desktop",
            "org.freedesktop.portal.Background",
            QDBusConnection.sessionBus(),
        )
        if not interface.isValid():
            raise RuntimeError("XDG Background portal is unavailable")
        reply = interface.call("RequestBackground", "", options)
        if reply.type() == QDBusMessage.MessageType.ErrorMessage:
            raise RuntimeError(reply.errorMessage())


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
