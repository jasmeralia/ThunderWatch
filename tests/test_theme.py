from __future__ import annotations

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
