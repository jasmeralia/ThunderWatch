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
