"""
Main application window.
"""

from __future__ import annotations

import datetime
import json
import os
from typing import Any, Optional

from PyQt6.QtCore import Qt, QSettings, QTimer
from PyQt6.QtGui import (
    QAction,
    QColor,
    QFont,
    QKeySequence,
    QShortcut,
    QTextCursor,
)


from PyQt6.QtWidgets import (
    QApplication,
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
    QPushButton,
    QScrollArea,
    QSplitter,
    QTabBar,
    QTabWidget,
    QTextEdit,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)



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

from admin_suite.vpn.service import VpnService

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
    SnippetManagerDialog,
    ThemeDialog,
)


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

        # UI must be created before loading/refreshing profiles.
        self._init_ui()
        self._init_shortcuts()

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

            self._sidebar_width = sidebar_width if (200 <= sidebar_width <= 500) else 280
            self.sidebar.setVisible(True)
            self.sidebar.setMinimumWidth(200)
            self.sidebar.setMaximumWidth(520)
            self.main_splitter.setCollapsible(0, False)

            copilot_visible = self._settings.value("copilot/visible", False, type=bool)
            copilot_width = self._settings.value("copilot/width", 340, type=int)
            self._copilot_width = copilot_width if (240 <= copilot_width <= 500) else 340

            if hasattr(self, "right_sidebar"):
                self.right_sidebar.setVisible(copilot_visible)

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
        self.sidebar.setMaximumWidth(560)

        self._sidebar_width = 280

        self.sidebar.setStyleSheet(
            f"background:{theme['panel']};"
            f"border-right:1px solid {theme['border']};"
        )

        sidebar_layout = QVBoxLayout(self.sidebar)
        sidebar_layout.setContentsMargins(0, 0, 0, 0)
        sidebar_layout.setSpacing(0)

        header = QLabel("  🛠 ADMIN SUITE v5")
        header.setFixedHeight(46)
        header.setStyleSheet(
            f"background:{theme['win']};"
            f"color:{theme['accent']};"
            "font-size:15px;font-weight:bold;padding:10px 12px;letter-spacing:1px;"
        )

        sidebar_layout.addWidget(header)

        self.sidebar_tabs = QTabWidget()
        self.sidebar_tabs.setDocumentMode(True)
        self.sidebar_tabs.setUsesScrollButtons(True)

        # Profiles tab.
        profiles_tab = QWidget()
        profiles_layout = QVBoxLayout(profiles_tab)
        profiles_layout.setContentsMargins(4, 4, 4, 4)
        profiles_layout.setSpacing(4)

        self.profile_filter = QLineEdit()
        self.profile_filter.setPlaceholderText("🔍 Filter profiles / tags...")
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
        profile_button_bar.setSpacing(2)

        for text, tooltip, callback in (
            ("➕", "Add profile", self.add_profile),
            ("📋", "Duplicate profile", self.duplicate_profile),
            ("✏️", "Edit profile", self.edit_profile),
            ("🗑️", "Delete profile", self.delete_profile),
            ("📡", "Ping all hosts", self._ping_all_profiles),
        ):
            button = QPushButton(text)
            button.setToolTip(tooltip)
            button.setFixedWidth(34)
            button.clicked.connect(callback)

            profile_button_bar.addWidget(button)

        profiles_layout.addLayout(profile_button_bar)

        # DB profiles tab.
        db_tab = QWidget()
        db_layout = QVBoxLayout(db_tab)
        db_layout.setContentsMargins(4, 4, 4, 4)
        db_layout.setSpacing(4)

        self.db_filter = QLineEdit()
        self.db_filter.setPlaceholderText("🔍 Filter DB profiles / hosts...")
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
        db_button_bar.setSpacing(2)

        for text, tooltip, callback in (
            ("➕", "Add DB profile", self.add_db_profile),
            ("📋", "Duplicate DB profile", self.duplicate_db_profile),
            ("✏️", "Edit DB profile", self.edit_db_profile),
            ("🗑️", "Delete DB profile", self.delete_db_profile),
        ):
            button = QPushButton(text)
            button.setToolTip(tooltip)
            button.setFixedWidth(34)
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
        tools_layout.setSpacing(2)

        tool_buttons = [
            ("🏠 Welcome Screen", self.open_welcome_tab),
            ("🗄️ Database Manager", self.open_db_manager),
            ("📋 Event Logs", self.toggle_event_logs),
            ("⚡ Local Shell", lambda: self.add_local_command_tab("bash", "Local Shell")),
            ("🔍 VAPT & Web Security Audit", lambda: self.open_vapt_tab()),
            ("🛡️ Security Hub", self.open_security_hub_selected),
            ("🔌 Port Forwarding & Tunnels", lambda: self.open_port_forwarding()),
            ("🌐 Web Hosting Manager", self.open_web_manager_selected),
            ("🖥 SysAdmin Dashboard", self.open_sysadmin_selected),
            ("📝 Ansible Multihost", self.open_ansible_tab),
            ("📜 Ansible Playbook", self.open_ansible_playbook_tab),
            ("📝 Snippets", self.open_snippets),
            ("🔑 SSH Key Manager", lambda: KeyManagerDialog(self, self.services).exec()),
            ("📥 Import ~/.ssh/config", self.import_ssh_config),
            ("🎬 Session Recordings", lambda: SessionLogViewerDialog(self, self.services).exec()),
            ("🎨 Themes", self.open_theme_dialog),
            ("⚙️ Connection Settings", self.open_config),
        ]

        for label, callback in tool_buttons:
            button = QPushButton(label)
            button.setStyleSheet("text-align:left;padding:6px 10px;")
            button.clicked.connect(callback)

            tools_layout.addWidget(button)

        tools_layout.addStretch()

        tools_scroll = QScrollArea()
        tools_scroll.setWidgetResizable(True)
        tools_scroll.setFrameShape(QFrame.Shape.NoFrame)
        tools_scroll.setWidget(tools)

        # Add sidebar tabs.
        self.sidebar_tabs.addTab(profiles_tab, "🐚 Profiles")
        self.sidebar_tabs.addTab(db_tab, "🗄 DBs")
        self.sidebar_tabs.addTab(recent_tab, "🕒 Recent")
        self.sidebar_tabs.addTab(tools_scroll, "🧰 Tools")

        sidebar_layout.addWidget(self.sidebar_tabs, 1)

        # Workspace.
        workspace = QWidget()
        workspace_layout = QVBoxLayout(workspace)
        workspace_layout.setContentsMargins(0, 0, 0, 0)
        workspace_layout.setSpacing(0)

        # Right sidebar container (Copilot + Event Logs tabs)
        self.right_sidebar = QWidget()
        self.right_sidebar.setMinimumWidth(0)
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

        from admin_suite.ai.assistant_tab import AIAssistantTab
        self.ai_tab = AIAssistantTab(self.services, self)
        self.event_logs_widget = EventLogsWidget(self.services, self)

        self.right_tabs.addTab(self.ai_tab, "🤖 Copilot")
        self.right_tabs.addTab(self.event_logs_widget, "📋 Event Logs")
        self.right_tabs.currentChanged.connect(self._on_right_tab_changed)

        right_sidebar_layout.addWidget(self.right_tabs, 1)

        self._copilot_width = 360

        self.main_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.main_splitter.addWidget(self.sidebar)
        self.main_splitter.addWidget(workspace)
        self.main_splitter.addWidget(self.right_sidebar)

        self.main_splitter.setStretchFactor(0, 0)
        self.main_splitter.setStretchFactor(1, 1)
        self.main_splitter.setStretchFactor(2, 0)
        self.main_splitter.setChildrenCollapsible(True)
        self.main_splitter.setSizes([280, 800, 360])

        main_layout.addWidget(self.main_splitter)

        # Navbar.
        navbar = QWidget()
        navbar.setFixedHeight(46)

        navbar.setStyleSheet(
            f"background:{theme['panel']};"
            f"border-bottom:1px solid {theme['border']};"
        )

        navbar_layout = QHBoxLayout(navbar)
        navbar_layout.setContentsMargins(8, 4, 8, 4)
        navbar_layout.setSpacing(6)

        self.sidebar_toggle_btn = QPushButton("◀")
        self.sidebar_toggle_btn.setToolTip("Toggle Left Sidebar (Ctrl+B)")
        self.sidebar_toggle_btn.clicked.connect(lambda: self.toggle_sidebar())

        palette_btn = QPushButton("⌘ (Ctrl+K)")
        palette_btn.clicked.connect(self.open_palette)

        self.broadcast_btn = QPushButton("📢 ")
        self.broadcast_btn.setStyleSheet(
            f"background:{theme['ok']};color:white;padding:6px 12px;"
        )
        self.broadcast_btn.clicked.connect(self.toggle_broadcast)

        self.vpn_btn = QPushButton("⚡ Connect VPN")
        self.vpn_btn.setStyleSheet(
            f"background:{theme['warn']};color:white;padding:6px 12px;font-weight:bold;"
        )
        self.vpn_btn.clicked.connect(self.vpn.toggle)

        # Single right sidebar toggle button
        self.right_sidebar_toggle_btn = QPushButton("▶")
        self.right_sidebar_toggle_btn.setToolTip("Toggle Right Sidebar (Copilot & Event Logs) (Ctrl+Shift+A)")
        self.right_sidebar_toggle_btn.clicked.connect(lambda: self.toggle_right_sidebar())

        # Compatibility references
        self.copilot_toggle_btn = self.right_sidebar_toggle_btn
        self.event_logs_btn = self.right_sidebar_toggle_btn

        self._update_right_sidebar_btn_styles()

        self.vpn_status = QLabel("● VPN: Unknown")
        self.vpn_status.setStyleSheet(
            f"color:{theme['sub']};padding:0 8px;"
        )

        navbar_layout.addWidget(self.sidebar_toggle_btn)
        navbar_layout.addWidget(palette_btn)
        navbar_layout.addWidget(self.broadcast_btn)
        navbar_layout.addWidget(self.vpn_btn)
        navbar_layout.addWidget(self.right_sidebar_toggle_btn)
        navbar_layout.addStretch()
        navbar_layout.addWidget(self.vpn_status)

        self.bell_btn = QPushButton("🔔 0")
        self.bell_btn.clicked.connect(
            lambda: NotificationCenterDialog(self, self.services).exec()
        )

        self.clock = QLabel("")
        self.clock.setStyleSheet(f"color:{theme['sub']};padding:0 8px;")

        navbar_layout.addWidget(self.bell_btn)
        navbar_layout.addWidget(self.clock)

        workspace_layout.addWidget(navbar)

        self._clock_timer = QTimer(self)
        self._clock_timer.timeout.connect(
            lambda: self.clock.setText(
                datetime.datetime.now().strftime("%a %b %d · %H:%M:%S")
            )
        )
        self._clock_timer.start(1000)

        # Tabs.
        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
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
        self.tabs.addTab(self.welcome_widget, "🏠 Welcome")

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
            self.mysql_status_btn = QPushButton("📈 MySQL Status")
            self.mysql_status_btn.clicked.connect(self.open_mysql_status_tab)

            toolbar = self.db_manager_widget.layout().itemAt(0).layout()

            if toolbar is not None:
                toolbar.addWidget(self.mysql_status_btn)

        except Exception:
            pass

        # Native Menu Bar
        self._init_menu_bar()

        self.statusBar().showMessage(
            "Ready — double-click a profile · Ctrl+K palette · Ctrl+T terminal · "
            "Ctrl+Shift+V VAPT audit · F8 broadcast · Ctrl+Shift+T reopen"
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

        act_tools_vapt = tools_menu.addAction("🔍 VAPT Web Security & Domain Audit")
        act_tools_vapt.setShortcut("Ctrl+Shift+V")
        act_tools_vapt.triggered.connect(lambda: self.open_vapt_tab())

        act_tools_sec = tools_menu.addAction("🛡️ Security Hub (Firewall, Fail2ban, Malware)")
        act_tools_sec.triggered.connect(self.open_security_hub_selected)

        act_tools_web = tools_menu.addAction("🌐 Web & Virtual Host Manager")
        act_tools_web.triggered.connect(self.open_web_manager_selected)

        act_tools_sys = tools_menu.addAction("🖥 SysAdmin Dashboard")
        act_tools_sys.triggered.connect(self.open_sysadmin_selected)

        act_tools_db = tools_menu.addAction("🗄️ Database Manager")
        act_tools_db.triggered.connect(self.open_db_manager)

        act_tools_tunnel = tools_menu.addAction("🔌 Port Forwarding & SSH Tunnels")
        act_tools_tunnel.triggered.connect(lambda: self.open_port_forwarding())

        act_tools_ansible = tools_menu.addAction("📝 Ansible Multi-Host Runner")
        act_tools_ansible.triggered.connect(self.open_ansible_tab)

        act_tools_playbook = tools_menu.addAction("📜 Ansible Playbook Executor")
        act_tools_playbook.triggered.connect(self.open_ansible_playbook_tab)

        tools_menu.addSeparator()

        act_vpn = tools_menu.addAction("⚡ Toggle VPN (WireGuard / OpenVPN)")
        act_vpn.triggered.connect(self.vpn.toggle)

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

        self.bell_btn.setText(
            f"🔔 {len(self.services.notifications.items)}"
        )

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
                text = "🔁 VPN: Connecting..."
                status = "● VPN: Connecting..."
                color = theme["warn"]

            elif busy_action == "disconnect":
                text = "🔁 VPN: Disconnecting..."
                status = "● VPN: Disconnecting..."
                color = theme["warn"]

            else:
                text = "🔁 VPN: Checking..."
                status = "● VPN: Checking..."
                color = theme["sub"]

        else:
            self.vpn_btn.setEnabled(True)

            if connected:
                text = "🔌 Disconnect VPN"
                status = "● VPN: Connected ✅"
                color = theme["ok"]

            else:
                text = "⚡ Connect VPN"
                status = "● VPN: Disconnected"
                color = theme["warn"]

        self.vpn_btn.setText(text)

        self.vpn_btn.setStyleSheet(
            f"background:{color};color:white;padding:6px 12px;font-weight:bold;"
        )

        self.vpn_status.setText(status)

        self.vpn_status.setStyleSheet(
            f"color:{color};padding:0 8px;"
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
                if name.lower() == "localhost":
                    self.services.set_sudo_password("Localhost", sudo_pass)
                    self.services.set_sudo_password("localhost", sudo_pass)

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
            self.tabs.addTab(self.db_manager_widget, "🗄 Database Manager")
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

    def _connect_terminal(self, terminal) -> None:
        try:
            terminal.input_sent.disconnect(self._on_terminal_input)
        except Exception:
            pass

        terminal.input_sent.connect(self._on_terminal_input)

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

            elif hasattr(widget, "inject_input"):
                out.append(widget)

        return out

    def toggle_broadcast(self) -> None:
        theme = self.services.theme.current

        if not self.broadcast_enabled:
            terminals = self.get_all_terminals()

            if len(terminals) < 2:
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
                return

            self.broadcast_enabled = True

            self.broadcast_btn.setText("📢 ON")

            self.broadcast_btn.setStyleSheet(
                f"background:{theme['warn']};color:#1b1e23;font-weight:bold;padding:6px 12px;"
            )

            self.services.notifications.push(
                "warn",
                "Broadcast ON",
                "Input is mirrored to all terminals",
            )

        else:
            self.broadcast_enabled = False

            self.broadcast_btn.setText("📢")

            self.broadcast_btn.setStyleSheet(
                f"background:{theme['ok']};color:white;padding:6px 12px;"
            )

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
        )

        self._connect_terminal(tab)

        index = self.tabs.addTab(tab, f"🐚 {name}")
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

        jump_creds = None
        jump_host = data.get("jump_host")
        jump_port = int(data.get("jump_port", 22) or 22)
        jump_user = data.get("jump_user")

        if data.get("use_jump"):
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

        self.add_terminal_tab(
            name,
            data.get("ssh_host", ""),
            data.get("ssh_port", 22),
            data.get("ssh_user", ""),
            creds,
            initial_cmd=data.get("initial_cmd", ""),
            use_jump=data.get("use_jump", False),
            jump_host=jump_host,
            jump_port=jump_port,
            jump_user=jump_user,
            jump_creds=jump_creds,
            use_agent=data.get("use_agent", False),
            profile_name=name,
        )

    def add_sftp_tab(
        self,
        name: str,
        host: str,
        port: int,
        user: str,
        creds: SshCredentials,
        *,
        use_agent: bool = False,
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
        )

        index = self.tabs.addTab(tab, f"📁 SFTP: {name}")
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

        self.add_sftp_tab(
            name,
            data.get("ssh_host", ""),
            data.get("ssh_port", 22),
            data.get("ssh_user", ""),
            creds,
            use_agent=data.get("use_agent", False),
        )

    def add_local_command_tab(self, command: str, name: str = "Local"):
        self.services.emit_log("TERMINAL", f"Spawned local command terminal: {command} ({name})", "INFO")
        tab = LocalTerminalTab(
            self.services,
            command=command,
            name=name,
        )

        self._connect_terminal(tab)

        index = self.tabs.addTab(tab, f"⚡ {name}")
        self.tabs.setCurrentIndex(index)

        return tab

    def open_ansible_tab(self):
        self.services.emit_log("ANSIBLE", "Opened Ansible Multi-Host runner tab", "INFO")
        tab = AnsibleTab(self.services, self)

        index = self.tabs.addTab(tab, "🚀 Ansible Runner")
        self.tabs.setCurrentIndex(index)

        return tab

    def open_ansible_playbook_tab(self):
        self.services.emit_log("ANSIBLE", "Opened Ansible Playbook runner tab", "INFO")
        tab = AnsiblePlaybookTab(self.services, self)

        index = self.tabs.addTab(tab, "📜 Ansible Playbook")
        self.tabs.setCurrentIndex(index)

        return tab

    def open_sysadmin(self, name: str):
        self.services.emit_log("SYSADMIN", f"Opened SysAdmin dashboard for: {name}", "INFO")
        data = self.profiles.get(name)

        tab = SysAdminTab(
            self.services,
            name,
            data,
            self,
        )

        index = self.tabs.addTab(tab, f"🖥 {name}")
        self.tabs.setCurrentIndex(index)

        return tab

    def open_sysadmin_local(self):
        local_data = self.profiles.get("localhost") or self.profiles.get("Localhost")
        tab = SysAdminTab(
            self.services,
            "Localhost",
            local_data,
            self,
        )

        index = self.tabs.addTab(tab, "🖥 Localhost")
        self.tabs.setCurrentIndex(index)

        return tab

    def open_sysadmin_selected(self):
        name = self._get_selected_profile_name()

        if name:
            self.open_sysadmin(name)
        else:
            self.open_sysadmin_local()

    def open_web_manager(self, name: str):
        self.services.emit_log("WEB", f"Opened Web Hosting Manager for: {name}", "INFO")
        data = self.profiles.get(name)
        tab = WebManagerTab(
            self.services,
            name,
            data,
            self,
        )
        index = self.tabs.addTab(tab, f"🌐 {name}")
        self.tabs.setCurrentIndex(index)
        return tab

    def open_web_manager_local(self):
        self.services.emit_log("WEB", "Opened Web Hosting Manager for Localhost", "INFO")
        local_data = self.profiles.get("localhost") or self.profiles.get("Localhost")
        tab = WebManagerTab(
            self.services,
            "Localhost",
            local_data,
            self,
        )
        index = self.tabs.addTab(tab, "🌐 Localhost")
        self.tabs.setCurrentIndex(index)
        return tab

    def open_web_manager_selected(self):
        name = self._get_selected_profile_name()
        if name:
            self.open_web_manager(name)
        else:
            self.open_web_manager_local()

    def open_security_hub(self, name: str):
        self.services.emit_log("SECURITY", f"Opened Security Hub for: {name}", "INFO")
        data = self.profiles.get(name)
        tab = SecurityHubTab(
            self.services,
            name,
            data,
            self,
        )
        index = self.tabs.addTab(tab, f"🛡️ {name}")
        self.tabs.setCurrentIndex(index)
        return tab

    def open_security_hub_local(self):
        self.services.emit_log("SECURITY", "Opened Security Hub for Localhost", "INFO")
        local_data = self.profiles.get("localhost") or self.profiles.get("Localhost")
        tab = SecurityHubTab(
            self.services,
            "Localhost",
            local_data,
            self,
        )
        index = self.tabs.addTab(tab, "🛡️ Localhost")
        self.tabs.setCurrentIndex(index)
        return tab

    def open_security_hub_selected(self):
        name = self._get_selected_profile_name()
        if name:
            self.open_security_hub(name)
        else:
            self.open_security_hub_local()

    def open_vapt_tab(self, target: str = "") -> VaptTab:
        if not target:
            name = self._get_selected_profile_name()
            if name and name in self.profiles:
                p = self.profiles[name]
                host = p.get("ssh_host") or p.get("host") or ""
                if host and host not in ("localhost", "127.0.0.1"):
                    target = host
        self.services.emit_log("VAPT", f"Opened VAPT & Web Security Audit tab (Target: {target or 'New'})", "INFO")
        tab = VaptTab(self.services, initial_target=target, parent=self)
        title = f"🔍 VAPT: {target}" if target else "🔍 VAPT Audit"
        index = self.tabs.addTab(tab, title)
        self.tabs.setCurrentIndex(index)
        return tab

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
            host=data.get("host", "localhost"),
            port=int(data.get("port", 22)),
            username=data.get("user", ""),
            creds=data.get("password") or data.get("key"),
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

        data = self.profiles[name]

        tab = SplitTerminalTab(self.services, name, data)

        for terminal in tab.terminals:
            self._connect_terminal(terminal)

        original_add_pane = tab.add_pane

        def wrapped_add_pane(orientation):
            original_add_pane(orientation)

            for terminal in tab.terminals:
                self._connect_terminal(terminal)

        tab.add_pane = wrapped_add_pane

        index = self.tabs.addTab(tab, f"⧉ {name}")
        self.tabs.setCurrentIndex(index)

        self.add_recent(name)

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

    def show_tab_menu(self, pos) -> None:
        index = self.tabs.tabBar().tabAt(pos)
        if index < 0 or self._is_permanent_tab(self.tabs.widget(index)):
            return

        menu = QMenu(self)

        menu.addAction("✏️ Rename").triggered.connect(
            lambda: self._rename_tab(index)
        )

        menu.addAction("❌ Close").triggered.connect(
            lambda: self.close_tab(index)
        )

        menu.addAction("❌ Close Others").triggered.connect(
            lambda: self._close_others(index)
        )

        menu.addAction("❌ Close to the Right").triggered.connect(
            lambda: self._close_right(index)
        )

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
            if s_w < 200 or s_w > 480:
                s_w = 280
            self.sidebar.setMinimumWidth(200)
            self.sidebar.setMaximumWidth(520)
            self.main_splitter.setCollapsible(0, False)
        else:
            s_w = 0
            self.sidebar.setMinimumWidth(0)
            self.main_splitter.setCollapsible(0, True)

        if right_open:
            r_w = getattr(self, "_copilot_width", 340)
            if r_w < 240 or r_w > 480:
                r_w = 340
            self.right_sidebar.setMinimumWidth(240)
            self.right_sidebar.setMaximumWidth(600)
            self.main_splitter.setCollapsible(2, False)
        else:
            r_w = 0
            self.right_sidebar.setMinimumWidth(0)
            self.main_splitter.setCollapsible(2, True)

        # Distribute remaining width to central workspace
        w_w = max(total - s_w - r_w, 400)

        # In case total width is cramped, proportionately share space
        needed = s_w + w_w + r_w
        if needed > total and total > 600:
            w_w = max(int(total * 0.5), 350)
            rem = total - w_w
            if left_open and right_open:
                s_w = int(rem * 0.45)
                r_w = rem - s_w
            elif left_open:
                s_w = rem
            elif right_open:
                r_w = rem

        self.main_splitter.setSizes([s_w, w_w, r_w])

    def _update_left_sidebar_btn_styles(self, is_open: bool) -> None:
        theme = self.services.theme.current
        if hasattr(self, "sidebar_toggle_btn"):
            if is_open:
                self.sidebar_toggle_btn.setText("◀")
                self.sidebar_toggle_btn.setStyleSheet(
                    f"background:{theme.get('accent', '#3daee9')};color:white;font-weight:bold;padding:4px 10px;border-radius:4px;"
                )
            else:
                self.sidebar_toggle_btn.setText("▶")
                self.sidebar_toggle_btn.setStyleSheet(
                    f"background:{theme.get('panel2', '#222222')};color:{theme.get('text', '#cccccc')};padding:4px 10px;border:1px solid {theme.get('border', '#444444')};border-radius:4px;"
                )

    def toggle_sidebar(self, force_visible: Optional[bool] = None) -> None:
        if not hasattr(self, "sidebar"):
            return

        is_open = not self.sidebar.isHidden() and (self.sidebar.width() > 50)
        target = not is_open if force_visible is None else bool(force_visible)

        if target:
            w = getattr(self, "_sidebar_width", 260)
            if w < 200 or w > 480:
                w = 260
            self._sidebar_width = w
            self.sidebar.setMinimumWidth(200)
            self.sidebar.setMaximumWidth(520)
            self.sidebar.show()
            self.main_splitter.setCollapsible(0, False)
        else:
            if hasattr(self, "main_splitter"):
                sizes = self.main_splitter.sizes()
                if sizes and sizes[0] > 100:
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
            if len(sizes) == 3 and sizes[2] > 50:
                self._copilot_width = sizes[2]
                try:
                    self._settings.setValue("copilot/width", sizes[2])
                except Exception:
                    pass
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
            if is_visible:
                self.right_sidebar_toggle_btn.setText("▶")
                self.right_sidebar_toggle_btn.setStyleSheet(
                    f"background:{theme.get('accent', '#3daee9')};color:white;font-weight:bold;padding:4px 10px;border-radius:4px;"
                )
            else:
                self.right_sidebar_toggle_btn.setText("◀")
                self.right_sidebar_toggle_btn.setStyleSheet(
                    f"background:{theme.get('panel2', '#222222')};color:{theme.get('text', '#cccccc')};padding:4px 10px;border:1px solid {theme.get('border', '#444444')};border-radius:4px;"
                )

    def _update_copilot_btn_style(self, visible: bool) -> None:
        self._update_right_sidebar_btn_styles()

    # ------------------------------------------------------------
    # Tools
    # ------------------------------------------------------------

    def open_palette(self):
        CommandPaletteDialog(self, self._build_commands()).exec()

    def _build_commands(self):
        commands = [
            {"name": "VPN Toggle", "cb": self.vpn.toggle},
            {
                "name": "Toggle Right Sidebar",
                "hint": "Ctrl+Shift+A",
                "cb": self.toggle_right_sidebar,
            },
            {
                "name": "Open AI Copilot",
                "hint": "Ctrl+I",
                "cb": lambda: self.toggle_copilot(True),
            },
            {
                "name": "Open Event & Audit Logs",
                "hint": "Ctrl+Shift+L",
                "cb": lambda: self.toggle_event_logs(True),
            },
            {
                "name": "Open Welcome Screen",
                "cb": self.open_welcome_tab,
            },
            {
                "name": "Open Database Manager",
                "cb": self.open_db_manager,
            },
            {
                "name": "New Terminal (selected profile)",
                "hint": "Ctrl+T",
                "cb": self._new_terminal_dialog,
            },
            {
                "name": "Open SFTP (selected profile)",
                "cb": lambda: self.open_sftp(self._get_selected_profile_name()),
            },
            {
                "name": "Open SysAdmin Dashboard (selected profile)",
                "cb": self.open_sysadmin_selected,
            },
            {
                "name": "Open SysAdmin Dashboard (Localhost)",
                "cb": self.open_sysadmin_local,
            },
            {
                "name": "Open Web Hosting Manager (selected profile)",
                "cb": self.open_web_manager_selected,
            },
            {
                "name": "Open Web Hosting Manager (Localhost)",
                "cb": self.open_web_manager_local,
            },
            {
                "name": "Open Security Hub (selected profile)",
                "cb": self.open_security_hub_selected,
            },
            {
                "name": "Open Security Hub (Localhost)",
                "cb": self.open_security_hub_local,
            },
            {
                "name": "Open VAPT & Web Security Audit",
                "hint": "Ctrl+Shift+V",
                "cb": lambda: self.open_vapt_tab(),
            },
            {
                "name": "Port Forwarding & Tunnels (selected profile)",
                "cb": lambda: self.open_port_forwarding(),
            },
            {
                "name": "Split Terminal (selected profile)",
                "cb": self.open_split_selected,
            },
            {"name": "Ansible & Multi-Host Runner", "cb": self.open_ansible_tab},
            {"name": "Ansible Playbook/Vault Runner", "cb": self.open_ansible_playbook_tab},
            {
                "name": "Local Shell",
                "cb": lambda: self.add_local_command_tab("bash", "Local Shell"),
            },
            {"name": "Toggle Broadcast Mode", "hint": "F8", "cb": self.toggle_broadcast},
            {"name": "Command Palette", "hint": "Ctrl+K", "cb": self.open_palette},
            {"name": "Connection Manager", "hint": "Ctrl+,", "cb": self.open_config},
            {"name": "Theme Manager", "cb": self.open_theme_dialog},
            {"name": "Snippets Library", "cb": self.open_snippets},
            {
                "name": "SSH Key Manager",
                "cb": lambda: KeyManagerDialog(self, self.services).exec(),
            },
            {"name": "Import ~/.ssh/config", "cb": self.import_ssh_config},
            {
                "name": "Session Recordings",
                "cb": lambda: SessionLogViewerDialog(self, self.services).exec(),
            },
            {
                "name": "Notification Center",
                "cb": lambda: NotificationCenterDialog(self, self.services).exec(),
            },
            {"name": "Toggle Sidebar", "hint": "Ctrl+B", "cb": self.toggle_sidebar},
            {"name": "Ping All Profiles", "cb": self._ping_all_profiles},
            {"name": "Reopen Closed Tab", "hint": "Ctrl+Shift+T", "cb": self._reopen_closed_tab},
        ]

        for name in self.profiles:
            commands.append(
                {
                    "name": f"Connect: {name}",
                    "hint": "terminal",
                    "cb": lambda n=name: self.connect_profile(n),
                }
            )

            commands.append(
                {
                    "name": f"SFTP: {name}",
                    "hint": "files",
                    "cb": lambda n=name: self.open_sftp(n),
                }
            )

            commands.append(
                {
                    "name": f"SysAdmin: {name}",
                    "hint": "dashboard",
                    "cb": lambda n=name: self.open_sysadmin(n),
                }
            )

            commands.append(
                {
                    "name": f"Web: {name}",
                    "hint": "hosting",
                    "cb": lambda n=name: self.open_web_manager(n),
                }
            )

            commands.append(
                {
                    "name": f"Security: {name}",
                    "hint": "firewall/fail2ban/audit",
                    "cb": lambda n=name: self.open_security_hub(n),
                }
            )

            commands.append(
                {
                    "name": f"Port Forwarding: {name}",
                    "hint": "ssh tunnels",
                    "cb": lambda n=name: self.open_port_forwarding(n),
                }
            )

        commands.append(
            {
                "name": "Add DB Profile",
                "hint": "database",
                "cb": self.add_db_profile,
            }
        )

        for name in self.db_profiles:
            commands.append(
                {
                    "name": f"Connect DB: {name}",
                    "hint": "database",
                    "cb": lambda n=name: self.activate_db_profile(n),
                }
            )

        return commands

    def open_config(self):
        ConnectionManagerDialog(self, self.services).exec()

    def open_theme_dialog(self):
        dialog = ThemeDialog(self, self.services)

        if dialog.exec() == QDialog.DialogCode.Accepted:
            name = dialog.chosen()

            if name:
                self.services.config.set("ui_theme", name)

                self.services.config.set(
                    "terminal_theme",
                    self.services.theme.get_theme(name).get("xterm", "dark"),
                )

                self.services.config.save()

                app = QApplication.instance()

                if app:
                    self.services.theme.apply(app, name)

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

        index = self.tabs.addTab(tab, "📈 MySQL Status")
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
                    widget.close()
                except Exception:
                    pass

        try:
            self.db_manager_widget.session_manager.stop_all()
        except Exception:
            pass

        event.accept()
