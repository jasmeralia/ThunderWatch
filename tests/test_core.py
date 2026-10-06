# ruff: noqa: PLC0415
from datetime import UTC, datetime

from thunderwatch.ipcheck import lookup
from thunderwatch.monitor import apply_delivery, apply_lookup
from thunderwatch.notifier import send_email
from thunderwatch.state import empty_state, read_state, write_state


def test_lookup_requires_independent_agreement_for_candidate_change():
    responses = {"p1": "8.8.4.4", "p2": "8.8.4.4"}
    result = lookup("ipv4", lambda url: responses[url], ("p1", "p2"))
    assert result.value == "8.8.4.4"
    assert result.providers == ("p1", "p2")


def test_lookup_marks_disagreement_inconclusive():
    responses = {"p1": "8.8.4.4", "p2": "1.1.1.1"}
    result = lookup("ipv4", lambda url: responses[url], ("p1", "p2"))
    assert result.value is None


def test_lookup_failure_never_returns_an_address():
    def offline(_: str) -> str:
        raise TimeoutError("offline")

    result = lookup("ipv4", offline, ("p1", "p2"))
    assert result.value is None


def test_failed_delivery_does_not_advance_reported_baseline():
    state = empty_state()
    state["pending"] = {"changes": {"ipv4": {"old": "9.9.9.9", "new": "8.8.4.4"}}}
    new, _ = apply_delivery(state, False, datetime.now(UTC), "SMTP down")
    assert new["last_reported"] == {}
    assert new["pending"]["last_error"] == "SMTP down"


def test_successful_delivery_advances_baseline():
    state = empty_state()
    state["pending"] = {"changes": {"ipv4": {"old": "9.9.9.9", "new": "8.8.4.4"}}}
    new, _ = apply_delivery(state, True, datetime.now(UTC))
    assert new["last_reported"]["ipv4"]["value"] == "8.8.4.4"
    assert new["pending"] == {}


def test_state_round_trip_and_history_cap(tmp_path):
    path = tmp_path / "state.json"
    state = empty_state()
    state["history"] = [{"i": i} for i in range(60)]
    write_state(path, state)
    restored = read_state(path)
    assert len(restored["history"]) == 50
    assert restored["history"][0] == {"i": 10}


def test_smtp_refused_recipient_is_failure_and_password_is_redacted():
    class SMTP:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def ehlo(self):
            pass

        def starttls(self):
            pass

        def login(self, user, password):
            raise RuntimeError(f"bad password {password}")

    ok, text = send_email("smtp", 587, "starttls", "u", "secret", "from", "to", object(), SMTP)
    assert not ok
    assert "secret" not in text


def test_lookup_rejects_non_global_and_normalizes_ipv6_prefix():
    import pytest

    from thunderwatch.ipcheck import normalize

    with pytest.raises(ValueError):
        normalize("10.0.0.1", "ipv4")
    assert normalize("2001:4860:4860::8888", "ipv6") == "2001:4860:4860::/64"


def test_pending_change_clears_when_address_flaps_back():
    from thunderwatch.ipcheck import LookupResult

    state = empty_state()
    state["last_reported"]["ipv4"] = {"value": "9.9.9.9", "time": "earlier"}
    state["pending"] = {"changes": {"ipv4": {"old": "9.9.9.9", "new": "8.8.4.4"}}}
    updated, actions = apply_lookup(
        state, {"ipv4": LookupResult("ipv4", "9.9.9.9", ("p1",))}, datetime.now(UTC)
    )
    assert updated["pending"] == {}
    assert not any(action["type"].startswith("send_") for action in actions)


def test_config_completeness_validates_smtp_and_interval():
    from thunderwatch.config import Config

    assert Config(
        setup_complete=True,
        smtp_host="smtp.example",
        smtp_username="u",
        smtp_from="a@example.com",
        smtp_recipient="c@example.com",
    ).complete
    assert not Config(
        setup_complete=True,
        smtp_host="smtp.example",
        smtp_username="u",
        smtp_from="a@example.com",
        smtp_recipient="c@example.com",
        interval_minutes=2,
    ).complete


