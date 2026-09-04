"""앱 엔트리 – python -m dccon.app"""

from __future__ import annotations

import logging
import sys

from PySide6.QtWidgets import QApplication

from .main_window import MainWindow

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("디시콘 저장기")
    app.setOrganizationName("dccon")

    w = MainWindow()
    w.show()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
