# ruff: noqa: PLC0415
"""Offscreen UI checks use synthetic data and never invoke network code."""

from __future__ import annotations

from typing import ClassVar

import pytest
from PyQt6.QtCore import QSettings
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import QApplication, QDialog, QPushButton

from thunderwatch.app import StatusWindow, current_addresses, should_setup
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


def test_windows_instance_server_name_is_scoped_to_account():
    from thunderwatch.app import instance_server_name

    assert instance_server_name(
        "win32", environment={"USERDOMAIN": "DOMAIN", "USERNAME": "jas"}
    ) == instance_server_name("win32", environment={"USERDOMAIN": "DOMAIN", "USERNAME": "jas"})
    assert instance_server_name(
        "win32", environment={"USERDOMAIN": "DOMAIN", "USERNAME": "jas"}
    ) != instance_server_name("win32", environment={"USERDOMAIN": "OTHER", "USERNAME": "jas"})


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


def test_status_window_opens_the_active_log_directory(app, tmp_path, monkeypatch):
    from thunderwatch import app as app_module

    opened = []

    def record_opened(url):
        opened.append(url)

    monkeypatch.setattr(app_module, "active_log_directory", lambda: tmp_path)
    monkeypatch.setattr(app_module.QDesktopServices, "openUrl", record_opened)
    scheduler = Scheduler(Config())
    window = StatusWindow(scheduler)
    button = next(
        button for button in window.findChildren(QPushButton) if button.text() == "Open Log Folder"
    )
    button.click()
    assert len(opened) == 1
    assert opened[0].toLocalFile() == str(tmp_path)
    scheduler.timer.stop()
    window.close()


def test_status_window_marks_ipv6_unavailable_after_failed_latest_lookup(app):
    scheduler = Scheduler(Config())
    window = StatusWindow(scheduler)
    window.update_state(
        {
            "last_observed": {"ipv6": {"value": "2001:db8:1::/64", "time": "last-good-check"}},
            "last_check": {
                "result": "partial",
                "families": {"ipv4": "success", "ipv6": "failure"},
            },
        },
        [],
    )
    assert "IPv6: not available · last seen last-good-check" in window.summary.text()
    assert "IPv6: 2001:db8:1::/64" not in window.summary.text()
    scheduler.timer.stop()
    window.close()


def test_copy_current_ip_omits_stale_and_disabled_addresses(app, monkeypatch):
    from thunderwatch import app as app_module

    scheduler = Scheduler(Config(ipv6=False))
    window = StatusWindow(scheduler)
    state = {
        "last_observed": {
            "ipv4": {"value": "8.8.4.4"},
            "ipv6": {"value": "2001:4860:4860::/64"},
        },
        "last_check": {"families": {"ipv4": "failure", "ipv6": "success"}},
    }
    monkeypatch.setattr(app_module, "read_state", lambda _path: state)
    window.copy_ip()
    assert QApplication.clipboard().text() == ""
    assert current_addresses(state, ipv6_enabled=False) == {}
    scheduler.timer.stop()
    window.close()


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


def test_wizard_invalidates_successful_test_when_smtp_fields_change(app):
    settings = QSettings("ThunderWatchTests", "WizardInvalidatesTest")
    settings.clear()
    settings.setValue("setup/complete", True)
    wizard = SetupWizard(settings)
    wizard.setStartId(4)
    wizard.show()
    app.processEvents()
    wizard.test_succeeded = True
    wizard.test_addresses = {"ipv4": "198.51.100.8"}
    wizard.recipient.setText("different@example.com")
    assert not wizard.test_succeeded
    assert wizard.test_addresses == {}
    assert not wizard.button(wizard.WizardButton.FinishButton).isEnabled()
    wizard.close()


def test_wizard_discards_test_result_when_fields_change_during_test(app):
    settings = QSettings("ThunderWatchTests", "WizardStaleTestResult")
    settings.clear()
    wizard = SetupWizard(settings)

    class WorkerStub:
        def __init__(self):
            self.current_addresses = {"ipv4": "198.51.100.8"}

    wizard._test_worker = WorkerStub()
    wizard._test_thread = object()
    wizard.test_button.setEnabled(False)
    wizard.host.setText("smtp.changed.example")
    wizard._test_finished(True, "Synthetic test succeeded")

    assert not wizard.test_succeeded
    assert wizard.test_addresses == {}
    assert "changed" in wizard.test_status.text().casefold()
    assert not wizard.test_button.isEnabled()
    wizard._test_worker_stopped()
    wizard.close()


