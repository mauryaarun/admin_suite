"""
Final Admin Suite entrypoint.
"""

from __future__ import annotations

import os
import sys

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFontDatabase
from PyQt6.QtWidgets import QApplication, QStyleFactory

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
    app.setApplicationName("Admin Suite")
    app.setDesktopFileName("AdminSuite")

    services = AppServices()
    current_theme = services.config.get("ui_theme", "System")

    if current_theme != "System":
        app.setStyle("Fusion")
    else:
        available_styles = [s.lower() for s in QStyleFactory.keys()]
        if "breeze" in available_styles:
            app.setStyle("Breeze")
        elif "windows" in available_styles and sys.platform.startswith("win"):
            app.setStyle("Windows")
        elif "macos" in available_styles or sys.platform == "darwin":
            pass
        else:
            app.setStyle("Fusion")

    try:
        app.setAttribute(Qt.ApplicationAttribute.AA_UseHighDpiPixmaps, True)
    except AttributeError:
        pass

    if current_theme != "System":
        from admin_suite.core.fonts import ui_font
        app.setFont(ui_font(10))
    else:
        sys_font = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont)
        if sys_font.family():
            app.setFont(sys_font)

    from admin_suite.core.paths import get_app_icon
    app_icon = get_app_icon()
    if app_icon and not app_icon.isNull():
        app.setWindowIcon(app_icon)

    services.apply_theme(app)

    window = MainWindow(services)
    if app_icon and not app_icon.isNull():
        window.setWindowIcon(app_icon)
    window.show()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
