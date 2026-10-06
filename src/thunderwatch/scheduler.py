"""Non-overlapping worker scheduling and retry backoff."""

from __future__ import annotations

import logging
import time
from typing import Any

from PyQt6.QtCore import QCoreApplication, QObject, QThread, QTimer, pyqtSignal
from PyQt6.QtNetwork import QNetworkInformation

from .config import Config
from .worker import CheckWorker

logger = logging.getLogger(__name__)


def reachability_became_online(
    previous: QNetworkInformation.Reachability, current: QNetworkInformation.Reachability
) -> bool:
    return (
        previous != QNetworkInformation.Reachability.Online
        and current == QNetworkInformation.Reachability.Online
    )


def resume_gap_elapsed(previous: float, current: float, interval_minutes: int) -> bool:
    return current - previous > interval_minutes * 120


class Scheduler(QObject):
    state_changed = pyqtSignal(dict, list)

    def __init__(
        self, config: Config, network_information: QNetworkInformation | None = None
    ) -> None:
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
        self._last_clock_check = time.time()
        self._network_information = network_information
        if self._network_information is None:
            try:
                if QNetworkInformation.instance() is None:
                    QNetworkInformation.loadDefaultBackend()
                self._network_information = QNetworkInformation.instance()
            except Exception:
                logger.exception("Could not initialize network reachability monitoring")
        self._previous_reachability = QNetworkInformation.Reachability.Unknown
        if self._network_information:
            self._previous_reachability = self._network_information.reachability()
            self._network_information.reachabilityChanged.connect(self._reachability_changed)
        self._clock_timer = QTimer(self)
        self._clock_timer.setInterval(30_000)
        self._clock_timer.timeout.connect(self._check_for_resume)
        self._clock_timer.start()
        self.timer.start(15_000)

    def _reachability_changed(self, reachability: QNetworkInformation.Reachability) -> None:
        if reachability_became_online(self._previous_reachability, reachability):
            logger.info("Network is online; scheduling an immediate IP check")
            self.run()
        self._previous_reachability = reachability

    def _check_for_resume(self) -> None:
        now = time.time()
        if resume_gap_elapsed(self._last_clock_check, now, self.config.interval_minutes):
            logger.info("Long system sleep detected; scheduling an immediate IP check")
            self.run()
        self._last_clock_check = now

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
        if thread is None:
            return
        while thread.isRunning():
            thread.wait(50)
            QCoreApplication.processEvents()

    def resume(self) -> None:
        self._paused = False
        if not self.running:
            self.schedule()

    def run(self) -> None:
        if self._paused:
            return
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
        thread.finished.connect(lambda worker_thread=thread: self._worker_stopped(worker_thread))
        self._worker = worker
        self._worker_thread = thread
        thread.start()

    def _worker_stopped(self, thread: QThread) -> None:
        if thread is not self._worker_thread:
            return
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
