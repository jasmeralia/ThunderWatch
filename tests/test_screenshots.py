# ruff: noqa: PLC0415
from pathlib import Path


def test_screenshot_generators_write_expected_synthetic_images(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from thunderwatch import app as app_module

    original_read_state = app_module.read_state
    from tools.screenshots.generate_readme_screenshots import capture_readme
    from tools.screenshots.generate_wizard_step_screenshots import capture_steps

    readme = capture_readme(tmp_path / "readme")
    steps = capture_steps(tmp_path / "wizard-steps")
    assert len(readme) == 6
    assert len(steps) == 5
    assert all(Path(path).is_file() and Path(path).stat().st_size > 0 for path in [*readme, *steps])
    assert app_module.read_state is original_read_state


def test_readme_capture_restores_process_state_when_rendering_fails(tmp_path, monkeypatch):
    import os

    import pytest
    from PyQt6.QtWidgets import QMainWindow
    from tools.screenshots.generate_readme_screenshots import capture_readme

    from thunderwatch import app as app_module

    keys = ("HOME", "XDG_CONFIG_HOME", "APPDATA", "LOCALAPPDATA", "QT_QPA_PLATFORM")
    previous_env = {key: f"test-{key.lower()}" for key in keys}
    for key, value in previous_env.items():
        monkeypatch.setenv(key, value)
    original_read_state = app_module.read_state

    def fail_capture(*_args, **_kwargs):
        raise RuntimeError("synthetic screenshot failure")

    monkeypatch.setattr(QMainWindow, "grab", fail_capture)
    with pytest.raises(RuntimeError, match="synthetic screenshot failure"):
        capture_readme(tmp_path / "readme-failure")

    assert app_module.read_state is original_read_state
    assert {key: os.environ.get(key) for key in keys} == previous_env


def test_wizard_capture_restores_environment_when_rendering_fails(tmp_path, monkeypatch):
    import os

    import pytest
    from PyQt6.QtWidgets import QWizard
    from tools.screenshots.generate_wizard_step_screenshots import capture_steps

    keys = ("HOME", "XDG_CONFIG_HOME", "QT_QPA_PLATFORM")
    previous_env = {key: f"test-{key.lower()}" for key in keys}
    for key, value in previous_env.items():
        monkeypatch.setenv(key, value)

    def fail_capture(*_args, **_kwargs):
        raise RuntimeError("synthetic screenshot failure")

    monkeypatch.setattr(QWizard, "grab", fail_capture)
    with pytest.raises(RuntimeError, match="synthetic screenshot failure"):
        capture_steps(tmp_path / "wizard-failure")

    assert {key: os.environ.get(key) for key in keys} == previous_env