def test_change_balloon_requires_a_successful_change_delivery():
    from thunderwatch.app import change_balloon_message

    actions = [
        {"type": "send_change", "changes": {"ipv4": {"new": "198.51.100.8"}}},
        {"type": "delivered"},
    ]
    assert change_balloon_message(actions) == "IPv4 changed to 198.51.100.8"
    assert change_balloon_message(actions[:1]) is None


def test_appimage_update_stages_download_beside_running_image(app, tmp_path, monkeypatch):
    from pathlib import Path

    from thunderwatch import update_dialog
    from thunderwatch.update_dialog import UpdateDialog
    from thunderwatch.updater import ReleaseAsset, UpdateOffer

    offer = UpdateOffer(
        "0.2.0",
        False,
        "notes",
        "https://example.invalid/release",
        ReleaseAsset("update.AppImage", "https://example.invalid/file", 10, "a" * 64),
    )
    current_dir = tmp_path / "mounted"
    download_dir = tmp_path / "app-data" / "updates"
    current_dir.mkdir()
    download_dir.mkdir(parents=True)
    current = current_dir / "ThunderWatch.AppImage"
    current.write_bytes(b"old image")
    downloaded = download_dir / "new.AppImage"
    downloaded.write_bytes(b"verified new image")
    calls = []
    quits = []
    monkeypatch.setenv("APPIMAGE", str(current))

    def write_helper(current_path, staged, helper, *, process_id):
        calls.append((Path(current_path), Path(staged), Path(helper), process_id))
        return Path(helper)

    monkeypatch.setattr(update_dialog, "write_appimage_update_helper", write_helper)
    monkeypatch.setattr(update_dialog.QProcess, "startDetached", lambda *_args: True)
    dialog = UpdateDialog(offer, lambda: quits.append(True))
    dialog._downloaded(True, "verified", downloaded)
    running, staged, helper, _pid = calls[0]
    assert running == current
    assert staged.parent == current.parent
    assert staged.read_bytes() == b"verified new image"
    assert helper.parent == current.parent
    assert quits == [True]
    staged.unlink()
    dialog.close()


@pytest.mark.parametrize(
    ("package", "suffix", "command"),
    [
        ("flatpak", "flatpak", "flatpak install --user --bundle --or-update"),
        ("snap", "snap", "sudo snap install --dangerous"),
    ],
)
def test_flatpak_and_snap_updates_offer_copyable_install_command(  # noqa: PLR0913, PLR0917
    app, tmp_path, monkeypatch, package, suffix, command
):
    from PyQt6.QtWidgets import QMessageBox

    from thunderwatch import update_dialog
    from thunderwatch.updater import ReleaseAsset, UpdateOffer

    path = tmp_path / f"ThunderWatch update.{suffix}"
    path.write_bytes(b"verified synthetic package")
    offer = UpdateOffer(
        "0.2.0",
        False,
        "notes",
        "https://example.invalid/release",
        ReleaseAsset(path.name, "https://example.invalid/file", path.stat().st_size, "a" * 64),
    )
    selected = object()
    messages = []
    labels = []

    class FakeMessageBox:
        ButtonRole = QMessageBox.ButtonRole
        StandardButton = QMessageBox.StandardButton

        def __init__(self, *_args):
            self.buttons = []

        def setWindowTitle(self, title):
            messages.append(("title", title))

        def setText(self, text):
            messages.append(("text", text))

        def setInformativeText(self, text):
            messages.append(("info", text))

        def addButton(self, label, *_args):
            labels.append(label)
            if label == "Copy install command":
                return selected
            return object()

        def exec(self):
            pass

        def clickedButton(self):
            return selected

    monkeypatch.setattr(update_dialog, "QMessageBox", FakeMessageBox)
    monkeypatch.delenv("APPIMAGE", raising=False)
    dialog = update_dialog.UpdateDialog(offer)
    dialog._downloaded(True, "verified", path)
    assert app.clipboard().text() == f"{command} '{path}'"
    assert "Copy install command" in labels
    assert "Open package folder" in labels
    assert str(path) in next(value for kind, value in messages if kind == "info")
    dialog.close()


