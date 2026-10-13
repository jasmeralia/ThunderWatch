from __future__ import annotations

import faulthandler
import logging
import os
import sys
import threading
import traceback
from pathlib import Path
from types import SimpleNamespace

from PyQt6.QtCore import QtMsgType
from PyQt6.QtWidgets import QApplication

from thunderwatch import error_handling, logging_setup
from thunderwatch.app import ExceptionLoggingApplication


def test_sys_hook_logs_traceback_and_flushes_handlers(monkeypatch, caplog):
    flushed = []

    class FlushHandler(logging.Handler):
        def emit(self, record):
            pass

        def flush(self):
            flushed.append(True)

    root = logging.getLogger()
    handler = FlushHandler()
    root.addHandler(handler)
    old_hook = sys.excepthook
    monkeypatch.setattr(sys, "excepthook", old_hook)
    try:
        hook = error_handling.install_sys_hook()
        try:
            raise RuntimeError("synthetic UI failure")
        except RuntimeError as exc:
            hook(type(exc), exc, exc.__traceback__)
        assert any(
            record.exc_info and "synthetic UI failure" in str(record.exc_info[1])
            for record in caplog.records
        )
        assert any(
            record.exc_info
            and "raise RuntimeError" in "".join(traceback.format_exception(*record.exc_info))
            for record in caplog.records
        )
        assert flushed
    finally:
        root.removeHandler(handler)
        sys.excepthook = old_hook


def test_thread_hook_logs_exception_and_thread_name(monkeypatch, caplog):
    old_hook = threading.excepthook
    hook = error_handling.install_thread_hook()
    try:
        try:
            raise RuntimeError("synthetic worker failure")
        except RuntimeError as exc:
            args = threading.ExceptHookArgs(
                (type(exc), exc, exc.__traceback__, threading.current_thread())
            )
        hook(args)
        assert any(
            record.exc_info and "synthetic worker failure" in str(record.exc_info[1])
            for record in caplog.records
        )
        assert any("MainThread" in record.getMessage() for record in caplog.records)
    finally:
        threading.excepthook = old_hook


def test_qt_message_handler_records_warnings_and_errors(monkeypatch, caplog):
    installed = {}

    def install(handler):
        installed["handler"] = handler

    monkeypatch.setattr(error_handling, "qInstallMessageHandler", install)
    error_handling.install_qt_message_handler()
    context = SimpleNamespace(category="qt.sample", file="widget.cpp", line=42, function="paint")
    installed["handler"](QtMsgType.QtWarningMsg, context, "synthetic Qt warning")
    message = next(
        record.getMessage()
        for record in caplog.records
        if "synthetic Qt warning" in record.getMessage()
    )
    assert "qt.sample" in message
    assert "widget.cpp:42" in message
    assert "paint" in message


def test_unraisable_hook_logs_finalizer_exception(monkeypatch, caplog):
    old_hook = sys.unraisablehook
    hook = error_handling.install_unraisable_hook()
    try:
        error = RuntimeError("synthetic finalizer failure")
        hook(
            SimpleNamespace(
                exc_value=error,
                exc_type=type(error),
                exc_traceback=error.__traceback__,
                object=object(),
            )
        )
        assert any(
            record.exc_info and "synthetic finalizer failure" in str(record.exc_info[1])
            for record in caplog.records
        )
    finally:
        sys.unraisablehook = old_hook


def test_faulthandler_writes_to_dedicated_fatal_log(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(faulthandler, "enable", lambda **kwargs: calls.append(kwargs))
    monkeypatch.setattr(error_handling._STATE, "fault_log_handle", None)
    error_handling.enable_fault_handler(tmp_path)
    try:
        path = tmp_path / "fatal_errors.log"
        assert path.exists()
        assert "Python:" in path.read_text(encoding="utf-8")
        assert calls[0]["all_threads"] is True
        assert Path(calls[0]["file"].name) == path
    finally:
        handle = error_handling._STATE.fault_log_handle
        if handle:
            handle.close()
        error_handling._STATE.fault_log_handle = None


def test_previous_native_crash_is_archived_and_logged(tmp_path, caplog):
    fatal_log = tmp_path / "fatal_errors.log"
    fatal_log.write_text("Fatal Python error: Segmentation fault\ntrace details\n")
    archived = error_handling.snapshot_previous_fatal_log(tmp_path)
    assert archived is not None
    assert "trace details" in archived.read_text(encoding="utf-8")
    assert fatal_log.read_text(encoding="utf-8") == ""
    assert any("previous fatal crash" in record.getMessage().lower() for record in caplog.records)


def test_previous_clean_session_header_is_cleared(tmp_path):
    fatal_log = tmp_path / "fatal_errors.log"
    fatal_log.write_text("ThunderWatch fatal log session 2026-10-06T00:00:00Z\n")
    assert error_handling.snapshot_previous_fatal_log(tmp_path) is None
    assert fatal_log.read_text(encoding="utf-8") == ""


def test_qt_event_handler_exception_is_forwarded_to_sys_hook(monkeypatch):
    app = QApplication.instance() or ExceptionLoggingApplication([])
    hook_calls = []

    def broken_notify(*_args):
        raise RuntimeError("synthetic Qt event failure")

    monkeypatch.setattr(QApplication, "notify", broken_notify)
    monkeypatch.setattr(
        sys,
        "excepthook",
        lambda exc_type, exc, tb: hook_calls.append((exc_type, str(exc), tb is not None)),
    )
    assert ExceptionLoggingApplication.notify(app, None, None) is False
    assert hook_calls == [(RuntimeError, "synthetic Qt event failure", True)]


def test_configure_logging_falls_back_when_app_data_is_unavailable(monkeypatch, tmp_path):
    user_uid = os.getuid() if hasattr(os, "getuid") else None
    fallback_name = f"ThunderWatch-{user_uid}" if user_uid is not None else "ThunderWatch"
    fallback_root = tmp_path / fallback_name
    fallback = fallback_root / "logs"
    monkeypatch.setattr(logging_setup, "app_data", lambda: tmp_path / "readonly")
    monkeypatch.setattr(logging_setup, "gettempdir", lambda: str(tmp_path))

    original_mkdir = Path.mkdir

    def fail_primary_path(self, *args, **kwargs):
        if self == tmp_path / "readonly" / "logs":
            raise PermissionError("synthetic")
        original_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", fail_primary_path)
    log_dir = logging_setup.configure_logging()
    try:
        assert log_dir == fallback
        assert logging_setup.active_log_directory() == fallback
        if user_uid is not None:
            assert fallback_root.stat().st_mode & 0o777 == 0o700
            assert fallback.stat().st_mode & 0o777 == 0o700
        logging.getLogger("thunderwatch.test").error("synthetic fallback log")
        for handler in logging.getLogger().handlers:
            handler.flush()
        assert "synthetic fallback log" in (fallback / "thunderwatch.log").read_text()
    finally:
        for handler in logging.getLogger().handlers:
            handler.close()
            logging.getLogger().removeHandler(handler)
