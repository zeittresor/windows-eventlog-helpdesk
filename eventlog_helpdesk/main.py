from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication, QMessageBox

from .main_window import MainWindow
from .paths import LOG_DIR, RESOURCES_DIR
from .version import APP_NAME, ORGANIZATION, VERSION


def configure_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        LOG_DIR / "application.log",
        maxBytes=2_000_000,
        backupCount=4,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler])


def main() -> int:
    configure_logging()
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(VERSION)
    app.setOrganizationName(ORGANIZATION)
    icon_path = RESOURCES_DIR / "app_icon.svg"
    if icon_path.is_file():
        app.setWindowIcon(QIcon(str(icon_path)))

    def handle_exception(exc_type, exc_value, exc_traceback) -> None:
        logging.getLogger(__name__).exception(
            "Unhandled exception",
            exc_info=(exc_type, exc_value, exc_traceback),
        )
        QMessageBox.critical(None, APP_NAME, f"Unexpected error:\n\n{exc_value}")

    sys.excepthook = handle_exception
    window = MainWindow()
    window.show()
    return app.exec()