def test_settings_waits_for_flatpak_portal_result_and_shows_denial(app, monkeypatch):
    from PyQt6.QtCore import QObject, pyqtSignal

    from thunderwatch.settings_dialog import SettingsDialog

    class PortalStub(QObject):
        request_failed = pyqtSignal(str)
        request_completed = pyqtSignal()

        def request_background(self, options):
            self.options = options

    monkeypatch.setenv("FLATPAK_ID", "io.github.jasmeralia.ThunderWatch")
    settings = QSettings("ThunderWatchTests", "FlatpakSettingsPortal")
    settings.clear()
    for key, value in {
        "setup/complete": True,
        "smtp/host": "smtp.example.com",
        "smtp/port": 587,
        "smtp/security": "starttls",
        "smtp/username": "alerts@example.com",
        "smtp/from": "alerts@example.com",
        "smtp/recipient": "you@example.com",
        "startup/autostart": True,
    }.items():
        settings.setValue(key, value)
    portal = PortalStub()
    dialog = SettingsDialog(settings, portal_client=portal)

    dialog._save()

    assert dialog.result() == 0
    assert dialog.settings_saved
    assert portal.options == {
        "autostart": True,
        "commandline": ["thunderwatch", "--autostart"],
    }
    portal.request_failed.emit("The desktop portal denied ThunderWatch automatic startup.")
    assert dialog.result() == 0
    assert "settings were saved" in dialog.startup_notice.text().casefold()
    assert "denied" in dialog.startup_notice.text().casefold()
    assert dialog.tabs.currentIndex() == 2
    dialog._save()
    assert dialog.result() == 0
    portal.request_completed.emit()
    assert dialog.result() == QDialog.DialogCode.Accepted
    dialog.close()


def test_settings_blocks_wizard_and_closure_while_portal_request_is_pending(app, monkeypatch):
    from PyQt6.QtCore import QObject, pyqtSignal
    from PyQt6.QtGui import QCloseEvent

    from thunderwatch import settings_dialog as settings_module
    from thunderwatch.settings_dialog import SettingsDialog

    wizard_calls = []

    class WizardStub:
        def __init__(self, _settings):
            wizard_calls.append("created")

        def exec(self):
            wizard_calls.append("executed")
            return QDialog.DialogCode.Accepted

    from PyQt6.QtWidgets import QDialog

    monkeypatch.setattr(settings_module, "SetupWizard", WizardStub)

    class PortalStub(QObject):
        request_failed = pyqtSignal(str)
        request_completed = pyqtSignal()

    dialog = SettingsDialog(
        QSettings("ThunderWatchTests", "PortalGuards"), portal_client=PortalStub()
    )
    dialog._portal_waiting = True
    dialog._sync_busy_controls()
    event = QCloseEvent()
    dialog.closeEvent(event)
    dialog.reject()
    dialog._run_wizard()
    assert not event.isAccepted()
    assert dialog.result() == 0
    assert not dialog.run_wizard.isEnabled()
    assert wizard_calls == []
    dialog._portal_waiting = False
    dialog.close()


def test_settings_blocks_wizard_while_email_test_thread_is_running(app, monkeypatch):
    from PyQt6.QtWidgets import QDialog

    from thunderwatch import settings_dialog as settings_module
    from thunderwatch.settings_dialog import SettingsDialog

    wizard_calls = []

    class WizardStub:
        def __init__(self, _settings):
            wizard_calls.append("created")

        def exec(self):
            wizard_calls.append("executed")
            return QDialog.DialogCode.Accepted

    monkeypatch.setattr(settings_module, "SetupWizard", WizardStub)

    dialog = SettingsDialog(QSettings("ThunderWatchTests", "WizardEmailGuard"))

    class RunningThread:
        def isRunning(self):
            return True

    dialog._test_thread = RunningThread()
    dialog._sync_busy_controls()
    dialog._run_wizard()
    assert dialog.result() == 0
    assert not dialog.run_wizard.isEnabled()
    assert wizard_calls == []
    dialog._test_thread = None
    dialog.close()


