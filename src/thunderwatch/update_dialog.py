"""Explicit update offer and verified-download dialog."""

from __future__ import annotations

import os
import platform
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from PyQt6.QtCore import QObject, QProcess, QThread, QUrl, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout

from .config import Config
from .paths import app_data
from .updater import (
    UpdateOffer,
    check_for_update,
    detect_package_type,
    fetch_release_feed,
    installed_version,
    verify_download,
    write_appimage_update_helper,
)


class UpdateDownloadWorker(QThread):
    completed = pyqtSignal(bool, str, object)

    def __init__(self, offer: UpdateOffer, destination: Path) -> None:
        super().__init__()
        self.offer = offer
        self.destination = destination

    def run(self) -> None:
        try:
            path = verify_download(
                self.offer.asset.url,
                self.offer.asset.size,
                self.offer.asset.sha256,
                self.destination,
            )
            self.completed.emit(True, "Verified update download.", path)
        except Exception as exc:
            self.completed.emit(False, str(exc) or type(exc).__name__, None)


class UpdateCheckWorker(QObject):
    """Fetch and select a compatible release off the GUI thread."""

    finished = pyqtSignal(object, str)

    def __init__(self, config: Config) -> None:
        super().__init__()
        self.config = config

    @pyqtSlot()
    def run(self) -> None:
        try:
            package = detect_package_type()
            if package is None:
                self.finished.emit(None, "Update checks require an installed package.")
                return
            system = "windows" if sys.platform == "win32" else "linux"
            offer = check_for_update(
                installed_version(),
                fetch_release_feed(),
                system,
                platform.machine(),
                package,
                self.config.include_beta,
            )
            self.finished.emit(offer, "")
        except Exception as exc:
            self.finished.emit(None, str(exc) or type(exc).__name__)


class UpdateDialog(QDialog):
    def __init__(self, offer: UpdateOffer, on_installed: Callable[[], None] | None = None) -> None:
        super().__init__()
        self.offer = offer
        self.on_installed = on_installed
        self.worker: UpdateDownloadWorker | None = None
        self.setWindowTitle("ThunderWatch update available")
        layout = QVBoxLayout(self)
        self.channel_label = QLabel(
            f"Version {offer.version} · {'Beta' if offer.is_beta else 'Stable'}"
        )
        self.notes = QPlainTextEdit()
        self.notes.setReadOnly(True)
        self.notes.setPlainText(offer.notes)
        self.size_label = QLabel(f"Download size: {offer.asset.size:,} bytes")
        self.status_label = QLabel("Review the release notes before downloading.")
        controls = QHBoxLayout()
        self.download_button = QPushButton("Download and Update")
        close = QPushButton("Close")
        controls.addWidget(self.download_button)
        controls.addWidget(close)
        layout.addWidget(self.channel_label)
        layout.addWidget(self.size_label)
        layout.addWidget(self.notes)
        layout.addWidget(self.status_label)
        layout.addLayout(controls)
        self.download_button.clicked.connect(self.download)
        close.clicked.connect(self.close)

    def download(self) -> None:
        folder = app_data() / "updates"
        folder.mkdir(parents=True, exist_ok=True)
        destination = folder / self.offer.asset.name
        self.download_button.setEnabled(False)
        self.status_label.setText("Downloading and verifying update…")
        self.worker = UpdateDownloadWorker(self.offer, destination)
        self.worker.completed.connect(self._downloaded)
        self.worker.start()

    @pyqtSlot(bool, str, object)
    def _downloaded(self, success: bool, message: str, path: object) -> None:
        self.download_button.setEnabled(True)
        if not success or not isinstance(path, Path):
            self.status_label.setText(f"Update failed: {message}")
            return
        self.status_label.setText(message)
        if sys.platform == "win32":
            subprocess.Popen([str(path)], close_fds=True)
            self._quit_application()
            return
        if os.environ.get("APPIMAGE"):
            current = Path(os.environ["APPIMAGE"]).resolve()
            helper = path.with_suffix(".update.sh")
            try:
                write_appimage_update_helper(current, path, helper, process_id=os.getpid())
                QProcess.startDetached(str(helper), [])
                self._quit_application()
            except Exception as exc:
                self.status_label.setText(f"Could not start AppImage update: {exc}")
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            self.status_label.setText(f"Downloaded {path}; open it to install the update.")

    def _quit_application(self) -> None:
        if self.on_installed:
            self.on_installed()
