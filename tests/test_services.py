# ruff: noqa: PLC0415
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from thunderwatch import secrets
from thunderwatch.autostart import set_autostart
from thunderwatch.config import Config
from thunderwatch.ipcheck import LookupResult, fetch, lookup, normalize
from thunderwatch.notifier import compose_email
from thunderwatch.paths import resource_path
from thunderwatch.scheduler import Scheduler
from thunderwatch.state import empty_state
from thunderwatch.worker import CheckWorker
from thunderwatch.worker import TestEmailWorker as EmailWorker


def test_fetch_sets_timeout_user_agent_and_caps_response(monkeypatch):
    seen: dict[str, Any] = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, limit):
            seen["limit"] = limit
            return b"8.8.4.4\n"

    def open_url(request, timeout):
        seen.update(url=request.full_url, agent=request.get_header("User-agent"), timeout=timeout)
        return Response()

    monkeypatch.setattr("thunderwatch.ipcheck.urllib.request.urlopen", open_url)
    assert fetch("https://provider.example/ip", "1.2.3") == "8.8.4.4"
    assert seen == {
        "url": "https://provider.example/ip",
        "agent": "ThunderWatch/1.2.3",
        "timeout": 10,
        "limit": 65,
    }


def test_fetch_rejects_oversized_response(monkeypatch):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, limit):
            return b"x" * 65

    monkeypatch.setattr(
        "thunderwatch.ipcheck.urllib.request.urlopen", lambda *args, **kwargs: Response()
    )
    with pytest.raises(ValueError, match="64 bytes"):
        fetch("https://provider.example/ip")


def test_lookup_uses_later_matching_provider_after_first_provider_fails():
    calls = []

    def fake(url):
        calls.append(url)
        if url == "bad":
            raise TimeoutError("offline")
        return "8.8.4.4"

    result = lookup("ipv4", fake, ("bad", "good", "later"), baseline="8.8.4.4")
    assert result.value == "8.8.4.4"
    assert calls == ["bad", "good"]


def test_ipv4_mapped_ipv6_and_cgnat_are_rejected():
    with pytest.raises(ValueError):
        normalize("::ffff:8.8.4.4", "ipv6")
    with pytest.raises(ValueError):
        normalize("100.64.0.1", "ipv4")


def test_compose_change_email_has_combined_families_and_detection_times():
    now = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)
    message = compose_email(
        "change",
        "Example Home",
        "example-host",
        "0.1.0",
        {
            "ipv4": {"old": "203.0.113.1", "new": "198.51.100.1", "providers": ["a", "b"]},
            "ipv6": {"old": "2001:db8::/64", "new": "2001:db8:1::/64", "providers": ["c", "d"]},
        },
        now=now,
    )
    assert "IPv4 changed to 198.51.100.1" in message["Subject"]
    assert "IPv6 changed to 2001:db8:1::/64" in message["Subject"]
    assert "Detected UTC: 2026-01-01T09:00:00+00:00" in message.get_content()
    assert "confirmed by a, b" in message.get_content()
    assert "update allowlists" in message.get_content()


def test_compose_started_and_test_subjects_include_location_and_current_address():
    started = compose_email("started", "Example Home", "host", "0.1.0", current={"ipv4": "8.8.4.4"})
    test = compose_email("test", "Example Home", "host", "0.1.0", current={"ipv4": "8.8.4.4"})
    assert started["Subject"] == "ThunderWatch: monitoring started for Example Home (8.8.4.4)"
    assert test["Subject"] == "ThunderWatch test from Example Home"
    assert "ipv4: 8.8.4.4" in test.get_content()


def test_send_email_sets_message_headers_and_verified_tls_context():
    import ssl
    from email.message import EmailMessage

    from thunderwatch.notifier import send_email

    seen = {}

    class SMTP:
        def __init__(self, *args, **kwargs):
            seen["context"] = kwargs.get("context")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def ehlo(self):
            pass

        def starttls(self, context=None):
            seen["starttls_context"] = context

        def login(self, *args):
            pass

        def send_message(self, message, **kwargs):
            names = ("From", "To", "Date", "Message-ID")
            seen["headers"] = {key: message.get(key) for key in names}
            return {}

    message = EmailMessage()
    message["Subject"] = "Synthetic test"
    ok, _ = send_email(
        "smtp.example.com",
        587,
        "starttls",
        "alerts@example.com",
        "synthetic-password",
        "alerts@example.com",
        "you@example.com",
        message,
        smtp_factory=SMTP,
    )
    context = seen["starttls_context"]
    assert ok
    assert isinstance(context, ssl.SSLContext)
    assert context.check_hostname
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert seen["headers"]["From"] == "alerts@example.com"
    assert seen["headers"]["To"] == "you@example.com"
    assert seen["headers"]["Date"]
    assert seen["headers"]["Message-ID"]


