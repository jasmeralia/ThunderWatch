# ruff: noqa: PLC0415
"""Offscreen UI checks use synthetic data and never invoke network code."""

from __future__ import annotations

from typing import ClassVar

import pytest
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication, QDialog, QPushButton

from thunderwatch.app import StatusWindow, should_setup
from thunderwatch.config import Config
from thunderwatch.scheduler import Scheduler
from thunderwatch.wizard import SetupWizard


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def test_incomplete_configuration_routes_to_setup():
    class Store:
        def value(self, key, default=None):
            return default

    assert should_setup(Store())


def test_status_window_renders_synthetic_history(app, tmp_path, monkeypatch):
    from thunderwatch import app as app_module

    monkeypatch.setattr(
        app_module,
        "read_state",
        lambda path: {
            "last_observed": {"ipv4": {"value": "203.0.113.8", "time": "2026-01-01T00:00:00Z"}},
            "last_reported": {"ipv4": {"value": "203.0.113.1", "time": "2026-01-01T00:00:00Z"}},
            "pending": {
                "changes": {"ipv4": {"old": "203.0.113.1", "new": "203.0.113.8"}},
                "last_error": "Synthetic SMTP failure",
            },
            "last_check": {
                "time": "2026-01-01T00:00:00Z",
                "result": "partial",
                "providers": {"ipv4": ["fake-a", "fake-b"]},
            },
            "history": [
                {"time": "2026-01-01T00:00:00Z", "type": "email_sent", "changes": "synthetic"}
            ],
        },
    )
    scheduler = Scheduler(Config())
    window = StatusWindow(scheduler)
    assert "203.0.113.8" in window.summary.text()
    assert window.history.rowCount() == 1
    assert "Last reported" in window.summary.text()
    assert "Synthetic SMTP failure" in window.summary.text()
    assert any(button.text() == "Settings" for button in window.findChildren(QPushButton))
    window.close()
    scheduler.timer.stop()


def test_wizard_provider_preset_only_sets_server_fields(app, monkeypatch):
    settings = QSettings("ThunderWatchTests", "WizardPreset")
    wizard = SetupWizard(settings)
    wizard._preset("Gmail")
    assert wizard.host.text() == "smtp.gmail.com"
    assert wizard.port.value() == 465
    assert wizard.security.currentText() == "ssl"
    assert wizard.username.text() == ""
    wizard.close()


def test_setup_finish_is_gated_by_successful_test_or_explicit_offline_save(app):
    settings = QSettings("ThunderWatchTests", "WizardFinishGate")
    settings.clear()
    settings.setValue("setup/complete", True)
    wizard = SetupWizard(settings)
    wizard.setStartId(4)
    wizard.show()
    app.processEvents()
    assert wizard.currentId() == 4
    assert not wizard.save_offline.isChecked()
    assert not wizard.validateCurrentPage()
    wizard.save_offline.setChecked(True)
    assert wizard.validateCurrentPage()
    wizard.close()


def test_update_dialog_shows_channel_release_notes_size_and_explicit_button(app):
    from thunderwatch.update_dialog import UpdateDialog
    from thunderwatch.updater import ReleaseAsset, UpdateOffer

    offer = UpdateOffer(
        "0.2.0-beta.1",
        True,
        "Synthetic release notes",
        "https://example.invalid/release",
        ReleaseAsset(
            "ThunderWatch-v0.2.0-beta.1-linux-amd64.deb",
            "https://example.invalid/asset",
            1234,
            "a" * 64,
        ),
    )
    dialog = UpdateDialog(offer)
    assert "Beta" in dialog.channel_label.text()
    assert "Synthetic release notes" in dialog.notes.toPlainText()
    assert dialog.download_button.text() == "Download and Update"
    dialog.close()


def test_update_check_worker_uses_fake_release_feed(monkeypatch):
    from thunderwatch import update_dialog
    from thunderwatch.config import Config
    from thunderwatch.updater import ReleaseAsset, UpdateOffer

    offer = UpdateOffer(
        "0.2.0",
        False,
        "notes",
        "https://example.invalid/release",
        ReleaseAsset("asset.deb", "https://example.invalid/asset", 10, "a" * 64),
    )
    monkeypatch.setattr(update_dialog, "installed_version", lambda: "0.1.0")
    monkeypatch.setattr(update_dialog, "detect_package_type", lambda: "deb")
    monkeypatch.setattr(update_dialog, "fetch_release_feed", lambda: [{"synthetic": True}])
    monkeypatch.setattr(update_dialog, "check_for_update", lambda *args: offer)
    worker = update_dialog.UpdateCheckWorker(Config(include_beta=False))
    results = []
    worker.finished.connect(lambda value, error: results.append((value, error)))
    worker.run()
    assert results == [(offer, "")]