def test_password_file_requires_opt_in_and_uses_owner_only_permissions(tmp_path, monkeypatch):
    import stat

    from thunderwatch import secrets

    store_password, read_password = secrets.store_password, secrets.read_password
    monkeypatch.setattr(
        secrets, "_keyring", lambda: (_ for _ in ()).throw(RuntimeError("disabled"))
    )
    path = tmp_path / "smtp-password"
    store_password("user", "host", "pw", path, allow_file=True)
    assert read_password("user", "host", path) == "pw"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_desktop_autostart_uses_appimage_path():
    from thunderwatch.autostart import desktop_file

    desktop = desktop_file("/usr/bin/thunderwatch", "/home/user/ThunderWatch.AppImage")
    assert 'Exec="/home/user/ThunderWatch.AppImage" --autostart' in desktop


def test_desktop_autostart_escapes_desktop_entry_arguments():
    from thunderwatch.autostart import desktop_file

    desktop = desktop_file('/opt/Thunder "Watch" 100%/app')
    assert 'Exec="/opt/Thunder \\"Watch\\" 100%%/app" --autostart' in desktop


def test_disabled_ipv6_is_removed_from_pending_changes_without_mutating_state():
    from thunderwatch.monitor import discard_pending_families

    state = empty_state()
    state["pending"] = {
        "changes": {
            "ipv4": {"old": "1.1.1.1", "new": "8.8.8.8"},
            "ipv6": {"old": "2001:db8::1", "new": "2001:db8::2"},
        },
        "last_error": "temporary SMTP failure",
    }
    result = discard_pending_families(state, {"ipv6"})
    assert set(result["pending"]["changes"]) == {"ipv4"}
    assert result["pending"]["last_error"] == "temporary SMTP failure"
    assert "ipv6" in state["pending"]["changes"]


def test_disabling_only_pending_family_clears_pending_state():
    from thunderwatch.monitor import discard_pending_families

    state = empty_state()
    state["pending"] = {"changes": {"ipv6": {"old": None, "new": "2001:db8::2"}}}
    assert discard_pending_families(state, {"ipv6"})["pending"] == {}


def test_smtp_identity_change_requires_replacement_password():
    from thunderwatch.config import Config, smtp_identity_changed

    original = Config(smtp_host="smtp.example", smtp_username="alerts")
    unchanged = Config(smtp_host="smtp.example", smtp_username="alerts")
    assert not smtp_identity_changed(original, unchanged)
    assert smtp_identity_changed(original, Config(smtp_host="smtp.other", smtp_username="alerts"))
    assert smtp_identity_changed(original, Config(smtp_host="smtp.example", smtp_username="other"))


def test_smtp_refused_recipient_is_failure():
    from email.message import EmailMessage

    from thunderwatch.notifier import send_email

    class SMTP:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def ehlo(self):
            pass

        def starttls(self, context=None):
            pass

        def login(self, *args):
            pass

        def send_message(self, *args, **kwargs):
            return {"to@example.com": (550, "refused")}

    ok, message = send_email(
        "smtp",
        587,
        "starttls",
        "u",
        "pw",
        "from@example.com",
        "to@example.com",
        EmailMessage(),
        SMTP,
    )
    assert not ok
    assert "refused" in message


def test_smoke_test_builds_qt_app_then_exits(monkeypatch):
    from thunderwatch.app import main

    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setattr("sys.argv", ["thunderwatch", "--smoke-test"])
    assert main() == 0
    from PyQt6.QtWidgets import QApplication

    from thunderwatch.theme import GLOBAL_QSS

    assert QApplication.instance().styleSheet() == GLOBAL_QSS


def test_complete_config_routes_to_tray_without_setup(monkeypatch):
    from thunderwatch.app import should_setup

    class Settings:
        def value(self, key, default=None):
            values = {
                "setup/complete": True,
                "smtp/host": "smtp.example.com",
                "smtp/port": 587,
                "smtp/security": "auto",
                "smtp/username": "user",
                "smtp/from": "a@example.com",
                "smtp/recipient": "b@example.com",
                "monitor/interval_minutes": 10,
            }
            return values.get(key, default)

    assert not should_setup(Settings())


def test_setup_wizard_exposes_all_five_steps():
    from PyQt6.QtCore import QSettings
    from PyQt6.QtWidgets import QApplication

    from thunderwatch.wizard import SetupWizard

    _app = QApplication.instance() or QApplication([])
    wizard = SetupWizard(QSettings("Test", "ThunderWatch"))
    assert wizard.pageIds() == [0, 1, 2, 3, 4]
    wizard.close()


def test_stale_delivery_advances_only_delivered_value_and_keeps_new_pending():
    now = datetime.now(UTC)
    state = empty_state()
    sent = {"ipv4": {"old": "9.9.9.9", "new": "8.8.4.4"}}
    state["pending"] = {"changes": {"ipv4": {"old": "8.8.4.4", "new": "1.1.1.1"}}}
    updated, _ = apply_delivery(state, True, now, sent_changes=sent)
    assert updated["last_reported"]["ipv4"]["value"] == "8.8.4.4"
    assert updated["pending"]["changes"]["ipv4"]["new"] == "1.1.1.1"


