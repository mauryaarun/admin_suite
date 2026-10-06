"""
Final Admin Suite entrypoint.
"""

from __future__ import annotations

import os
import sys

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

# ------------------------------------------------------------
# Force xcb when running under Wayland to avoid keyboard issues.
# ------------------------------------------------------------

if os.environ.get("ADMIN_SUITE_ALLOW_WAYLAND") != "1":
    _is_wayland = (
        os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"
        or bool(os.environ.get("WAYLAND_DISPLAY"))
    )

    _current_platform = os.environ.get("QT_QPA_PLATFORM", "").lower()

    if _is_wayland and (
        _current_platform == ""
        or "wayland" in _current_platform
    ):
        os.environ["QT_QPA_PLATFORM"] = "xcb"

from pathlib import Path

_pkg_parent = str(Path(__file__).resolve().parent.parent)
if _pkg_parent not in sys.path:
    sys.path.insert(0, _pkg_parent)

from admin_suite.services import AppServices
from admin_suite.ui.main_window import MainWindow

import logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)

def main() -> int:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    try:
        app.setAttribute(Qt.ApplicationAttribute.AA_UseHighDpiPixmaps, True)
    except AttributeError:
        pass

    services = AppServices()

    services.apply_theme(app)

    window = MainWindow(services)
    window.show()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