def test_settings_dialog_has_email_monitoring_startup_and_updates_tabs(app):
    from thunderwatch.settings_dialog import SettingsDialog

    settings = QSettings("ThunderWatchTests", "SettingsDialog")
    dialog = SettingsDialog(settings)
    tabs = dialog.tabs
    assert [tabs.tabText(i) for i in range(tabs.count())] == [
        "Email",
        "Monitoring",
        "Startup",
        "Updates",
    ]
    dialog.close()


def test_settings_dialog_save_reconfigures_scheduler_interval(app, monkeypatch):
    from thunderwatch.config import Config
    from thunderwatch.scheduler import Scheduler

    scheduler = Scheduler(Config(interval_minutes=10))
    scheduler.reconfigure(Config(interval_minutes=20))
    assert scheduler.config.interval_minutes == 20
    assert scheduler.timer.interval() == 20 * 60_000
    scheduler.timer.stop()


def test_desktop_shell_builds_tray_and_routes_without_setup(app, monkeypatch):
    from thunderwatch import app as app_module
    from thunderwatch.state import empty_state

    class Signal:
        def connect(self, callback):
            pass

    class Server:
        def __init__(self):
            self.newConnection = Signal()

        def listen(self, name):
            return True

        def hasPendingConnections(self):
            return False

    class Settings:
        values: ClassVar[dict[str, object]] = {
            "setup/complete": True,
            "smtp/host": "smtp.example.com",
            "smtp/port": 587,
            "smtp/security": "auto",
            "smtp/username": "alerts@example.com",
            "smtp/from": "alerts@example.com",
            "smtp/recipient": "you@example.com",
            "monitor/location": "Example Home",
            "monitor/interval_minutes": 10,
            "monitor/ipv6": True,
            "startup/autostart": False,
            "updates/automatic": False,
            "updates/include_beta": False,
        }

        def __init__(self, *args):
            pass

        def value(self, key, default=None):
            return self.values.get(key, default)

    monkeypatch.setattr(app_module, "QSettings", Settings)
    monkeypatch.setattr(app_module, "QLocalServer", Server)
    monkeypatch.setattr(app_module, "set_autostart", lambda *args: None)
    monkeypatch.setattr(app_module, "read_state", lambda _path: empty_state())
    monkeypatch.setattr(
        app_module.QSystemTrayIcon, "isSystemTrayAvailable", staticmethod(lambda: True)
    )
    client = app_module.ThunderWatchApp(app, False)
    labels = [action.text() for action in client.tray.contextMenu().actions()]
    assert labels == [
        "About",
        "Check for Updates",
        "Check IP Now",
        "Copy Current IP",
        "Send Test Email",
        "Settings…",
        "Show Status",
        "",
        "Quit",
    ]
    client.scheduler.timer.stop()
    client._tray_timer.stop()
    client.tray.hide()
    client.window.close()


def test_settings_dialog_saves_valid_values_and_reapplies_autostart(app, monkeypatch):
    from thunderwatch import settings_dialog

    settings = QSettings("ThunderWatchTests", "SettingsSave")
    settings.clear()
    settings.setValue("setup/complete", True)
    dialog = settings_dialog.SettingsDialog(settings)
    dialog.host.setText("smtp.example.com")
    dialog.username.setText("alerts@example.com")
    dialog.sender_edit.setText("alerts@example.com")
    dialog.recipient.setText("you@example.com")
    dialog.location.setText("Example Home")
    applied = []

    def reapply_autostart(enabled):
        applied.append(enabled)

    monkeypatch.setattr(settings_dialog, "set_autostart", reapply_autostart)
    dialog._save()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert settings.value("monitor/interval_minutes") == 10
    assert applied == [True]


def test_update_dialog_reports_download_failure_without_network(app):
    from thunderwatch.update_dialog import UpdateDialog
    from thunderwatch.updater import ReleaseAsset, UpdateOffer

    offer = UpdateOffer(
        "0.2.0",
        False,
        "notes",
        "https://example.invalid/release",
        ReleaseAsset("update.deb", "https://example.invalid/file", 10, "a" * 64),
    )
    dialog = UpdateDialog(offer)
    dialog._downloaded(False, "synthetic checksum failure", None)
    assert "synthetic checksum failure" in dialog.status_label.text()
    dialog.close()
