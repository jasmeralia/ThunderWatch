"""ThunderWatch desktop application entry point."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable
from typing import Any

from PyQt6.QtCore import QSettings, QThread, QTimer, QUrl
from PyQt6.QtGui import QAction, QDesktopServices, QIcon
from PyQt6.QtNetwork import QLocalServer, QLocalSocket
from PyQt6.QtWidgets import (
    QApplication,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSystemTrayIcon,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .autostart import set_autostart
from .config import Config
from .logging_setup import configure_logging
from .paths import app_data, resource_path, state_path
from .scheduler import Scheduler
from .settings_dialog import SettingsDialog
from .state import read_state
from .theme import apply_theme
from .tray import badge_icon
from .update_dialog import UpdateCheckWorker, UpdateDialog
from .updater import UpdateOffer
from .wizard import SetupWizard
from .worker import TestEmailWorker

ORG, APP = "WindsOfStorm", "ThunderWatch"
_APP_HOLDER: dict[str, QApplication | None] = {"instance": None}


def should_setup(settings: Any) -> bool:
    return not Config.from_store(settings).complete


def tray_wait_state(available: bool, elapsed_seconds: int) -> str:
    if available:
        return "ready"
    return "fallback" if elapsed_seconds >= 60 else "wait"


def pending_balloon_transition(was_pending: bool, is_pending: bool) -> bool:
    return is_pending and not was_pending


def schedule_automatic_update(timer: QTimer, enabled: bool, delay_ms: int = 30_000) -> None:
    if not enabled:
        timer.stop()
    elif not timer.isActive():
        timer.start(delay_ms)


def claim_local_server(
    server: QLocalServer,
    name: str,
    notify_existing: Callable[[str], bool],
    remove_stale: Callable[[str], bool],
) -> bool:
    """Claim the local-server name, cleaning stale sockets without duplicating a live app."""
    if server.listen(name):
        return True
    if notify_existing(name):
        return False
    remove_stale(name)
    return server.listen(name)


def notify_running_instance(name: str) -> bool:
    socket = QLocalSocket()
    socket.connectToServer(name)
    if not socket.waitForConnected(500):
        return False
    socket.write(b"show")
    socket.flush()
    return True


class StatusWindow(QMainWindow):
    def __init__(
        self,
        scheduler: Scheduler,
        on_test: Callable[[], None] | None = None,
        on_settings: Callable[[], None] | None = None,
    ) -> None:
        super().__init__()
        self.scheduler = scheduler
        self.tray_fallback = False
        self.notice = ""
        self.setWindowTitle("ThunderWatch status")
        layout = QVBoxLayout()
        self.summary = QLabel("Waiting for first check…")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.history = QTableWidget(0, 3)
        self.history.setHorizontalHeaderLabels(["Time", "Event", "Details"])
        header = self.history.horizontalHeader()
        if header:
            header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.history)
        check = QPushButton("Check IP Now")
        check.clicked.connect(scheduler.run)
        layout.addWidget(check)
        test_email = QPushButton("Send Test Email")
        if on_test:
            test_email.clicked.connect(on_test)
        layout.addWidget(test_email)
        settings = QPushButton("Settings")
        if on_settings:
            settings.clicked.connect(on_settings)
        layout.addWidget(settings)
        copy = QPushButton("Copy Current IP")
        copy.clicked.connect(self.copy_ip)
        layout.addWidget(copy)
        logs = QPushButton("Open Log Folder")
        logs.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(app_data() / "logs")))
        )
        layout.addWidget(logs)
        widget = QWidget()
        widget.setLayout(layout)
        self.setCentralWidget(widget)
        scheduler.state_changed.connect(self.update_state)
        self.update_state(read_state(state_path()), [])

    def copy_ip(self) -> None:
        values = [
            item.get("value", "")
            for item in read_state(state_path()).get("last_observed", {}).values()
        ]
        clipboard = QApplication.clipboard()
        if clipboard:
            clipboard.setText("\n".join(values))

    def update_state(self, state: dict[str, Any], actions: list[dict[str, Any]]) -> None:
        observed = state.get("last_observed", {})
        reported = state.get("last_reported", {})
        last_check = state.get("last_check", {})
        lines = []
        for family in ("ipv4", "ipv6"):
            if family == "ipv6" and not self.scheduler.config.ipv6:
                continue
            current = observed.get(family)
            if current:
                detail = f"{family.upper()}: {current.get('value', 'not available')}"
                lines.append(f"{detail} · {current.get('time', '')}")
            elif family == "ipv6":
                previous = state.get("last_observed", {}).get(family, {}).get("time", "never")
                lines.append(f"IPv6: not available · last seen {previous}")
            else:
                lines.append("IPv4: not available")
        lines.append(
            f"Last check: {last_check.get('time', 'never')} · {last_check.get('result', 'not run')}"
        )
        for family, value in reported.items():
            reported_text = f"Last reported {family.upper()}: {value.get('value', '')}"
            lines.append(f"{reported_text} · {value.get('time', '')}")
        pending = state.get("pending", {})
        if pending:
            changes = pending.get("changes", {})
            lines.append(
                "Pending email: "
                + "; ".join(
                    f"{family.upper()} {value.get('old') or 'new'} -> {value.get('new')}"
                    for family, value in changes.items()
                )
            )
            if pending.get("last_error"):
                lines.append(f"SMTP error: {pending['last_error']}")
        remaining = self.scheduler.timer.remainingTime()
        next_check = (
            f"in {max(remaining, 0) // 1000}s"
            if self.scheduler.timer.isActive()
            else "after current check"
        )
        lines.append(f"Next check: {next_check}")
        if self.notice:
            lines.insert(0, self.notice)
        self.summary.setText("\n".join(lines))
        history = state.get("history", [])[-50:]
        self.history.setRowCount(len(history))
        for row, event in enumerate(history):
            self.history.setItem(row, 0, QTableWidgetItem(str(event.get("time", ""))))
            self.history.setItem(row, 1, QTableWidgetItem(str(event.get("type", "event"))))
            self.history.setItem(
                row, 2, QTableWidgetItem(str(event.get("error", event.get("changes", ""))))
            )

    def closeEvent(self, event: Any) -> None:
        if self.tray_fallback:
            event.ignore()
            self.showMinimized()
        elif QSystemTrayIcon.isSystemTrayAvailable():
            event.ignore()
            self.hide()
        else:
            event.accept()


def build_smoke_objects(app: QApplication) -> tuple[Scheduler, StatusWindow, QSystemTrayIcon]:
    """Construct the desktop shell with synthetic configuration and no I/O."""
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
    tray = QSystemTrayIcon(QIcon(str(resource_path("icons/thunderwatch.png"))), app)
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
    tray.setContextMenu(menu)
    tray.setToolTip("ThunderWatch - Example Home")
    return scheduler, window, tray


class ThunderWatchApp:
    def __init__(self, app: QApplication, show: bool) -> None:  # noqa: PLR0915
        self.app = app
        self.settings = QSettings(ORG, APP)
        self.scheduler: Scheduler | None = None
        self.tray: QSystemTrayIcon | None = None
        self.window: StatusWindow | None = None
        self._test_worker: TestEmailWorker | None = None
        self._test_thread: QThread | None = None
        self._update_worker: UpdateCheckWorker | None = None
        self._update_thread: QThread | None = None
        self._update_manual = False
        self._pending_alerted = False
        self._tray_elapsed = 0
        self._tray_timer = QTimer()
        self._tray_timer.setInterval(1000)
        self._tray_timer.timeout.connect(self._poll_tray)
        self._automatic_update_timer = QTimer()
        self._automatic_update_timer.setSingleShot(True)
        self._automatic_update_timer.timeout.connect(self._automatic_update_check)
        self.server = QLocalServer()
        name = f"thunderwatch-{os.getuid() if hasattr(os, 'getuid') else 'user'}"
        if not claim_local_server(
            self.server, name, notify_running_instance, QLocalServer.removeServer
        ):
            QTimer.singleShot(0, app.quit)
            return
        self.server.newConnection.connect(self.show_status)
        if should_setup(self.settings):
            wizard = SetupWizard(self.settings)
            if wizard.exec() != wizard.DialogCode.Accepted:
                QTimer.singleShot(0, app.quit)
                return
        config = Config.from_store(self.settings)
        self.scheduler = Scheduler(config)
        try:
            set_autostart(config.autostart)
        except Exception as exc:
            self._autostart_error = str(exc)
        icon = resource_path("icons/thunderwatch.png")
        self.tray = QSystemTrayIcon(QIcon(str(icon)), app)
        menu = QMenu()
        for label, callback in (
            ("About", self.about),
            ("Check for Updates", lambda *_args: self.check_for_updates(True)),
            ("Check IP Now", self.scheduler.run),
            ("Copy Current IP", self.copy_ip),
            ("Send Test Email", self.send_test_email),
            ("Settings…", self.open_settings),
            ("Show Status", self.show_status),
        ):
            action = QAction(label, self.tray)
            action.triggered.connect(callback)
            menu.addAction(action)
        menu.addSeparator()
        quit_action = QAction("Quit", self.tray)
        quit_action.triggered.connect(app.quit)
        menu.addAction(quit_action)
        self.tray.setContextMenu(menu)
        self.tray.setToolTip(f"ThunderWatch - {config.location}")
        self.tray.activated.connect(
            lambda reason: (
                self.show_status()
                if reason == QSystemTrayIcon.ActivationReason.DoubleClick
                else None
            )
        )
        self.tray.show()
        if getattr(self, "_autostart_error", ""):
            self.tray.showMessage(
                "ThunderWatch startup registration",
                self._autostart_error,
                QSystemTrayIcon.MessageIcon.Warning,
            )
        self.window = StatusWindow(self.scheduler, self.send_test_email, self.open_settings)
        self.scheduler.state_changed.connect(self.refresh_tray)
        self._automatic_updates = config.automatic_updates
        if show:
            QTimer.singleShot(0, self.show_status)
        self._tray_timer.start()
        self._poll_tray()
        schedule_automatic_update(self._automatic_update_timer, config.automatic_updates)

    def _poll_tray(self) -> None:
        state = tray_wait_state(QSystemTrayIcon.isSystemTrayAvailable(), self._tray_elapsed)
        if state == "ready":
            self._tray_timer.stop()
        elif state == "fallback":
            self._tray_timer.stop()
            if self.window:
                self.window.tray_fallback = True
                self.window.notice = (
                    "No system tray was found. This window will stand in for the tray; "
                    "closing it minimizes ThunderWatch."
                )
                self.window.update_state(read_state(state_path()), [])
                self.show_status()
        else:
            self._tray_elapsed += 1

    def show_status(self, *args: object) -> None:
        if self.server.hasPendingConnections():
            connection = self.server.nextPendingConnection()
            if connection:
                connection.readAll()
        if self.window:
            self.window.show()
            self.window.raise_()
            self.window.activateWindow()

    def copy_ip(self, *args: object) -> None:
        if self.window:
            self.window.copy_ip()

    def send_test_email(self, *args: object) -> None:
        if self._test_thread and self._test_thread.isRunning():
            return
        config = Config.from_store(self.settings)
        thread = QThread()
        worker = TestEmailWorker(config)
        worker.moveToThread(thread)
        worker.finished.connect(self._test_email_done)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._test_worker_stopped)
        self._test_worker = worker
        self._test_thread = thread
        thread.started.connect(worker.run)
        thread.start()

    def _test_worker_stopped(self) -> None:
        self._test_worker = None
        self._test_thread = None

    def _test_email_done(self, success: bool, message: str) -> None:
        icon = QMessageBox.Icon.Information if success else QMessageBox.Icon.Warning
        QMessageBox(icon, "ThunderWatch test email", message, QMessageBox.StandardButton.Ok).exec()

    def _automatic_update_check(self) -> None:
        if self._automatic_updates:
            self.check_for_updates(False)
            schedule_automatic_update(
                self._automatic_update_timer,
                True,
                24 * 60 * 60 * 1000,
            )

    def check_for_updates(self, manual: bool = True) -> None:
        if self._update_thread and self._update_thread.isRunning():
            self._update_manual = self._update_manual or manual
            return
        self._update_manual = manual
        worker = UpdateCheckWorker(Config.from_store(self.settings))
        thread = QThread()
        worker.moveToThread(thread)
        worker.finished.connect(self._update_check_finished)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._update_worker_stopped)
        self._update_worker = worker
        self._update_thread = thread
        thread.started.connect(worker.run)
        thread.start()

    def _update_worker_stopped(self) -> None:
        self._update_worker = None
        self._update_thread = None

    def _update_check_finished(self, offer: object, error: str) -> None:
        if error:
            if self._update_manual:
                QMessageBox.warning(None, "Update check failed", error)
            return
        if isinstance(offer, UpdateOffer):
            if not self._update_manual and self.tray:
                self.tray.showMessage(
                    "ThunderWatch update available",
                    f"Version {offer.version} is ready to review.",
                    QSystemTrayIcon.MessageIcon.Information,
                )
            dialog = UpdateDialog(offer, self.app.quit)
            dialog.show()
            self._update_dialog = dialog
        elif self._update_manual:
            QMessageBox.information(None, "ThunderWatch", "No compatible update is available.")

    def refresh_tray(self, state: dict[str, Any], actions: list[dict[str, Any]]) -> None:
        if self.tray:
            result = state.get("last_check", {}).get("result", "unknown")
            location = Config.from_store(self.settings).location
            addresses = state.get("last_observed", {})
            address_text = " · ".join(
                f"{family.upper()}: {details.get('value', 'not available')}"
                for family, details in addresses.items()
            )
            checked_at = state.get("last_check", {}).get("time", "Never")
            self.tray.setToolTip(
                f"ThunderWatch - {location}\n{address_text}\nLast check: {checked_at} ({result})"
            )
            pending = bool(state.get("pending"))
            icon_state = (
                "danger"
                if pending
                else "warning"
                if state.get("ipv4_failure_streak", 0) >= 3
                else "healthy"
            )
            self.tray.setIcon(badge_icon(None, icon_state))
            if pending_balloon_transition(self._pending_alerted, pending):
                self.tray.showMessage(
                    "ThunderWatch",
                    "Email delivery is failing",
                    QSystemTrayIcon.MessageIcon.Critical,
                )
            self._pending_alerted = pending

    def about(self, *args: object) -> None:
        QMessageBox.information(
            None,
            "About ThunderWatch",
            "ThunderWatch watches public IP addresses and emails confirmed changes.",
        )

    def open_settings(self, *args: object) -> None:
        dialog = SettingsDialog(self.settings)
        if dialog.exec() == SettingsDialog.DialogCode.Accepted:
            config = Config.from_store(self.settings)
            if self.scheduler:
                self.scheduler.reconfigure(config)
            self._automatic_updates = config.automatic_updates
            schedule_automatic_update(self._automatic_update_timer, config.automatic_updates)
            if self.tray:
                self.tray.setToolTip(f"ThunderWatch - {config.location}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--autostart", action="store_true")
    parser.add_argument("--smoke-test", action="store_true")
    parser.add_argument("--show", action="store_true")
    args = parser.parse_args()
    app = QApplication(sys.argv[:1])
    _APP_HOLDER["instance"] = app
    app.setOrganizationName(ORG)
    app.setApplicationName(APP)
    app.setQuitOnLastWindowClosed(False)
    apply_theme(app)
    if args.smoke_test:
        scheduler, window, tray = build_smoke_objects(app)
        scheduler.timer.stop()
        window.close()
        tray.hide()
        return 0
    configure_logging()
    name = f"thunderwatch-{os.getuid() if hasattr(os, 'getuid') else 'user'}"
    if notify_running_instance(name):
        return 0
    ThunderWatchApp(app, args.show)
    return app.exec()
