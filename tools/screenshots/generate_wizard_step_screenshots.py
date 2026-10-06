"""Capture one synthetic image for each setup wizard page."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def capture_steps(output_dir: str | Path) -> list[Path]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    env_keys = ("HOME", "XDG_CONFIG_HOME", "QT_QPA_PLATFORM")
    previous_env = {key: os.environ.get(key) for key in env_keys}
    with tempfile.TemporaryDirectory(prefix="thunderwatch-wizard-") as scratch:
        os.environ["HOME"] = scratch
        os.environ["XDG_CONFIG_HOME"] = str(Path(scratch) / "config")
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PyQt6.QtCore import QSettings
        from PyQt6.QtWidgets import QApplication

        from thunderwatch.theme import apply_theme
        from thunderwatch.wizard import SetupWizard

        app = QApplication.instance() or QApplication([])
        apply_theme(app)
        settings = QSettings(str(Path(scratch) / "synthetic.ini"), QSettings.Format.IniFormat)
        settings.setValue("setup/complete", True)
        wizard = SetupWizard(settings)
        wizard.resize(640, 520)
        wizard.show()
        results = []
        names = (
            "01-welcome.png",
            "02-email-server.png",
            "03-recipient-location.png",
            "04-monitoring-startup.png",
            "05-test-and-finish.png",
        )
        for index, name in enumerate(names):
            if index == 4:
                wizard.test_succeeded = True
                wizard.test_addresses = {
                    "ipv4": "203.0.113.42",
                    "ipv6": "2001:db8:1234:5678::/64",
                }
                wizard.test_status.setText("Test email sent to you@example.com.")
            wizard.setCurrentId(index)
            app.processEvents()
            path = output / name
            wizard.grab().save(str(path))
            results.append(path)
        wizard.close()
        for key, value in previous_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        return results


if __name__ == "__main__":
    capture_steps(Path(__file__).resolve().parents[2] / "docs/images/wizard-steps")
