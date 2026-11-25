"""
Regression tests for GUI modernization, SVG vector icons, standard cross-platform fonts,
Shift+Left/Right tab switching, right-click terminal theme menu, and UI bug fixes.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from PyQt6.QtCore import QEvent, QSize, Qt
from PyQt6.QtGui import QColor, QKeyEvent
from PyQt6.QtWidgets import (
    QApplication,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QWidget,
)

pkg_parent = str(Path(__file__).resolve().parent.parent.parent)
if pkg_parent not in sys.path:
    sys.path.insert(0, pkg_parent)


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if not app:
        app = QApplication(["--platform", "offscreen"])
    return app


@pytest.fixture
def isolated_services(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))

    import admin_suite.core.config as cfg_mod
    import admin_suite.core.paths as paths
    import admin_suite.ui.main_window as mw_mod

    data_dir = tmp_path / "data"
    cfg_dir = tmp_path / "config"
    log_dir = tmp_path / "logs"
    data_dir.mkdir(parents=True, exist_ok=True)
    cfg_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(paths, "DATA_DIR", data_dir)
    monkeypatch.setattr(paths, "CONFIG_DIR", cfg_dir)
    monkeypatch.setattr(paths, "LOG_DIR", log_dir)
    monkeypatch.setattr(paths, "CONFIG_FILE", cfg_dir / "config.json")
    monkeypatch.setattr(paths, "PROFILES_FILE", data_dir / "profiles.json")
    monkeypatch.setattr(paths, "DB_PROFILES_FILE", data_dir / "db_profiles.json")
    monkeypatch.setattr(paths, "LAST_SESSION_FILE", data_dir / "last_session.json")
    monkeypatch.setattr(paths, "RECENT_FILE", data_dir / "recent.json")

    if hasattr(cfg_mod, "CONFIG_FILE"):
        monkeypatch.setattr(cfg_mod, "CONFIG_FILE", cfg_dir / "config.json")

    monkeypatch.setattr(mw_mod, "PROFILES_FILE", data_dir / "profiles.json")
    monkeypatch.setattr(mw_mod, "DB_PROFILES_FILE", data_dir / "db_profiles.json")
    monkeypatch.setattr(mw_mod, "LAST_SESSION_FILE", data_dir / "last_session.json")
    monkeypatch.setattr(mw_mod, "RECENT_FILE", data_dir / "recent.json")

    from admin_suite.services import AppServices

    srv = AppServices()
    srv.paths = paths
    srv.config.path = cfg_dir / "config.json"
    srv.config.load()
    return srv


# =============================================================================
# 1. Cross-Platform Font Helpers
# =============================================================================


def test_standard_fonts_no_comma_literal_families(qapp):
    from admin_suite.core.fonts import (
        MONO_FONT_FAMILIES,
        UI_FONT_FAMILIES,
        mono_font,
        qss_font_family_mono,
        qss_font_family_ui,
        ui_font,
    )

    uf = ui_font(11, bold=True)
    mf = mono_font(10, italic=True)

    assert uf.pointSize() == 11
    assert uf.bold() is True
    assert mf.pointSize() == 10
    assert mf.italic() is True

    u_fams = uf.families()
    m_fams = mf.families()
    assert len(u_fams) >= len(UI_FONT_FAMILIES)
    assert len(m_fams) >= len(MONO_FONT_FAMILIES)
    assert all("," not in fam for fam in u_fams)
    assert all("," not in fam for fam in m_fams)

    assert "sans-serif" in qss_font_family_ui()
    assert "monospace" in qss_font_family_mono()


# =============================================================================
# 2. Vector SVG Icons & Icon-Only Medium Buttons
# =============================================================================


def test_vector_svg_icons_and_medium_icon_only_buttons(qapp):
    from admin_suite.core.icons import (
        DEFAULT_ICON_SIZE,
        available_icons,
        get_icon,
        get_swatch_icon,
        style_icon_button,
    )

    assert DEFAULT_ICON_SIZE == 20
    icons = available_icons()
    for name in ("menu", "terminal", "palette", "split", "settings", "database", "shield", "close"):
        assert name in icons
        ic = get_icon(name)
        assert not ic.isNull()
        assert not ic.pixmap(20, 20).isNull()

    swatch = get_swatch_icon("#0f172a", "#e2e8f0", "#38bdf8", size=16)
    assert not swatch.isNull()
    assert not swatch.pixmap(16, 16).isNull()

    btn = QPushButton("Old Verbose Label")
    style_icon_button(btn, "terminal", "New Terminal (Ctrl+T)", icon_only=True)
    assert btn.text() == ""
    assert not btn.icon().isNull()
    assert btn.iconSize() == QSize(20, 20)
    assert btn.toolTip() == "New Terminal (Ctrl+T)"
    assert btn.accessibleName() == "New Terminal (Ctrl+T)"
    assert btn.objectName() == "IconToolBtn"


# =============================================================================
# 3. Shift + Left / Right Terminal & Workspace Tab Switching
# =============================================================================


def test_terminal_bridge_and_assets_shift_arrow_support(qapp):
    from admin_suite.terminal.assets import SHARED_TERMINAL_HTML
    from admin_suite.terminal.bridge import TerminalBridge

    bridge = TerminalBridge()
    deltas: list[int] = []
    bridge.tab_switch_requested.connect(deltas.append)

    bridge.request_tab_switch(-1)
    bridge.request_tab_switch(1)
    assert deltas == [-1, 1]

    assert "_handleTabSwitchKeyEvent" in SHARED_TERMINAL_HTML
    assert "bridge.request_tab_switch" in SHARED_TERMINAL_HTML
    assert "xterm-helper-textarea" in SHARED_TERMINAL_HTML


def test_native_terminal_shift_arrow_emits_tab_switch(qapp):
    from admin_suite.terminal.native_terminal import NativeTerminalWidget

    term = NativeTerminalWidget()
    deltas: list[int] = []
    term.tab_switch_requested.connect(deltas.append)

    ev_left = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Left, Qt.KeyboardModifier.ShiftModifier)
    ev_right = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier)
    term.keyPressEvent(ev_left)
    term.keyPressEvent(ev_right)

    assert deltas == [-1, 1]
    term.deleteLater()


def test_main_window_shift_arrow_tab_switching_and_text_guard(qapp, isolated_services):
    from admin_suite.ui.main_window import MainWindow

    win = MainWindow(isolated_services)
    w1 = QWidget(win)
    w2 = QWidget(win)
    line_edit = QLineEdit(w1)
    plain_edit = QPlainTextEdit(w2)

    idx1 = win.tabs.addTab(w1, "Tab A")
    idx2 = win.tabs.addTab(w2, "Tab B")
    win.show()
    QApplication.processEvents()

    # Start on tab idx1 and simulate non-editor focus via eventFilter directly on window
    win.tabs.setCurrentIndex(idx1)
    win.tabs.setFocus()
    QApplication.processEvents()

    ev_right = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier)
    handled_right = win.eventFilter(win.tabs, ev_right)
    assert handled_right is True
    assert win.tabs.currentIndex() == idx2

    ev_left = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Left, Qt.KeyboardModifier.ShiftModifier)
    handled_left = win.eventFilter(win.tabs, ev_left)
    assert handled_left is True
    assert win.tabs.currentIndex() == idx1

    # When a QLineEdit or QPlainTextEdit has focus, Shift+Left/Right must NOT be hijacked
    line_edit.setFocus()
    QApplication.processEvents()
    ev_line = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier)
    assert win.eventFilter(line_edit, ev_line) is False
    assert win.tabs.currentIndex() == idx1

    win.tabs.setCurrentIndex(idx2)
    plain_edit.setFocus()
    QApplication.processEvents()
    ev_plain = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Left, Qt.KeyboardModifier.ShiftModifier)
    assert win.eventFilter(plain_edit, ev_plain) is False
    assert win.tabs.currentIndex() == idx2

    win.close()


# =============================================================================
# 4. Terminal Theme Right-Click Menu & Live Theme Updates
# =============================================================================


def test_terminal_context_menu_theme_switcher_and_apply_all(qapp, isolated_services, monkeypatch):
    import admin_suite.terminal.base_tab as base_tab_mod
    from admin_suite.core.theme import TERMINAL_THEMES
    from admin_suite.terminal.base_tab import TerminalBaseTab
    from admin_suite.terminal.native_terminal import NativeTerminalWidget
    from admin_suite.ui.main_window import MainWindow

    monkeypatch.setattr(base_tab_mod, "WEBENGINE_AVAILABLE", False)

    win = MainWindow(isolated_services)

    t1 = TerminalBaseTab(isolated_services, name="term-1")
    t2 = TerminalBaseTab(isolated_services, name="term-2")

    assert isinstance(t1.native_view, NativeTerminalWidget)
    assert isinstance(t2.native_view, NativeTerminalWidget)

    t1.terminal_theme_apply_all_requested.connect(win.apply_theme_to_open_terminals)
    t2.terminal_theme_apply_all_requested.connect(win.apply_theme_to_open_terminals)
    win.tabs.addTab(t1, "term-1")
    win.tabs.addTab(t2, "term-2")

    menu = t1.build_context_menu()
    theme_actions = [a for a in menu.actions() if a.menu() is not None and a.text() == "Terminal Theme"]
    assert len(theme_actions) == 1

    theme_submenu = theme_actions[0].menu()
    sub_items = [a for a in theme_submenu.actions() if not a.isSeparator()]
    data_keys = {a.data() for a in sub_items if a.data()}
    assert set(TERMINAL_THEMES.keys()).issubset(data_keys)

    apply_all_actions = [a for a in sub_items if a.text() == "Apply Theme to All Open Terminals"]
    assert len(apply_all_actions) == 1

    target_act = next(a for a in sub_items if a.data() == "tokyo_night")
    target_act.trigger()

    expected_bg = QColor(TERMINAL_THEMES["tokyo_night"]["background"])
    assert isolated_services.config.get("terminal_theme") == "tokyo_night"
    assert t1.native_view._bg_color == expected_bg

    # Trigger "Apply Theme to All Open Terminals" -> updates t2 as well
    apply_all_actions[0].trigger()
    assert t2.native_view._bg_color == expected_bg

    # Toolbar theme button should also be icon-only (20x20)
    assert t1.theme_btn.text() == ""
    assert t1.theme_btn.iconSize() == QSize(20, 20)

    win.close()


# =============================================================================
# 5. Bug Fixes & MainWindow Icon-Only Toolbar Verification
# =============================================================================


def test_main_window_tab_menu_and_port_forwarding_and_ssh_profile_data(qapp, isolated_services, monkeypatch):
    import admin_suite.terminal.base_tab as base_tab_mod
    import admin_suite.terminal.ssh_tab as ssh_tab_mod
    from admin_suite.ssh.credentials import SshCredentials
    from admin_suite.terminal.ssh_tab import SshTerminalTab
    from admin_suite.ui.main_window import MainWindow

    monkeypatch.setattr(base_tab_mod, "WEBENGINE_AVAILABLE", False)
    monkeypatch.setattr(ssh_tab_mod, "WEBENGINE_AVAILABLE", False)

    win = MainWindow(isolated_services)

    # Top navbar action buttons use 20x20 vector icons without text labels
    for btn in (
        win.sidebar_toggle_btn,
        win.palette_btn,
        win.broadcast_btn,
        win.admin_console_btn,
        win.vpn_btn,
        win.vpn_cfg_btn,
        win.right_sidebar_toggle_btn,
    ):
        assert btn.text() == ""
        assert not btn.icon().isNull()
        assert btn.iconSize() == QSize(20, 20)

    # Permanent tab 0 (Welcome) -> build_tab_menu returns None
    assert win.build_tab_menu(0) is None

    # Regular tab -> build_tab_menu returns populated QMenu without NameError
    sample_tab = QLabel("Regular Tab", win)
    idx = win.tabs.addTab(sample_tab, "Regular")
    tab_menu = win.build_tab_menu(idx)
    assert tab_menu is not None
    menu_labels = [a.text() for a in tab_menu.actions() if not a.isSeparator()]
    assert "Pop Out to Floating Window" in menu_labels
    assert "Rename" in menu_labels
    assert "Close" in menu_labels

    # SshTerminalTab retains passed profile_data rather than overwriting with {}
    profile = {
        "ssh_host": "10.20.30.40",
        "ssh_port": 2222,
        "ssh_user": "deploy",
        "auth_type": "password",
    }
    ssh_tab = SshTerminalTab(
        isolated_services,
        host="10.20.30.40",
        port=2222,
        user="deploy",
        creds=SshCredentials(),
        profile_name="prod-app",
        profile_data=profile,
    )
    assert ssh_tab.profile_data == profile
    ssh_tab.deleteLater()

    # MainWindow.open_port_forwarding reads ssh_host/ssh_port/ssh_user keys cleanly
    captured = {}

    class DummyNetworkingDialog:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def show(self):
            captured["shown"] = True

    import admin_suite.ui.networking_dialog as net_mod

    monkeypatch.setattr(net_mod, "NetworkingToolsDialog", DummyNetworkingDialog)
    win.profiles["prod-app"] = profile
    win.open_port_forwarding("prod-app")

    assert captured.get("shown") is True
    assert captured.get("host") == "10.20.30.40"
    assert captured.get("port") == 2222
    assert captured.get("username") == "deploy"
    assert captured.get("profile_data") == profile

    win.close()
