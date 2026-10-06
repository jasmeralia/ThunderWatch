"""Non-overlapping worker scheduling and retry backoff."""

from __future__ import annotations

from typing import Any

from PyQt6.QtCore import QObject, QThread, QTimer, pyqtSignal

from .config import Config
from .worker import CheckWorker


class Scheduler(QObject):
    state_changed = pyqtSignal(dict, list)

    def __init__(self, config: Config) -> None:
        super().__init__()
        self.config = config
        self.failures = 0
        self.running = False
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.run)
        self._worker: CheckWorker | None = None
        self._worker_thread: QThread | None = None
        QTimer.singleShot(15_000, self.run)

    def reconfigure(self, config: Config) -> None:
        self.config = config
        self.failures = 0
        if not self.running:
            self.schedule(config.interval_minutes)

    def schedule(self, minutes: int | None = None) -> None:
        self.timer.start((minutes or self.config.interval_minutes) * 60_000)

    def run(self) -> None:
        if self.running:
            return
        self.timer.stop()
        self.running = True
        thread = QThread(self)
        worker = CheckWorker(self.config)
        worker.moveToThread(thread)
        thread.started.connect(worker.check)
        worker.finished.connect(self.done)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._worker_stopped)
        self._worker = worker
        self._worker_thread = thread
        thread.start()

    def _worker_stopped(self) -> None:
        self._worker = None
        self._worker_thread = None

    def done(self, state: dict[str, Any], actions: list[dict[str, Any]]) -> None:
        self.running = False
        failed = state.get("last_check", {}).get("result") != "success"
        self.failures = self.failures + 1 if failed else 0
        self.state_changed.emit(state, actions)
        delay = min(2 ** max(self.failures - 1, 0), 8) if failed else self.config.interval_minutes
        self.schedule(min(delay, self.config.interval_minutes))
