"""Process-wide Python, Qt, thread, and native crash logging."""

from __future__ import annotations

import faulthandler
import logging
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Any, TextIO

from PyQt6.QtCore import QMessageLogContext, QtMsgType, qInstallMessageHandler

logger = logging.getLogger("thunderwatch.crashes")


@dataclass
class _CrashState:
    fault_log_handle: TextIO | None = None


_STATE = _CrashState()


def _flush_logs() -> None:
    for handler in logging.getLogger().handlers:
        try:
            handler.flush()
        except Exception:
            continue


def install_sys_hook() -> Callable[
    [type[BaseException], BaseException, TracebackType | None], None
]:
    """Log uncaught main-thread exceptions with their full traceback."""

    def handle_exception(
        exc_type: type[BaseException],
        exc_value: BaseException,
        exc_traceback: TracebackType | None,
    ) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        logger.critical(
            "Unhandled application exception: %s",
            exc_type.__name__,
            exc_info=(exc_type, exc_value, exc_traceback),
        )
        _flush_logs()

    sys.excepthook = handle_exception
    return handle_exception


def install_thread_hook() -> Callable[[threading.ExceptHookArgs], None]:
    """Log uncaught Python thread exceptions with thread identity and traceback."""

    def handle_exception(args: threading.ExceptHookArgs) -> None:
        exc_value = args.exc_value or RuntimeError("Unknown thread exception")
        logger.critical(
            "Unhandled exception in thread %s: %s",
            args.thread.name if args.thread else "unknown",
            type(exc_value).__name__,
            exc_info=(args.exc_type, exc_value, args.exc_traceback),
        )
        _flush_logs()

    threading.excepthook = handle_exception
    return handle_exception


def install_unraisable_hook() -> Callable[[Any], None]:
    """Log exceptions Python cannot propagate, such as failures in finalizers."""

    def handle_exception(args: sys.UnraisableHookArgs) -> None:
        exc_value = args.exc_value or RuntimeError("Unknown unraisable exception")
        logger.error(
            "Unraisable exception in %s: %s",
            type(args.object).__name__,
            type(exc_value).__name__,
            exc_info=(args.exc_type, exc_value, args.exc_traceback),
        )
        _flush_logs()

    sys.unraisablehook = handle_exception
    return handle_exception


def install_qt_message_handler() -> Callable[[QtMsgType, QMessageLogContext, str | None], None]:
    """Forward Qt warnings and fatal messages into the rotating application log."""
    previous_handler: Callable[[QtMsgType, QMessageLogContext, str | None], None] | None = None

    def handle_message(
        message_type: QtMsgType, context: QMessageLogContext, message: str | None
    ) -> None:
        level = {
            QtMsgType.QtDebugMsg: logging.DEBUG,
            QtMsgType.QtInfoMsg: logging.INFO,
            QtMsgType.QtWarningMsg: logging.WARNING,
            QtMsgType.QtCriticalMsg: logging.ERROR,
            QtMsgType.QtFatalMsg: logging.CRITICAL,
        }.get(message_type, logging.INFO)
        category = getattr(context, "category", "") or ""
        file_name = getattr(context, "file", "") or ""
        line = getattr(context, "line", 0) or 0
        function = getattr(context, "function", "") or ""
        logger.log(
            level,
            "Qt message: %s (category=%s, source=%s:%s, function=%s)",
            message or "",
            category,
            file_name,
            line,
            function,
            extra={
                "qt_category": category,
                "qt_file": file_name,
                "qt_line": line,
                "qt_function": function,
            },
        )
        if message_type == QtMsgType.QtFatalMsg:
            _flush_logs()
        if previous_handler is not None:
            previous_handler(message_type, context, message)

    previous_handler = qInstallMessageHandler(handle_message)
    return handle_message


def enable_fault_handler(log_dir: Path) -> Path | None:
    """Write Python fatal-signal tracebacks for every thread to a persistent file."""
    if _STATE.fault_log_handle is not None:
        return Path(str(_STATE.fault_log_handle.name))
    path = log_dir / "fatal_errors.log"
    handle: TextIO | None = None
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        handle = path.open("a", encoding="utf-8")
        handle.write(
            f"\nThunderWatch fatal log session {datetime.now(UTC).isoformat()}\n"
            f"Platform: {sys.platform}\nPython: {sys.version}\n\n"
        )
        handle.flush()
        faulthandler.enable(file=handle, all_threads=True)
    except Exception:
        logger.exception("Could not enable fatal-signal logging")
        if handle is not None:
            handle.close()
        return None
    assert handle is not None
    _STATE.fault_log_handle = handle
    logger.info("Fatal-signal logging enabled at %s", path)
    return path


def snapshot_previous_fatal_log(log_dir: Path) -> Path | None:
    """Archive fatal details from a previous process before opening a new session."""
    source = log_dir / "fatal_errors.log"
    try:
        content = source.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    if "Fatal Python error" not in content and "Windows fatal exception" not in content:
        if content:
            try:
                source.write_text("", encoding="utf-8")
            except OSError:
                logger.exception("Could not reset the previous fatal log at %s", source)
        return None
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
    archived = log_dir / f"fatal_errors-{stamp}.log"
    try:
        archived.write_text(content, encoding="utf-8")
        source.write_text("", encoding="utf-8")
    except OSError:
        logger.exception("Could not archive the previous fatal log at %s", source)
        return source
    logger.error("A previous fatal crash was captured in %s", archived)
    return archived