def test_check_worker_emits_failure_result_if_lookup_raises(monkeypatch):
    from pathlib import Path

    from thunderwatch import worker
    from thunderwatch.config import Config

    monkeypatch.setattr(worker, "state_path", lambda: Path("synthetic-state.json"))
    monkeypatch.setattr(worker, "read_state", lambda _path: empty_state())
    monkeypatch.setattr(worker, "write_state", lambda *_args: None)

    def fail_lookup(*_args, **_kwargs):
        raise OSError("disk/network failure")

    monkeypatch.setattr(worker, "lookup", fail_lookup)
    check = worker.CheckWorker(Config(ipv6=False))
    results = []
    check.finished.connect(lambda state, actions: results.append((state, actions)))
    check.check()
    assert len(results) == 1
    assert results[0][0]["last_check"]["result"] == "failure"
    assert any(action["type"] == "retry" for action in results[0][1])


def test_check_worker_completes_if_state_persistence_raises(monkeypatch):
    from pathlib import Path

    from thunderwatch import worker
    from thunderwatch.config import Config

    monkeypatch.setattr(worker, "state_path", lambda: Path("synthetic-state.json"))
    monkeypatch.setattr(worker, "read_state", lambda _path: empty_state())
    monkeypatch.setattr(
        worker,
        "lookup",
        lambda family, **_kwargs: LookupResult(family, "8.8.4.4", ("fake-a", "fake-b")),
    )

    def fail_write(*_args):
        raise OSError("synthetic persistence failure")

    monkeypatch.setattr(worker, "write_state", fail_write)
    check = worker.CheckWorker(Config(ipv6=False))
    results = []
    check.finished.connect(lambda state, actions: results.append((state, actions)))
    check.check()
    assert len(results) == 1
    assert results[0][0]["last_check"]["error"] == "OSError"
    assert any(action["type"] == "retry" for action in results[0][1])


def test_secret_store_prefers_keyring_and_reads_it_back(monkeypatch):
    class FakeKeyring:
        def __init__(self):
            self.values = {}

        def get_keyring(self):
            return object()

        def set_password(self, service, account, password):
            self.values[(service, account)] = password

        def get_password(self, service, account):
            return self.values.get((service, account))

    backend = FakeKeyring()
    monkeypatch.setattr(secrets, "_keyring", lambda: backend)
    secrets.store_password(
        "alerts", "smtp.example", "synthetic-secret", Path("unused"), allow_file=False
    )
    assert secrets.read_password("alerts", "smtp.example") == "synthetic-secret"


def test_secret_store_refuses_file_without_opt_in(monkeypatch, tmp_path):
    monkeypatch.setattr(
        secrets, "_keyring", lambda: (_ for _ in ()).throw(RuntimeError("no keyring"))
    )
    with pytest.raises(RuntimeError, match="opt in"):
        secrets.store_password("user", "host", "pw", tmp_path / "password", allow_file=False)
    assert not (tmp_path / "password").exists()


def test_secret_read_returns_none_when_backend_and_fallback_are_unavailable(monkeypatch, tmp_path):
    monkeypatch.setattr(secrets, "_keyring", lambda: (_ for _ in ()).throw(RuntimeError("locked")))
    assert secrets.read_password("user", "host", tmp_path / "missing") is None


def test_keyring_read_failure_does_not_use_stale_password_file(monkeypatch, tmp_path):
    class BrokenKeyring:
        def get_keyring(self):
            return object()

        def get_password(self, *_args):
            raise RuntimeError("keyring locked")

    fallback = tmp_path / "password"
    fallback.write_text("stale-secret", encoding="utf-8")
    monkeypatch.setattr(secrets, "_keyring", BrokenKeyring)
    assert secrets.read_password("user", "host", fallback) is None


def test_healthy_keyring_missing_entry_can_use_opted_in_file_fallback(monkeypatch, tmp_path):
    class EmptyKeyring:
        def get_keyring(self):
            return object()

        def get_password(self, *_args):
            return None

    fallback = tmp_path / "password"
    fallback.write_text("file-secret", encoding="utf-8")
    monkeypatch.setattr(secrets, "_keyring", EmptyKeyring)
    assert secrets.read_password("user", "host", fallback) == "file-secret"