def test_ipv6_failure_requests_retry_even_if_ipv4_succeeded():
    from thunderwatch.ipcheck import LookupResult

    state, actions = apply_lookup(
        empty_state(),
        {
            "ipv4": LookupResult("ipv4", "8.8.4.4", ("v4a", "v4b")),
            "ipv6": LookupResult("ipv6", None, error="offline"),
        },
        datetime.now(UTC),
    )
    assert state["last_check"]["result"] == "partial"
    assert any(action["type"] == "retry" for action in actions)


def test_confirmed_candidate_is_added_to_history():
    from thunderwatch.ipcheck import LookupResult

    state, actions = apply_lookup(
        empty_state(),
        {"ipv4": LookupResult("ipv4", "8.8.4.4", ("provider-a", "provider-b"))},
        datetime.now(UTC),
    )
    assert state["history"][-1]["type"] == "change_detected"
    assert actions[0]["type"] == "send_started"


def test_mixed_first_ipv6_observation_does_not_hide_ipv4_change():
    from thunderwatch.ipcheck import LookupResult

    state = empty_state()
    state["last_reported"]["ipv4"] = {"value": "9.9.9.9", "time": "earlier"}
    updated, actions = apply_lookup(
        state,
        {
            "ipv4": LookupResult("ipv4", "8.8.4.4", ("v4a", "v4b")),
            "ipv6": LookupResult("ipv6", "2001:4860:4860::/64", ("v6a", "v6b")),
        },
        datetime.now(UTC),
    )
    assert actions[0]["type"] == "send_change"
    assert set(actions[0]["changes"]) == {"ipv4", "ipv6"}
    assert updated["pending"]["changes"]["ipv4"]["old"] == "9.9.9.9"


def test_config_parses_qsettings_false_strings_as_false():
    from thunderwatch.config import Config

    class Store:
        def value(self, key, default=None):
            values = {
                "setup/complete": "true",
                "smtp/host": "smtp.example.com",
                "smtp/username": "alerts@example.com",
                "smtp/from": "alerts@example.com",
                "smtp/recipient": "you@example.com",
                "monitor/ipv6": "false",
                "startup/autostart": "false",
                "updates/automatic": "false",
                "updates/include_beta": "false",
            }
            return values.get(key, default)

    config = Config.from_store(Store())
    assert config.complete
    assert not config.ipv6
    assert not config.autostart
    assert not config.automatic_updates
    assert not config.include_beta


def test_consecutive_failures_are_summarized_in_one_history_row():
    from thunderwatch.ipcheck import LookupResult

    moment = datetime.now(UTC)
    state, _ = apply_lookup(
        empty_state(), {"ipv4": LookupResult("ipv4", None, error="offline")}, moment
    )
    state, _ = apply_lookup(state, {"ipv4": LookupResult("ipv4", None, error="offline")}, moment)
    failures = [row for row in state["history"] if row["type"] == "lookups_failing"]
    assert failures[0]["attempts"] == 2


def test_ipv6_failures_do_not_increment_ipv4_offline_streak():
    from thunderwatch.ipcheck import LookupResult

    state = empty_state()
    for _ in range(4):
        state, _ = apply_lookup(
            state,
            {
                "ipv4": LookupResult("ipv4", "8.8.4.4", ("v4a", "v4b")),
                "ipv6": LookupResult("ipv6", None, error="offline"),
            },
            datetime.now(UTC),
        )
    assert state["ipv4_failure_streak"] == 0


def test_ipv4_failure_streak_increments_and_resets_on_success():
    from thunderwatch.ipcheck import LookupResult

    state = empty_state()
    failed = {"ipv4": LookupResult("ipv4", None, error="offline")}
    ok = {"ipv4": LookupResult("ipv4", "8.8.4.4", ("v4a", "v4b"))}
    for _ in range(3):
        state, _ = apply_lookup(state, failed, datetime.now(UTC))
    assert state["ipv4_failure_streak"] == 3
    state, _ = apply_lookup(state, ok, datetime.now(UTC))
    assert state["ipv4_failure_streak"] == 0


def test_resource_lookup_finds_shared_icon_from_source_tree():
    from thunderwatch.paths import resource_path

    assert resource_path("icons/thunderwatch.png").is_file()


