"""Non-overlapping worker scheduling and retry backoff."""

from __future__ import annotations

from typing import Any

from PyQt6.QtCore import QEventLoop, QObject, QThread, QTimer, pyqtSignal

from .config import Config
from .worker import CheckWorker


class Scheduler(QObject):
    state_changed = pyqtSignal(dict, list)

    def __init__(self, config: Config) -> None:
        super().__init__()
        self.config = config
        self.failures = 0
        self.running = False
        self._paused = False
        self._run_requested = False
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.run)
        self._worker: CheckWorker | None = None
        self._worker_thread: QThread | None = None
        self.timer.start(15_000)

    def reconfigure(self, config: Config) -> None:
        self.config = config
        self.failures = 0
        if not self.running:
            self.schedule(config.interval_minutes)

    def schedule(self, minutes: int | None = None) -> None:
        delay = self.config.interval_minutes if minutes is None else minutes
        self.timer.start(delay * 60_000)

    def pause_and_wait(self) -> None:
        """Stop scheduled work and wait for any in-flight state update to finish."""
        self._paused = True
        self.timer.stop()
        thread = self._worker_thread
        if thread is None or not thread.isRunning():
            return
        loop = QEventLoop()
        thread.finished.connect(loop.quit)
        if thread.isRunning():
            loop.exec()
        thread.finished.disconnect(loop.quit)

    def resume(self) -> None:
        self._paused = False
        if not self.running:
            self.schedule()

    def run(self) -> None:
        if self.running:
            self._run_requested = True
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
        run_requested = self._run_requested
        self._run_requested = False
        last_result = state.get("last_check", {}).get("result")
        failed = state.get("ipv4_failure_streak", 0) > 0 or last_result not in {
            "success",
            "partial",
        }
        self.failures = self.failures + 1 if failed else 0
        delay = min(2 ** max(self.failures - 1, 0), 8) if failed else self.config.interval_minutes
        if self._paused:
            pass
        elif run_requested:
            self.timer.start(0)
        else:
            self.schedule(min(delay, self.config.interval_minutes))
        self.state_changed.emit(state, actions)