def test_wizard_retest_clears_pending_change_already_reported_by_test_email(app, monkeypatch):
    from pathlib import Path

    from thunderwatch import wizard as wizard_module

    settings = QSettings("ThunderWatchTests", "WizardRetestPending")
    settings.clear()
    settings.setValue("setup/complete", True)
    settings.setValue("smtp/host", "smtp.example.com")
    settings.setValue("smtp/username", "alerts@example.com")
    wizard = SetupWizard(settings)
    wizard.host.setText("smtp.example.com")
    wizard.username.setText("alerts@example.com")
    wizard.sender_edit.setText("alerts@example.com")
    wizard.recipient.setText("to@example.com")
    wizard.setStartId(4)
    wizard.show()
    app.processEvents()
    wizard.test_succeeded = True
    wizard.test_addresses = {"ipv4": "198.51.100.5"}
    state = {
        "last_reported": {"ipv4": {"value": "198.51.100.4", "time": "old"}},
        "pending": {
            "changes": {"ipv4": {"old": "198.51.100.4", "new": "198.51.100.5"}},
            "last_error": "synthetic SMTP failure",
        },
    }
    monkeypatch.setattr(wizard_module, "read_state", lambda _path: state)
    monkeypatch.setattr(wizard_module, "write_state", lambda _path, updated: state.update(updated))
    monkeypatch.setattr(wizard_module, "set_autostart", lambda *_args: None)
    monkeypatch.setattr(wizard_module, "state_path", lambda: Path("synthetic-state.json"))
    wizard.accept()
    assert state["last_reported"]["ipv4"]["value"] == "198.51.100.5"
    assert state["pending"] == {}
    wizard.close()


def test_rerun_wizard_pauses_scheduler_until_state_update_finishes(app, monkeypatch):
    from PyQt6.QtWidgets import QDialog

    from thunderwatch import settings_dialog as settings_module
    from thunderwatch.settings_dialog import SettingsDialog

    events = []

    class SchedulerStub:
        def pause_and_wait(self):
            events.append("pause")

        def resume(self):
            events.append("resume")

    class WizardStub:
        def __init__(self, _settings):
            pass

        def exec(self):
            events.append("wizard")
            return QDialog.DialogCode.Rejected

    monkeypatch.setattr(settings_module, "SetupWizard", WizardStub)
    dialog = SettingsDialog(QSettings("ThunderWatchTests", "WizardSerialState"), SchedulerStub())
    dialog._run_wizard()
    assert events == ["pause", "wizard", "resume"]
    dialog.close()


def test_scheduler_pause_waits_until_inflight_state_write_finishes(app, monkeypatch):
    from threading import Event, Timer

    from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot

    from thunderwatch import scheduler as scheduler_module

    entered = Event()
    release_write = Event()
    write_finished = Event()

    class SlowWorker(QObject):
        finished = pyqtSignal(object, list)

        def __init__(self, _config):
            super().__init__()

        @pyqtSlot()
        def check(self):
            entered.set()
            release_write.wait(2)
            write_finished.set()
            self.finished.emit({"last_check": {"result": "success"}, "ipv4_failure_streak": 0}, [])

    monkeypatch.setattr(scheduler_module, "CheckWorker", SlowWorker)
    scheduler = Scheduler(Config())
    scheduler.timer.stop()
    scheduler._clock_timer.stop()
    scheduler.run()
    assert entered.wait(1)
    Timer(0.05, release_write.set).start()

    scheduler.pause_and_wait()

    assert write_finished.is_set()
    assert scheduler._paused
    scheduler.timer.stop()
    scheduler._clock_timer.stop()


@pytest.mark.parametrize("dialog_kind", ["wizard", "settings", "update"])
def test_worker_dialog_refuses_close_while_thread_is_running(app, dialog_kind):
    from thunderwatch.settings_dialog import SettingsDialog
    from thunderwatch.update_dialog import UpdateDialog
    from thunderwatch.updater import ReleaseAsset, UpdateOffer

    class RunningThread:
        def isRunning(self):
            return True

    settings = QSettings("ThunderWatchTests", f"ThreadClose-{dialog_kind}")
    settings.clear()
    if dialog_kind == "wizard":
        dialog = SetupWizard(settings)
        dialog._test_thread = RunningThread()
    elif dialog_kind == "settings":
        dialog = SettingsDialog(settings)
        dialog._test_thread = RunningThread()
    else:
        offer = UpdateOffer(
            "0.2.0",
            False,
            "notes",
            "https://example.invalid/release",
            ReleaseAsset("update.deb", "https://example.invalid/file", 10, "a" * 64),
        )
        dialog = UpdateDialog(offer)
        dialog.worker = RunningThread()
    event = QCloseEvent()
    dialog.closeEvent(event)
    assert not event.isAccepted()