def test_test_email_worker_uses_injected_fake_services(monkeypatch):
    from thunderwatch import worker
    from thunderwatch.config import Config

    seen = {}
    monkeypatch.setattr(worker, "read_password", lambda *args: "synthetic-password")
    monkeypatch.setattr(
        worker,
        "lookup",
        lambda family, **kwargs: __import__(
            "thunderwatch.ipcheck", fromlist=["LookupResult"]
        ).LookupResult(family, "8.8.4.4", ("fake-a", "fake-b")),
    )
    monkeypatch.setattr(
        worker, "send_email", lambda *args: (seen.setdefault("sent", True), "accepted")
    )
    sender = worker.TestEmailWorker(
        Config(
            smtp_host="smtp.example.com",
            smtp_username="user",
            smtp_from="from@example.com",
            smtp_recipient="to@example.com",
            setup_complete=True,
        ),
        password_file=__import__("pathlib").Path("unused"),
    )
    results = []
    sender.finished.connect(lambda ok, message: results.append((ok, message)))
    sender.run()
    assert seen["sent"]
    assert results == [(True, "accepted")]


def test_first_provider_matching_baseline_is_enough_and_stops_fallback():
    calls = []
    responses = {"first": "8.8.4.4", "second": "1.1.1.1"}

    def fake_fetch(url):
        calls.append(url)
        return responses[url]

    result = lookup("ipv4", fake_fetch, ("first", "second"), baseline="8.8.4.4")
    assert result.value == "8.8.4.4"
    assert calls == ["first"]


def test_smoke_objects_use_synthetic_config_without_reading_settings():
    from PyQt6.QtWidgets import QApplication, QSystemTrayIcon

    from thunderwatch.app import build_smoke_objects

    app = QApplication.instance() or QApplication([])
    scheduler, window, tray = build_smoke_objects(app)
    assert window.windowTitle() == "ThunderWatch status"
    assert isinstance(tray, QSystemTrayIcon)
    scheduler.timer.stop()
    window.close()
    tray.hide()


def test_flatpak_autostart_requests_background_portal(monkeypatch):
    from thunderwatch.autostart import set_autostart

    class Portal:
        def __init__(self):
            self.calls = []

        def request_background(self, options):
            self.calls.append(options)

    portal = Portal()
    monkeypatch.setenv("FLATPAK_ID", "io.github.jasmeralia.ThunderWatch")
    set_autostart(True, portal_client=portal)
    assert portal.calls[0]["autostart"] is True
    assert portal.calls[0]["commandline"] == ["thunderwatch", "--autostart"]


def test_tray_wait_state_falls_back_only_after_sixty_seconds():
    from thunderwatch.app import tray_wait_state

    assert tray_wait_state(True, 0) == "ready"
    assert tray_wait_state(False, 59) == "wait"
    assert tray_wait_state(False, 60) == "fallback"


def test_pending_balloon_is_only_shown_on_transition_into_pending():
    from thunderwatch.app import pending_balloon_transition

    assert pending_balloon_transition(False, True)
    assert not pending_balloon_transition(True, True)
    assert not pending_balloon_transition(False, False)


def test_auto_tls_port_465_uses_implicit_tls_factory_only():
    import ssl
    from email.message import EmailMessage

    calls = []

    class SMTP:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def ehlo(self):
            calls.append("ehlo")

        def starttls(self):
            calls.append("starttls")

        def login(self, *args):
            calls.append("login")

        def send_message(self, *args, **kwargs):
            return {}

    def ssl_factory(host, port, timeout, context=None):
        calls.append((host, port, timeout, context))
        return SMTP()

    ok, _ = send_email(
        "smtp.example.com",
        465,
        "auto",
        "u",
        "pw",
        "from",
        "to",
        EmailMessage(),
        smtp_factory=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError()),
        ssl_factory=ssl_factory,
    )
    assert ok
    assert calls[0][:3] == ("smtp.example.com", 465, 30)
    assert calls[0][3].check_hostname
    assert calls[0][3].verify_mode == ssl.CERT_REQUIRED
    assert "starttls" not in calls


def test_corrupt_and_unknown_state_versions_degrade_to_empty(tmp_path):
    import json

    path = tmp_path / "state.json"
    path.write_text("not json", encoding="utf-8")
    assert read_state(path)["last_reported"] == {}
    path.write_text(json.dumps({"version": 999, "history": []}), encoding="utf-8")
    assert read_state(path)["version"] == 1
