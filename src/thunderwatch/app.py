"""ThunderWatch desktop application entry point."""

from __future__ import annotations

import argparse
import getpass
import hashlib
import logging
import os
import sys
from collections.abc import Callable, Mapping
from typing import Any

from PyQt6.QtCore import QEvent, QObject, QSettings, QThread, QTimer, QUrl
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

from . import __version__
from .autostart import QtBackgroundPortal, set_autostart
from .config import Config
from .error_handling import (
    enable_fault_handler,
    install_qt_message_handler,
    install_sys_hook,
    install_thread_hook,
    install_unraisable_hook,
    snapshot_previous_fatal_log,
)
from .logging_setup import active_log_directory, configure_logging
from .paths import resource_path, state_path
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
logger = logging.getLogger(__name__)


class ExceptionLoggingApplication(QApplication):
    """Forward exceptions raised inside Qt event handlers to the process hook."""

    def notify(self, receiver: QObject | None, event: QEvent | None) -> bool:
        try:
            return super().notify(receiver, event)
        except Exception as exc:
            sys.excepthook(type(exc), exc, exc.__traceback__)
            return False


def should_setup(settings: Any) -> bool:
    return not Config.from_store(settings).complete


def instance_server_name(
    platform: str = sys.platform,
    *,
    uid: int | None = None,
    environment: Mapping[str, str] | None = None,
) -> str:
    """Build a stable local-server name that does not collide across Windows accounts."""
    if platform != "win32":
        return f"thunderwatch-{os.getuid() if uid is None else uid}"
    values = os.environ if environment is None else environment
    identity = f"{values.get('USERDOMAIN', '')}\\{values.get('USERNAME', '')}".casefold()
    if identity == "\\":
        identity = getpass.getuser().casefold()
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
    return f"thunderwatch-{digest}"


def tray_wait_state(available: bool, elapsed_seconds: int) -> str:
    if available:
        return "ready"
    return "fallback" if elapsed_seconds >= 60 else "wait"


def pending_balloon_transition(was_pending: bool, is_pending: bool) -> bool:
    return is_pending and not was_pending


def current_addresses(state: dict[str, Any], ipv6_enabled: bool = True) -> dict[str, str]:
    """Return only addresses confirmed by the latest check for enabled families."""
    observed = state.get("last_observed", {})
    last_check = state.get("last_check", {})
    result: dict[str, str] = {}
    for family in ("ipv4", "ipv6"):
        if family == "ipv6" and not ipv6_enabled:
            continue
        status = last_check.get("families", {}).get(family)
        if status is None:
            status = (
                "success"
                if last_check.get("result") == "success"
                or last_check.get("providers", {}).get(family)
                else "failure"
            )
        details = observed.get(family)
        if details and status == "success" and details.get("value"):
            result[family] = details["value"]
    return result


