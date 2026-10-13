# ruff: noqa: PLC0415
from __future__ import annotations

from PyQt6.QtWidgets import QApplication


def test_badge_icon_distinguishes_healthy_warning_and_danger():
    from thunderwatch.tokens import DANGER, WARNING
    from thunderwatch.tray import badge_icon

    _app = QApplication.instance() or QApplication([])
    healthy = badge_icon(None, "healthy", 64).pixmap(64, 64).toImage()
    warning = badge_icon(None, "warning", 64).pixmap(64, 64).toImage()
    danger = badge_icon(None, "danger", 64).pixmap(64, 64).toImage()
    point = (55, 55)
    assert warning.pixelColor(*point).name().lower() == WARNING.lower()
    assert danger.pixelColor(*point).name().lower() == DANGER.lower()
    assert healthy.pixelColor(*point) != warning.pixelColor(*point)
