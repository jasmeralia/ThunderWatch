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


def test_qsettings_storage_uses_test_temp_directory(isolated_qsettings):
    settings = QSettings("ThunderWatchTests", "PathIsolation")
    assert str(settings.fileName()).startswith(str(isolated_qsettings))


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


def test_rerun_wizard_prefills_all_persisted_preferences(app):
    settings = QSettings("ThunderWatchTests", "WizardAllPreferences")
    settings.clear()
    for key, value in {
        "smtp/host": "smtp.example.com",
        "smtp/port": 465,
        "smtp/security": "ssl",
        "smtp/username": "alerts@example.com",
        "smtp/from": "alerts@example.com",
        "smtp/recipient": "you@example.com",
        "monitor/location": "Example Home",
        "monitor/interval_minutes": 60,
        "monitor/ipv6": False,
        "startup/autostart": False,
        "updates/include_beta": True,
    }.items():
        settings.setValue(key, value)
    wizard = SetupWizard(settings)
    assert wizard.security.currentText() == "ssl"
    assert wizard.interval.value() == 60
    assert not wizard.ipv6.isChecked()
    assert not wizard.autostart.isChecked()
    assert wizard.beta.isChecked()
    wizard.close()


def test_rerun_wizard_reapplies_saved_autostart_setting(app, monkeypatch):
    from thunderwatch import wizard as wizard_module

    settings = QSettings("ThunderWatchTests", "WizardAutostart")
    settings.clear()
    for key, value in {
        "setup/complete": True,
        "startup/autostart": False,
        "smtp/host": "smtp.example.com",
        "smtp/username": "alerts@example.com",
        "smtp/from": "alerts@example.com",
        "smtp/recipient": "you@example.com",
    }.items():
        settings.setValue(key, value)
    wizard = SetupWizard(settings)
    wizard.host.setText("smtp.example.com")
    wizard.username.setText("alerts@example.com")
    wizard.sender_edit.setText("alerts@example.com")
    wizard.recipient.setText("you@example.com")
    applied = []

    def set_autostart(enabled):
        applied.append(enabled)

    monkeypatch.setattr(wizard_module, "set_autostart", set_autostart, raising=False)
    wizard.accept()
    assert applied == [False]


def test_rerun_wizard_requires_password_when_smtp_identity_changes(app, monkeypatch):
    from thunderwatch import wizard as wizard_module

    settings = QSettings("ThunderWatchTests", "WizardIdentityChange")
    settings.clear()
    for key, value in {
        "setup/complete": True,
        "smtp/host": "smtp.example.com",
        "smtp/username": "old@example.com",
        "smtp/from": "old@example.com",
        "smtp/recipient": "you@example.com",
    }.items():
        settings.setValue(key, value)
    wizard = SetupWizard(settings)
    wizard.username.setText("new@example.com")
    warnings = []
    monkeypatch.setattr(wizard_module.QMessageBox, "warning", lambda *args: warnings.append(args))
    wizard.accept()
    assert settings.value("smtp/username") == "old@example.com"
    assert warnings
    wizard.close()


def test_offline_setup_rejects_invalid_recipient(app, monkeypatch):
    from thunderwatch import wizard as wizard_module

    settings = QSettings("ThunderWatchTests", "WizardInvalidOfflineRecipient")
    settings.clear()
    wizard = SetupWizard(settings)
    wizard.host.setText("smtp.example.com")
    wizard.username.setText("alerts@example.com")
    wizard.sender_edit.setText("alerts@example.com")
    wizard.recipient.setText("not-an-email")
    wizard.password.setText("synthetic-password")
    wizard.save_offline.setChecked(True)
    warnings = []
    monkeypatch.setattr(wizard_module.QMessageBox, "warning", lambda *args: warnings.append(args))
    wizard.accept()
    assert not settings.value("setup/complete", False, type=bool)
    assert warnings
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


def test_settings_dialog_keeps_saved_false_string_preferences_disabled(app):
    from thunderwatch.settings_dialog import SettingsDialog

    settings = QSettings("ThunderWatchTests", "SettingsFalseStrings")
    settings.clear()
    settings.setValue("updates/automatic", "false")
    settings.setValue("monitor/ipv6", "false")
    settings.setValue("startup/autostart", "false")
    settings.setValue("updates/include_beta", "false")
    dialog = SettingsDialog(settings)
    assert not dialog.automatic.isChecked()
    assert not dialog.ipv6.isChecked()
    assert not dialog.autostart.isChecked()
    assert not dialog.beta.isChecked()
    dialog.close()