def change_balloon_message(actions: list[dict[str, Any]]) -> str | None:
    if not any(action.get("type") == "delivered" for action in actions):
        return None
    changes: dict[str, Any] = next(
        (action.get("changes", {}) for action in actions if action.get("type") == "send_change"),
        {},
    )
    if not changes:
        return None
    family_names = {"ipv4": "IPv4", "ipv6": "IPv6 prefix"}
    return "; ".join(
        f"{family_names.get(family, family.upper())} changed to {change.get('new', '')}"
        for family, change in changes.items()
    )


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
        logs.clicked.connect(self.open_log_folder)
        layout.addWidget(logs)
        widget = QWidget()
        widget.setLayout(layout)
        self.setCentralWidget(widget)
        scheduler.state_changed.connect(self.update_state)
        self.update_state(read_state(state_path()), [])

    def open_log_folder(self) -> None:
        log_dir = active_log_directory()
        if log_dir is None:
            QMessageBox.information(
                self,
                "Log files unavailable",
                "ThunderWatch could not create a log file. Diagnostic messages were "
                "sent to stderr.",
            )
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(log_dir)))

    def copy_ip(self) -> None:
        values = current_addresses(read_state(state_path()), self.scheduler.config.ipv6)
        clipboard = QApplication.clipboard()
        if clipboard:
            clipboard.setText("\n".join(values.values()))

    def update_state(self, state: dict[str, Any], actions: list[dict[str, Any]]) -> None:
        observed = state.get("last_observed", {})
        reported = state.get("last_reported", {})
        last_check = state.get("last_check", {})
        lines = []
        for family in ("ipv4", "ipv6"):
            if family == "ipv6" and not self.scheduler.config.ipv6:
                continue
            current = observed.get(family)
            family_status = last_check.get("families", {}).get(family)
            if family_status is None:
                family_status = (
                    "success"
                    if last_check.get("result") == "success"
                    or last_check.get("providers", {}).get(family)
                    else "failure"
                )
            if current and family_status == "success":
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
    def __init__(  # noqa: PLR0915
        self, app: QApplication, show: bool, server: QLocalServer | None = None
    ) -> None:
        self.app = app
        self.settings = QSettings(ORG, APP)
        self.scheduler: Scheduler | None = None
        self.tray: QSystemTrayIcon | None = None
        self.window: StatusWindow | None = None
        self._test_worker: TestEmailWorker | None = None
        self._test_thread: QThread | None = None
        self._update_worker: UpdateCheckWorker | None = None
        self._update_thread: QThread | None = None
        self._update_dialog: UpdateDialog | None = None
        self._update_manual = False
        self._pending_alerted = False
        self._tray_elapsed = 0
        self._tray_timer = QTimer()
        self._tray_timer.setInterval(1000)
        self._tray_timer.timeout.connect(self._poll_tray)
        self._automatic_update_timer = QTimer()
        self._automatic_update_timer.setSingleShot(True)
        self._automatic_update_timer.timeout.connect(self._automatic_update_check)
        self.server = server if server is not None else QLocalServer()
        self.app.aboutToQuit.connect(self._wait_for_workers)
        if server is None:
            name = instance_server_name()
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
        self._background_portal = QtBackgroundPortal() if os.environ.get("FLATPAK_ID") else None
        if self._background_portal:
            self._background_portal.request_failed.connect(self._background_portal_failed)
        try:
            set_autostart(config.autostart, portal_client=self._background_portal)
        except Exception as exc:
            logger.exception("Could not configure automatic startup")
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
        self.tray.messageClicked.connect(self.show_status)
        self.refresh_tray(read_state(state_path()), [])
        self._automatic_updates = config.automatic_updates
        if show:
            QTimer.singleShot(0, self.show_status)
        self._tray_timer.start()
        self._poll_tray()
        schedule_automatic_update(self._automatic_update_timer, config.automatic_updates)

    def _background_portal_failed(self, message: str) -> None:
        self._autostart_error = message
        logger.warning("Automatic startup was not registered: %s", message)
        if self.tray:
            self.tray.showMessage(
                "ThunderWatch startup registration", message, QSystemTrayIcon.MessageIcon.Warning
            )

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
        thread = QThread(self.app)
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
        thread = QThread(self.app)
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

    def _wait_for_workers(self) -> None:
        threads = [self._test_thread, self._update_thread]
        if self.scheduler and self.scheduler._worker_thread:
            threads.append(self.scheduler._worker_thread)
        if self._update_dialog and self._update_dialog.worker:
            threads.append(self._update_dialog.worker)
        for thread in threads:
            if thread and thread.isRunning():
                thread.requestInterruption()
                thread.quit()
                thread.wait()

    def _update_check_finished(self, offer: object, error: str) -> None:
        if error:
            if self._update_manual:
                QMessageBox.warning(None, "Update check failed", error)
            return
        if isinstance(offer, UpdateOffer):
            if self._update_dialog and self._update_dialog.isVisible():
                self._update_dialog.raise_()
                self._update_dialog.activateWindow()
                return
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
            config = Config.from_store(self.settings)
            location = config.location
            addresses = current_addresses(state, config.ipv6)
            address_text = " · ".join(
                f"{family.upper()}: {value}" for family, value in addresses.items()
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
            if message := change_balloon_message(actions):
                self.tray.showMessage(
                    "ThunderWatch", message, QSystemTrayIcon.MessageIcon.Information
                )
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
        dialog = SettingsDialog(self.settings, self.scheduler)
        accepted = dialog.exec() == SettingsDialog.DialogCode.Accepted
        if accepted or dialog.settings_saved:
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
    app = ExceptionLoggingApplication(sys.argv[:1])
    _APP_HOLDER["instance"] = app
    app.setOrganizationName(ORG)
    app.setApplicationName(APP)
    app.setQuitOnLastWindowClosed(False)
    apply_theme(app)
    if args.smoke_test:
        _setup_crash_diagnostics()
        scheduler, window, tray = build_smoke_objects(app)
        scheduler.timer.stop()
        window.close()
        tray.hide()
        return 0
    server = QLocalServer()
    if not claim_local_server(
        server,
        instance_server_name(),
        notify_running_instance,
        QLocalServer.removeServer,
    ):
        return 0
    _setup_crash_diagnostics()
    ThunderWatchApp(app, args.show, server)
    return app.exec()


def _setup_crash_diagnostics() -> None:
    log_dir = configure_logging()
    install_sys_hook()
    install_thread_hook()
    install_unraisable_hook()
    install_qt_message_handler()
    snapshot_previous_fatal_log(log_dir)
    enable_fault_handler(log_dir)
    logger.info(
        "ThunderWatch starting: version=%s platform=%s Python=%s",
        __version__,
        sys.platform,
        sys.version.split()[0],
    )
