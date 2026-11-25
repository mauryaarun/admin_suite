"""
Main application window.
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional

from PyQt6.QtCore import QEvent, QSettings, QSize, Qt, QTimer
from PyQt6.QtGui import (
    QAction,
    QColor,
    QFont,
    QIcon,
    QKeySequence,
    QPainter,
    QShortcut,
    QTextCursor,
)


from PyQt6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QSplitterHandle,
    QTabBar,
    QTabWidget,
    QTextEdit,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)



from admin_suite.core.icons import get_icon, style_icon_button

from admin_suite.core.paths import (
    DB_PROFILES_FILE,
    LAST_SESSION_FILE,
    PROFILES_FILE,
    RECENT_FILE,
)

from admin_suite.core.utils import (
    read_json,
    write_json_secure,
)

from admin_suite.ssh.credentials import (
    SshCredentials,
    profile_creds,
)

from admin_suite.ssh.remote_exec import RemoteExecThread

from admin_suite.terminal.ssh_tab import SshTerminalTab
from admin_suite.terminal.local_tab import LocalTerminalTab
from admin_suite.terminal.split_tab import SplitTerminalTab

from admin_suite.sftp.tab import SFTPTab

from admin_suite.db.manager import DatabaseManagerWidget
from admin_suite.db.mysql_status import MySQLStatusTab

from admin_suite.ansible.tab import AnsibleTab
from admin_suite.ansible.playbook import AnsiblePlaybookTab

from admin_suite.sysadmin.dashboard import SysAdminTab
from admin_suite.web.tab import WebManagerTab
from admin_suite.security.tab import SecurityHubTab
from admin_suite.vapt.tab import VaptTab
from admin_suite.admin_console.console import AdminConsoleTab

from admin_suite.vpn.service import VpnService
from admin_suite.vpn.dialog import VpnConfigDialog

from admin_suite.ui.toasts import Toast, NotificationCenterDialog
from admin_suite.ui.palette import CommandPaletteDialog
from admin_suite.ui.event_logs import EventLogsWidget
from admin_suite.ui.welcome import WelcomeWidget

from admin_suite.ui.dialogs import (
    ConnectionManagerDialog,
    DbProfileDialog,
    KeyManagerDialog,
    ProfileDialog,
    SessionLogViewerDialog,
    SettingsBackupDialog,
    SnippetManagerDialog,
    ThemeDialog,
)
from admin_suite.ui.workspace import WorkspaceManager
from admin_suite.ui.task_drawer import TaskStatusBarWidget, TaskDrawerDialog


class _MainSidebarSplitterHandle(QSplitterHandle):
    """Horizontal splitter handle with hover highlight, grip cursor, and double-click collapse/expand."""

    def __init__(self, orientation: Qt.Orientation, parent: QSplitter):
        super().__init__(orientation, parent)
        self.setCursor(Qt.CursorShape.SplitHCursor)

    def mouseDoubleClickEvent(self, event):
        splitter = self.splitter()
        if splitter:
            mw = splitter.window()
            if self == splitter.handle(1) and hasattr(mw, "toggle_sidebar"):
                mw.toggle_sidebar()
                event.accept()
                return
            elif self == splitter.handle(2) and hasattr(mw, "toggle_right_sidebar"):
                mw.toggle_right_sidebar()
                event.accept()
                return
        super().mouseDoubleClickEvent(event)


class _MainSidebarSplitter(QSplitter):
    """Horizontal splitter supporting custom handles and double-click collapse."""

    def __init__(self, orientation: Qt.Orientation = Qt.Orientation.Horizontal, parent=None):
        super().__init__(orientation, parent)
        self.setHandleWidth(7)
        self.setChildrenCollapsible(True)

    def createHandle(self) -> QSplitterHandle:
        return _MainSidebarSplitterHandle(self.orientation(), self)


def parse_ssh_config(path: str) -> list[dict[str, Any]]:
    """
    Parse ~/.ssh/config into Admin Suite profile dictionaries.
    """
    profiles = []
    current = None

    try:
        with open(path, encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()

                if not line or line.startswith("#"):
                    continue

                key, _, value = line.partition(" ")

                key = key.lower()
                value = value.strip()

                if key == "host" and "*" not in value:
                    if current:
                        profiles.append(current)

                    current = {
                        "name": value,
                        "group": "ssh-config",
                        "ssh_host": value,
                        "ssh_user": "",
                        "ssh_port": "22",
                        "auth_method": "Password",
                        "ssh_pass": "",
                        "ssh_key_path": "",
                        "tags": "imported",
                        "favorite": False,
                    }

                elif current:
                    if key == "hostname":
                        current["ssh_host"] = value

                    elif key == "user":
                        current["ssh_user"] = value

                    elif key == "port":
                        current["ssh_port"] = value

                    elif key == "identityfile":
                        current["ssh_key_path"] = os.path.expanduser(value)
                        current["auth_method"] = "SSH Key"

        if current:
            profiles.append(current)

    except Exception:
        pass

    return [p for p in profiles if p["ssh_user"]]


class MainWindow(QMainWindow):
    """
    Admin Suite main window.
    """

    def __init__(self, services):
        super().__init__()

        self.services = services

        self.setWindowTitle("Admin Suite")
        from admin_suite.core.paths import get_app_icon
        _icon = get_app_icon()
        if _icon and not _icon.isNull():
            self.setWindowIcon(_icon)
        self.resize(1440, 860)
        self.setMinimumSize(1000, 600)

        self.profiles: dict[str, dict[str, Any]] = {}
        self.db_profiles: dict[str, dict[str, Any]] = {}
        self.recent_connections: list[str] = []

        self.broadcast_enabled = False

        self._profile_status: dict[str, str] = {}
        self._ping_threads = []

        self._last_closed = None

        self._settings = QSettings("AdminSuite", "v5")

        # VPN.
        self.vpn = VpnService(self.services)
        self.vpn.result.connect(self._vpn_dispatch)

        # Ping queue.
        self._ping_queue: list[str] = []
        self._ping_active = 0
        self._ping_max = int(self.services.config.get("ping_max_concurrency", 8))

        # Workspace manager for multi-pane docking and detachment
        self.workspace_mgr = WorkspaceManager(self)

        # UI must be created before loading/refreshing profiles.
        self._init_ui()
        self._init_shortcuts()
        self._register_actions()

        # Load state after widgets exist.
        self.load_profiles()
        self.load_db_profiles()
        self.load_recent()

        # Signals.
        self.services.notifications.pushed.connect(self._on_notification)

        self.services.emit_log("system", "Admin Suite v5 started.")

        # Timers.
        QTimer.singleShot(2200, self._ping_all_profiles)
        QTimer.singleShot(800, self._offer_session_restore)
        QTimer.singleShot(1200, self.vpn.check_status)

        self._vpn_timer = QTimer(self)
        self._vpn_timer.timeout.connect(self.vpn.check_status)
        self._vpn_timer.start(7000)

        # Geometry.
        geo = self._settings.value("geometry")

        if geo:
            self.restoreGeometry(geo)
            if self.width() < 1000 or self.height() < 600:
                self.resize(max(self.width(), 1280), max(self.height(), 760))

        try:
            # Always start application with left sidebar open as requested
            sidebar_visible = True
            sidebar_width = self._settings.value("sidebar/width", 280, type=int)

            self._sidebar_width = sidebar_width if sidebar_width >= 160 else 280
            self.sidebar.setVisible(True)
            self.sidebar.setMinimumWidth(160)
            self.sidebar.setMaximumWidth(16777215)
            self.main_splitter.setCollapsible(0, False)

            copilot_visible = self._settings.value("copilot/visible", False, type=bool)
            copilot_width = self._settings.value("copilot/width", 360, type=int)
            self._copilot_width = copilot_width if copilot_width >= 200 else 360

            if hasattr(self, "right_sidebar"):
                self.right_sidebar.setVisible(copilot_visible)
                self.right_sidebar.setMinimumWidth(200 if copilot_visible else 0)
                self.right_sidebar.setMaximumWidth(16777215)

            self._update_left_sidebar_btn_styles(True)
            self._update_right_sidebar_btn_styles()

            self._apply_splitter_layout()

        except Exception:
            pass

    # ------------------------------------------------------------
    # UI initialization
    # ------------------------------------------------------------

    def _init_ui(self) -> None:
        theme = self.services.theme.current

        central = QWidget()
        self.setCentralWidget(central)

        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Sidebar.
        self.sidebar = QWidget()
        self.sidebar.setMinimumWidth(0)
        self.sidebar.setMaximumWidth(16777215)

        self._sidebar_width = 280

        self.sidebar.setStyleSheet(
            f"background:{theme['panel']};"
            f"border-right:1px solid {theme['border']};"
        )

        sidebar_layout = QVBoxLayout(self.sidebar)
        sidebar_layout.setContentsMargins(0, 0, 0, 0)
        sidebar_layout.setSpacing(0)

        self.sidebar_tabs = QTabWidget()
        self.sidebar_tabs.setDocumentMode(True)
        self.sidebar_tabs.setUsesScrollButtons(True)
        self.sidebar_tabs.setIconSize(QSize(16, 16))

        icon_color = theme.get("text", "#eff0f1")

        # Profiles tab.
        profiles_tab = QWidget()
        profiles_layout = QVBoxLayout(profiles_tab)
        profiles_layout.setContentsMargins(4, 4, 4, 4)
        profiles_layout.setSpacing(4)

        self.profile_filter = QLineEdit()
        self.profile_filter.setPlaceholderText("Filter profiles / tags...")
        self.profile_filter.textChanged.connect(self.refresh_profile_tree)

        self.profile_tree = QTreeWidget()
        self.profile_tree.setHeaderHidden(True)
        self.profile_tree.itemDoubleClicked.connect(self.on_profile_activated)

        self.profile_tree.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )

        self.profile_tree.customContextMenuRequested.connect(
            self.show_profile_menu
        )

        profiles_layout.addWidget(self.profile_filter)
        profiles_layout.addWidget(self.profile_tree, 1)

        profile_button_bar = QHBoxLayout()
        profile_button_bar.setContentsMargins(0, 0, 0, 0)
        profile_button_bar.setSpacing(4)

        for icon_name, tooltip, callback in (
            ("plus", "Add SSH profile", self.add_profile),
            ("copy", "Duplicate SSH profile", self.duplicate_profile),
            ("edit", "Edit SSH profile", self.edit_profile),
            ("trash", "Delete SSH profile", self.delete_profile),
            ("wifi", "Ping all hosts", self._ping_all_profiles),
        ):
            button = QPushButton()
            style_icon_button(
                button,
                icon_name,
                tooltip=tooltip,
                color=icon_color,
                min_width=36,
                min_height=32,
            )
            button.clicked.connect(callback)
            profile_button_bar.addWidget(button)

        profiles_layout.addLayout(profile_button_bar)

        # DB profiles tab.
        db_tab = QWidget()
        db_layout = QVBoxLayout(db_tab)
        db_layout.setContentsMargins(4, 4, 4, 4)
        db_layout.setSpacing(4)

        self.db_filter = QLineEdit()
        self.db_filter.setPlaceholderText("Filter DB profiles / hosts...")
        self.db_filter.textChanged.connect(self.refresh_db_list)
        db_layout.addWidget(self.db_filter)

        self.db_list = QListWidget()
        self.db_list.itemDoubleClicked.connect(self.on_db_profile_activated)

        self.db_list.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )

        self.db_list.customContextMenuRequested.connect(self.show_db_menu)

        db_layout.addWidget(self.db_list, 1)

        db_button_bar = QHBoxLayout()
        db_button_bar.setContentsMargins(0, 0, 0, 0)
        db_button_bar.setSpacing(4)

        for icon_name, tooltip, callback in (
            ("plus", "Add DB profile", self.add_db_profile),
            ("copy", "Duplicate DB profile", self.duplicate_db_profile),
            ("edit", "Edit DB profile", self.edit_db_profile),
            ("trash", "Delete DB profile", self.delete_db_profile),
        ):
            button = QPushButton()
            style_icon_button(
                button,
                icon_name,
                tooltip=tooltip,
                color=icon_color,
                min_width=36,
                min_height=32,
            )
            button.clicked.connect(callback)
            db_button_bar.addWidget(button)

        db_layout.addLayout(db_button_bar)

        # Recent tab.
        recent_tab = QWidget()
        recent_layout = QVBoxLayout(recent_tab)
        recent_layout.setContentsMargins(4, 4, 4, 4)
        recent_layout.setSpacing(4)

        self.recent_list = QListWidget()
        self.recent_list.itemDoubleClicked.connect(self.on_recent_activated)

        recent_layout.addWidget(self.recent_list, 1)

        # Tools tab.
        tools = QWidget()
        tools_layout = QVBoxLayout(tools)
        tools_layout.setContentsMargins(4, 4, 4, 4)
        tools_layout.setSpacing(3)

        tool_buttons = [
            ("home", "Welcome Screen", self.open_welcome_tab),
            ("database", "Database Manager", self.open_db_manager),
            ("list", "Event Logs", self.toggle_event_logs),
            ("terminal", "Local Shell", lambda: self.add_local_command_tab("bash", "Local Shell")),
            ("radar", "VAPT & Web Security Audit", lambda: self.open_vapt_tab()),
            ("shield_check", "Security Hub", self.open_security_hub_selected),
            ("link", "Port Forwarding & Tunnels", lambda: self.open_port_forwarding()),
            ("globe", "Web Hosting Manager", self.open_web_manager_selected),
            ("activity", "SysAdmin Dashboard", self.open_sysadmin_selected),
            ("ansible", "Ansible Multihost", self.open_ansible_tab),
            ("code", "Ansible Playbook", self.open_ansible_playbook_tab),
            ("book", "Snippets", self.open_snippets),
            ("key", "SSH Key Manager", lambda: KeyManagerDialog(self, self.services).exec()),
            ("upload", "Import ~/.ssh/config", self.import_ssh_config),
            ("file_text", "Session Recordings", lambda: SessionLogViewerDialog(self, self.services).exec()),
            ("palette", "Themes", self.open_theme_dialog),
            ("settings", "Global Connection Settings", self.open_config),
        ]

        for icon_name, label, callback in tool_buttons:
            button = QPushButton(label)
            button.setIcon(get_icon(icon_name, color=icon_color, size=18))
            button.setIconSize(QSize(18, 18))
            button.setStyleSheet("text-align:left;padding:7px 10px;")
            button.clicked.connect(callback)

            tools_layout.addWidget(button)

        tools_layout.addStretch()

        tools_scroll = QScrollArea()
        tools_scroll.setWidgetResizable(True)
        tools_scroll.setFrameShape(QFrame.Shape.NoFrame)
        tools_scroll.setWidget(tools)

        # Add sidebar tabs.
        self.sidebar_tabs.addTab(profiles_tab, get_icon("terminal", color=icon_color, size=16), "SSH")
        self.sidebar_tabs.addTab(db_tab, get_icon("database", color=icon_color, size=16), "DB")
        self.sidebar_tabs.addTab(recent_tab, get_icon("clock", color=icon_color, size=16), "Recent")
        self.sidebar_tabs.addTab(tools_scroll, get_icon("sliders", color=icon_color, size=16), "Tools")

        sidebar_layout.addWidget(self.sidebar_tabs, 1)

        # Workspace.
        workspace = QWidget()
        workspace_layout = QVBoxLayout(workspace)
        workspace_layout.setContentsMargins(0, 0, 0, 0)
        workspace_layout.setSpacing(0)

        # Right sidebar container (Copilot + Event Logs tabs)
        self.right_sidebar = QWidget()
        self.right_sidebar.setMinimumWidth(0)
        self.right_sidebar.setMaximumWidth(16777215)
        self.right_sidebar.setStyleSheet(
            f"background:{theme['panel']};"
            f"border-left:1px solid {theme['border']};"
        )
        right_sidebar_layout = QVBoxLayout(self.right_sidebar)
        right_sidebar_layout.setContentsMargins(0, 0, 0, 0)
        right_sidebar_layout.setSpacing(0)

        self.right_tabs = QTabWidget()
        self.right_tabs.setDocumentMode(True)
        self.right_tabs.setUsesScrollButtons(True)
        self.right_tabs.setIconSize(QSize(16, 16))

        from admin_suite.ai.assistant_tab import AIAssistantTab
        self.ai_tab = AIAssistantTab(self.services, self)
        self.event_logs_widget = EventLogsWidget(self.services, self)

        self.right_tabs.addTab(self.ai_tab, get_icon("sparkles", color=icon_color, size=16), "Copilot")
        self.right_tabs.addTab(self.event_logs_widget, get_icon("list", color=icon_color, size=16), "Event Logs")
        self.right_tabs.currentChanged.connect(self._on_right_tab_changed)

        right_sidebar_layout.addWidget(self.right_tabs, 1)

        self._copilot_width = 360

        self.main_splitter = _MainSidebarSplitter(Qt.Orientation.Horizontal)
        self.main_splitter.setObjectName("main_splitter")
        self.main_splitter.addWidget(self.sidebar)
        self.main_splitter.addWidget(workspace)
        self.main_splitter.addWidget(self.right_sidebar)

        self.main_splitter.setStretchFactor(0, 0)
        self.main_splitter.setStretchFactor(1, 1)
        self.main_splitter.setStretchFactor(2, 0)
        self.main_splitter.setChildrenCollapsible(True)
        self.main_splitter.setSizes([280, 800, 360])

        self.main_splitter.handle(1).setToolTip("Drag to resize left sidebar | Double-click to collapse/expand")
        self.main_splitter.handle(2).setToolTip("Drag to resize right sidebar | Double-click to collapse/expand")
        self.main_splitter.splitterMoved.connect(self._on_main_splitter_moved)

        self.main_splitter.setStyleSheet(
            f"#main_splitter::handle:horizontal {{"
            f"    background: {theme.get('panel2', '#2a2e32')};"
            f"    border-left: 1px solid {theme.get('border', '#31363b')};"
            f"    border-right: 1px solid {theme.get('border', '#31363b')};"
            f"}}"
            f"#main_splitter::handle:horizontal:hover {{"
            f"    background: {theme.get('accent', '#3daee9')};"
            f"    border-left: 1px solid {theme.get('accent', '#3daee9')};"
            f"    border-right: 1px solid {theme.get('accent', '#3daee9')};"
            f"}}"
        )

        main_layout.addWidget(self.main_splitter)

        # Navbar.
        navbar = QWidget()
        navbar.setFixedHeight(46)

        navbar.setStyleSheet(
            f"background:{theme['panel']};"
            f"border-bottom:1px solid {theme['border']};"
        )

        self.navbar = navbar
        navbar_layout = QHBoxLayout(navbar)
        navbar_layout.setContentsMargins(8, 4, 8, 4)
        navbar_layout.setSpacing(6)

        self.sidebar_toggle_btn = QPushButton()
        self.sidebar_toggle_btn.setCheckable(True)
        style_icon_button(
            self.sidebar_toggle_btn,
            "sidebar_left",
            tooltip="Toggle Left Sidebar (Ctrl+B)",
            color=icon_color,
            min_width=36,
            min_height=32,
        )
        self.sidebar_toggle_btn.clicked.connect(lambda: self.toggle_sidebar())

        self.palette_btn = QPushButton()
        style_icon_button(
            self.palette_btn,
            "command",
            tooltip="Command Palette (Ctrl+K / Ctrl+Shift+P)",
            color=icon_color,
            min_width=36,
            min_height=32,
        )
        self.palette_btn.clicked.connect(self.open_palette)

        self.broadcast_btn = QPushButton()
        self.broadcast_btn.setCheckable(True)
        self.broadcast_btn.setChecked(False)
        style_icon_button(
            self.broadcast_btn,
            "broadcast",
            tooltip="Broadcast Input to Open Terminals (F8): Off",
            color=icon_color,
            min_width=36,
            min_height=32,
        )
        self.broadcast_btn.clicked.connect(self.toggle_broadcast)

        self.admin_console_btn = QPushButton()
        style_icon_button(
            self.admin_console_btn,
            "terminal_square",
            tooltip="Open Admin Console",
            color=theme.get("accent", "#3daee9"),
            min_width=36,
            min_height=32,
        )
        self.admin_console_btn.clicked.connect(self.open_admin_console_selected)

        self.vpn_btn = QPushButton()
        style_icon_button(
            self.vpn_btn,
            "vpn",
            tooltip="Toggle VPN Connection",
            color=theme.get("warn", "#f39c12"),
            min_width=36,
            min_height=32,
        )
        self.vpn_btn.clicked.connect(self.vpn.toggle)

        self.vpn_cfg_btn = QPushButton()
        style_icon_button(
            self.vpn_cfg_btn,
            "settings",
            tooltip="Configure VPN Profiles & WireGuard Server Setup",
            color=icon_color,
            min_width=36,
            min_height=32,
        )
        self.vpn_cfg_btn.clicked.connect(lambda: self.open_vpn_config_dialog())

        # Single right sidebar toggle button
        self.right_sidebar_toggle_btn = QPushButton()
        self.right_sidebar_toggle_btn.setCheckable(True)
        style_icon_button(
            self.right_sidebar_toggle_btn,
            "sidebar_right",
            tooltip="Toggle Right Sidebar (Ctrl+Shift+A)",
            color=icon_color,
            min_width=36,
            min_height=32,
        )
        self.right_sidebar_toggle_btn.clicked.connect(lambda: self.toggle_right_sidebar())

        # Compatibility references
        self.copilot_toggle_btn = self.right_sidebar_toggle_btn
        self.event_logs_btn = self.right_sidebar_toggle_btn

        self._update_right_sidebar_btn_styles()

        self.vpn_status = QLabel("● VPN: Unknown")
        self.vpn_status.setStyleSheet(
            f"color:{theme['sub']};padding:0 8px;font-weight:500;"
        )

        navbar_layout.addWidget(self.sidebar_toggle_btn)
        navbar_layout.addWidget(self.palette_btn)
        navbar_layout.addWidget(self.admin_console_btn)
        navbar_layout.addWidget(self.broadcast_btn)
        navbar_layout.addWidget(self.vpn_btn)
        navbar_layout.addWidget(self.vpn_cfg_btn)

        navbar_layout.addStretch()
        navbar_layout.addWidget(self.vpn_status)

        self.bell_btn = QPushButton()
        style_icon_button(
            self.bell_btn,
            "bell",
            tooltip="Notifications (0)",
            color=icon_color,
            min_width=36,
            min_height=32,
        )
        self.bell_btn.clicked.connect(
            lambda: NotificationCenterDialog(self, self.services).exec()
        )

        navbar_layout.addWidget(self.bell_btn)

        navbar_layout.addWidget(self.right_sidebar_toggle_btn)

        workspace_layout.addWidget(navbar)

        # Tabs.
        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.tabs.setIconSize(QSize(16, 16))
        self.tabs.tabCloseRequested.connect(self.close_tab)

        self.tabs.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )

        self.tabs.customContextMenuRequested.connect(self.show_tab_menu)

        self.tabs.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.tabs.currentChanged.connect(self._focus_current_terminal)

        workspace_layout.addWidget(self.tabs, 1)

        # Welcome screen (Starting tab)
        self.welcome_widget = WelcomeWidget(self.services, self)
        self.tabs.addTab(self.welcome_widget, get_icon("home", color=icon_color, size=16), "")
        self.tabs.setTabToolTip(0, "Home / Welcome")

        # Database manager (created on-demand, parented to tabs and hidden until opened)
        self.db_manager_widget = DatabaseManagerWidget(self.services, self.tabs)
        self.db_manager_widget.hide()

        # Compatibility alias for debug console
        self.debug_console = getattr(self.event_logs_widget, "console", None)

        # Disable close button on Welcome permanent tab
        w_idx = self.tabs.indexOf(self.welcome_widget)
        if w_idx >= 0:
            self.tabs.tabBar().setTabButton(
                w_idx,
                QTabBar.ButtonPosition.RightSide,
                None,
            )

        # MySQL status button inside DB manager toolbar.
        try:
            self.mysql_status_btn = QPushButton()
            style_icon_button(
                self.mysql_status_btn,
                "activity",
                tooltip="MySQL / MariaDB Server Status",
                color=icon_color,
                min_width=36,
                min_height=32,
            )
            self.mysql_status_btn.clicked.connect(self.open_mysql_status_tab)

            toolbar = self.db_manager_widget.layout().itemAt(0).layout()

            if toolbar is not None:
                toolbar.addWidget(self.mysql_status_btn)

        except Exception:
            pass

        # Native Menu Bar
        self._init_menu_bar()

        self.task_status_widget = TaskStatusBarWidget(self.services, self)
        self.statusBar().addPermanentWidget(self.task_status_widget)

        self.statusBar().showMessage(
            "Ready — double-click a profile · Shift+Left/Right switch tabs · "
            "Ctrl+K palette · Ctrl+T terminal · Ctrl+Shift+V VAPT · F8 broadcast"
        )

    def _init_shortcuts(self) -> None:
        self._sc = []

        for key, callback in (
            ("Ctrl+K", self.open_palette),
            ("Ctrl+Shift+P", self.open_palette),
            ("Ctrl+T", self._new_terminal_dialog),
            ("Ctrl+W", self._close_current_tab),
            ("Ctrl+B", self.toggle_sidebar),
            ("Ctrl+Shift+A", self.toggle_right_sidebar),
            ("Ctrl+I", self.toggle_copilot),
            ("Ctrl+Shift+L", self.toggle_event_logs),
            ("Ctrl+Shift+V", lambda: self.open_vapt_tab()),
            ("F8", self.toggle_broadcast),
            ("F11", self._toggle_fullscreen),
            ("Ctrl+,", self.open_config),
            ("Ctrl+Shift+T", self._reopen_closed_tab),
        ):
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.activated.connect(callback)

            self._sc.append(shortcut)

        self._app_filter_installed = False
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
            self._app_filter_installed = True

    def _switch_tab_by_delta(self, delta: int) -> None:
        """Switch main workspace tab sequentially by one step (-1 previous, +1 next)."""
        if not hasattr(self, "tabs"):
            return
        count = self.tabs.count()
        if count <= 1 or delta == 0:
            return
        step = -1 if int(delta) < 0 else 1
        next_idx = (self.tabs.currentIndex() + step) % count
        self.tabs.setCurrentIndex(next_idx)

        next_widget = self.tabs.widget(next_idx)
        if next_widget is not None:
            if hasattr(next_widget, "focus_terminal") and callable(
                next_widget.focus_terminal
            ):
                next_widget.focus_terminal()
            else:
                fw = QApplication.focusWidget()
                if fw is None or (
                    fw is not next_widget and not next_widget.isAncestorOf(fw)
                ):
                    next_widget.setFocus()

    @staticmethod
    def _is_text_input_widget(widget: Optional[QWidget]) -> bool:
        """Return True if widget accepts Shift+Left/Right for text selection."""
        if widget is None:
            return False
        if isinstance(
            widget,
            (QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox),
        ):
            return True
        if isinstance(widget, QComboBox) and widget.isEditable():
            return True
        return False

    @staticmethod
    def _is_terminal_delegate_widget(widget: Optional[QWidget]) -> bool:
        """Return True for NativeTerminalWidget and WebEngine views/delegates."""
        if widget is None:
            return False
        from admin_suite.terminal.native_terminal import NativeTerminalWidget

        cur: Any = widget
        while cur is not None:
            if isinstance(cur, NativeTerminalWidget):
                return True
            if isinstance(cur, QWidget) and cur.inherits("QWebEngineView"):
                return True
            cls_name = type(cur).__name__
            if "WebEngine" in cls_name or "RenderWidgetHost" in cls_name:
                return True
            cur = cur.parent()
        return False

    def eventFilter(self, obj, event):
        if (
            event.type() == QEvent.Type.KeyPress
            and event.modifiers() == Qt.KeyboardModifier.ShiftModifier
            and event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right)
        ):
            if not isinstance(obj, QWidget):
                return super().eventFilter(obj, event)
            target_widget = QApplication.focusWidget() or obj
            if isinstance(target_widget, QWidget) and target_widget.window() is self:
                # Never hijack Shift+Left/Right inside text-editing widgets or terminals
                # (terminals emit tab_switch_requested directly once per keystroke).
                if (
                    self._is_text_input_widget(target_widget)
                    or self._is_text_input_widget(obj)
                    or self._is_terminal_delegate_widget(target_widget)
                    or self._is_terminal_delegate_widget(obj)
                ):
                    return super().eventFilter(obj, event)

                delta = -1 if event.key() == Qt.Key.Key_Left else 1
                self._switch_tab_by_delta(delta)
                event.accept()
                return True

        return super().eventFilter(obj, event)

    def _register_actions(self) -> None:
        """Register primary application actions into the ActionRegistry."""
        reg = getattr(self.services, "actions", None)
        if not reg:
            return

        reg.register_action(
            "admin_console.selected",
            "Open Unified Admin Console (selected profile)",
            self.open_admin_console_selected,
            category="Administration",
            shortcut="Ctrl+Shift+D",
            icon="🖥️",
        )
        reg.register_action(
            "vpn.config",
            "Open VPN Configuration & Profiles",
            lambda: self.open_vpn_config_dialog(0),
            category="VPN & Mesh",
            icon="⚙",
        )
        reg.register_action(
            "vpn.wireguard_server",
            "WireGuard VPN Server Setup on Connected Server",
            self.open_wireguard_server_setup,
            category="VPN & Mesh",
            icon="⚡",
        )
        reg.register_action(
            "vpn.toggle",
            "VPN Connect / Disconnect Toggle",
            self.vpn.toggle,
            category="VPN & Mesh",
            icon="🔌",
        )
        reg.register_action(
            "sidebar.right_toggle",
            "Toggle Right Sidebar (Copilot / Event Logs)",
            self.toggle_right_sidebar,
            category="Navigation",
            shortcut="Ctrl+Shift+A",
            icon="▶",
        )
        reg.register_action(
            "ai.copilot",
            "Open AI Copilot",
            lambda: self.toggle_copilot(True),
            category="Tools",
            shortcut="Ctrl+I",
            icon="🤖",
        )
        reg.register_action(
            "logs.events",
            "Open Event & Audit Logs",
            lambda: self.toggle_event_logs(True),
            category="Tools",
            shortcut="Ctrl+Shift+L",
            icon="📋",
        )
        reg.register_action(
            "welcome.open",
            "Open Welcome Screen",
            self.open_welcome_tab,
            category="Navigation",
            icon="🏠",
        )
        reg.register_action(
            "db.manager",
            "Open Database Manager",
            self.open_db_manager,
            category="Database",
            icon="🗄️",
        )
        reg.register_action(
            "terminal.new",
            "New Terminal (selected profile)",
            self._new_terminal_dialog,
            category="Terminal",
            shortcut="Ctrl+T",
            icon="🐚",
        )
        reg.register_action(
            "sftp.open",
            "Open SFTP (selected profile)",
            lambda: self.open_sftp(self._get_selected_profile_name()),
            category="Files & SFTP",
            icon="📁",
        )
        reg.register_action(
            "settings.export_backup",
            "Export Encrypted Settings Backup (.zip)",
            lambda: self.open_settings_backup("export"),
            category="Settings",
            icon="🔒",
        )
        reg.register_action(
            "settings.import_backup",
            "Import Encrypted Settings Backup (.zip)",
            lambda: self.open_settings_backup("import"),
            category="Settings",
            icon="📦",
        )
        reg.register_action(
            "sysadmin.selected",
            "Open SysAdmin Dashboard (selected profile)",
            self.open_sysadmin_selected,
            category="Administration",
            icon="📊",
        )
        reg.register_action(
            "sysadmin.local",
            "Open SysAdmin Dashboard (Localhost)",
            self.open_sysadmin_local,
            category="Administration",
            icon="📊",
        )
        reg.register_action(
            "web.selected",
            "Open Web Hosting Manager (selected profile)",
            self.open_web_manager_selected,
            category="Administration",
            icon="🌐",
        )
        reg.register_action(
            "web.local",
            "Open Web Hosting Manager (Localhost)",
            self.open_web_manager_local,
            category="Administration",
            icon="🌐",
        )
        reg.register_action(
            "security.selected",
            "Open Security Hub (selected profile)",
            self.open_security_hub_selected,
            category="Security",
            icon="🛡️",
        )
        reg.register_action(
            "security.local",
            "Open Security Hub (Localhost)",
            self.open_security_hub_local,
            category="Security",
            icon="🛡️",
        )
        reg.register_action(
            "vapt.audit",
            "Open VAPT & Web Security Audit",
            lambda: self.open_vapt_tab(),
            category="Security",
            shortcut="Ctrl+Shift+V",
            icon="🔍",
        )
        reg.register_action(
            "tunnel.forwarding",
            "Port Forwarding & Tunnels (selected profile)",
            lambda: self.open_port_forwarding(),
            category="VPN & Mesh",
            icon="🔌",
        )
        reg.register_action(
            "terminal.split",
            "Split Terminal (selected profile)",
            self.open_split_selected,
            category="Terminal",
            icon="🪟",
        )
        reg.register_action(
            "ansible.multihost",
            "Ansible & Multi-Host Runner",
            self.open_ansible_tab,
            category="Automation",
            icon="🤖",
        )
        reg.register_action(
            "ansible.playbook",
            "Ansible Playbook / Vault Runner",
            self.open_ansible_playbook_tab,
            category="Automation",
            icon="📜",
        )
        reg.register_action(
            "terminal.local_shell",
            "Local Shell",
            lambda: self.add_local_command_tab("bash", "Local Shell"),
            category="Terminal",
            icon="⚡",
        )
        reg.register_action(
            "terminal.broadcast",
            "Toggle Broadcast Mode",
            self.toggle_broadcast,
            category="Terminal",
            shortcut="F8",
            icon="📢",
        )
        reg.register_action(
            "palette.open",
            "Command Palette",
            self.open_palette,
            category="Navigation",
            shortcut="Ctrl+K",
            icon="⌘",
        )
        reg.register_action(
            "settings.connection",
            "Connection Manager",
            self.open_config,
            category="Settings",
            shortcut="Ctrl+,",
            icon="⚙",
        )
        reg.register_action(
            "settings.theme",
            "Theme Manager",
            self.open_theme_dialog,
            category="Settings",
            icon="🎨",
        )
        reg.register_action(
            "tools.snippets",
            "Snippets Library",
            self.open_snippets,
            category="Tools",
            icon="📝",
        )
        reg.register_action(
            "tools.key_manager",
            "SSH Key Manager",
            lambda: KeyManagerDialog(self, self.services).exec(),
            category="Tools",
            icon="🔑",
        )
        reg.register_action(
            "tools.import_ssh_config",
            "Import ~/.ssh/config",
            self.import_ssh_config,
            category="Tools",
            icon="📥",
        )
        reg.register_action(
            "tools.recordings",
            "Session Recordings",
            lambda: SessionLogViewerDialog(self, self.services).exec(),
            category="Tools",
            icon="🎬",
        )
        reg.register_action(
            "tools.notifications",
            "Notification Center",
            lambda: NotificationCenterDialog(self, self.services).exec(),
            category="Tools",
            icon="🔔",
        )
        reg.register_action(
            "sidebar.left_toggle",
            "Toggle Left Sidebar",
            self.toggle_sidebar,
            category="Navigation",
            shortcut="Ctrl+B",
            icon="◀",
        )
        reg.register_action(
            "profiles.ping_all",
            "Ping All Profiles",
            self._ping_all_profiles,
            category="Profiles",
            icon="📡",
        )
        reg.register_action(
            "tabs.reopen_closed",
            "Reopen Closed Tab",
            self._reopen_closed_tab,
            category="Navigation",
            shortcut="Ctrl+Shift+T",
            icon="🔄",
        )
        reg.register_action(
            "tasks.open",
            "Open Background Task Center",
            lambda: TaskDrawerDialog(self.services, self).exec(),
            category="Tools",
            shortcut="Ctrl+Shift+J",
            icon="⏳",
        )

    def _init_menu_bar(self) -> None:
        menubar = self.menuBar()
        menubar.clear()

        # -------------------- File Menu --------------------
        file_menu = menubar.addMenu("&File")

        act_new_term = file_menu.addAction("➕ New Terminal Tab")
        act_new_term.setShortcut("Ctrl+T")
        act_new_term.triggered.connect(self._new_terminal_dialog)

        act_local_shell = file_menu.addAction("⚡ New Local Shell")
        act_local_shell.triggered.connect(lambda: self.add_local_command_tab("bash", "Local Shell"))

        file_menu.addSeparator()

        act_vapt = file_menu.addAction("🔍 VAPT & Web Security Audit")
        act_vapt.setShortcut("Ctrl+Shift+V")
        act_vapt.triggered.connect(lambda: self.open_vapt_tab())

        act_db = file_menu.addAction("🗄️ Database Manager")
        act_db.triggered.connect(self.open_db_manager)

        act_sec = file_menu.addAction("🛡️ Security Hub")
        act_sec.triggered.connect(self.open_security_hub_selected)

        act_web = file_menu.addAction("🌐 Web Hosting Manager")
        act_web.triggered.connect(self.open_web_manager_selected)

        act_sys = file_menu.addAction("🖥 SysAdmin Dashboard")
        act_sys.triggered.connect(self.open_sysadmin_selected)

        act_ans = file_menu.addAction("📝 Ansible Multi-Host Runner")
        act_ans.triggered.connect(self.open_ansible_tab)

        act_play = file_menu.addAction("📜 Ansible Playbook Executor")
        act_play.triggered.connect(self.open_ansible_playbook_tab)

        file_menu.addSeparator()

        act_import = file_menu.addAction("📥 Import ~/.ssh/config")
        act_import.triggered.connect(self.import_ssh_config)

        act_export_settings = file_menu.addAction("🔒 Export Encrypted Settings...")
        act_export_settings.triggered.connect(
            lambda: self.open_settings_backup("export")
        )

        act_import_settings = file_menu.addAction("📦 Import Encrypted Settings...")
        act_import_settings.triggered.connect(
            lambda: self.open_settings_backup("import")
        )

        act_logs = file_menu.addAction("🎬 Session Recordings")
        act_logs.triggered.connect(lambda: SessionLogViewerDialog(self, self.services).exec())

        file_menu.addSeparator()

        act_close_tab = file_menu.addAction("❌ Close Current Tab")
        act_close_tab.setShortcut("Ctrl+W")
        act_close_tab.triggered.connect(self._close_current_tab)

        act_reopen_tab = file_menu.addAction("🔄 Reopen Closed Tab")
        act_reopen_tab.setShortcut("Ctrl+Shift+T")
        act_reopen_tab.triggered.connect(self._reopen_closed_tab)

        file_menu.addSeparator()

        act_exit = file_menu.addAction("🚪 Exit")
        act_exit.setShortcut("Ctrl+Q")
        act_exit.triggered.connect(self.close)

        # -------------------- Edit Menu --------------------
        edit_menu = menubar.addMenu("&Edit")

        act_add_prof = edit_menu.addAction("➕ Add SSH Profile...")
        act_add_prof.triggered.connect(self.add_profile)

        act_add_db = edit_menu.addAction("➕ Add Database Profile...")
        act_add_db.triggered.connect(self.add_db_profile)

        edit_menu.addSeparator()

        act_snip = edit_menu.addAction("📋 Manage Snippets...")
        act_snip.triggered.connect(self.open_snippets)

        act_keys = edit_menu.addAction("🔑 SSH Key Manager...")
        act_keys.triggered.connect(lambda: KeyManagerDialog(self, self.services).exec())

        edit_menu.addSeparator()

        act_theme = edit_menu.addAction("🎨 Themes...")
        act_theme.triggered.connect(self.open_theme_dialog)

        act_pref = edit_menu.addAction("⚙️ Connection Settings...")
        act_pref.setShortcut("Ctrl+,")
        act_pref.triggered.connect(self.open_config)

        # -------------------- View Menu --------------------
        view_menu = menubar.addMenu("&View")

        act_toggle_side = view_menu.addAction("🗂️ Toggle Left Sidebar")
        act_toggle_side.setShortcut("Ctrl+B")
        act_toggle_side.triggered.connect(self.toggle_sidebar)

        act_toggle_copilot = view_menu.addAction("🤖 Toggle AI Copilot / Right Sidebar")
        act_toggle_copilot.setShortcut("Ctrl+Shift+A")
        act_toggle_copilot.triggered.connect(self.toggle_right_sidebar)

        act_toggle_event = view_menu.addAction("📋 Toggle Event Logs")
        act_toggle_event.setShortcut("Ctrl+Shift+L")
        act_toggle_event.triggered.connect(self.toggle_event_logs)

        act_welcome = view_menu.addAction("🏠 Welcome Screen")
        act_welcome.triggered.connect(self.open_welcome_tab)

        act_pal = view_menu.addAction("🔍 Command Palette...")
        act_pal.setShortcut("Ctrl+K")
        act_pal.triggered.connect(self.open_palette)

        view_menu.addSeparator()

        self.act_show_statusbar = view_menu.addAction("Show Status Bar")
        self.act_show_statusbar.setCheckable(True)
        self.act_show_statusbar.setChecked(True)
        self.act_show_statusbar.toggled.connect(lambda visible: self.statusBar().setVisible(visible))

        view_menu.addSeparator()

        act_fullscreen = view_menu.addAction("⛶ Toggle Full Screen")
        act_fullscreen.setShortcut("F11")
        act_fullscreen.triggered.connect(self._toggle_fullscreen)

        # -------------------- Tools & Security Menu --------------------
        tools_menu = menubar.addMenu("&Tools")

        act_tools_admin = tools_menu.addAction("🖥️ Unified Admin Console")
        act_tools_admin.setShortcut("Ctrl+Shift+D")
        act_tools_admin.triggered.connect(self.open_admin_console_selected)

        act_tools_sec = tools_menu.addAction("🛡️ Security Center")
        act_tools_sec.triggered.connect(self.open_security_hub_selected)

        act_tools_sys = tools_menu.addAction("🖥 SysAdmin Dashboard")
        act_tools_sys.triggered.connect(self.open_sysadmin_selected)

        act_tools_web = tools_menu.addAction("🌐 Web & Virtual Host Manager")
        act_tools_web.triggered.connect(self.open_web_manager_selected)

        act_tools_vapt = tools_menu.addAction("🔍 VAPT Security & Domain Audit")
        act_tools_vapt.setShortcut("Ctrl+Shift+V")
        act_tools_vapt.triggered.connect(lambda: self.open_vapt_tab())

        act_tools_db = tools_menu.addAction("🗄️ Database Manager")
        act_tools_db.triggered.connect(self.open_db_manager)

        act_tools_tunnel = tools_menu.addAction("🔌 Port Forwarding & SSH Tunnels")
        act_tools_tunnel.triggered.connect(lambda: self.open_port_forwarding())

        act_tools_ansible = tools_menu.addAction("📝 Ansible Multi-Host Runner")
        act_tools_ansible.triggered.connect(self.open_ansible_tab)

        act_tools_playbook = tools_menu.addAction("📜 Ansible Playbook Executor")
        act_tools_playbook.triggered.connect(self.open_ansible_playbook_tab)

        tools_menu.addSeparator()

        act_vpn = tools_menu.addAction("⚡ Toggle Active VPN")
        act_vpn.triggered.connect(self.vpn.toggle)

        act_vpn_cfg = tools_menu.addAction("🔒 VPN Configuration & Profiles...")
        act_vpn_cfg.triggered.connect(lambda: self.open_vpn_config_dialog(0))

        act_wg_srv = tools_menu.addAction("🛡️ WireGuard VPN Server Setup on Connected Server...")
        act_wg_srv.triggered.connect(self.open_wireguard_server_setup)

        tools_menu.addSeparator()

        act_bcast = tools_menu.addAction("📢 Toggle Broadcast Mode")
        act_bcast.setShortcut("F8")
        act_bcast.triggered.connect(self.toggle_broadcast)

        # -------------------- Window Menu --------------------
        win_menu = menubar.addMenu("&Window")

        act_next_tab = win_menu.addAction("➡️ Next Tab")
        act_next_tab.setShortcut("Ctrl+Tab")
        act_next_tab.triggered.connect(self._next_tab)

        act_prev_tab = win_menu.addAction("⬅️ Previous Tab")
        act_prev_tab.setShortcut("Ctrl+Shift+Tab")
        act_prev_tab.triggered.connect(self._prev_tab)

        win_menu.addSeparator()

        act_close_others = win_menu.addAction("Close Other Tabs")
        act_close_others.triggered.connect(self._close_other_tabs)

        act_close_all = win_menu.addAction("Close All Tabs")
        act_close_all.triggered.connect(self._close_all_tabs)

        # -------------------- Help Menu --------------------
        help_menu = menubar.addMenu("&Help")

        act_help_pal = help_menu.addAction("🔍 Command Palette Guide")
        act_help_pal.triggered.connect(self.open_palette)

        act_help_notif = help_menu.addAction("🔔 Notification Center")
        act_help_notif.triggered.connect(lambda: NotificationCenterDialog(self, self.services).exec())

        act_help_keys = help_menu.addAction("📘 Keyboard Shortcuts Reference")
        act_help_keys.triggered.connect(self._show_shortcuts_dialog)

        help_menu.addSeparator()

        act_about = help_menu.addAction("ℹ️ About Admin Suite")
        act_about.triggered.connect(self._show_about_dialog)

    def _next_tab(self) -> None:
        idx = self.tabs.currentIndex()
        if idx < self.tabs.count() - 1:
            self.tabs.setCurrentIndex(idx + 1)
        elif self.tabs.count() > 0:
            self.tabs.setCurrentIndex(0)

    def _prev_tab(self) -> None:
        idx = self.tabs.currentIndex()
        if idx > 0:
            self.tabs.setCurrentIndex(idx - 1)
        elif self.tabs.count() > 0:
            self.tabs.setCurrentIndex(self.tabs.count() - 1)

    def _close_other_tabs(self) -> None:
        curr = self.tabs.currentIndex()
        for idx in reversed(range(self.tabs.count())):
            if idx != curr and self.tabs.widget(idx) != self.welcome_widget:
                self.close_tab(idx)

    def _close_all_tabs(self) -> None:
        for idx in reversed(range(self.tabs.count())):
            if self.tabs.widget(idx) != self.welcome_widget:
                self.close_tab(idx)

    def _toggle_fullscreen(self) -> None:
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def _show_shortcuts_dialog(self) -> None:
        text = (
            "<b>Keyboard Shortcuts:</b><br><br>"
            "• <b>Ctrl+T</b> — New Terminal<br>"
            "• <b>Ctrl+W</b> — Close Current Tab<br>"
            "• <b>Ctrl+Shift+T</b> — Reopen Closed Tab<br>"
            "• <b>Ctrl+Shift+V</b> — Open VAPT & Web Security Audit<br>"
            "• <b>Ctrl+K / Ctrl+Shift+P</b> — Command Palette<br>"
            "• <b>Ctrl+B</b> — Toggle Left Sidebar<br>"
            "• <b>Ctrl+Shift+A</b> — Toggle Copilot / Right Sidebar<br>"
            "• <b>Ctrl+Shift+L</b> — Toggle Event Logs<br>"
            "• <b>Ctrl+,</b> — Connection Settings<br>"
            "• <b>F8</b> — Toggle Broadcast Mode<br>"
            "• <b>F11</b> — Toggle Fullscreen<br>"
            "• <b>Shift+Left / Shift+Right</b> — Switch Open Tabs<br>"
            "• <b>Ctrl+Tab / Ctrl+Shift+Tab</b> — Switch Tabs<br>"
        )
        QMessageBox.information(self, "Keyboard Shortcuts", text)

    def _show_about_dialog(self) -> None:
        from admin_suite import __version__
        text = (
            f"<h2>Admin Suite v{__version__}</h2>"
            "<p>A comprehensive multi-platform Linux Administration, Remote Server Management, "
            "Database Administration, VAPT Web Security Auditing, and Automation Suite.</p>"
            "<p><b>Features:</b></p>"
            "<ul>"
            "<li>SSH Terminal & Local Shell with Broadcast Mode</li>"
            "<li>VAPT Web Application & Domain Security Auditor</li>"
            "<li>Unified Security Hub (Firewall, Fail2ban, Rootkits, SSL)</li>"
            "<li>Database Manager (MySQL, PostgreSQL, SQLite)</li>"
            "<li>Web Hosting & Virtual Host Management</li>"
            "<li>SysAdmin Realtime Monitoring</li>"
            "<li>Ansible Multi-Host & Playbook Automation</li>"
            "<li>SFTP File Manager & SSH Tunnels</li>"
            "</ul>"
        )
        QMessageBox.about(self, "About Admin Suite", text)

    # ------------------------------------------------------------
    # Notifications/debug
    # ------------------------------------------------------------

    def _on_notification(self, n) -> None:
        Toast(
            self.services.theme.current,
            n["level"],
            n["title"],
            n["msg"],
            self,
        )

        count = len(self.services.notifications.items)
        theme = self.services.theme.current
        icon_name = "bell_ring" if count > 0 else "bell"
        icon_col = theme.get("warn", "#f39c12") if count > 0 else theme.get("text", "#eff0f1")
        self.bell_btn.setIcon(get_icon(icon_name, color=icon_col, size=20))
        self.bell_btn.setToolTip(f"Notifications ({count})")
        self.bell_btn.setAccessibleName(f"Notifications ({count})")

    def append_debug(self, text: str) -> None:
        """Compatibility helper to append or emit debug text."""
        self.services.events.info("SYSTEM", text)

    def _notify(self, msg: str, timeout: int = 4000) -> None:
        self.statusBar().showMessage(msg, timeout)

    # ------------------------------------------------------------
    # VPN UI
    # ------------------------------------------------------------

    def _vpn_dispatch(self, res: dict) -> None:
        op = res.get("op")

        if op == "poll_status":
            self._apply_polled_vpn_state(res)

        elif op == "toggle_status":
            self._toggle_after_state_check(res)

        elif op == "connect":
            self._finish_vpn_connect(res)

        elif op == "disconnect":
            self._finish_vpn_disconnect(res)

    def update_vpn_ui(
        self,
        connected: bool,
        busy: bool = False,
        busy_action: Optional[str] = None,
    ) -> None:
        theme = self.services.theme.current

        if busy:
            self.vpn_btn.setEnabled(False)

            if busy_action == "connect":
                tip = "VPN: Connecting..."
                status = "● VPN: Connecting..."
                color = theme["warn"]
                icon_name = "sync"

            elif busy_action == "disconnect":
                tip = "VPN: Disconnecting..."
                status = "● VPN: Disconnecting..."
                color = theme["warn"]
                icon_name = "sync"

            else:
                tip = "VPN: Checking..."
                status = "● VPN: Checking..."
                color = theme["sub"]
                icon_name = "vpn"

        else:
            self.vpn_btn.setEnabled(True)
            prof = self.vpn.get_active_profile()
            p_name = f" ({prof.get('name')})" if prof else ""

            if connected:
                tip = f"Disconnect VPN{p_name}"
                status = f"● VPN{p_name}: Connected"
                color = theme["ok"]
                icon_name = "shield_check"

            else:
                tip = f"Connect VPN{p_name}"
                status = f"● VPN{p_name}: Disconnected"
                color = theme["warn"]
                icon_name = "vpn"

        self.vpn_btn.setText("")
        self.vpn_btn.setIcon(get_icon(icon_name, color=color, size=20))
        self.vpn_btn.setToolTip(tip)
        self.vpn_btn.setAccessibleName(tip)
        self.vpn_btn.setStyleSheet(
            f"QPushButton#IconToolBtn {{ border-color:{color}; }}"
            if connected
            else ""
        )

        self.vpn_status.setText(status)

        self.vpn_status.setStyleSheet(
            f"color:{color};padding:0 8px;font-weight:500;"
        )

    def _apply_polled_vpn_state(self, res: dict) -> None:
        state = res.get("state")

        if state is None:
            if not self.vpn.busy:
                self.vpn_status.setText("● VPN: Unknown")

                self.vpn_status.setStyleSheet(
                    f"color:{self.services.theme.current['sub']};padding:0 8px;"
                )

        else:
            self.vpn.connected = bool(state)

            if not self.vpn.busy:
                self.update_vpn_ui(bool(state))

        if res.get("pending_toggle"):
            QTimer.singleShot(50, self.vpn.toggle)

    def _toggle_after_state_check(self, res: dict) -> None:
        state = res.get("state")

        if state is None:
            state = bool(self.vpn.connected)
        else:
            self.vpn.connected = bool(state)

        self.update_vpn_ui(bool(state))

        if state:
            self.vpn.disconnect_vpn()
        else:
            self.vpn.connect_vpn()

    def _finish_vpn_connect(self, res: dict) -> None:
        if res.get("error"):
            self.update_vpn_ui(False)

            self.services.notifications.push(
                "error",
                "VPN",
                res["error"],
            )

            return

        state = res.get("state")
        rc = res.get("rc", -1)

        if state is None:
            connected = rc == 0
        else:
            connected = bool(state)

        self.vpn.connected = connected

        self.update_vpn_ui(connected)

        if connected:
            self.services.notifications.push("ok", "VPN", "Connected")
            self.services.audit("VPN", "VPN connection established")
        else:
            msg = (
                res.get("err", "").strip()
                or res.get("out", "").strip()
                or "VPN connection failed"
            )[:300]

            self.services.notifications.push("error", "VPN", msg)
            self.services.emit_log("VPN", f"VPN connection failed: {msg}", "ERROR")

    def _finish_vpn_disconnect(self, res: dict) -> None:
        if res.get("error"):
            self.update_vpn_ui(self.vpn.connected)

            self.services.notifications.push(
                "error",
                "VPN",
                res["error"],
            )

            return

        state = res.get("state")
        rc = res.get("rc", -1)

        if state is None:
            connected = False if rc == 0 else bool(self.vpn.connected)
        else:
            connected = bool(state)

        self.vpn.connected = connected

        self.update_vpn_ui(connected)

        if not connected:
            self.services.notifications.push("info", "VPN", "Disconnected")
            self.services.audit("VPN", "VPN disconnected")
        else:
            msg = (
                res.get("err", "").strip()
                or res.get("out", "").strip()
                or "VPN disconnect failed"
            )[:300]

            self.services.notifications.push("error", "VPN", msg)
            self.services.emit_log("VPN", f"VPN disconnect failed: {msg}", "ERROR")

    # ------------------------------------------------------------
    # Profiles
    # ------------------------------------------------------------

    def load_profiles(self) -> None:
        self.profiles = read_json(PROFILES_FILE, {}) or {}

        # Ensure a default localhost profile is present for local administration
        if "localhost" not in self.profiles and "Localhost" not in self.profiles:
            import getpass
            current_user = getpass.getuser()
            self.profiles["localhost"] = {
                "name": "localhost",
                "group": "Local System",
                "tags": "localhost, system",
                "favorite": True,
                "ssh_host": "127.0.0.1",
                "ssh_user": current_user,
                "ssh_port": "22",
                "auth_method": "Password",
                "is_local": True,
                "use_local_exec": True,
            }

        for name, data in self.profiles.items():
            ssh_pass = self.services.secrets.get(f"prof_{name}", "")
            if ssh_pass:
                data["ssh_pass"] = ssh_pass

            jump_pass = self.services.secrets.get(f"prof_jump_{name}", "")
            if jump_pass:
                data["jump_pass"] = jump_pass

            sudo_pass = self.services.secrets.get(f"prof_sudo_{name}", "")
            if sudo_pass:
                data["sudo_pass"] = sudo_pass
                self.services.set_sudo_password(name, sudo_pass)
                if name.lower() in ("localhost", "local"):
                    self.services.set_sudo_password("Localhost", sudo_pass)
                    self.services.set_sudo_password("localhost", sudo_pass)
                    self.services.set_sudo_password("Local", sudo_pass)

        self.refresh_profile_tree()

    def save_profiles(self) -> None:
        clean = {}

        for name, data in self.profiles.items():
            c = dict(data)

            ssh_pass = c.pop("ssh_pass", "") or ""
            jump_pass = c.pop("jump_pass", "") or ""
            sudo_pass = c.pop("sudo_pass", "") or ""

            self.services.secrets.set(f"prof_{name}", ssh_pass)
            self.services.secrets.set(f"prof_jump_{name}", jump_pass)
            if sudo_pass:
                self.services.secrets.set(f"prof_sudo_{name}", sudo_pass)

            clean[name] = c

        write_json_secure(PROFILES_FILE, clean)
        if hasattr(self, "welcome_widget"):
            self.welcome_widget.refresh_stats()

    def refresh_profile_tree(self, *args) -> None:
        import getpass
        self.profile_tree.clear()

        theme = self.services.theme.current

        filt = self.profile_filter.text().lower()

        groups = {}
        favorites = []

        for name, data in self.profiles.items():
            haystack = (
                name
                + " "
                + data.get("tags", "")
                + " "
                + data.get("group", "")
                + " "
                + str(data.get("ssh_host", ""))
                + " "
                + str(data.get("ssh_user", ""))
            ).lower()

            if filt and filt not in haystack:
                continue

            if data.get("favorite"):
                favorites.append((name, data))

            groups.setdefault(
                data.get("group", "Default"),
                [],
            ).append((name, data))

        def add_group(group_name, items, icon="📁"):
            group_item = QTreeWidgetItem([f"{icon} {group_name} ({len(items)})"])
            group_item.setForeground(0, QColor(theme["accent"]))

            font = group_item.font(0)
            font.setBold(True)
            group_item.setFont(0, font)

            for name, data in sorted(items, key=lambda x: x[0].lower()):
                is_local = (
                    bool(data.get("is_local"))
                    or name.lower() == "localhost"
                    or (
                        data.get("ssh_host") in ("localhost", "127.0.0.1")
                        and bool(data.get("use_local_exec", True))
                    )
                )

                user = data.get("ssh_user") or (getpass.getuser() if is_local else "")
                host = data.get("ssh_host", "127.0.0.1" if is_local else "")
                port = str(data.get("ssh_port", "22"))

                if is_local:
                    status = "ok"
                    dot = "💻"
                    host_info = f"{user}@localhost"
                    jump_info = ""
                else:
                    status = self._profile_status.get(name, "unknown")
                    dot = {
                        "ok": "🟢",
                        "fail": "🔴",
                    }.get(status, "⚪")
                    host_info = f"{user}@{host}:{port}"
                    jump_info = ""
                    if data.get("use_jump"):
                        j_target = data.get("jump_profile") or data.get("jump_host") or "bastion"
                        jump_info = f"  🔀 {j_target}"

                fav_star = " ⭐" if data.get("favorite") else ""
                child = QTreeWidgetItem([f"{dot} {name}  [{host_info}]{jump_info}{fav_star}"])
                child.setData(0, Qt.ItemDataRole.UserRole, name)

                auth_type = "Local Shell" if is_local else data.get("auth_method", "Password")
                jump_desc = (data.get("jump_profile") or data.get("jump_host")) if data.get("use_jump") else "Direct"
                has_sudo = bool(data.get("sudo_pass") or self.services.get_sudo_password(name))
                sudo_desc = "Configured" if has_sudo else ("root user" if user == "root" else "Prompts as needed")

                child.setToolTip(
                    0,
                    f"Profile: {name}\n"
                    f"Target: {host_info}\n"
                    f"Auth: {auth_type}\n"
                    f"Jump Route: {jump_desc}\n"
                    f"Elevation: {sudo_desc}\n"
                    f"Status: {status}\n"
                    f"Tags: {data.get('tags', '')}\n"
                    f"Group: {data.get('group', 'Default')}",
                )

                group_item.addChild(child)

            self.profile_tree.addTopLevelItem(group_item)
            group_item.setExpanded(True)

        if favorites:
            add_group("Favorites", favorites, "⭐")

        for group_name in sorted(groups):
            add_group(group_name, groups[group_name])

    def add_profile(self) -> None:
        dialog = ProfileDialog(self, self.services)

        if dialog.exec() == QDialog.DialogCode.Accepted:
            data = dialog.get_data()

            if data["name"] in self.profiles:
                QMessageBox.warning(
                    self,
                    "Duplicate",
                    "Profile name already exists.",
                )
                return

            self.profiles[data["name"]] = data

            self.save_profiles()
            self.refresh_profile_tree()

            self.services.notifications.push(
                "ok",
                "Profile added",
                data["name"],
            )
    def duplicate_profile(self) -> None:
        name = self._get_selected_profile_name()

        if not name:
            QMessageBox.information(self, "Duplicate", "Select a profile to duplicate.")
            return

        orig_data = self.profiles.get(name)
        if not orig_data:
            return

        data = dict(orig_data)
        base_name = f"{name} (Copy)"
        dup_name = base_name
        counter = 2
        while dup_name in self.profiles:
            dup_name = f"{name} (Copy {counter})"
            counter += 1

        data["name"] = dup_name

        # Ensure passwords from secrets are carried over
        ssh_pass = self.services.secrets.get(f"prof_{name}", "") or data.get("ssh_pass", "")
        jump_pass = self.services.secrets.get(f"prof_jump_{name}", "") or data.get("jump_pass", "")
        sudo_pass = self.services.secrets.get(f"prof_sudo_{name}", "") or data.get("sudo_pass", "")

        if ssh_pass:
            data["ssh_pass"] = ssh_pass
        if jump_pass:
            data["jump_pass"] = jump_pass
        if sudo_pass:
            data["sudo_pass"] = sudo_pass

        dialog = ProfileDialog(self, self.services, edit_data=data)
        dialog.setWindowTitle(f"Duplicate Profile — {name}")

        if dialog.exec() == QDialog.DialogCode.Accepted:
            new_data = dialog.get_data()
            new_name = new_data["name"]

            if new_name in self.profiles:
                QMessageBox.warning(self, "Duplicate", f"Profile '{new_name}' already exists.")
                return

            self.profiles[new_name] = new_data
            self.save_profiles()
            self.refresh_profile_tree()

            self.services.notifications.push(
                "ok",
                "Profile duplicated",
                f"Cloned '{name}' to '{new_name}'",
            )
            self.services.audit("SSH", f"Duplicated profile '{name}' to '{new_name}'")

    def edit_profile(self) -> None:
        name = self._get_selected_profile_name()

        if not name:
            QMessageBox.information(self, "Edit", "Select a profile.")
            return

        data = dict(self.profiles.get(name) or {})
        data["name"] = name

        dialog = ProfileDialog(self, self.services, edit_data=data)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        new = dialog.get_data()

        if new["name"] != name:
            if new["name"] in self.profiles:
                QMessageBox.warning(self, "Duplicate", "Name exists.")
                return

            del self.profiles[name]

            self.services.secrets.set(f"prof_{name}", "")
            self.services.secrets.set(f"prof_jump_{name}", "")

        self.profiles[new["name"]] = new

        self.save_profiles()
        self.refresh_profile_tree()

        self.services.notifications.push(
            "ok",
            "Profile updated",
            new["name"],
        )
        self.services.audit("SSH", f"Updated profile '{new['name']}'")

    def delete_profile(self) -> None:
        name = self._get_selected_profile_name()

        if not name:
            QMessageBox.information(self, "Delete", "Select a profile.")
            return

        if QMessageBox.question(
            self,
            "Delete",
            f"Delete profile '{name}'?",
        ) != QMessageBox.StandardButton.Yes:
            return

        if name in self.profiles:
            del self.profiles[name]

        self.services.secrets.set(f"prof_{name}", "")
        self.services.secrets.set(f"prof_jump_{name}", "")

        self.save_profiles()
        self.refresh_profile_tree()

        self.services.notifications.push(
            "info",
            "Profile deleted",
            name,
        )
        self.services.audit("SSH", f"Deleted profile '{name}'")

    def _get_selected_profile_name(self) -> Optional[str]:
        item = self.profile_tree.currentItem()

        if item and item.parent():
            return item.data(0, Qt.ItemDataRole.UserRole)

        return None

    def show_profile_menu(self, pos) -> None:
        item = self.profile_tree.itemAt(pos)

        menu = QMenu(self)

        name = None

        if item and item.parent():
            name = item.data(0, Qt.ItemDataRole.UserRole)

        if name:
            menu.addAction("🐚 Open Terminal").triggered.connect(
                lambda: self.connect_profile(name)
            )

            menu.addAction("📁 Open SFTP").triggered.connect(
                lambda: self.open_sftp(name)
            )

            menu.addAction("🖥 SysAdmin Dashboard").triggered.connect(
                lambda: self.open_sysadmin(name)
            )

            menu.addAction("🌐 Web Hosting Manager").triggered.connect(
                lambda: self.open_web_manager(name)
            )

            menu.addAction("🛡️ Security Hub").triggered.connect(
                lambda: self.open_security_hub(name)
            )

            menu.addAction("🔌 Port Forwarding & Tunnels").triggered.connect(
                lambda: self.open_port_forwarding(name)
            )

            menu.addAction("⧉ Split Terminal").triggered.connect(
                lambda: (
                    self.profile_tree.setCurrentItem(item),
                    self.open_split_selected(),
                )
            )

            menu.addSeparator()

            menu.addAction("📡 Test Connection").triggered.connect(
                lambda: self._ping_profile(name)
            )

            menu.addSeparator()

            menu.addAction("📋 Duplicate Profile").triggered.connect(
                self.duplicate_profile
            )
            menu.addAction("✏️ Edit").triggered.connect(self.edit_profile)
            menu.addAction("🗑 Delete").triggered.connect(self.delete_profile)

        else:
            menu.addAction("➕ Add Profile").triggered.connect(self.add_profile)

            menu.addAction("📥 Import ~/.ssh/config").triggered.connect(
                self.import_ssh_config
            )

        menu.exec(self.profile_tree.viewport().mapToGlobal(pos))

    def on_profile_activated(self, item: QTreeWidgetItem, column: int) -> None:
        if item.parent():
            name = item.data(0, Qt.ItemDataRole.UserRole)

            if name:
                #self.connect_profile(name)
                self.open_split_selected()

    def import_ssh_config(self) -> None:
        path = os.path.expanduser("~/.ssh/config")

        if not os.path.exists(path):
            QMessageBox.information(self, "Import", f"{path} not found.")
            return

        imported = 0

        for profile in parse_ssh_config(path):
            if profile["name"] not in self.profiles:
                self.profiles[profile["name"]] = profile
                imported += 1

        self.save_profiles()
        self.refresh_profile_tree()

        self.services.notifications.push(
            "ok",
            "SSH config imported",
            f"{imported} new profile(s)",
        )

    # ------------------------------------------------------------
    # Ping
    # ------------------------------------------------------------

    def _ping_profile(self, name: str) -> None:
        data = self.profiles.get(name)

        if not data:
            return

        self._profile_status[name] = "unknown"
        self.refresh_profile_tree()

        worker = RemoteExecThread(
            data,
            "echo OK && hostname",
            timeout=8,
        )

        def _on(out: str, rc: int, n=name):
            self._profile_status[n] = "ok" if rc == 0 else "fail"
            self.refresh_profile_tree()

            self._notify(f"{n}: {'OK' if rc == 0 else 'FAIL'}")

        worker.finished_cmd.connect(_on)

        worker.start()

        self._ping_threads = [
            x for x in self._ping_threads if x.isRunning()
        ] + [worker]

    def _ping_all_profiles(self) -> None:
        self._notify("Pinging all profiles...")

        self._ping_queue = list(self.profiles.keys())
        self._ping_active = 0

        self._ping_max = int(
            self.services.config.get("ping_max_concurrency", 8)
        )

        self._process_ping_queue()

    def _process_ping_queue(self) -> None:
        while self._ping_active < self._ping_max and self._ping_queue:
            name = self._ping_queue.pop(0)

            data = self.profiles.get(name)

            if not data:
                continue

            self._ping_active += 1

            self._profile_status[name] = "unknown"
            self.refresh_profile_tree()

            worker = RemoteExecThread(
                data,
                "echo OK && hostname",
                timeout=8,
            )

            def _on(out: str, rc: int, n=name):
                self._profile_status[n] = "ok" if rc == 0 else "fail"

                self.refresh_profile_tree()

                self._notify(f"{n}: {'OK' if rc == 0 else 'FAIL'}")

                self._ping_active = max(0, self._ping_active - 1)

                self._process_ping_queue()

            worker.finished_cmd.connect(_on)

            worker.start()

            self._ping_threads = [
                x for x in self._ping_threads if x.isRunning()
            ] + [worker]

        if not self._ping_queue and self._ping_active == 0:
            self.refresh_profile_tree()

    # ------------------------------------------------------------
    # DB profiles
    # ------------------------------------------------------------

    def load_db_profiles(self) -> None:
        self.db_profiles = read_json(DB_PROFILES_FILE, {}) or {}

        for name, data in self.db_profiles.items():
            db_pass = self.services.secrets.get(f"dbprof_{name}", "")

            if db_pass:
                data["db_pass"] = db_pass

            ssh_pass = self.services.secrets.get(f"dbprof_ssh_{name}", "")

            if ssh_pass:
                data["ssh_pass"] = ssh_pass

        self.refresh_db_list()

    def save_db_profiles(self) -> None:
        clean = {}

        for name, data in self.db_profiles.items():
            c = dict(data)

            db_pass = c.pop("db_pass", "") or ""
            ssh_pass = c.pop("ssh_pass", "") or ""

            self.services.secrets.set(f"dbprof_{name}", db_pass)
            self.services.secrets.set(f"dbprof_ssh_{name}", ssh_pass)

            clean[name] = c

        write_json_secure(DB_PROFILES_FILE, clean)
        if hasattr(self, "welcome_widget"):
            self.welcome_widget.refresh_stats()

    def refresh_db_list(self) -> None:
        self.db_list.clear()

        filt = self.db_filter.text().lower() if hasattr(self, "db_filter") else ""

        for name, data in sorted(self.db_profiles.items()):
            backend = str(data.get("backend", "mysql")).lower()
            db_host = data.get("db_host", "")
            db_port = data.get("db_port", "")
            db_user = data.get("db_user", "")
            db_name = data.get("db_name", "")
            sqlite_path = data.get("sqlite_path", "")

            haystack = f"{name} {backend} {db_host} {db_user} {db_name} {sqlite_path}".lower()
            if filt and filt not in haystack:
                continue

            icon = {
                "mysql": "🐬",
                "postgresql": "🐘",
                "postgres": "🐘",
                "sqlite": "📁",
            }.get(backend, "🗄️")

            tunnel_badge = ""
            if data.get("use_tunnel"):
                prof = data.get("ssh_profile") or "SSH"
                tunnel_badge = f"  🔀 {prof}"

            if backend == "sqlite":
                filename = os.path.basename(sqlite_path) or sqlite_path or "unnamed.db"
                item_text = f"{icon} {name}  [SQLite • {filename}]"
                tooltip = f"DB Profile: {name}\nBackend: SQLite\nPath: {sqlite_path}"
            else:
                target = f"{db_user}@{db_host}:{db_port}" if db_user else f"{db_host}:{db_port}"
                if db_name:
                    target += f"/{db_name}"
                item_text = f"{icon} {name}  [{target}]{tunnel_badge}"
                tooltip = (
                    f"DB Profile: {name}\n"
                    f"Backend: {backend.upper()}\n"
                    f"Host: {db_host}:{db_port}\n"
                    f"User: {db_user}\n"
                    f"Database: {db_name or '(default)'}\n"
                    f"SSH Tunnel: {'Enabled (' + (data.get('ssh_profile') or 'Custom') + ')' if data.get('use_tunnel') else 'Direct'}"
                )

            item = QListWidgetItem(item_text)
            item.setData(Qt.ItemDataRole.UserRole, name)
            item.setToolTip(tooltip)
            self.db_list.addItem(item)

    def _get_selected_db_profile_name(self) -> Optional[str]:
        item = self.db_list.currentItem()

        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def duplicate_db_profile(self) -> None:
        name = self._get_selected_db_profile_name()

        if not name:
            QMessageBox.information(self, "Duplicate", "Select a DB profile to duplicate.")
            return

        orig_data = self.db_profiles.get(name)
        if not orig_data:
            return

        data = dict(orig_data)
        base_name = f"{name} (Copy)"
        dup_name = base_name
        counter = 2
        while dup_name in self.db_profiles:
            dup_name = f"{name} (Copy {counter})"
            counter += 1

        data["name"] = dup_name

        db_pass = self.services.secrets.get(f"dbprof_{name}", "") or data.get("db_pass", "")
        ssh_pass = self.services.secrets.get(f"dbprof_ssh_{name}", "") or data.get("ssh_pass", "")
        if db_pass:
            data["db_pass"] = db_pass
        if ssh_pass:
            data["ssh_pass"] = ssh_pass

        dialog = DbProfileDialog(self, self.services, edit_data=data)
        dialog.setWindowTitle(f"Duplicate DB Profile — {name}")

        if dialog.exec() == QDialog.DialogCode.Accepted:
            new_data = dialog.get_data()
            new_name = new_data["name"]

            if new_name in self.db_profiles:
                QMessageBox.warning(self, "Duplicate", f"DB profile '{new_name}' already exists.")
                return

            self.db_profiles[new_name] = new_data
            self.save_db_profiles()
            self.refresh_db_list()

            self.services.notifications.push(
                "ok",
                "DB Profile Duplicated",
                f"Cloned '{name}' to '{new_name}'",
            )
            self.services.audit("DB", f"Duplicated DB profile '{name}' to '{new_name}'")

    def add_db_profile(self) -> None:
        dialog = DbProfileDialog(self, self.services)

        if dialog.exec() == QDialog.DialogCode.Accepted:
            data = dialog.get_data()

            if data["name"] in self.db_profiles:
                QMessageBox.warning(
                    self,
                    "Duplicate",
                    "DB profile name already exists.",
                )
                return

            self.db_profiles[data["name"]] = data

            self.save_db_profiles()
            self.refresh_db_list()

            self.services.notifications.push(
                "ok",
                "DB profile added",
                data["name"],
            )
            self.services.audit("DB", f"Added DB profile '{data['name']}' ({data.get('backend', 'mysql')})")

    def edit_db_profile(self) -> None:
        name = self._get_selected_db_profile_name()

        if not name:
            QMessageBox.information(self, "Edit", "Select a DB profile.")
            return

        data = dict(self.db_profiles.get(name) or {})
        data["name"] = name

        dialog = DbProfileDialog(self, self.services, edit_data=data)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        new = dialog.get_data()

        if new["name"] != name:
            if new["name"] in self.db_profiles:
                QMessageBox.warning(self, "Duplicate", "Name exists.")
                return

            del self.db_profiles[name]

            self.services.secrets.set(f"dbprof_{name}", "")
            self.services.secrets.set(f"dbprof_ssh_{name}", "")

        self.db_profiles[new["name"]] = new

        self.save_db_profiles()
        self.refresh_db_list()
        self.services.audit("DB", f"Updated DB profile '{new['name']}' ({new.get('backend', 'mysql')})")

    def delete_db_profile(self) -> None:
        name = self._get_selected_db_profile_name()

        if not name:
            QMessageBox.information(self, "Delete", "Select a DB profile.")
            return

        if QMessageBox.question(
            self,
            "Delete",
            f"Delete DB profile '{name}'?",
        ) != QMessageBox.StandardButton.Yes:
            return

        if name in self.db_profiles:
            del self.db_profiles[name]

        self.services.secrets.set(f"dbprof_{name}", "")
        self.services.secrets.set(f"dbprof_ssh_{name}", "")

        self.save_db_profiles()
        self.refresh_db_list()
        self.services.audit("DB", f"Deleted DB profile '{name}'")

    def show_db_menu(self, pos) -> None:
        item = self.db_list.itemAt(pos)

        menu = QMenu(self)

        if item:
            self.db_list.setCurrentItem(item)

            name = item.data(Qt.ItemDataRole.UserRole)

            menu.addAction("🔌 Connect").triggered.connect(
                lambda: self.activate_db_profile(name)
            )

            menu.addSeparator()

            menu.addAction("📋 Duplicate Profile").triggered.connect(
                self.duplicate_db_profile
            )
            menu.addAction("✏️ Edit").triggered.connect(self.edit_db_profile)
            menu.addAction("🗑 Delete").triggered.connect(self.delete_db_profile)

        else:
            menu.addAction("➕ Add DB Profile").triggered.connect(
                self.add_db_profile
            )

        menu.exec(self.db_list.viewport().mapToGlobal(pos))

    def on_db_profile_activated(self, item: QListWidgetItem) -> None:
        name = item.data(Qt.ItemDataRole.UserRole)

        if name:
            self.activate_db_profile(name)

    def open_db_manager(self) -> DatabaseManagerWidget:
        if self.tabs.indexOf(self.db_manager_widget) == -1:
            icon = get_icon("database", color=self.services.theme.current.get("text", "#e2e8f0"), size=16)
            self.tabs.addTab(self.db_manager_widget, icon, "Database Manager")
        self.tabs.setCurrentWidget(self.db_manager_widget)
        self.db_manager_widget.show()
        return self.db_manager_widget

    def open_welcome_tab(self) -> WelcomeWidget:
        idx = self.tabs.indexOf(self.welcome_widget)
        if idx == -1:
            idx = self.tabs.insertTab(0, self.welcome_widget, "🏠 Welcome")
            self.tabs.tabBar().setTabButton(
                idx,
                QTabBar.ButtonPosition.RightSide,
                None,
            )
        self.tabs.setCurrentIndex(idx)
        return self.welcome_widget

    def activate_db_profile(self, name: str) -> None:
        data = self.db_profiles.get(name)

        if not data:
            return

        profile = dict(data)

        self.open_db_manager()
        self.db_manager_widget.set_active_profile(profile)
        self.db_manager_widget.load_schemas()

        self._notify(f"Database profile activated: {name}")
        self.services.emit_log(
            "DB",
            f"Database profile activated: {name} ({profile.get('backend', 'mysql')})",
            "INFO",
        )

    # ------------------------------------------------------------
    # Recent
    # ------------------------------------------------------------

    def load_recent(self) -> None:
        self.recent_connections = read_json(RECENT_FILE, []) or []
        self.refresh_recent()

    def add_recent(self, name: str) -> None:
        if name in self.recent_connections:
            self.recent_connections.remove(name)

        self.recent_connections.insert(0, name)
        self.recent_connections = self.recent_connections[:10]

        write_json_secure(RECENT_FILE, self.recent_connections)

        self.refresh_recent()

    def refresh_recent(self) -> None:
        self.recent_list.clear()

        for name in self.recent_connections:
            if name in self.profiles:
                self.recent_list.addItem(name)

        if hasattr(self, "welcome_widget"):
            self.welcome_widget.refresh_recent()

    def on_recent_activated(self, item: QListWidgetItem) -> None:
        if item.text() in self.profiles:
            self.connect_profile(item.text())

    # ------------------------------------------------------------
    # Terminals
    # ------------------------------------------------------------

    def _connect_terminal(self, terminal, *, connect_tab_switch: bool = True) -> None:
        try:
            terminal.input_sent.disconnect(self._on_terminal_input)
        except Exception:
            pass

        terminal.input_sent.connect(self._on_terminal_input)

        if hasattr(terminal, "tab_switch_requested"):
            try:
                terminal.tab_switch_requested.disconnect(self._switch_tab_by_delta)
            except Exception:
                pass
            if connect_tab_switch:
                terminal.tab_switch_requested.connect(self._switch_tab_by_delta)

        if hasattr(terminal, "terminal_theme_apply_all_requested"):
            try:
                terminal.terminal_theme_apply_all_requested.disconnect(
                    self.apply_theme_to_open_terminals
                )
            except Exception:
                pass
            terminal.terminal_theme_apply_all_requested.connect(
                self.apply_theme_to_open_terminals
            )

    def _on_terminal_input(self, data: str) -> None:
        if not self.broadcast_enabled:
            return

        sender = self.sender()

        for terminal in self.get_all_terminals():
            if terminal is not sender and hasattr(terminal, "inject_input"):
                terminal.inject_input(data)

    def get_all_terminals(self) -> list:
        out = []

        for i in range(self.tabs.count()):
            widget = self.tabs.widget(i)

            if isinstance(widget, SplitTerminalTab):
                out.extend(widget.terminals)

            elif hasattr(widget, "ssh_terminal") and widget.ssh_terminal is not None:
                out.append(widget.ssh_terminal)

            elif hasattr(widget, "inject_input"):
                out.append(widget)

        return out

    def toggle_broadcast(self) -> None:
        theme = self.services.theme.current

        if not self.broadcast_enabled:
            terminals = self.get_all_terminals()

            if len(terminals) < 2:
                if hasattr(self, "broadcast_btn"):
                    self.broadcast_btn.setChecked(False)
                QMessageBox.information(
                    self,
                    "Broadcast",
                    "Open at least two terminals first.",
                )
                return

            if QMessageBox.question(
                self,
                "Broadcast Mode",
                f"Everything you type will be sent to ALL {len(terminals)} open terminals.\n"
                "Enable broadcast?",
            ) != QMessageBox.StandardButton.Yes:
                if hasattr(self, "broadcast_btn"):
                    self.broadcast_btn.setChecked(False)
                return

            self.broadcast_enabled = True

            self.broadcast_btn.setChecked(True)
            self.broadcast_btn.setIcon(
                get_icon("broadcast", color="#ffffff", size=20)
            )
            self.broadcast_btn.setToolTip(
                "Broadcast Input to Open Terminals (F8): ON — click to disable"
            )
            self.broadcast_btn.setStyleSheet(
                f"QPushButton#IconToolBtn {{ background:{theme['warn']}; border-color:{theme['warn']}; }}"
            )

            self.services.notifications.push(
                "warn",
                "Broadcast ON",
                "Input is mirrored to all terminals",
            )

        else:
            self.broadcast_enabled = False

            self.broadcast_btn.setChecked(False)
            self.broadcast_btn.setIcon(
                get_icon("broadcast", color=theme.get("text", "#eff0f1"), size=20)
            )
            self.broadcast_btn.setToolTip("Broadcast Input to Open Terminals (F8): Off")
            self.broadcast_btn.setStyleSheet("")

            self.services.notifications.push(
                "info",
                "Broadcast OFF",
                "Terminals are independent again",
            )

        self.services.audit(
            "TERMINAL",
            f"Terminal broadcast input {'ENABLED' if self.broadcast_enabled else 'DISABLED'}",
        )

    def add_terminal_tab(
        self,
        name: str,
        host: str,
        port: int,
        user: str,
        creds: SshCredentials,
        *,
        initial_cmd: str = "",
        use_jump: bool = False,
        jump_host: Optional[str] = None,
        jump_port: int = 22,
        jump_user: Optional[str] = None,
        jump_creds: Optional[SshCredentials] = None,
        use_agent: bool = False,
        profile_name: Optional[str] = None,
        profile_data: Optional[dict[str, Any]] = None,
    ):
        tab = SshTerminalTab(
            self.services,
            host=host,
            port=port,
            user=user,
            creds=creds,
            initial_cmd=initial_cmd,
            name=name,
            use_jump=use_jump,
            jump_host=jump_host,
            jump_port=jump_port,
            jump_user=jump_user,
            jump_creds=jump_creds,
            use_agent=use_agent,
            profile_name=profile_name,
            profile_data=profile_data,
        )

        self._connect_terminal(tab)

        icon = get_icon("terminal", color=self.services.theme.current.get("text", "#e2e8f0"), size=16)
        index = self.tabs.addTab(tab, icon, name)
        self.tabs.setCurrentIndex(index)

        self.add_recent(profile_name or name)

        self._notify(f"Terminal: {name}")

        return tab

    def connect_profile(self, name: str) -> None:
        data = self.profiles.get(name)

        if not data:
            return

        is_local = (
            bool(data.get("is_local"))
            or name.lower() == "localhost"
            or (
                data.get("ssh_host") in ("127.0.0.1", "localhost")
                and bool(data.get("use_local_exec", True))
            )
        )

        if is_local:
            import os
            self.services.audit("TERMINAL", f"Opened local terminal for '{name}'")
            cmd = data.get("initial_cmd") or os.environ.get("SHELL", "/bin/bash")
            self.add_local_command_tab(command=cmd, name=name)
            self.add_recent(name)
            self._notify(f"Terminal (Local): {name}")
            return

        self.services.audit("SSH", f"Connecting SSH terminal for '{name}' ({data.get('ssh_user')}@{data.get('ssh_host')}:{data.get('ssh_port', 22)})")

        creds = profile_creds(data)
        use_jump, jump_host, jump_port, jump_user, jump_creds = self._resolve_jump_params(data)

        self.add_terminal_tab(
            name,
            data.get("ssh_host", ""),
            data.get("ssh_port", 22),
            data.get("ssh_user", ""),
            creds,
            initial_cmd=data.get("initial_cmd", ""),
            use_jump=use_jump,
            jump_host=jump_host,
            jump_port=jump_port,
            jump_user=jump_user,
            jump_creds=jump_creds,
            use_agent=data.get("use_agent", False),
            profile_name=name,
            profile_data=data,
        )

    def _resolve_jump_params(
        self, data: dict
    ) -> tuple[bool, Optional[str], int, Optional[str], Optional[SshCredentials]]:
        """Resolve jump-host connection parameters from a profile dictionary."""
        jump_creds = None
        jump_host = data.get("jump_host")
        jump_port = int(data.get("jump_port", 22) or 22)
        jump_user = data.get("jump_user")
        use_jump = bool(data.get("use_jump"))

        if use_jump:
            jump_prof = data.get("jump_profile")
            if jump_prof and jump_prof in self.profiles:
                jp = self.profiles[jump_prof]
                if not jump_host:
                    jump_host = jp.get("ssh_host")
                    jump_port = int(jp.get("ssh_port", 22) or 22)
                    jump_user = jp.get("ssh_user")
                j_creds = profile_creds(jp)
                jump_creds = SshCredentials(
                    password=data.get("jump_pass") or j_creds.password,
                    passphrase=j_creds.passphrase,
                    key_path=data.get("jump_key_path") or j_creds.key_path,
                )
            else:
                jump_creds = SshCredentials(
                    password=data.get("jump_pass") or None,
                    key_path=data.get("jump_key_path") or None,
                )

        return use_jump, jump_host, jump_port, jump_user, jump_creds

    def add_sftp_tab(
        self,
        name: str,
        host: str,
        port: int,
        user: str,
        creds: SshCredentials,
        *,
        use_agent: bool = False,
        use_jump: bool = False,
        jump_host: Optional[str] = None,
        jump_port: int = 22,
        jump_user: Optional[str] = None,
        jump_creds: Optional[SshCredentials] = None,
        profile_name: Optional[str] = None,
        profile_data: Optional[dict] = None,
    ):
        self.services.audit("SFTP", f"Opening SFTP session for '{name}' ({user}@{host}:{port})")
        tab = SFTPTab(
            self.services,
            main_window=self,
            host=host,
            port=port,
            user=user,
            creds=creds,
            name=name,
            use_agent=use_agent,
            use_jump=use_jump,
            jump_host=jump_host,
            jump_port=jump_port,
            jump_user=jump_user,
            jump_creds=jump_creds,
            profile_name=profile_name or name,
            profile_data=profile_data,
        )
        if getattr(tab, "ssh_terminal", None) is not None:
            self._connect_terminal(tab.ssh_terminal)

        icon = get_icon("folder_open", color=self.services.theme.current.get("text", "#e2e8f0"), size=16)
        index = self.tabs.addTab(tab, icon, f"SFTP: {name}")
        self.tabs.setCurrentIndex(index)

        return tab

    def open_sftp(self, name: Optional[str]) -> None:
        if not name:
            QMessageBox.information(self, "SFTP", "Select a profile first.")
            return

        data = self.profiles.get(name)

        if not data:
            return

        creds = profile_creds(data)
        use_jump, jump_host, jump_port, jump_user, jump_creds = self._resolve_jump_params(data)

        self.add_sftp_tab(
            name,
            data.get("ssh_host", ""),
            data.get("ssh_port", 22),
            data.get("ssh_user", ""),
            creds,
            use_agent=data.get("use_agent", False),
            use_jump=use_jump,
            jump_host=jump_host,
            jump_port=jump_port,
            jump_user=jump_user,
            jump_creds=jump_creds,
            profile_name=name,
            profile_data=data,
        )

    def add_local_command_tab(self, command: str, name: str = "Local"):
        self.services.emit_log("TERMINAL", f"Spawned local command terminal: {command} ({name})", "INFO")
        tab = LocalTerminalTab(
            self.services,
            command=command,
            name=name,
        )

        self._connect_terminal(tab)

        icon = get_icon("terminal", color=self.services.theme.current.get("text", "#e2e8f0"), size=16)
        index = self.tabs.addTab(tab, icon, name)
        self.tabs.setCurrentIndex(index)

        return tab

    def open_ansible_tab(self):
        self.services.emit_log("ANSIBLE", "Opened Ansible Multi-Host runner tab", "INFO")
        tab = AnsibleTab(self.services, self)

        icon = get_icon("ansible", color=self.services.theme.current.get("text", "#e2e8f0"), size=16)
        index = self.tabs.addTab(tab, icon, "Ansible Runner")
        self.tabs.setCurrentIndex(index)

        return tab

    def open_ansible_playbook_tab(self):
        self.services.emit_log("ANSIBLE", "Opened Ansible Playbook runner tab", "INFO")
        tab = AnsiblePlaybookTab(self.services, self)

        icon = get_icon("code", color=self.services.theme.current.get("text", "#e2e8f0"), size=16)
        index = self.tabs.addTab(tab, icon, "Ansible Playbook")
        self.tabs.setCurrentIndex(index)

        return tab

    def open_admin_console(self, name: str = "", initial_category: Optional[str] = None, auto_refresh: bool = True) -> AdminConsoleTab:
        if not name:
            name = self._get_selected_profile_name() or "Localhost"
        data = self.profiles.get(name)
        if not data and name.lower() in ("localhost", "local"):
            data = self.profiles.get("localhost") or self.profiles.get("Localhost")

        # Reuse existing open tab if available
        for i in range(self.tabs.count()):
            widget = self.tabs.widget(i)
            if isinstance(widget, AdminConsoleTab) and widget.profile_name.lower() == name.lower():
                if initial_category and hasattr(widget, "master_tabs"):
                    cat_map = {
                        "system": 0, "sysadmin": 0,
                        "security": 1,
                        "network": 2, "vpn": 2,
                        "web": 3, "hosting": 3,
                        "vapt": 4, "recon": 4,
                    }
                    target_idx = cat_map.get(initial_category.lower(), 0)
                    widget.master_tabs.setCurrentIndex(target_idx)
                self.tabs.setCurrentIndex(i)
                return widget

        self.services.emit_log("ADMIN", f"Opened Admin Console for: {name} (Category: {initial_category or 'Default'})", "INFO")
        tab = AdminConsoleTab(
            self.services,
            name,
            data,
            initial_category=initial_category,
            auto_refresh=auto_refresh,
            parent=self,
        )
        cat_suffix = f" [{initial_category.title()}]" if initial_category else ""
        title = f"🖥️ {name}{cat_suffix}"
        index = self.tabs.addTab(tab, title)
        self.tabs.setCurrentIndex(index)
        return tab

    def open_admin_console_selected(self) -> AdminConsoleTab:
        name = self._get_selected_profile_name() or "Localhost"
        return self.open_admin_console(name)

    def open_vpn_config_dialog(self, initial_tab: int = 0) -> None:
        dlg = VpnConfigDialog(self.services, parent=self, initial_tab=initial_tab)
        dlg.exec()

    def open_wireguard_server_setup(self) -> None:
        self.open_vpn_config_dialog(initial_tab=1)

    def open_sysadmin(self, name: str):
        return self.open_admin_console(name, initial_category="system")

    def open_sysadmin_local(self):
        return self.open_admin_console("Localhost", initial_category="system")

    def open_sysadmin_selected(self):
        name = self._get_selected_profile_name() or "Localhost"
        return self.open_admin_console(name, initial_category="system")

    def open_web_manager(self, name: str):
        return self.open_admin_console(name, initial_category="web")

    def open_web_manager_local(self):
        return self.open_admin_console("Localhost", initial_category="web")

    def open_web_manager_selected(self):
        name = self._get_selected_profile_name() or "Localhost"
        return self.open_admin_console(name, initial_category="web")

    def open_security_hub(self, name: str):
        return self.open_admin_console(name, initial_category="security")

    def open_security_hub_local(self):
        return self.open_admin_console("Localhost", initial_category="security")

    def open_security_hub_selected(self):
        name = self._get_selected_profile_name() or "Localhost"
        return self.open_admin_console(name, initial_category="security")

    def open_vapt_tab(self, target: str = ""):
        name = self._get_selected_profile_name() or "Localhost"
        return self.open_admin_console(name, initial_category="vapt")

    def open_port_forwarding(self, profile_name: Optional[str] = None):
        self.services.audit("SSH", f"Opened port forwarding & tunnels dialog")
        if not profile_name:
            profile_name = self._get_selected_profile_name()

        if not profile_name:
            if self.profiles:
                items = list(self.profiles.keys())
                item, ok = QInputDialog.getItem(
                    self, "Port Forwarding & Tunnels", "Select SSH Profile for Tunneling:", items, 0, False
                )
                if ok and item:
                    profile_name = item
                else:
                    return
            else:
                QMessageBox.information(
                    self, "Port Forwarding", "Add an SSH profile first to configure port forwarding."
                )
                return

        data = self.profiles.get(profile_name, {})
        from admin_suite.ui.networking_dialog import NetworkingToolsDialog
        dialog = NetworkingToolsDialog(
            services=self.services,
            host=data.get("ssh_host", data.get("host", "localhost")),
            port=int(data.get("ssh_port", data.get("port", 22)) or 22),
            username=data.get("ssh_user", data.get("user", "")),
            creds=profile_creds(data),
            parent=self,
            profile_data=data,
        )
        dialog.show()

    def open_split_selected(self):
        name = self._get_selected_profile_name()

        if not name:
            QMessageBox.information(
                self,
                "Split Terminal",
                "Select a profile first.",
            )
            return

        return self.open_split_profile(name)

    def open_split_profile(self, name: str):
        data = self.profiles[name]

        tab = SplitTerminalTab(self.services, name, data)
        tab.tab_switch_requested.connect(self._switch_tab_by_delta)
        tab.terminal_theme_apply_all_requested.connect(
            self.apply_theme_to_open_terminals
        )
        tab.pane_added.connect(
            lambda terminal: self._connect_terminal(terminal, connect_tab_switch=False)
        )

        for terminal in tab.terminals:
            self._connect_terminal(terminal, connect_tab_switch=False)

        icon = get_icon("split_h", color=self.services.theme.current.get("text", "#e2e8f0"), size=16)
        index = self.tabs.addTab(tab, icon, name)
        self.tabs.setCurrentIndex(index)

        self.add_recent(name)
        return tab

    def _new_terminal_dialog(self):
        name = self._get_selected_profile_name()

        if name:
            self.connect_profile(name)

        elif self.profiles:
            name, ok = QInputDialog.getItem(
                self,
                "New Terminal",
                "Profile:",
                list(self.profiles.keys()),
                0,
                False,
            )

            if ok and name:
                self.connect_profile(name)

    def _focus_current_terminal(self, index: int) -> None:
        widget = self.tabs.widget(index)

        if isinstance(widget, SshTerminalTab):
            QTimer.singleShot(50, widget.force_focus)

        elif isinstance(widget, LocalTerminalTab):
            QTimer.singleShot(50, widget.force_focus)

        elif isinstance(widget, SplitTerminalTab) and widget.terminals:
            QTimer.singleShot(50, widget.terminals[-1].force_focus)

        if (
            hasattr(self, "ai_tab")
            and hasattr(self, "right_sidebar")
            and self.right_sidebar.isVisible()
            and hasattr(self, "right_tabs")
            and self.right_tabs.currentWidget() == self.ai_tab
        ):
            self.ai_tab.update_context()

    # ------------------------------------------------------------
    # Tab management
    # ------------------------------------------------------------

    def _is_permanent_tab(self, widget) -> bool:
        permanent = (
            getattr(self, "welcome_widget", None),
        )
        return widget is not None and widget in permanent

    def close_tab(self, index: int) -> None:
        widget = self.tabs.widget(index)
        if self._is_permanent_tab(widget):
            return

        if widget == getattr(self, "db_manager_widget", None):
            self.tabs.removeTab(index)
            widget.hide()
            return

        if isinstance(widget, SshTerminalTab):
            self._last_closed = (
                self.add_terminal_tab,
                [],
                dict(
                    name=widget.name,
                    host=widget.host,
                    port=widget.port,
                    user=widget.user,
                    creds=widget.creds,
                    initial_cmd=widget.initial_cmd,
                    use_jump=widget.use_jump,
                    jump_host=widget.jump_host,
                    jump_port=widget.jump_port,
                    jump_user=widget.jump_user,
                    jump_creds=widget.jump_creds,
                    use_agent=widget.use_agent,
                    profile_name=widget.profile_name,
                ),
            )

        elif isinstance(widget, LocalTerminalTab):
            self._last_closed = (
                self.add_local_command_tab,
                [widget.command, widget.name],
                {},
            )

        self.tabs.removeTab(index)

        if widget is not None:
            try:
                if hasattr(widget, "stop_all_workers"):
                    widget.stop_all_workers()
            except Exception:
                pass
            try:
                widget.close()
            except Exception:
                pass

            widget.deleteLater()

    def _close_current_tab(self):
        index = self.tabs.currentIndex()
        if index >= 0 and not self._is_permanent_tab(self.tabs.widget(index)):
            self.close_tab(index)

    def _reopen_closed_tab(self):
        if self._last_closed:
            func, args, kwargs = self._last_closed

            func(*args, **kwargs)

            self._last_closed = None

    def build_tab_menu(self, index: int) -> Optional[QMenu]:
        if index < 0 or self._is_permanent_tab(self.tabs.widget(index)):
            return None

        menu = QMenu(self)
        icon_color = self.services.theme.current.get("text", "#e2e8f0")

        menu.addAction(get_icon("external_link", color=icon_color, size=16), "Pop Out to Floating Window").triggered.connect(
            lambda: self.workspace_mgr.detach_tab(self.tabs, index)
        )
        menu.addSeparator()

        menu.addAction(get_icon("edit", color=icon_color, size=16), "Rename").triggered.connect(
            lambda: self._rename_tab(index)
        )

        menu.addAction(get_icon("close", color=icon_color, size=16), "Close").triggered.connect(
            lambda: self.close_tab(index)
        )

        menu.addAction(get_icon("trash", color=icon_color, size=16), "Close Others").triggered.connect(
            lambda: self._close_others(index)
        )

        menu.addAction(get_icon("chevron_right", color=icon_color, size=16), "Close to the Right").triggered.connect(
            lambda: self._close_right(index)
        )
        return menu

    def show_tab_menu(self, pos) -> None:
        index = self.tabs.tabBar().tabAt(pos)
        menu = self.build_tab_menu(index)
        if menu is not None:
            menu.exec(self.tabs.mapToGlobal(pos))

    def _rename_tab(self, index: int):
        name, ok = QInputDialog.getText(
            self,
            "Rename Tab",
            "New name:",
            text=self.tabs.tabText(index),
        )

        if ok and name:
            self.tabs.setTabText(index, name)

    def _close_others(self, keep: int):
        keep_widget = self.tabs.widget(keep)
        for i in range(self.tabs.count() - 1, -1, -1):
            w = self.tabs.widget(i)
            if w != keep_widget and not self._is_permanent_tab(w):
                self.close_tab(i)

    def _close_right(self, index: int):
        for i in range(self.tabs.count() - 1, index, -1):
            w = self.tabs.widget(i)
            if not self._is_permanent_tab(w):
                self.close_tab(i)

    # ------------------------------------------------------------
    # Sidebar
    # ------------------------------------------------------------

    def _on_main_splitter_moved(self, pos: int, index: int) -> None:
        if not hasattr(self, "main_splitter"):
            return
        sizes = self.main_splitter.sizes()
        if len(sizes) == 3:
            if hasattr(self, "sidebar") and not self.sidebar.isHidden() and sizes[0] >= 100:
                self._sidebar_width = sizes[0]
                try:
                    self._settings.setValue("sidebar/width", sizes[0])
                except Exception:
                    pass
            if hasattr(self, "right_sidebar") and not self.right_sidebar.isHidden() and sizes[2] >= 100:
                self._copilot_width = sizes[2]
                try:
                    self._settings.setValue("copilot/width", sizes[2])
                except Exception:
                    pass

    def _apply_splitter_layout(self) -> None:
        """
        Synchronizes QSplitter sizes cleanly based on sidebar visibilities.
        Prevents dead zones and ensures smooth transitions.
        """
        if not hasattr(self, "main_splitter"):
            return

        total = self.main_splitter.width()
        if total <= 100:
            total = max(self.width(), 1280)

        left_open = hasattr(self, "sidebar") and not self.sidebar.isHidden()
        right_open = hasattr(self, "right_sidebar") and not self.right_sidebar.isHidden()

        # Enforce actual minimum sizes so QSplitter cannot collapse an open widget
        if left_open:
            s_w = getattr(self, "_sidebar_width", 280)
            if s_w < 160:
                s_w = 280
            self.sidebar.setMinimumWidth(160)
            self.sidebar.setMaximumWidth(16777215)
            self.main_splitter.setCollapsible(0, False)
        else:
            s_w = 0
            self.sidebar.setMinimumWidth(0)
            self.main_splitter.setCollapsible(0, True)

        if right_open:
            r_w = getattr(self, "_copilot_width", 360)
            if r_w < 200:
                r_w = 360
            self.right_sidebar.setMinimumWidth(200)
            self.right_sidebar.setMaximumWidth(16777215)
            self.main_splitter.setCollapsible(2, False)
        else:
            r_w = 0
            self.right_sidebar.setMinimumWidth(0)
            self.main_splitter.setCollapsible(2, True)

        # Distribute remaining width to central workspace
        w_w = max(total - s_w - r_w, 200)

        # In case total width is cramped, proportionately share space
        needed = s_w + w_w + r_w
        if needed > total and total > 500:
            w_w = max(int(total * 0.4), 250)
            rem = total - w_w
            if left_open and right_open:
                s_w = max(int(rem * 0.45), 160)
                r_w = max(rem - s_w, 200)
            elif left_open:
                s_w = max(rem, 160)
            elif right_open:
                r_w = max(rem, 200)

        self.main_splitter.setSizes([s_w, w_w, r_w])

    def _update_left_sidebar_btn_styles(self, is_open: bool) -> None:
        theme = self.services.theme.current
        if hasattr(self, "sidebar_toggle_btn"):
            self.sidebar_toggle_btn.setText("")
            self.sidebar_toggle_btn.setChecked(bool(is_open))
            color = "#ffffff" if is_open else theme.get("text", "#eff0f1")
            self.sidebar_toggle_btn.setIcon(get_icon("sidebar_left", color=color, size=20))
            self.sidebar_toggle_btn.setToolTip(
                "Hide Left Sidebar (Ctrl+B)" if is_open else "Show Left Sidebar (Ctrl+B)"
            )

    def toggle_sidebar(self, force_visible: Optional[bool] = None) -> None:
        if not hasattr(self, "sidebar"):
            return

        is_open = not self.sidebar.isHidden() and (self.sidebar.width() > 50)
        target = not is_open if force_visible is None else bool(force_visible)

        if target:
            w = getattr(self, "_sidebar_width", 280)
            if w < 160:
                w = 280
            self._sidebar_width = w
            self.sidebar.setMinimumWidth(160)
            self.sidebar.setMaximumWidth(16777215)
            self.sidebar.show()
            self.main_splitter.setCollapsible(0, False)
        else:
            if hasattr(self, "main_splitter"):
                sizes = self.main_splitter.sizes()
                if sizes and sizes[0] > 60:
                    self._sidebar_width = sizes[0]
            self.sidebar.setMinimumWidth(0)
            self.sidebar.hide()
            self.main_splitter.setCollapsible(0, True)

        self._apply_splitter_layout()
        self._update_left_sidebar_btn_styles(target)

        try:
            self._settings.setValue("sidebar/visible", target)
            if target and hasattr(self, "_sidebar_width"):
                self._settings.setValue("sidebar/width", self._sidebar_width)
        except Exception:
            pass

    def _on_right_tab_changed(self, index: int) -> None:
        self._update_right_sidebar_btn_styles()
        if (
            hasattr(self, "ai_tab")
            and hasattr(self, "right_tabs")
            and self.right_tabs.currentWidget() == self.ai_tab
        ):
            self.ai_tab.update_context()

    def _show_right_sidebar(self) -> None:
        if not hasattr(self, "right_sidebar"):
            return
        w = getattr(self, "_copilot_width", 360)
        if w < 200:
            w = 360
        self._copilot_width = w
        self.right_sidebar.setMinimumWidth(200)
        self.right_sidebar.setMaximumWidth(16777215)
        self.right_sidebar.show()
        self._apply_splitter_layout()
        try:
            self._settings.setValue("copilot/visible", True)
        except Exception:
            pass

    def _hide_right_sidebar(self) -> None:
        if not hasattr(self, "right_sidebar"):
            return
        if hasattr(self, "main_splitter"):
            sizes = self.main_splitter.sizes()
            if len(sizes) == 3 and sizes[2] > 60:
                self._copilot_width = sizes[2]
                try:
                    self._settings.setValue("copilot/width", sizes[2])
                except Exception:
                    pass
        self.right_sidebar.setMinimumWidth(0)
        self.right_sidebar.hide()
        self._apply_splitter_layout()
        try:
            self._settings.setValue("copilot/visible", False)
        except Exception:
            pass

    def toggle_right_sidebar(self, force_visible: Optional[bool] = None) -> None:
        """Toggle open or close the right sidebar."""
        if not hasattr(self, "right_sidebar"):
            return

        is_open = not self.right_sidebar.isHidden()
        target = not is_open if force_visible is None else bool(force_visible)

        if target:
            self._show_right_sidebar()
            if (
                hasattr(self, "right_tabs")
                and hasattr(self, "ai_tab")
                and self.right_tabs.currentWidget() == self.ai_tab
            ):
                self.ai_tab.update_context()
        else:
            self._hide_right_sidebar()

        self._update_right_sidebar_btn_styles()

    def toggle_copilot(self, force_visible: Optional[bool] = None) -> None:
        """Switch to Copilot tab and ensure right sidebar is visible, or toggle if active."""
        if not hasattr(self, "right_sidebar"):
            return

        is_open = not self.right_sidebar.isHidden()
        is_copilot_active = (
            hasattr(self, "right_tabs")
            and self.right_tabs.currentWidget() == getattr(self, "ai_tab", None)
        )

        if force_visible is True:
            self._show_right_sidebar()
            if hasattr(self, "right_tabs") and hasattr(self, "ai_tab"):
                self.right_tabs.setCurrentWidget(self.ai_tab)
                self.ai_tab.update_context()
        elif force_visible is False:
            self._hide_right_sidebar()
        else:
            if is_open and is_copilot_active:
                self._hide_right_sidebar()
            else:
                self._show_right_sidebar()
                if hasattr(self, "right_tabs") and hasattr(self, "ai_tab"):
                    self.right_tabs.setCurrentWidget(self.ai_tab)
                    self.ai_tab.update_context()

        self._update_right_sidebar_btn_styles()

    def toggle_event_logs(self, force_visible: Optional[bool] = None) -> None:
        """Switch to Event Logs tab and ensure right sidebar is visible, or toggle if active."""
        if not hasattr(self, "right_sidebar"):
            return

        is_open = not self.right_sidebar.isHidden()
        is_logs_active = (
            hasattr(self, "right_tabs")
            and self.right_tabs.currentWidget() == getattr(self, "event_logs_widget", None)
        )

        if force_visible is True:
            self._show_right_sidebar()
            if hasattr(self, "right_tabs") and hasattr(self, "event_logs_widget"):
                self.right_tabs.setCurrentWidget(self.event_logs_widget)
        elif force_visible is False:
            self._hide_right_sidebar()
        else:
            if is_open and is_logs_active:
                self._hide_right_sidebar()
            else:
                self._show_right_sidebar()
                if hasattr(self, "right_tabs") and hasattr(self, "event_logs_widget"):
                    self.right_tabs.setCurrentWidget(self.event_logs_widget)

        self._update_right_sidebar_btn_styles()

    def _update_right_sidebar_btn_styles(self) -> None:
        theme = self.services.theme.current
        is_visible = hasattr(self, "right_sidebar") and not self.right_sidebar.isHidden()

        if hasattr(self, "right_sidebar_toggle_btn"):
            self.right_sidebar_toggle_btn.setText("")
            self.right_sidebar_toggle_btn.setChecked(bool(is_visible))
            color = "#ffffff" if is_visible else theme.get("text", "#eff0f1")
            self.right_sidebar_toggle_btn.setIcon(get_icon("sidebar_right", color=color, size=20))
            self.right_sidebar_toggle_btn.setToolTip(
                "Hide Right Sidebar (Ctrl+Shift+A)" if is_visible else "Show Right Sidebar (Ctrl+Shift+A)"
            )

    def _update_copilot_btn_style(self, visible: bool) -> None:
        self._update_right_sidebar_btn_styles()

    # ------------------------------------------------------------
    # Tools
    # ------------------------------------------------------------

    def open_palette(self):
        CommandPaletteDialog(self, self._build_commands()).exec()

    def _build_commands(self):
        reg = getattr(self.services, "actions", None)
        commands = reg.to_palette_items() if reg else []

        for name in self.profiles:
            commands.append(
                {
                    "name": f"Connect: {name}",
                    "hint": "terminal",
                    "category": "Profiles",
                    "cb": lambda n=name: self.connect_profile(n),
                }
            )

            commands.append(
                {
                    "name": f"SFTP: {name}",
                    "hint": "files",
                    "category": "Files & SFTP",
                    "cb": lambda n=name: self.open_sftp(n),
                }
            )

            commands.append(
                {
                    "name": f"SysAdmin: {name}",
                    "hint": "dashboard",
                    "category": "Administration",
                    "cb": lambda n=name: self.open_sysadmin(n),
                }
            )

            commands.append(
                {
                    "name": f"Web: {name}",
                    "hint": "hosting",
                    "category": "Administration",
                    "cb": lambda n=name: self.open_web_manager(n),
                }
            )

            commands.append(
                {
                    "name": f"Security: {name}",
                    "hint": "firewall/fail2ban/audit",
                    "category": "Security",
                    "cb": lambda n=name: self.open_security_hub(n),
                }
            )

            commands.append(
                {
                    "name": f"Port Forwarding: {name}",
                    "hint": "ssh tunnels",
                    "category": "VPN & Mesh",
                    "cb": lambda n=name: self.open_port_forwarding(n),
                }
            )

        commands.append(
            {
                "name": "Add DB Profile",
                "hint": "database",
                "category": "Database",
                "cb": self.add_db_profile,
            }
        )

        for name in self.db_profiles:
            commands.append(
                {
                    "name": f"Connect DB: {name}",
                    "hint": "database",
                    "category": "Database",
                    "cb": lambda n=name: self.activate_db_profile(n),
                }
            )

        return commands

    def open_config(self):
        ConnectionManagerDialog(self, self.services).exec()

    def open_settings_backup(self, mode: str = "export") -> None:
        SettingsBackupDialog(
            self,
            self.services,
            initial_mode=mode,
            on_imported=self._on_settings_imported,
        ).exec()

    def _on_settings_imported(self, summary: dict) -> None:
        """Reload configuration, profiles, database list, and theme after archive import."""
        try:
            self.services.config.load()
        except Exception:
            pass
        try:
            self.load_profiles()
            self.load_db_profiles()
            self.load_recent()
        except Exception:
            pass

        ui_theme = self.services.config.get("ui_theme")
        if ui_theme:
            term_theme = self.services.config.get("terminal_theme")
            self._apply_ui_theme(ui_theme, terminal_theme=term_theme)

    def apply_theme_to_open_terminals(self, theme_name: Optional[str] = None) -> None:
        """Apply terminal color scheme to every open terminal tab and split pane."""
        if theme_name:
            self.services.config.set("terminal_theme", theme_name)
            self.services.config.save()

        for term in self.get_all_terminals():
            try:
                if theme_name and hasattr(term, "set_terminal_theme"):
                    term.set_terminal_theme(theme_name, save_config=False)
                elif hasattr(term, "apply_terminal_theme"):
                    term.apply_terminal_theme(theme_name)
            except Exception:
                pass

    def _apply_ui_theme(
        self, name: str, *, terminal_theme: Optional[str] = None
    ) -> None:
        app = QApplication.instance()
        if app:
            self.services.theme.apply(app, name)

        theme = self.services.theme.current
        if hasattr(self, "sidebar"):
            self.sidebar.setStyleSheet(
                f"background:{theme['panel']};border-right:1px solid {theme['border']};"
            )
        if hasattr(self, "right_sidebar"):
            self.right_sidebar.setStyleSheet(
                f"background:{theme['panel']};border-left:1px solid {theme['border']};"
            )
        if hasattr(self, "navbar"):
            self.navbar.setStyleSheet(
                f"background:{theme['panel']};border-bottom:1px solid {theme['border']};"
            )

        self.apply_theme_to_open_terminals(terminal_theme)
        if hasattr(self, "sidebar"):
            self._update_left_sidebar_btn_styles(not self.sidebar.isHidden())
        self._update_right_sidebar_btn_styles()

    def open_theme_dialog(self):
        dialog = ThemeDialog(self, self.services)

        if dialog.exec() == QDialog.DialogCode.Accepted:
            name = dialog.chosen()

            if name:
                self.services.config.set("ui_theme", name)

                xterm_name = self.services.theme.get_theme(name).get("xterm", "dark")
                self.services.config.set("terminal_theme", xterm_name)

                self.services.config.save()

                self._apply_ui_theme(name, terminal_theme=xterm_name)

                self.services.notifications.push(
                    "ok",
                    "Theme applied",
                    name,
                )

    def open_snippets(self):
        SnippetManagerDialog(self, self.services).exec()

    def run_snippet_in_terminal(self, command: str):
        if "{{" in command and "}}" in command:
            from admin_suite.ui.dialogs import SnippetVariablesDialog
            dlg = SnippetVariablesDialog(command, self)
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return
            command = dlg.get_rendered_command()

        index = self.tabs.currentIndex()

        if index > 1:
            widget = self.tabs.widget(index)

            if isinstance(widget, SshTerminalTab):
                widget.send_command(command)
                return

            if hasattr(widget, "inject_input"):
                widget.inject_input(command + "\n")
                return

            if isinstance(widget, SplitTerminalTab) and widget.terminals:
                widget.terminals[0].inject_input(command + "\n")
                return

        QMessageBox.information(
            self,
            "Snippets",
            "Open a terminal tab first.",
        )

    def open_mysql_status_tab(self):
        cfg = self.db_manager_widget._build_cfg()

        if cfg.get("backend", "") != "mysql":
            QMessageBox.information(
                self,
                "MySQL Status",
                "MySQL Server Status is available only when the active DB backend is MySQL.",
            )
            return None

        for i in range(self.tabs.count()):
            widget = self.tabs.widget(i)

            if isinstance(widget, MySQLStatusTab):
                self.tabs.setCurrentIndex(i)
                widget.refresh()
                return widget

        tab = MySQLStatusTab(
            self.services,
            self.db_manager_widget,
            self,
        )

        icon = get_icon("activity", color=self.services.theme.current.get("text", "#e2e8f0"), size=16)
        index = self.tabs.addTab(tab, icon, "MySQL Status")
        self.tabs.setCurrentIndex(index)

        return tab

    # ------------------------------------------------------------
    # Session restore
    # ------------------------------------------------------------

    def _offer_session_restore(self):
        try:
            if os.path.exists(LAST_SESSION_FILE):
                names = read_json(LAST_SESSION_FILE, [])

                names = [n for n in names if n in self.profiles]

                if names and QMessageBox.question(
                    self,
                    "Restore Session",
                    f"Restore {len(names)} terminal session(s) from last run?\n"
                    f"{', '.join(names)}",
                ) == QMessageBox.StandardButton.Yes:
                    for name in names:
                        self.connect_profile(name)

        except Exception as e:
            self.services.emit_log(
                "system",
                f"Session restore failed: {e}",
            )

    def _collect_open_profile_names(self):
        names = []

        for terminal in self.get_all_terminals():
            profile_name = getattr(terminal, "profile_name", None)

            if (
                profile_name
                and profile_name in self.profiles
                and profile_name not in names
            ):
                names.append(profile_name)

        return names

    # ------------------------------------------------------------
    # Close
    # ------------------------------------------------------------

    def closeEvent(self, event):
        try:
            write_json_secure(
                LAST_SESSION_FILE,
                self._collect_open_profile_names(),
            )

        except Exception:
            pass

        try:
            self._settings.setValue("geometry", self.saveGeometry())

            if hasattr(self, "main_splitter"):
                self._settings.setValue(
                    "splitter/state",
                    self.main_splitter.saveState(),
                )

            if hasattr(self, "sidebar"):
                self._settings.setValue(
                    "sidebar/visible",
                    not self.sidebar.isHidden(),
                )

                if hasattr(self, "main_splitter"):
                    sizes = self.main_splitter.sizes()
                    if sizes and sizes[0] > 50:
                        self._settings.setValue("sidebar/width", sizes[0])

            if hasattr(self, "right_sidebar"):
                self._settings.setValue(
                    "copilot/visible",
                    not self.right_sidebar.isHidden(),
                )

                if hasattr(self, "main_splitter"):
                    sizes = self.main_splitter.sizes()
                    if len(sizes) == 3 and sizes[2] > 50:
                        self._settings.setValue("copilot/width", sizes[2])

        except Exception:
            pass

        for i in range(self.tabs.count() - 1, -1, -1):
            widget = self.tabs.widget(i)

            if widget is not None and not self._is_permanent_tab(widget):
                try:
                    if hasattr(widget, "stop_all_workers"):
                        widget.stop_all_workers()
                    widget.close()
                except Exception:
                    pass

        try:
            self.db_manager_widget.stop_all_workers()
        except Exception:
            pass

        try:
            self.workspace_mgr.close_all_detached()
        except Exception:
            pass

        try:
            if getattr(self, "_app_filter_installed", False):
                app = QApplication.instance()
                if app is not None:
                    app.removeEventFilter(self)
                self._app_filter_installed = False
        except Exception:
            pass

        try:
            self.services.shutdown()
        except Exception:
            pass

        event.accept()