def test_scheduler_retains_worker_until_thread_finishes(app, monkeypatch):
    import gc

    from PyQt6.QtCore import QObject, pyqtSignal

    from thunderwatch import scheduler as scheduler_module

    class Worker(QObject):
        finished = pyqtSignal(dict, list)

        def __init__(self, _config):
            super().__init__()

        def check(self):
            pass

    monkeypatch.setattr(scheduler_module, "CheckWorker", Worker)
    scheduler = Scheduler(Config())
    scheduler.run()
    thread = scheduler._worker_thread
    gc.collect()
    try:
        assert scheduler._worker is not None
        assert thread is not None
        scheduler._worker.finished.emit({"last_check": {"result": "success"}}, [])
        assert thread.wait(2000)
        app.processEvents()
        assert scheduler._worker is None
    finally:
        if thread is not None and thread.isRunning():
            thread.quit()
            thread.wait(2000)
        scheduler.timer.stop()


def test_local_server_claim_removes_stale_socket_but_never_continues_unowned(monkeypatch):
    from thunderwatch.app import claim_local_server

    class Server:
        def __init__(self, answers):
            self.answers = iter(answers)
            self.calls = 0

        def listen(self, _name):
            self.calls += 1
            return next(self.answers)

    removed = []
    stale = Server([False, True])
    assert claim_local_server(stale, "test", lambda _name: False, removed.append)
    assert stale.calls == 2
    assert removed == ["test"]

    occupied = Server([False])
    assert not claim_local_server(occupied, "test", lambda _name: True, removed.append)
    assert occupied.calls == 1

    unavailable = Server([False, False])
    assert not claim_local_server(unavailable, "test", lambda _name: False, removed.append)
    assert unavailable.calls == 2


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

        @staticmethod
        def removeServer(name):
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
    for key, value in {
        "setup/complete": True,
        "smtp/host": "smtp.example.com",
        "smtp/username": "alerts@example.com",
        "smtp/from": "alerts@example.com",
        "smtp/recipient": "you@example.com",
    }.items():
        settings.setValue(key, value)
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


def test_settings_dialog_requires_password_when_smtp_identity_changes(app, monkeypatch):
    from thunderwatch import settings_dialog

    settings = QSettings("ThunderWatchTests", "SettingsIdentityChange")
    settings.clear()
    for key, value in {
        "setup/complete": True,
        "smtp/host": "smtp.example.com",
        "smtp/username": "old@example.com",
        "smtp/from": "old@example.com",
        "smtp/recipient": "you@example.com",
    }.items():
        settings.setValue(key, value)
    dialog = settings_dialog.SettingsDialog(settings)
    dialog.username.setText("new@example.com")
    stored = []
    warnings = []
    monkeypatch.setattr(settings_dialog, "store_password", lambda *args: stored.append(args))
    monkeypatch.setattr(settings_dialog.QMessageBox, "warning", lambda *args: warnings.append(args))
    dialog._save()
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert settings.value("smtp/username") == "old@example.com"
    assert stored == []
    assert warnings
    dialog.close()


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


def test_update_dialog_removes_previous_artifacts_before_retry(app, tmp_path, monkeypatch):
    from thunderwatch import update_dialog
    from thunderwatch.update_dialog import UpdateDialog
    from thunderwatch.updater import ReleaseAsset, UpdateOffer

    offer = UpdateOffer(
        "0.2.0",
        False,
        "notes",
        "https://example.invalid/release",
        ReleaseAsset("update.deb", "https://example.invalid/file", 10, "a" * 64),
    )
    folder = tmp_path / "updates"
    folder.mkdir()
    destination = folder / offer.asset.name
    helper = destination.with_suffix(".update.sh")
    destination.write_text("old download", encoding="utf-8")
    helper.write_text("old helper", encoding="utf-8")
    monkeypatch.setattr(update_dialog, "app_data", lambda: tmp_path)
    starts = []

    class FakeWorker:
        def __init__(self, _offer, target):
            self.destination = target
            self.completed = Signal()

        def start(self):
            starts.append(self.destination)

    class Signal:
        def connect(self, _callback):
            pass

    monkeypatch.setattr(update_dialog, "UpdateDownloadWorker", FakeWorker)
    dialog = UpdateDialog(offer)
    dialog.download()
    assert starts == [destination]
    assert not destination.exists()
    assert not helper.exists()
    dialog.close()


def test_automatic_update_timer_does_not_create_duplicate_chains(app):
    from PyQt6.QtCore import QTimer

    from thunderwatch.app import schedule_automatic_update

    timer = QTimer()
    schedule_automatic_update(timer, True)
    assert timer.isActive()
    assert timer.interval() == 30_000
    schedule_automatic_update(timer, True)
    assert timer.interval() == 30_000
    schedule_automatic_update(timer, False)
    assert not timer.isActive()
