"""Rotating application log setup."""

import logging
from logging.handlers import RotatingFileHandler

from .paths import app_data


def configure_logging() -> None:
    folder = app_data() / "logs"
    folder.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        folder / "thunderwatch.log", maxBytes=1_000_000, backupCount=5, encoding="utf-8"
    )
    logging.basicConfig(
        level=logging.INFO,
        handlers=[handler],
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