def test_update_dialog_shows_channel_release_notes_size_and_explicit_button(app, monkeypatch):
    from thunderwatch import update_dialog
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
    monkeypatch.setattr(update_dialog, "installed_version", lambda: "0.1.0")
    dialog = UpdateDialog(offer)
    assert "0.1.0" in dialog.channel_label.text()
    assert offer.version in dialog.channel_label.text()
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


def test_settings_dialog_reject_is_blocked_while_test_thread_runs(app):
    from thunderwatch.settings_dialog import SettingsDialog

    class RunningThread:
        running = True

        def isRunning(self):
            return self.running

    settings = QSettings("ThunderWatchTests", "SettingsRejectWhileTesting")
    settings.clear()
    dialog = SettingsDialog(settings)
    thread = RunningThread()
    dialog._test_thread = thread
    dialog.show()
    app.processEvents()
    dialog.reject()
    assert dialog.isVisible()
    thread.running = False
    dialog.close()


def test_update_dialog_reject_is_blocked_while_download_runs(app):
    from thunderwatch.update_dialog import UpdateDialog
    from thunderwatch.updater import ReleaseAsset, UpdateOffer

    class RunningWorker:
        running = True

        def isRunning(self):
            return self.running

    offer = UpdateOffer(
        "0.2.0",
        False,
        "notes",
        "https://example.invalid/release",
        ReleaseAsset("update.exe", "https://example.invalid/file", 10, "a" * 64),
    )
    dialog = UpdateDialog(offer)
    worker = RunningWorker()
    dialog.worker = worker
    dialog.show()
    app.processEvents()
    dialog.reject()
    assert dialog.isVisible()
    worker.running = False
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


def test_scheduler_pause_waits_for_in_flight_worker(app, monkeypatch):
    from PyQt6.QtCore import QObject, QThread, pyqtSignal

    from thunderwatch import scheduler as scheduler_module

    class Worker(QObject):
        finished = pyqtSignal(dict, list)

        def __init__(self, _config):
            super().__init__()

        def check(self):
            QThread.msleep(30)
            self.finished.emit({"last_check": {"result": "success"}}, [])

    monkeypatch.setattr(scheduler_module, "CheckWorker", Worker)
    scheduler = Scheduler(Config())
    scheduler.run()
    thread = scheduler._worker_thread
    assert thread is not None
    scheduler.pause_and_wait()
    assert not thread.isRunning()
    assert not scheduler.running
    assert not scheduler.timer.isActive()
    scheduler.resume()
    assert scheduler.timer.isActive()
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


def test_scheduler_owns_and_starts_initial_delay_timer(app):
    scheduler = Scheduler(Config())
    assert scheduler.timer.isActive()
    assert scheduler.timer.interval() == 15_000
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

    def reapply_autostart(enabled, **_kwargs):
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


def test_linux_package_download_gives_install_instructions(app, tmp_path, monkeypatch):
    from thunderwatch import update_dialog
    from thunderwatch.update_dialog import UpdateDialog
    from thunderwatch.updater import ReleaseAsset, UpdateOffer

    offer = UpdateOffer(
        "0.2.0",
        False,
        "notes",
        "https://example.invalid/release",
        ReleaseAsset(
            "ThunderWatch-v0.2.0-linux-amd64.deb", "https://example.invalid/file", 10, "a" * 64
        ),
    )
    monkeypatch.setattr(update_dialog.QDesktopServices, "openUrl", lambda _url: True)
    dialog = UpdateDialog(offer)
    dialog._downloaded(True, "verified", tmp_path / offer.asset.name)
    assert "Install it with your package manager" in dialog.status_label.text()
    assert "restart ThunderWatch" in dialog.status_label.text()
    dialog.close()


