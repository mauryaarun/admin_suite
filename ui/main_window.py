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

from admin_suite.vpn.service import VpnService

from admin_suite.ui.toasts import Toast, NotificationCenterDialog
from admin_suite.ui.palette import CommandPaletteDialog

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
        self.resize(1440, 860)

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
        self.services.debug.log_emitted.connect(self.append_debug)
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

        try:
            sidebar_visible = self._settings.value("sidebar/visible", True, type=bool)
            sidebar_width = self._settings.value("sidebar/width", 280, type=int)

            self._sidebar_width = sidebar_width if sidebar_width > 0 else 280

            self.sidebar.setVisible(sidebar_visible)

            if hasattr(self, "sidebar_toggle_btn"):
                self.sidebar_toggle_btn.setText(
                    "◀" if sidebar_visible else "▶"
                )

            copilot_visible = self._settings.value("copilot/visible", True, type=bool)
            copilot_width = self._settings.value("copilot/width", 340, type=int)
            self._copilot_width = copilot_width if copilot_width > 0 else 340

            if hasattr(self, "ai_tab"):
                self.ai_tab.setVisible(copilot_visible)
            self._update_copilot_btn_style(copilot_visible)

            if hasattr(self, "main_splitter"):
                total = max(self.main_splitter.width(), self.width(), 1440)
                s_w = self._sidebar_width if sidebar_visible else 0
                c_w = self._copilot_width if copilot_visible else 0
                w_w = max(total - s_w - c_w, 600)
                self.main_splitter.setSizes([s_w, w_w, c_w])

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
        header.setStyleSheet(
            f"background:{theme['win']};"
            f"color:{theme['accent']};"
            "font-size:15px;font-weight:bold;padding:12px;letter-spacing:1px;"
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
            ("⚡ Local Shell", lambda: self.add_local_command_tab("bash", "Local Shell")),
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

        # Copilot panel (Right sidebar)
        from admin_suite.ai.assistant_tab import AIAssistantTab
        self.ai_tab = AIAssistantTab(self.services, self)
        self.ai_tab.setMinimumWidth(0)
        self._copilot_width = 340

        self.main_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.main_splitter.addWidget(self.sidebar)
        self.main_splitter.addWidget(workspace)
        self.main_splitter.addWidget(self.ai_tab)

        self.main_splitter.setStretchFactor(0, 0)
        self.main_splitter.setStretchFactor(1, 1)
        self.main_splitter.setStretchFactor(2, 0)
        self.main_splitter.setChildrenCollapsible(True)
        self.main_splitter.setSizes([280, 820, 340])

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

        self.sidebar_toggle_btn = QToolButton()
        self.sidebar_toggle_btn.setText("◀")
        self.sidebar_toggle_btn.setToolTip("Toggle Sidebar (Ctrl+B)")

        self.sidebar_toggle_btn.setStyleSheet(
            f"background:{theme['panel2']};"
            f"border:1px solid {theme['border']};"
            "border-radius:4px;padding:4px 8px;"
        )

        self.sidebar_toggle_btn.clicked.connect(self.toggle_sidebar)

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

        self.copilot_toggle_btn = QPushButton("🤖 Copilot ▶")
        self.copilot_toggle_btn.setToolTip("Toggle AI Copilot Sidebar (Ctrl+Shift+A or Ctrl+I)")
        self.copilot_toggle_btn.clicked.connect(self.toggle_copilot)
        self._update_copilot_btn_style(True)

        self.vpn_status = QLabel("● VPN: Unknown")
        self.vpn_status.setStyleSheet(
            f"color:{theme['sub']};padding:0 8px;"
        )

        navbar_layout.addWidget(self.sidebar_toggle_btn)
        navbar_layout.addWidget(palette_btn)
        navbar_layout.addWidget(self.broadcast_btn)
        navbar_layout.addWidget(self.vpn_btn)
        navbar_layout.addWidget(self.copilot_toggle_btn)
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

        # Database manager.
        self.db_manager_widget = DatabaseManagerWidget(self.services, self)

        self.tabs.addTab(self.db_manager_widget, "🗄 Database Manager")

        # Debug console.
        self.debug_console = QTextEdit()
        self.debug_console.setReadOnly(True)
        self.debug_console.setFont(QFont("JetBrains Mono, Consolas", 9))
        self.tabs.addTab(self.debug_console, "⚠️ Debug")

        # Disable close buttons on permanent tabs
        for tab_widget in (self.db_manager_widget, self.debug_console):
            t_idx = self.tabs.indexOf(tab_widget)
            if t_idx >= 0:
                self.tabs.tabBar().setTabButton(
                    t_idx,
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

        self.statusBar().showMessage(
            "Ready — double-click a profile · Ctrl+K palette · Ctrl+T terminal · "
            "F8 broadcast · Ctrl+Shift+T reopen"
        )

    def _init_shortcuts(self) -> None:
        self._sc = []

        for key, callback in (
            ("Ctrl+K", self.open_palette),
            ("Ctrl+Shift+P", self.open_palette),
            ("Ctrl+T", self._new_terminal_dialog),
            ("Ctrl+W", self._close_current_tab),
            ("Ctrl+B", self.toggle_sidebar),
            ("Ctrl+Shift+A", self.toggle_copilot),
            ("Ctrl+I", self.toggle_copilot),
            ("F8", self.toggle_broadcast),
            ("Ctrl+,", self.open_config),
            ("Ctrl+Shift+T", self._reopen_closed_tab),
        ):
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.activated.connect(callback)

            self._sc.append(shortcut)

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
        try:
            doc = self.debug_console.document()

            if doc.blockCount() > 5000:
                cursor = QTextCursor(doc)
                cursor.movePosition(QTextCursor.MoveOperation.Start)

                for _ in range(1000):
                    cursor.movePosition(
                        QTextCursor.MoveOperation.Down,
                        QTextCursor.MoveMode.KeepAnchor,
                    )

                cursor.removeSelectedText()
                cursor.deleteChar()

        except Exception:
            pass

        self.debug_console.append(text)
        self.debug_console.moveCursor(QTextCursor.MoveOperation.End)

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
        else:
            msg = (
                res.get("err", "").strip()
                or res.get("out", "").strip()
                or "VPN connection failed"
            )[:300]

            self.services.notifications.push("error", "VPN", msg)

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
        else:
            msg = (
                res.get("err", "").strip()
                or res.get("out", "").strip()
                or "VPN disconnect failed"
            )[:300]

            self.services.notifications.push("error", "VPN", msg)

    # ------------------------------------------------------------
    # Profiles
    # ------------------------------------------------------------

    def load_profiles(self) -> None:
        self.profiles = read_json(PROFILES_FILE, {}) or {}

        for name, data in self.profiles.items():
            ssh_pass = self.services.secrets.get(f"prof_{name}", "")

            if ssh_pass:
                data["ssh_pass"] = ssh_pass

            jump_pass = self.services.secrets.get(f"prof_jump_{name}", "")

            if jump_pass:
                data["jump_pass"] = jump_pass

        self.refresh_profile_tree()

    def save_profiles(self) -> None:
        clean = {}

        for name, data in self.profiles.items():
            c = dict(data)

            ssh_pass = c.pop("ssh_pass", "") or ""
            jump_pass = c.pop("jump_pass", "") or ""

            self.services.secrets.set(f"prof_{name}", ssh_pass)
            self.services.secrets.set(f"prof_jump_{name}", jump_pass)

            clean[name] = c

        write_json_secure(PROFILES_FILE, clean)

    def refresh_profile_tree(self, *args) -> None:
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
            group_item = QTreeWidgetItem([f"{icon} {group_name}"])
            group_item.setForeground(0, QColor(theme["accent"]))

            font = group_item.font(0)
            font.setBold(True)
            group_item.setFont(0, font)

            for name, data in sorted(items, key=lambda x: x[0].lower()):
                status = self._profile_status.get(name, "unknown")

                dot = {
                    "ok": "🟢",
                    "fail": "🔴",
                }.get(status, "⚪")

                child = QTreeWidgetItem([f"{dot} {name}"])
                child.setData(0, Qt.ItemDataRole.UserRole, name)

                child.setToolTip(
                    0,
                    f"{data.get('ssh_user', '')}@"
                    f"{data.get('ssh_host', '')}:"
                    f"{data.get('ssh_port', '22')}\n"
                    f"Status: {status}\n"
                    f"Tags: {data.get('tags', '')}",
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

    def refresh_db_list(self) -> None:
        self.db_list.clear()

        for name, data in sorted(self.db_profiles.items()):
            item = QListWidgetItem(
                f"🗄 {name}  [{data.get('backend', 'mysql')}]"
            )

            item.setData(Qt.ItemDataRole.UserRole, name)

            item.setToolTip(
                f"{data.get('db_user', '')}@"
                f"{data.get('db_host', '')}:{data.get('db_port', '')}"
            )

            self.db_list.addItem(item)

    def _get_selected_db_profile_name(self) -> Optional[str]:
        item = self.db_list.currentItem()

        return item.data(Qt.ItemDataRole.UserRole) if item else None

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

    def show_db_menu(self, pos) -> None:
        item = self.db_list.itemAt(pos)

        menu = QMenu(self)

        if item:
            self.db_list.setCurrentItem(item)

            name = item.data(Qt.ItemDataRole.UserRole)

            menu.addAction("🔌 Connect").triggered.connect(
                lambda: self.activate_db_profile(name)
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

    def activate_db_profile(self, name: str) -> None:
        data = self.db_profiles.get(name)

        if not data:
            return

        profile = dict(data)

        self.db_manager_widget.set_active_profile(profile)

        self.tabs.setCurrentWidget(self.db_manager_widget)

        self.db_manager_widget.load_schemas()

        self._notify(f"Database profile activated: {name}")

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

        creds = profile_creds(data)

        jump_creds = None

        if data.get("use_jump"):
            jump_creds = SshCredentials(
                password=data.get("jump_pass") or None
            )

        self.add_terminal_tab(
            name,
            data.get("ssh_host", ""),
            data.get("ssh_port", 22),
            data.get("ssh_user", ""),
            creds,
            initial_cmd=data.get("initial_cmd", ""),
            use_jump=data.get("use_jump", False),
            jump_host=data.get("jump_host"),
            jump_port=int(data.get("jump_port", 22) or 22),
            jump_user=data.get("jump_user"),
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
        tab = AnsibleTab(self.services, self)

        index = self.tabs.addTab(tab, "🚀 Ansible Runner")
        self.tabs.setCurrentIndex(index)

        return tab

    def open_ansible_playbook_tab(self):
        tab = AnsiblePlaybookTab(self.services, self)

        index = self.tabs.addTab(tab, "📜 Ansible Playbook")
        self.tabs.setCurrentIndex(index)

        return tab

    def open_sysadmin(self, name: str):
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
        tab = SysAdminTab(
            self.services,
            "Localhost",
            None,
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
        tab = WebManagerTab(
            self.services,
            "Localhost",
            None,
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
        tab = SecurityHubTab(
            self.services,
            "Localhost",
            None,
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

    def open_port_forwarding(self, profile_name: Optional[str] = None):
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

        if hasattr(self, "ai_tab") and self.ai_tab.isVisible():
            self.ai_tab.update_context()

    # ------------------------------------------------------------
    # Tab management
    # ------------------------------------------------------------

    def _is_permanent_tab(self, widget) -> bool:
        permanent = (
            getattr(self, "db_manager_widget", None),
            getattr(self, "ai_tab", None),
            getattr(self, "debug_console", None),
        )
        return widget is not None and widget in permanent

    def close_tab(self, index: int) -> None:
        widget = self.tabs.widget(index)
        if self._is_permanent_tab(widget):
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

        if index >= 2:
            self.close_tab(index)

    def _reopen_closed_tab(self):
        if self._last_closed:
            func, args, kwargs = self._last_closed

            func(*args, **kwargs)

            self._last_closed = None

    def show_tab_menu(self, pos) -> None:
        index = self.tabs.tabBar().tabAt(pos)

        if index <= 1:
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

    def toggle_sidebar(self) -> None:
        if not hasattr(self, "sidebar"):
            return

        visible = not self.sidebar.isVisible()

        if visible:
            self.sidebar.show()

            width = getattr(self, "_sidebar_width", 280)

            if hasattr(self, "main_splitter"):
                sizes = self.main_splitter.sizes()
                if len(sizes) == 3:
                    c_w = sizes[2] if hasattr(self, "ai_tab") and self.ai_tab.isVisible() else 0
                    total = sum(sizes) if sum(sizes) > 0 else max(self.width(), 1200)
                    w_w = max(total - width - c_w, 400)
                    self.main_splitter.setSizes([width, w_w, c_w])
                else:
                    total = self.main_splitter.width()
                    if total <= 0:
                        total = self.width()
                    self.main_splitter.setSizes(
                        [width, max(total - width, 500)]
                    )

        else:
            if hasattr(self, "main_splitter"):
                sizes = self.main_splitter.sizes()

                if sizes and sizes[0] > 0:
                    self._sidebar_width = sizes[0]

            self.sidebar.hide()

        if hasattr(self, "sidebar_toggle_btn"):
            self.sidebar_toggle_btn.setText("◀" if visible else "▶")

        try:
            self._settings.setValue("sidebar/visible", visible)

            if visible:
                self._settings.setValue(
                    "sidebar/width",
                    getattr(self, "_sidebar_width", 280),
                )

        except Exception:
            pass

    def toggle_copilot(self, force_visible: Optional[bool] = None) -> None:
        if not hasattr(self, "ai_tab"):
            return

        visible = force_visible if force_visible is not None else not self.ai_tab.isVisible()

        if visible:
            self.ai_tab.show()
            width = getattr(self, "_copilot_width", 340)

            if hasattr(self, "main_splitter"):
                sizes = self.main_splitter.sizes()
                if len(sizes) == 3:
                    s_w = sizes[0] if hasattr(self, "sidebar") and self.sidebar.isVisible() else 0
                    total = sum(sizes) if sum(sizes) > 0 else max(self.width(), 1200)
                    w_w = max(total - s_w - width, 400)
                    self.main_splitter.setSizes([s_w, w_w, width])

            self.ai_tab.update_context()
        else:
            if hasattr(self, "main_splitter"):
                sizes = self.main_splitter.sizes()
                if len(sizes) == 3 and sizes[2] > 0:
                    self._copilot_width = sizes[2]

            self.ai_tab.hide()

        self._update_copilot_btn_style(visible)

        try:
            self._settings.setValue("copilot/visible", visible)
            if visible:
                self._settings.setValue(
                    "copilot/width",
                    getattr(self, "_copilot_width", 340),
                )
        except Exception:
            pass

    def _update_copilot_btn_style(self, visible: bool) -> None:
        if not hasattr(self, "copilot_toggle_btn"):
            return
        theme = self.services.theme.current
        if visible:
            self.copilot_toggle_btn.setText("🤖 Copilot ▶")
            self.copilot_toggle_btn.setStyleSheet(
                f"background:{theme.get('accent', '#3daee9')};color:white;font-weight:bold;padding:4px 10px;border-radius:4px;"
            )
        else:
            self.copilot_toggle_btn.setText("🤖 Copilot ◀")
            self.copilot_toggle_btn.setStyleSheet(
                f"background:{theme.get('panel2', '#222222')};color:{theme.get('text', '#cccccc')};padding:4px 10px;border:1px solid {theme.get('border', '#444444')};border-radius:4px;"
            )

    # ------------------------------------------------------------
    # Tools
    # ------------------------------------------------------------

    def open_palette(self):
        CommandPaletteDialog(self, self._build_commands()).exec()

    def _build_commands(self):
        commands = [
            {"name": "VPN Toggle", "cb": self.vpn.toggle},
            {
                "name": "Toggle AI Copilot Sidebar",
                "hint": "Ctrl+Shift+A",
                "cb": self.toggle_copilot,
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
                    self.sidebar.isVisible(),
                )

                if self.sidebar.isVisible() and hasattr(self, "main_splitter"):
                    sizes = self.main_splitter.sizes()

                    if sizes and sizes[0] > 0:
                        self._settings.setValue("sidebar/width", sizes[0])

        except Exception:
            pass

        for i in range(self.tabs.count() - 1, 1, -1):
            widget = self.tabs.widget(i)

            if widget is not None:
                try:
                    widget.close()
                except Exception:
                    pass

        try:
            self.db_manager_widget.session_manager.stop_all()
        except Exception:
            pass

        event.accept()
