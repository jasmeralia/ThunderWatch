"""Tabbed settings editor for the running ThunderWatch instance."""

from __future__ import annotations

import logging
import os

from PyQt6.QtCore import QSettings, QThread, pyqtSlot
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .autostart import BackgroundPortal, QtBackgroundPortal, set_autostart
from .config import Config, smtp_identity_changed
from .paths import password_path
from .scheduler import Scheduler
from .secrets import store_password
from .wizard import SetupWizard
from .worker import TestEmailWorker

logger = logging.getLogger(__name__)


class SettingsDialog(QDialog):
    def __init__(  # noqa: PLR0915
        self,
        settings: QSettings,
        scheduler: Scheduler | None = None,
        portal_client: BackgroundPortal | None = None,
    ) -> None:
        super().__init__()
        self.settings = settings
        self.scheduler = scheduler
        self.settings_saved = False
        self.portal_client = portal_client or (
            QtBackgroundPortal() if os.environ.get("FLATPAK_ID") else None
        )
        self._portal_waiting = False
        if self.portal_client:
            request_failed = getattr(self.portal_client, "request_failed", None)
            request_completed = getattr(self.portal_client, "request_completed", None)
            if request_failed is not None:
                request_failed.connect(self._portal_request_failed)
            if request_completed is not None:
                request_completed.connect(self._portal_request_completed)
        config = Config.from_store(settings)
        self._test_thread: QThread | None = None
        self._test_worker: TestEmailWorker | None = None
        self.setWindowTitle("ThunderWatch settings")
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
        email = QWidget()
        form = QFormLayout(email)
        self.host = QLineEdit(str(settings.value("smtp/host", "")))
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(int(settings.value("smtp/port", 587)))
        self.security = QComboBox()
        self.security.addItems(["auto", "ssl", "starttls"])
        self.security.setCurrentText(str(settings.value("smtp/security", "auto")))
        self.username = QLineEdit(str(settings.value("smtp/username", "")))
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.sender_edit = QLineEdit(str(settings.value("smtp/from", "")))
        self.recipient = QLineEdit(str(settings.value("smtp/recipient", "")))
        self.allow_file = QCheckBox("Store password in a file only my account can read")
        self.test_button = QPushButton("Send Test Email")
        for label, widget in (
            ("SMTP host", self.host),
            ("Port", self.port),
            ("Security", self.security),
            ("Username", self.username),
            ("New password (optional)", self.password),
            ("From address", self.sender_edit),
            ("Recipient", self.recipient),
        ):
            form.addRow(label, widget)
        form.addRow(self.allow_file)
        self.test_button.clicked.connect(self._send_test)
        form.addRow(self.test_button)
        self.tabs.addTab(email, "Email")
        monitoring = QWidget()
        form = QFormLayout(monitoring)
        self.location = QLineEdit(str(settings.value("monitor/location", "")))
        self.interval = QSpinBox()
        self.interval.setRange(5, 720)
        self.interval.setValue(int(settings.value("monitor/interval_minutes", 10)))
        self.ipv6 = QCheckBox("Also monitor the IPv6 prefix")
        self.ipv6.setChecked(config.ipv6)
        form.addRow("Location label", self.location)
        form.addRow("Interval (minutes)", self.interval)
        form.addRow(self.ipv6)
        self.tabs.addTab(monitoring, "Monitoring")
        startup = QWidget()
        form = QFormLayout(startup)
        self.autostart = QCheckBox("Start ThunderWatch when I sign in")
        self.autostart.setChecked(config.autostart)
        form.addRow(self.autostart)
        self.startup_notice = QLabel("")
        self.startup_notice.setWordWrap(True)
        form.addRow(self.startup_notice)
        self.startup_tab = startup
        self.tabs.addTab(startup, "Startup")
        updates = QWidget()
        form = QFormLayout(updates)
        self.automatic = QCheckBox("Check automatically")
        self.automatic.setChecked(config.automatic_updates)
        self.beta = QCheckBox("Include beta updates")
        self.beta.setChecked(config.include_beta)
        form.addRow(self.automatic)
        form.addRow(self.beta)
        self.tabs.addTab(updates, "Updates")
        controls = QHBoxLayout()
        self.run_wizard = QPushButton("Run Setup Wizard Again")
        self.run_wizard.clicked.connect(self._run_wizard)
        controls.addWidget(self.run_wizard)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        save_button = buttons.button(QDialogButtonBox.StandardButton.Save)
        assert save_button is not None
        self.save_button = save_button
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        controls.addWidget(buttons)
        layout.addLayout(controls)

    def _send_test(self) -> None:
        config = Config(
            setup_complete=True,
            smtp_host=self.host.text().strip(),
            smtp_port=self.port.value(),
            smtp_security=self.security.currentText(),
            smtp_username=self.username.text().strip(),
            smtp_from=self.sender_edit.text().strip(),
            smtp_recipient=self.recipient.text().strip(),
            location=self.location.text().strip(),
            interval_minutes=self.interval.value(),
            ipv6=self.ipv6.isChecked(),
        )
        if not config.complete:
            QMessageBox.warning(
                self, "Invalid settings", "Complete the SMTP fields before testing email."
            )
            return
        worker = TestEmailWorker(config, password_override=self.password.text() or None)
        thread = QThread(self)
        worker.moveToThread(thread)
        worker.finished.connect(self._test_finished)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._test_worker_stopped)
        self._test_worker = worker
        self._test_thread = thread
        self.test_button.setEnabled(False)
        thread.started.connect(worker.run)
        thread.start()

    def _test_worker_stopped(self) -> None:
        self._test_worker = None
        self._test_thread = None

    def closeEvent(self, event: QCloseEvent | None) -> None:
        if event is None:
            return
        if self._test_thread and self._test_thread.isRunning():
            event.ignore()
            return
        super().closeEvent(event)

    def reject(self) -> None:
        if self._test_thread and self._test_thread.isRunning():
            return
        super().reject()

    def _test_finished(self, success: bool, message: str) -> None:
        self.test_button.setEnabled(True)
        icon = QMessageBox.Icon.Information if success else QMessageBox.Icon.Warning
        QMessageBox(icon, "ThunderWatch test email", message, QMessageBox.StandardButton.Ok).exec()

    def _run_wizard(self) -> None:
        if self.scheduler:
            self.scheduler.pause_and_wait()
        try:
            wizard = SetupWizard(self.settings)
            if wizard.exec() == QDialog.DialogCode.Accepted:
                self.accept()
        finally:
            if self.scheduler:
                self.scheduler.resume()

    @pyqtSlot(str)
    def _portal_request_failed(self, message: str) -> None:
        if not self._portal_waiting:
            return
        self._portal_waiting = False
        self.save_button.setEnabled(True)
        self.startup_notice.setText(f"Settings were saved, but automatic startup failed: {message}")
        self.tabs.setCurrentWidget(self.startup_tab)

    @pyqtSlot()
    def _portal_request_completed(self) -> None:
        if self._portal_waiting:
            self._portal_waiting = False
            self.accept()

    def _save(self) -> None:
        if self._test_thread and self._test_thread.isRunning():
            QMessageBox.warning(self, "Test still running", "Wait for the email test to finish.")
            return
        config = Config(
            setup_complete=True,
            smtp_host=self.host.text().strip(),
            smtp_port=self.port.value(),
            smtp_security=self.security.currentText(),
            smtp_username=self.username.text().strip(),
            smtp_from=self.sender_edit.text().strip(),
            smtp_recipient=self.recipient.text().strip(),
            location=self.location.text().strip(),
            interval_minutes=self.interval.value(),
            ipv6=self.ipv6.isChecked(),
            autostart=self.autostart.isChecked(),
            automatic_updates=self.automatic.isChecked(),
            include_beta=self.beta.isChecked(),
        )
        if not config.complete:
            QMessageBox.warning(
                self,
                "Invalid settings",
                "Check the SMTP fields, email addresses, security, and interval.",
            )
            return
        previous = Config.from_store(self.settings)
        if smtp_identity_changed(previous, config) and not self.password.text():
            QMessageBox.warning(
                self,
                "Password required",
                "Enter the password for the new SMTP account.",
            )
            return
        if self.password.text():
            try:
                store_password(
                    config.smtp_username,
                    config.smtp_host,
                    self.password.text(),
                    password_path(),
                    self.allow_file.isChecked(),
                )
            except Exception as exc:
                logger.exception("Could not save SMTP password from settings")
                QMessageBox.critical(self, "Password not saved", str(exc))
                return
        for key, value in {
            "setup/complete": True,
            "smtp/host": config.smtp_host,
            "smtp/port": config.smtp_port,
            "smtp/security": config.smtp_security,
            "smtp/username": config.smtp_username,
            "smtp/from": config.smtp_from,
            "smtp/recipient": config.smtp_recipient,
            "monitor/location": config.location,
            "monitor/interval_minutes": config.interval_minutes,
            "monitor/ipv6": config.ipv6,
            "startup/autostart": config.autostart,
            "updates/automatic": config.automatic_updates,
            "updates/include_beta": config.include_beta,
        }.items():
            self.settings.setValue(key, value)
        self.settings_saved = True
        wait_for_portal = bool(os.environ.get("FLATPAK_ID") and self.portal_client)
        self._portal_waiting = wait_for_portal
        if wait_for_portal:
            self.save_button.setEnabled(False)
            self.startup_notice.setText("Waiting for desktop startup permission…")
        try:
            set_autostart(config.autostart, portal_client=self.portal_client)
        except Exception as exc:
            logger.exception("Could not configure automatic startup from settings")
            self._portal_waiting = False
            self.save_button.setEnabled(True)
            self.startup_notice.setText(str(exc))
            self.tabs.setCurrentWidget(self.startup_tab)
            return
        if not wait_for_portal:
            self.accept()
