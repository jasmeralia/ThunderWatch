"""Capture synthetic ThunderWatch UI screenshots without reading user settings."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def capture_readme(output_dir: str | Path) -> list[Path]:
    """Render the README screenshot set using isolated Qt paths and synthetic data."""
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    env_keys = ("HOME", "XDG_CONFIG_HOME", "APPDATA", "LOCALAPPDATA", "QT_QPA_PLATFORM")
    previous_env = {key: os.environ.get(key) for key in env_keys}
    with tempfile.TemporaryDirectory(prefix="thunderwatch-screenshots-") as scratch:
        root = Path(scratch)
        for key in ("HOME", "XDG_CONFIG_HOME", "APPDATA", "LOCALAPPDATA"):
            os.environ[key] = str(root / key.lower())
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PyQt6.QtCore import QSettings, Qt
        from PyQt6.QtGui import QPainter, QPixmap
        from PyQt6.QtWidgets import QApplication, QMenu

        import thunderwatch.app as app_module
        from thunderwatch.app import StatusWindow
        from thunderwatch.config import Config
        from thunderwatch.scheduler import Scheduler
        from thunderwatch.settings_dialog import SettingsDialog
        from thunderwatch.theme import apply_theme
        from thunderwatch.tray import badge_icon
        from thunderwatch.wizard import SetupWizard

        app = QApplication.instance() or QApplication([])
        apply_theme(app)
        settings = QSettings(str(root / "synthetic.ini"), QSettings.Format.IniFormat)
        settings.setValue("setup/complete", True)
        settings.setValue("smtp/host", "smtp.example.com")
        settings.setValue("smtp/username", "alerts@example.com")
        settings.setValue("smtp/from", "alerts@example.com")
        settings.setValue("smtp/recipient", "you@example.com")
        settings.setValue("monitor/location", "Example Home")
        app_module.read_state = lambda _path: {
            "last_observed": {},
            "last_reported": {},
            "pending": {},
            "history": [],
        }
        config = Config(
            setup_complete=True,
            smtp_host="smtp.example.com",
            smtp_username="alerts@example.com",
            smtp_from="alerts@example.com",
            smtp_recipient="you@example.com",
            location="Example Home",
        )
        scheduler = Scheduler(config)
        window = StatusWindow(scheduler)
        history = [
            {
                "time": "2026-01-01 09:00",
                "type": "change_detected",
                "changes": "203.0.113.10 -> 198.51.100.12",
            },
            {
                "time": "2026-01-01 09:00",
                "type": "email_sent",
                "changes": "synthetic change notice",
            },
        ]
        synthetic = {
            "last_observed": {
                "ipv4": {"value": "198.51.100.12", "time": "2026-01-01 09:00 UTC"},
                "ipv6": {"value": "2001:db8:1234:5678::/64", "time": "2026-01-01 09:00 UTC"},
            },
            "history": history,
        }
        window.update_state(synthetic, [])
        window.resize(620, 380)
        window.show()
        app.processEvents()
        status = output / "status-window.png"
        window.grab().save(str(status))
        pending = {
            **synthetic,
            "pending": {
                "changes": {"ipv4": {"old": "203.0.113.10", "new": "198.51.100.12"}},
                "last_error": "Synthetic SMTP connection failure",
            },
            "history": [
                *history,
                {
                    "time": "2026-01-01 09:01",
                    "type": "email_failed",
                    "error": "Synthetic SMTP connection failure",
                },
            ],
        }
        window.update_state(pending, [])
        app.processEvents()
        pending_path = output / "status-pending.png"
        window.grab().save(str(pending_path))
        menu = QMenu()
        for label in (
            "About",
            "Check for Updates",
            "Check IP Now",
            "Copy Current IP",
            "Send Test Email",
            "Settings…",
            "Show Status",
        ):
            menu.addAction(label)
        menu.addSeparator()
        menu.addAction("Quit")
        menu.ensurePolished()
        menu_path = output / "tray-menu.png"
        menu.grab().save(str(menu_path))
        strip = QPixmap(240, 80)
        strip.fill(Qt.GlobalColor.transparent)
        painter = QPainter(strip)
        for index, state in enumerate(("healthy", "warning", "danger")):
            painter.drawPixmap(index * 80 + 8, 8, badge_icon(None, state, 64).pixmap(64, 64))
        painter.end()
        states_path = output / "tray-states.png"
        strip.save(str(states_path))
        settings.setValue("setup/complete", True)
        wizard = SetupWizard(settings)
        wizard.resize(640, 520)
        wizard.show()
        app.processEvents()
        wizard.setCurrentId(0)
        app.processEvents()
        wizard_path = output / "setup-wizard.png"
        wizard.grab().save(str(wizard_path))
        settings_dialog = SettingsDialog(settings)
        settings_dialog.resize(620, 460)
        settings_dialog.show()
        app.processEvents()
        settings_path = output / "settings-dialog.png"
        settings_dialog.grab().save(str(settings_path))
        settings_dialog.close()
        wizard.close()
        window.close()
        scheduler.timer.stop()
        menu.close()
        for key, value in previous_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        return [status, pending_path, menu_path, states_path, settings_path, wizard_path]


if __name__ == "__main__":
    repo = Path(__file__).resolve().parents[2]
    capture_readme(repo / "docs/images")