def test_worker_runs_fake_lookup_and_delivery_and_persists_after_acceptance(monkeypatch):
    persisted = []
    monkeypatch.setattr("thunderwatch.worker.state_path", lambda: Path("synthetic-state.json"))
    monkeypatch.setattr("thunderwatch.worker.password_path", lambda: Path("synthetic-password"))
    monkeypatch.setattr("thunderwatch.worker.read_state", lambda _path: empty_state())
    monkeypatch.setattr(
        "thunderwatch.worker.write_state", lambda _path, state: persisted.append(state.copy())
    )
    monkeypatch.setattr(
        "thunderwatch.worker.lookup",
        lambda family, **kwargs: LookupResult(family, "8.8.4.4", ("fake-a", "fake-b")),
    )
    monkeypatch.setattr("thunderwatch.worker.read_password", lambda *args: "synthetic-password")
    monkeypatch.setattr("thunderwatch.worker.send_email", lambda *args: (True, "accepted"))
    worker = CheckWorker(
        Config(
            setup_complete=True,
            smtp_host="smtp.example.com",
            smtp_username="u",
            smtp_from="from@example.com",
            smtp_recipient="to@example.com",
            ipv6=False,
        )
    )
    results = []
    worker.finished.connect(lambda state, actions: results.append((state, actions)))
    worker.check()
    assert persisted[-1]["last_reported"]["ipv4"]["value"] == "8.8.4.4"
    assert results[0][0]["pending"] == {}


def test_test_email_worker_reports_unavailable_password_without_sending(monkeypatch):
    monkeypatch.setattr("thunderwatch.worker.read_password", lambda *args: None)
    monkeypatch.setattr("thunderwatch.worker.password_path", lambda: Path("missing-password"))
    monkeypatch.setattr(
        "thunderwatch.worker.send_email", lambda *args: pytest.fail("SMTP must not run")
    )
    worker = EmailWorker(Config(smtp_host="smtp.example.com", smtp_username="u"))
    results = []
    worker.finished.connect(lambda ok, message: results.append((ok, message)))
    worker.run()
    assert results == [(False, "Email password unavailable")]


def test_scheduler_partial_result_uses_retry_backoff_without_network():
    scheduler = Scheduler(Config(interval_minutes=10))
    states = []
    scheduler.state_changed.connect(lambda state, actions: states.append((state, actions)))
    scheduler.done({"last_check": {"result": "partial"}}, [{"type": "retry"}])
    assert scheduler.failures == 1
    assert scheduler.timer.interval() == 60_000
    scheduler.done({"last_check": {"result": "success"}}, [])
    assert scheduler.failures == 0
    assert scheduler.timer.interval() == 10 * 60_000
    scheduler.timer.stop()


def test_scheduler_coalesces_check_requested_while_running():
    scheduler = Scheduler(Config(interval_minutes=10))
    scheduler.running = True
    scheduler.run()
    assert scheduler._run_requested
    scheduler.done({"last_check": {"result": "success"}}, [])
    assert scheduler.timer.interval() == 0
    scheduler.timer.stop()


def test_app_data_paths_and_frozen_resource_location(tmp_path, monkeypatch):
    monkeypatch.setattr("sys._MEIPASS", str(tmp_path), raising=False)
    assert resource_path("icons/test.png") == tmp_path / "resources/icons/test.png"


def test_linux_autostart_creates_and_removes_desktop_entry(tmp_path, monkeypatch):
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("FLATPAK_ID", raising=False)
    monkeypatch.delenv("SNAP", raising=False)
    set_autostart(True, "/opt/Thunder Watch/thunderwatch")
    desktop = tmp_path / "autostart/thunderwatch.desktop"
    assert 'Exec="/opt/Thunder Watch/thunderwatch" --autostart' in desktop.read_text()
    set_autostart(False)
    assert not desktop.exists()


def test_snap_autostart_uses_snap_user_data(tmp_path, monkeypatch):
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setenv("SNAP", "/snap/thunderwatch/current")
    monkeypatch.setenv("SNAP_USER_DATA", str(tmp_path / "snap-data"))
    monkeypatch.delenv("FLATPAK_ID", raising=False)
    set_autostart(True)
    assert (tmp_path / "snap-data/.config/autostart/thunderwatch.desktop").exists()


def test_windows_autostart_updates_per_user_run_registry(monkeypatch):
    import sys
    from types import SimpleNamespace

    calls = []
    fake = SimpleNamespace(
        HKEY_CURRENT_USER=1,
        KEY_SET_VALUE=2,
        REG_SZ=3,
        OpenKey=lambda *args: "key",
        SetValueEx=lambda *args: calls.append(("set", args[1], args[4])),
        DeleteValue=lambda *args: calls.append(("delete", args[1])),
        CloseKey=lambda *args: calls.append(("close",)),
    )
    monkeypatch.setitem(sys.modules, "winreg", fake)
    monkeypatch.setattr("sys.platform", "win32")
    set_autostart(True, "C:/Apps/ThunderWatch.exe")
    set_autostart(False, "C:/Apps/ThunderWatch.exe")
    assert calls[0] == ("set", "ThunderWatch", '"C:/Apps/ThunderWatch.exe" --autostart')
    assert ("delete", "ThunderWatch") in calls
