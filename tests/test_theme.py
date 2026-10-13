from __future__ import annotations

import sys

import pytest
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication, QWidget

from thunderwatch import theme, tokens


def test_galefling_palette_is_applied_to_qt_application() -> None:
    app = QApplication.instance() or QApplication([])

    theme.apply_theme(app)

    palette = app.palette()
    assert palette.color(palette.ColorRole.Window) == QColor(tokens.SURFACE)
    assert palette.color(palette.ColorRole.Highlight) == QColor(tokens.ACCENT)
    assert palette.color(palette.ColorRole.HighlightedText) == QColor(tokens.CANVAS)
    assert app.styleSheet() == theme.GLOBAL_QSS
    assert tokens.SURFACE_RAISED in theme.GLOBAL_QSS
    assert tokens.BORDER in theme.GLOBAL_QSS


def test_theme_reapplies_palette_when_stylesheet_is_already_current() -> None:
    app = QApplication.instance() or QApplication([])
    theme.apply_theme(app)
    app.setPalette(QPalette())

    theme.apply_theme(app)

    assert app.palette().color(QPalette.ColorRole.Window) == QColor(tokens.SURFACE)


def test_theme_applies_windows_title_bar_to_each_shown_window(monkeypatch):
    app = QApplication.instance() or QApplication([])
    themed = []
    monkeypatch.setattr(theme, "_set_windows_dark_title_bar", themed.append)
    theme.apply_theme(app)
    window = QWidget()

    window.show()
    app.processEvents()

    assert window in themed
    window.close()


def test_windows_title_bar_falls_back_when_modern_dwm_attribute_is_unsupported(monkeypatch):
    app = QApplication.instance() or QApplication([])
    calls = []

    class NativeFunction:
        argtypes = None
        restype = None

        def __call__(self, hwnd, attribute, value, size):
            calls.append((hwnd, attribute, value, size))
            return -1 if attribute.value == 20 else 0

    class DwmApi:
        DwmSetWindowAttribute = NativeFunction()

    monkeypatch.setattr(theme.sys, "platform", "win32")
    monkeypatch.setattr(theme.ctypes, "WinDLL", lambda *_args, **_kwargs: DwmApi(), raising=False)
    window = QWidget()

    assert theme._set_windows_dark_title_bar(window)
    assert [call[1].value for call in calls] == [20, 19]
    assert all(call[3] == theme.ctypes.sizeof(theme.wintypes.BOOL) for call in calls)
    assert DwmApi.DwmSetWindowAttribute.argtypes == [
        theme.wintypes.HWND,
        theme.wintypes.DWORD,
        theme.ctypes.c_void_p,
        theme.wintypes.DWORD,
    ]
    window.close()
    del app


@pytest.mark.skipif(sys.platform != "win32", reason="requires the Windows DWM and native HWND")
def test_windows_dwm_accepts_dark_title_bar_for_a_shown_qt_window():
    app = QApplication.instance() or QApplication([])
    theme.apply_theme(app)
    window = QWidget()
    window.show()
    app.processEvents()

    try:
        assert window.isVisible()
        assert int(window.winId()) != 0
        assert theme._set_windows_dark_title_bar(window)
    finally:
        window.close()
