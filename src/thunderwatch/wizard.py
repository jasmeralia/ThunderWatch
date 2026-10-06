"""First-run setup wizard."""

from __future__ import annotations

import socket

from PyQt6.QtCore import QSettings, QThread
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QWizard,
    QWizardPage,
)

from .config import Config
from .monitor import _stamp
from .paths import password_path, state_path
from .secrets import store_password
from .state import read_state, write_state
from .worker import TestEmailWorker


class SetupWizard(QWizard):
    def __init__(self, settings: QSettings):  # noqa: PLR0915
        super().__init__()
        self.settings = settings
        self.setWindowTitle("ThunderWatch setup")
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)
        self.addPage(
            self._page(
                "Welcome",
                "ThunderWatch checks your public IP over HTTPS and emails confirmed changes.",
            )
        )
        email = self._page(
            "Email server",
            "Configure SMTP. Your password is stored in the OS keyring when available.",
        )
        form = QFormLayout(email)
        self.host = QLineEdit()
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(587)
        self.security = QComboBox()
        self.security.addItems(["auto", "ssl", "starttls"])
        self.username = QLineEdit()
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.sender_edit = QLineEdit()
        self.preset = QComboBox()
        self.preset.addItems(["Custom", "Gmail", "Outlook.com", "Fastmail"])
        self.preset.currentTextChanged.connect(self._preset)
        for title, widget in (
            ("Provider preset", self.preset),
            ("SMTP host", self.host),
            ("Port", self.port),
            ("Security", self.security),
            ("Username", self.username),
            ("Password", self.password),
            ("From address", self.sender_edit),
        ):
            form.addRow(title, widget)
        self.allow_file = QCheckBox("Store password in a file only my account can read")
        form.addRow(self.allow_file)
        recipient = self._page(
            "Recipient and location",
            "Use one recipient address. Mailing list aliases can reach multiple people.",
        )
        form = QFormLayout(recipient)
        self.recipient = QLineEdit()
        self.location = QLineEdit(socket.gethostname())
        form.addRow("Recipient", self.recipient)
        form.addRow("Location label", self.location)
        monitor = self._page(
            "Monitoring and startup", "Configure monitoring and update preferences."
        )
        form = QFormLayout(monitor)
        self.interval = QSpinBox()
        self.interval.setRange(5, 720)
        self.interval.setValue(10)
        self.ipv6 = QCheckBox("Also monitor the IPv6 prefix")
        self.ipv6.setChecked(True)
        self.autostart = QCheckBox("Start ThunderWatch when I sign in")
        self.autostart.setChecked(True)
        self.beta = QCheckBox("Include beta updates")
        form.addRow("Interval (minutes)", self.interval)
        form.addRow(self.ipv6)
        form.addRow(self.autostart)
        form.addRow(self.beta)
        finish = self._page(
            "Test and finish",
            "Check IP Now after setup to test reachability. Save without a test when offline.",
        )
        self.save_offline = QCheckBox("Save without a successful test")
        self.save_offline.setChecked(False)
        self.test_button = QPushButton("Send Test Email")
        self.test_status = QLabel("No test email sent yet.")
        self.test_succeeded = False
        self.test_addresses: dict[str, str] = {}
        self._test_thread: QThread | None = None
        self._test_worker: TestEmailWorker | None = None
        self.save_offline.toggled.connect(self._update_finish_button)
        self.currentIdChanged.connect(self._update_finish_button)
        self.test_button.clicked.connect(self.send_test_email)
        finish_form = QFormLayout(finish)
        finish_form.addRow(QLabel("IPv4 and IPv6 will be checked after setup."))
        finish_form.addRow(self.test_button)
        finish_form.addRow(self.test_status)
        finish_form.addRow(self.save_offline)
        for page in (email, recipient, monitor, finish):
            self.addPage(page)
        self.host.setText(str(settings.value("smtp/host", "")))
        self.port.setValue(int(settings.value("smtp/port", 587)))
        self.username.setText(str(settings.value("smtp/username", "")))
        self.sender_edit.setText(str(settings.value("smtp/from", "")))
        self.recipient.setText(str(settings.value("smtp/recipient", "")))
        self.location.setText(str(settings.value("monitor/location", self.location.text())))
        self._update_finish_button()

    def _update_finish_button(self, *_args: object) -> None:
        button = self.button(QWizard.WizardButton.FinishButton)
        if button:
            button.setEnabled(self.test_succeeded or self.save_offline.isChecked())

    def validateCurrentPage(self) -> bool:
        if self.currentId() == 4 and not (self.test_succeeded or self.save_offline.isChecked()):
            self.test_status.setText("Send a successful test email or choose to save offline.")
            return False
        return True

    def send_test_email(self) -> None:
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
        if not config.complete or not self.password.text():
            self.test_status.setText("Complete the SMTP fields and password before sending a test.")
            return
        thread = QThread(self)
        worker = TestEmailWorker(config, password_override=self.password.text())
        worker.moveToThread(thread)
        worker.finished.connect(self._test_finished)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        self._test_worker = worker
        self._test_thread = thread
        self.test_button.setEnabled(False)
        self.test_status.setText("Looking up addresses and sending the test email…")
        thread.started.connect(worker.run)
        thread.start()

    def _test_finished(self, success: bool, message: str) -> None:
        if success and self._test_worker:
            self.test_succeeded = True
            self.test_addresses = self._test_worker.current_addresses.copy()
        self.test_status.setText(message)
        self.test_button.setEnabled(True)
        self._update_finish_button()

    def reject(self) -> None:
        if not self.settings.value("setup/complete", False):
            answer = QMessageBox.question(
                self,
                "Quit setup?",
                "ThunderWatch can't monitor without email settings. Quit?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        super().reject()

    @staticmethod
    def _page(title: str, subtitle: str) -> QWizardPage:
        page = QWizardPage()
        page.setTitle(title)
        page.setSubTitle(subtitle)
        return page

    def _preset(self, name: str) -> None:
        presets = {
            "Gmail": ("smtp.gmail.com", 465, "ssl"),
            "Outlook.com": ("smtp-mail.outlook.com", 587, "starttls"),
            "Fastmail": ("smtp.fastmail.com", 465, "ssl"),
        }
        if name in presets:
            host, port, security = presets[name]
            self.host.setText(host)
            self.port.setValue(port)
            self.security.setCurrentText(security)

    def accept(self) -> None:
        if not all(
            x.text().strip() for x in (self.host, self.username, self.sender_edit, self.recipient)
        ):
            QMessageBox.warning(
                self,
                "Missing settings",
                "Enter the SMTP host, username, From address, and one recipient.",
            )
            return
        if not self.password.text() and not self.settings.value("setup/complete", False):
            QMessageBox.warning(self, "Password required", "Enter the SMTP password.")
            return
        values = {
            "setup/complete": True,
            "smtp/host": self.host.text().strip(),
            "smtp/port": self.port.value(),
            "smtp/security": self.security.currentText(),
            "smtp/username": self.username.text().strip(),
            "smtp/from": self.sender_edit.text().strip(),
            "smtp/recipient": self.recipient.text().strip(),
            "monitor/location": self.location.text().strip(),
            "monitor/interval_minutes": self.interval.value(),
            "monitor/ipv6": self.ipv6.isChecked(),
            "startup/autostart": self.autostart.isChecked(),
            "updates/include_beta": self.beta.isChecked(),
        }
        if self.password.text():
            try:
                store_password(
                    self.username.text().strip(),
                    self.host.text().strip(),
                    self.password.text(),
                    password_path(),
                    self.allow_file.isChecked(),
                )
            except Exception as exc:
                QMessageBox.critical(self, "Password not saved", str(exc))
                return
        for key, value in values.items():
            self.settings.setValue(key, value)
        if self.test_succeeded and self.test_addresses:
            state = read_state(state_path())
            timestamp = _stamp(__import__("datetime").datetime.now().astimezone())
            for family, value in self.test_addresses.items():
                state.setdefault("last_reported", {})[family] = {"value": value, "time": timestamp}
            write_state(state_path(), state)
        super().accept()