def test_windows_update_launch_resets_pyinstaller_environment_and_detaches(
    app, tmp_path, monkeypatch
):
    from thunderwatch import update_dialog
    from thunderwatch.update_dialog import UpdateDialog
    from thunderwatch.updater import ReleaseAsset, UpdateOffer

    offer = UpdateOffer(
        "0.2.0",
        False,
        "notes",
        "https://example.invalid/release",
        ReleaseAsset("update.exe", "https://example.invalid/file", 10, "a" * 64),
    )
    launches = []
    quit_calls = []
    monkeypatch.setattr(update_dialog.sys, "platform", "win32")
    monkeypatch.setattr(
        update_dialog.subprocess, "Popen", lambda *args, **kwargs: launches.append((args, kwargs))
    )
    monkeypatch.setattr(update_dialog.subprocess, "DETACHED_PROCESS", 0x00000008, raising=False)
    monkeypatch.setattr(
        update_dialog.subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200, raising=False
    )
    monkeypatch.setenv("_PYI_APPLICATION_HOME_DIR", "synthetic-temp")
    monkeypatch.delenv("PYINSTALLER_RESET_ENVIRONMENT", raising=False)
    dialog = UpdateDialog(offer, lambda: quit_calls.append(True))
    dialog._downloaded(True, "verified", tmp_path / "installer.exe")
    args, kwargs = launches[0]
    assert args[0] == [str(tmp_path / "installer.exe")]
    assert kwargs["env"]["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
    assert kwargs["creationflags"] & update_dialog.subprocess.DETACHED_PROCESS
    assert kwargs["creationflags"] & update_dialog.subprocess.CREATE_NEW_PROCESS_GROUP
    assert kwargs["env"].get("_PYI_APPLICATION_HOME_DIR") is None
    assert quit_calls == [True]
    dialog.close()


def test_appimage_update_launch_failure_keeps_application_running(app, tmp_path, monkeypatch):
    from thunderwatch import update_dialog
    from thunderwatch.update_dialog import UpdateDialog
    from thunderwatch.updater import ReleaseAsset, UpdateOffer

    offer = UpdateOffer(
        "0.2.0",
        False,
        "notes",
        "https://example.invalid/release",
        ReleaseAsset("update.AppImage", "https://example.invalid/file", 10, "a" * 64),
    )
    quits = []
    monkeypatch.setenv("APPIMAGE", str(tmp_path / "old.AppImage"))
    monkeypatch.setattr(
        update_dialog, "write_appimage_update_helper", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(update_dialog.QProcess, "startDetached", lambda *_args: False)
    dialog = UpdateDialog(offer, lambda: quits.append(True))
    dialog._downloaded(True, "verified", tmp_path / "new.AppImage")
    assert quits == []
    assert "Could not start AppImage update" in dialog.status_label.text()
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
    stale_release = folder / "ThunderWatch-v0.1.0-linux-amd64.rpm"
    stale_release.write_text("old rpm", encoding="utf-8")
    monkeypatch.setattr(update_dialog, "app_data", lambda: tmp_path)
    starts = []

    class FakeWorker:
        def __init__(self, _offer, target):
            self.destination = target
            self.completed = Signal()

        def start(self):
            starts.append(self.destination)

        def isRunning(self):
            return False

    class Signal:
        def connect(self, _callback):
            pass

    monkeypatch.setattr(update_dialog, "UpdateDownloadWorker", FakeWorker)
    dialog = UpdateDialog(offer)
    dialog.download()
    assert starts == [destination]
    assert not destination.exists()
    assert not helper.exists()
    assert not stale_release.exists()
    dialog.close()


def test_quit_requests_worker_interruption_before_waiting():
    from types import SimpleNamespace

    from thunderwatch.app import ThunderWatchApp

    class FakeThread:
        def __init__(self):
            self.calls = []

        def isRunning(self):
            return True

        def requestInterruption(self):
            self.calls.append("interrupt")

        def quit(self):
            self.calls.append("quit")

        def wait(self):
            self.calls.append("wait")

    thread = FakeThread()
    instance = SimpleNamespace(
        _test_thread=thread,
        _update_thread=None,
        scheduler=None,
        _update_dialog=None,
    )
    ThunderWatchApp._wait_for_workers(instance)
    assert thread.calls == ["interrupt", "quit", "wait"]


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
