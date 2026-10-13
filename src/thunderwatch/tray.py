"""Tray icon badge compositor shared with Storm Desktop Suite conventions."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QIcon, QImage, QPainter, QPen, QPixmap

from .paths import resource_path
from .tokens import DANGER, WARNING


def badge_icon(source: str | Path | None, state: str, size: int = 64) -> QIcon:
    """Return the suite icon with an optional amber or red corner dot."""
    if source is None:
        source = resource_path("icons/thunderwatch.png")
    image = QImage(str(source))
    if image.isNull():
        image = QImage(size, size, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
    canvas = QImage(size, size, QImage.Format.Format_ARGB32)
    canvas.fill(Qt.GlobalColor.transparent)
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.drawImage(
        0,
        0,
        image.scaled(
            size,
            size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        ),
    )
    colors = {"warning": WARNING, "danger": DANGER}
    if state in colors:
        diameter = max(14, size // 4)
        x = size - diameter - 1
        y = size - diameter - 1
        painter.setPen(QPen(QColor("#0A0D13"), max(2, size // 32)))
        painter.setBrush(QColor(colors[state]))
        painter.drawEllipse(x, y, diameter, diameter)
    painter.end()
    return QIcon(QPixmap.fromImage(canvas))
