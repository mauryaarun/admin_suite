"""
Welcome screen widget for Admin Suite.
Displays quick actions, recent connections, shortcuts, and system overview on launch.
Optimized for zero-scrollbar display with collapsible title and shortcuts panes.
"""

from __future__ import annotations

import os
from typing import Any, Callable, Optional

from PyQt6.QtCore import Qt, QSettings
from PyQt6.QtGui import QFont, QPixmap
from PyQt6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from admin_suite.core.paths import get_app_icon_path


class WelcomeWidget(QWidget):
    """
    Modern landing / welcome screen for Admin Suite.
    Compact, zero-scrollbar layout with collapsible title pane and shortcuts reference.
    """

    def __init__(self, services, main_window, parent=None):
        super().__init__(parent)
        self.services = services
        self.main_window = main_window

        self._settings = QSettings("AdminSuite", "v5")
        self._title_collapsed = self._settings.value("welcome/title_collapsed", False, type=bool)
        self._shortcuts_collapsed = self._settings.value("welcome/shortcuts_collapsed", False, type=bool)
        self._recent_collapsed = self._settings.value("welcome/recent_collapsed", False, type=bool)

        theme = self.services.theme.current

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # Scroll area with scrollbars disabled for clean responsive presentation
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet(f"background:{theme.get('win', '#1e1e1e')};border:none;")

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(18, 12, 18, 12)
        content_layout.setSpacing(10)

        # ------------------------------------------------------------
        # Collapsible Hero Banner / Application Title Pane
        # ------------------------------------------------------------
        self.hero_frame = QFrame()
        self.hero_frame.setStyleSheet(
            f"background: {theme.get('panel2', '#252526')};"
            f"border: 1px solid {theme.get('border', '#333333')};"
            "border-radius: 8px; padding: 4px;"
        )
        self.hero_layout = QVBoxLayout(self.hero_frame)
        self.hero_layout.setContentsMargins(8, 6, 8, 6)
        self.hero_layout.setSpacing(4)

        icon_path = str(get_app_icon_path())

        # 1. Expanded Title Pane Widget
        self.hero_expanded_widget = QWidget()
        hero_exp_layout = QVBoxLayout(self.hero_expanded_widget)
        hero_exp_layout.setContentsMargins(0, 0, 0, 0)
        hero_exp_layout.setSpacing(4)

        top_row = QHBoxLayout()
        top_row.setSpacing(10)

        if os.path.exists(icon_path):
            icon_lbl = QLabel()
            pm = QPixmap(icon_path).scaled(
                40, 40, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
            )
            icon_lbl.setPixmap(pm)
            icon_lbl.setFixedSize(40, 40)
            top_row.addWidget(icon_lbl)

        title_vbox = QVBoxLayout()
        title_vbox.setSpacing(2)
        title_h = QHBoxLayout()
        title_h.setSpacing(8)

        title_lbl = QLabel("🛠 ADMIN SUITE")
        title_lbl.setFont(QFont("Segoe UI, Inter, Sans-serif", 15, QFont.Weight.Bold))
        title_lbl.setStyleSheet(f"color:{theme.get('accent', '#3daee9')};")

        badge_lbl = QLabel("v5")
        badge_lbl.setStyleSheet(
            f"background:{theme.get('accent', '#3daee9')};color:white;"
            "font-size:10px;font-weight:bold;padding:1px 6px;border-radius:6px;"
        )
        badge_tag = QLabel("Workstation")
        badge_tag.setStyleSheet(
            f"background:{theme.get('win', '#1a1a1a')};color:{theme.get('sub', '#888')};"
            "font-size:10px;padding:1px 6px;border-radius:4px;border:1px solid #333;"
        )

        title_h.addWidget(title_lbl)
        title_h.addWidget(badge_lbl)
        title_h.addWidget(badge_tag)
        title_h.addStretch()

        subtitle_lbl = QLabel("Unified DevOps, Systems, Database, and Multi-Cloud Workbench")
        subtitle_lbl.setStyleSheet(f"color:{theme.get('sub', '#aaaaaa')};font-size:11.5px;")

        title_vbox.addLayout(title_h)
        title_vbox.addWidget(subtitle_lbl)
        top_row.addLayout(title_vbox, 1)

        # Quick stats line in expanded pane
        self.stat_profiles = QLabel("🐚 SSH: <b>0</b>")
        self.stat_dbs = QLabel("🗄️ DBs: <b>0</b>")
        self.stat_theme = QLabel("🎨 Theme: <b>Default</b>")
        pill_style = (
            f"background:{theme.get('win', '#181818')};color:{theme.get('text', '#ccc')};"
            f"font-size:11px;padding:3px 8px;border-radius:4px;border:1px solid {theme.get('border', '#333')};"
        )
        for lbl in (self.stat_profiles, self.stat_dbs, self.stat_theme):
            lbl.setStyleSheet(pill_style)
            top_row.addWidget(lbl)

        # Title collapse toggle button
        self.title_toggle_btn = QPushButton("▲ Collapse")
        self.title_toggle_btn.setToolTip("Collapse header banner to save screen space")
        self.title_toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.title_toggle_btn.setStyleSheet(
            f"background:{theme.get('win', '#181818')};border:1px solid {theme.get('border', '#333')};"
            f"color:{theme.get('sub', '#888')};font-size:11px;padding:3px 8px;border-radius:4px;"
        )
        self.title_toggle_btn.clicked.connect(self._toggle_title_pane)
        top_row.addWidget(self.title_toggle_btn)

        hero_exp_layout.addLayout(top_row)
        self.hero_layout.addWidget(self.hero_expanded_widget)

        # 2. Collapsed Title Pane Widget (Compact single line)
        self.hero_collapsed_widget = QWidget()
        hero_col_layout = QHBoxLayout(self.hero_collapsed_widget)
        hero_col_layout.setContentsMargins(0, 0, 0, 0)
        hero_col_layout.setSpacing(8)

        if os.path.exists(icon_path):
            icon_lbl_sm = QLabel()
            pm_sm = QPixmap(icon_path).scaled(
                22, 22, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
            )
            icon_lbl_sm.setPixmap(pm_sm)
            icon_lbl_sm.setFixedSize(22, 22)
            hero_col_layout.addWidget(icon_lbl_sm)

        col_title = QLabel("🛠 ADMIN SUITE v5")
        col_title.setFont(QFont("Segoe UI, Inter, Sans-serif", 11, QFont.Weight.Bold))
        col_title.setStyleSheet(f"color:{theme.get('accent', '#3daee9')};")
        hero_col_layout.addWidget(col_title)

        self.stat_profiles_sm = QLabel("🐚 SSH: <b>0</b>")
        self.stat_dbs_sm = QLabel("🗄️ DBs: <b>0</b>")
        self.stat_theme_sm = QLabel("🎨 Theme: <b>Default</b>")
        for lbl in (self.stat_profiles_sm, self.stat_dbs_sm, self.stat_theme_sm):
            lbl.setStyleSheet(pill_style)
            hero_col_layout.addWidget(lbl)

        hero_col_layout.addStretch()

        self.title_expand_btn = QPushButton("▼ Expand")
        self.title_expand_btn.setToolTip("Expand application title pane")
        self.title_expand_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.title_expand_btn.setStyleSheet(
            f"background:{theme.get('win', '#181818')};border:1px solid {theme.get('border', '#333')};"
            f"color:{theme.get('sub', '#888')};font-size:11px;padding:3px 8px;border-radius:4px;"
        )
        self.title_expand_btn.clicked.connect(self._toggle_title_pane)
        hero_col_layout.addWidget(self.title_expand_btn)

        self.hero_layout.addWidget(self.hero_collapsed_widget)

        # Apply initial collapsed state for title pane
        self.hero_expanded_widget.setVisible(not self._title_collapsed)
        self.hero_collapsed_widget.setVisible(self._title_collapsed)

        content_layout.addWidget(self.hero_frame)

        # ------------------------------------------------------------
        # Quick Actions Grid (Updated & Streamlined)
        # ------------------------------------------------------------
        qa_header_layout = QHBoxLayout()
        qa_title = QLabel("🚀 Quick Actions")
        qa_title.setFont(QFont("Segoe UI, Inter, Sans-serif", 12, QFont.Weight.Bold))
        qa_title.setStyleSheet(f"color:{theme.get('text', '#ffffff')};")
        qa_header_layout.addWidget(qa_title)

        qa_hint = QLabel("Select an action to launch workstations and management sessions")
        qa_hint.setStyleSheet(f"color:{theme.get('sub', '#777')};font-size:11px;margin-left:6px;")
        qa_header_layout.addWidget(qa_hint)
        qa_header_layout.addStretch()
        content_layout.addLayout(qa_header_layout)

        cards_grid = QGridLayout()
        cards_grid.setSpacing(10)

        cards_data: list[tuple[str, list[tuple[str, Callable[[], Any]]]]] = [
            (
                "🐚 Terminals & SSH",
                [
                    ("⚡ Quick Terminal (Ctrl+T)", self.main_window._new_terminal_dialog),
                    ("➕ Add SSH Profile", self.main_window.add_profile),
                    ("💻 Local Shell (bash)", lambda: self.main_window.add_local_command_tab("bash", "Local Shell")),
                    ("🪟 Split Multi-Terminal", self._open_split_quick),
                ],
            ),
            (
                "🗄️ Database Manager",
                [
                    ("🗄️ Database Workbench", self.main_window.open_db_manager),
                    ("➕ Add DB Profile (MySQL/PG/SQLite)", self.main_window.add_db_profile),
                    ("📈 MySQL Server Status", self.main_window.open_mysql_status_tab),
                    ("🔍 DB Query History", self.main_window.open_db_manager),
                ],
            ),
            (
                "📁 SFTP & Network Tools",
                [
                    ("📁 SFTP File Manager", self._open_sftp_quick),
                    ("🔌 Port Forwarding & Tunnels", lambda: self.main_window.open_port_forwarding()),
                    ("🔑 SSH Key Manager", self._open_key_manager),
                    ("📥 Import ~/.ssh/config", self.main_window.import_ssh_config),
                ],
            ),
            (
                "🖥️ SysAdmin & Linux OS",
                [
                    ("📊 SysAdmin Dashboard", self.main_window.open_sysadmin_selected),
                    ("⚙️ Linux & Distro Settings", self._open_linux_settings),
                    ("🎬 Session Log Recordings", self._open_session_logs),
                    ("📡 Ping All Configured Hosts", self.main_window._ping_all_profiles),
                ],
            ),
            (
                "📝 DevOps & Automation",
                [
                    ("📝 Ansible Multi-Host Runner", self.main_window.open_ansible_tab),
                    ("📜 Ansible Playbook Executor", self.main_window.open_ansible_playbook_tab),
                    ("📋 Command Snippets Library", self.main_window.open_snippets),
                    ("📢 Toggle Broadcast Mode (F8)", self.main_window.toggle_broadcast),
                ],
            ),
            (
                "🛡️ Security, Web & AI",
                [
                    ("🔍 VAPT & Web Security Audit", self.main_window.open_vapt_tab),
                    ("🛡️ Security Hub (Firewall/Fail2ban)", self.main_window.open_security_hub_selected),
                    ("🌐 Web & Virtual Host Manager", self.main_window.open_web_manager_selected),
                    ("⚡ WireGuard / OpenVPN Toggle", self.main_window.vpn.toggle),
                    ("🤖 AI Copilot (Ctrl+Shift+A)", lambda: self.main_window.toggle_copilot(True)),
                ],
            ),
        ]

        row, col = 0, 0
        for card_title, actions in cards_data:
            card = self._create_card(card_title, actions, theme)
            cards_grid.addWidget(card, row, col)
            col += 1
            if col >= 3:
                col = 0
                row += 1

        content_layout.addLayout(cards_grid)

        # ------------------------------------------------------------
        # Bottom Row: Side-by-Side Recent Connections & Keyboard Shortcuts
        # Both sections are collapsible to save vertical space.
        # ------------------------------------------------------------
        bottom_layout = QHBoxLayout()
        bottom_layout.setSpacing(10)

        # 1. Recent Connections Frame
        self.recent_frame = QFrame()
        self.recent_frame.setStyleSheet(
            f"background:{theme.get('panel2', '#222')};"
            f"border:1px solid {theme.get('border', '#333')};"
            "border-radius:6px;padding:6px;"
        )
        recent_box = QVBoxLayout(self.recent_frame)
        recent_box.setContentsMargins(6, 4, 6, 4)
        recent_box.setSpacing(4)

        recent_header = QHBoxLayout()
        recent_title = QLabel("🕒 Recent Connections")
        recent_title.setFont(QFont("Segoe UI, Inter, Sans-serif", 11, QFont.Weight.Bold))
        recent_title.setStyleSheet(f"color:{theme.get('text', '#ffffff')};")
        recent_header.addWidget(recent_title)
        recent_header.addStretch()

        self.recent_toggle_btn = QPushButton("▲ Collapse" if not self._recent_collapsed else "▼ Expand")
        self.recent_toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.recent_toggle_btn.setStyleSheet(
            f"background:{theme.get('win', '#181818')};border:1px solid {theme.get('border', '#333')};"
            f"color:{theme.get('sub', '#888')};font-size:10px;padding:2px 6px;border-radius:3px;"
        )
        self.recent_toggle_btn.clicked.connect(self._toggle_recent_pane)
        recent_header.addWidget(self.recent_toggle_btn)
        recent_box.addLayout(recent_header)

        self.recent_body = QWidget()
        self.recent_layout = QVBoxLayout(self.recent_body)
        self.recent_layout.setContentsMargins(0, 2, 0, 0)
        self.recent_layout.setSpacing(3)
        self.recent_body.setVisible(not self._recent_collapsed)
        recent_box.addWidget(self.recent_body)

        bottom_layout.addWidget(self.recent_frame, 1)

        # 2. Keyboard Shortcuts Frame (Collapsible)
        self.shortcuts_frame = QFrame()
        self.shortcuts_frame.setStyleSheet(
            f"background:{theme.get('panel2', '#222')};"
            f"border:1px solid {theme.get('border', '#333')};"
            "border-radius:6px;padding:6px;"
        )
        shortcuts_box = QVBoxLayout(self.shortcuts_frame)
        shortcuts_box.setContentsMargins(6, 4, 6, 4)
        shortcuts_box.setSpacing(4)

        shortcuts_header = QHBoxLayout()
        shortcuts_title = QLabel("⌨️ Productivity Shortcuts")
        shortcuts_title.setFont(QFont("Segoe UI, Inter, Sans-serif", 11, QFont.Weight.Bold))
        shortcuts_title.setStyleSheet(f"color:{theme.get('text', '#ffffff')};")
        shortcuts_header.addWidget(shortcuts_title)
        shortcuts_header.addStretch()

        self.shortcuts_toggle_btn = QPushButton("▲ Collapse" if not self._shortcuts_collapsed else "▼ Expand")
        self.shortcuts_toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.shortcuts_toggle_btn.setStyleSheet(
            f"background:{theme.get('win', '#181818')};border:1px solid {theme.get('border', '#333')};"
            f"color:{theme.get('sub', '#888')};font-size:10px;padding:2px 6px;border-radius:3px;"
        )
        self.shortcuts_toggle_btn.clicked.connect(self._toggle_shortcuts_pane)
        shortcuts_header.addWidget(self.shortcuts_toggle_btn)
        shortcuts_box.addLayout(shortcuts_header)

        self.shortcuts_body = QWidget()
        sc_grid = QGridLayout(self.shortcuts_body)
        sc_grid.setContentsMargins(0, 2, 0, 0)
        sc_grid.setHorizontalSpacing(8)
        sc_grid.setVerticalSpacing(3)

        shortcuts_list = [
            ("Ctrl + K", "Command Palette"),
            ("Ctrl + T", "New Terminal"),
            ("Ctrl + Shift + A", "AI Copilot"),
            ("Ctrl + Shift + L", "Event & Audit Logs"),
            ("Ctrl + B", "Toggle Sidebar"),
            ("F8", "Broadcast Input"),
            ("Ctrl + W", "Close Tab"),
            ("Ctrl + Shift + T", "Reopen Last Tab"),
        ]

        for i, (key, desc) in enumerate(shortcuts_list):
            k_lbl = QLabel(f"<code>{key}</code>")
            k_lbl.setStyleSheet(
                f"background:{theme.get('win', '#141414')};color:{theme.get('accent', '#3daee9')};"
                "font-weight:bold;padding:1px 5px;border-radius:3px;font-size:10.5px;"
            )
            d_lbl = QLabel(desc)
            d_lbl.setStyleSheet(f"color:{theme.get('text', '#bbb')};font-size:11px;")

            r_idx = i // 2
            c_idx = (i % 2) * 2
            sc_grid.addWidget(k_lbl, r_idx, c_idx)
            sc_grid.addWidget(d_lbl, r_idx, c_idx + 1)

        self.shortcuts_body.setVisible(not self._shortcuts_collapsed)
        shortcuts_box.addWidget(self.shortcuts_body)

        bottom_layout.addWidget(self.shortcuts_frame, 1)

        content_layout.addLayout(bottom_layout)

        scroll.setWidget(content)
        root_layout.addWidget(scroll)

        self.refresh_stats()
        self.refresh_recent()

    def _create_card(
        self,
        title: str,
        actions: list[tuple[str, Callable[[], Any]]],
        theme: dict[str, str],
    ) -> QFrame:
        card = QFrame()
        card.setStyleSheet(
            f"background:{theme.get('panel2', '#222')};"
            f"border:1px solid {theme.get('border', '#333')};"
            "border-radius:6px;padding:6px;"
        )
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(5, 5, 5, 5)
        card_layout.setSpacing(3)

        header = QLabel(title)
        header.setFont(QFont("Segoe UI, Inter, Sans-serif", 10, QFont.Weight.Bold))
        header.setStyleSheet(f"color:{theme.get('accent', '#3daee9')};padding-bottom:1px;")
        card_layout.addWidget(header)

        btn_style = (
            f"QPushButton {{"
            f"  text-align:left;padding:4px 7px;background:{theme.get('win', '#181818')};"
            f"  border:1px solid {theme.get('border', '#2e2e2e')};border-radius:4px;"
            f"  color:{theme.get('text', '#ccc')};font-size:11px;"
            f"}}"
            f"QPushButton:hover {{"
            f"  background:{theme.get('panel', '#2a2a2a')};border-color:{theme.get('accent', '#3daee9')};"
            f"  color:#ffffff;"
            f"}}"
            f"QPushButton:pressed {{"
            f"  background:{theme.get('win', '#121212')};"
            f"}}"
        )

        for text, callback in actions:
            btn = QPushButton(text)
            btn.setStyleSheet(btn_style)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(callback)
            card_layout.addWidget(btn)

        return card

    def _toggle_title_pane(self) -> None:
        """Toggle collapsed state of application title pane."""
        self._title_collapsed = not self._title_collapsed
        self.hero_expanded_widget.setVisible(not self._title_collapsed)
        self.hero_collapsed_widget.setVisible(self._title_collapsed)
        self._settings.setValue("welcome/title_collapsed", self._title_collapsed)

    def _toggle_shortcuts_pane(self) -> None:
        """Toggle collapsed state of shortcuts reference pane."""
        self._shortcuts_collapsed = not self._shortcuts_collapsed
        self.shortcuts_body.setVisible(not self._shortcuts_collapsed)
        self.shortcuts_toggle_btn.setText("▼ Expand" if self._shortcuts_collapsed else "▲ Collapse")
        self._settings.setValue("welcome/shortcuts_collapsed", self._shortcuts_collapsed)

    def _toggle_recent_pane(self) -> None:
        """Toggle collapsed state of recent connections pane."""
        self._recent_collapsed = not self._recent_collapsed
        self.recent_body.setVisible(not self._recent_collapsed)
        self.recent_toggle_btn.setText("▼ Expand" if self._recent_collapsed else "▲ Collapse")
        self._settings.setValue("welcome/recent_collapsed", self._recent_collapsed)

    def refresh_recent(self) -> None:
        """Update recent connections list."""
        while self.recent_layout.count():
            item = self.recent_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                # Clear child layouts
                while item.layout().count():
                    child = item.layout().takeAt(0)
                    if child.widget():
                        child.widget().deleteLater()

        recent = getattr(self.main_window, "recent_connections", [])
        theme = self.services.theme.current

        if not recent:
            empty_lbl = QLabel("No recent connections · Select or add a profile in the sidebar")
            empty_lbl.setStyleSheet(f"color:{theme.get('sub', '#777')};font-size:11px;padding:3px;")
            self.recent_layout.addWidget(empty_lbl)
            return

        for name in recent[:4]:
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(6)
            lbl = QLabel(f"● {name}")
            lbl.setStyleSheet(f"color:{theme.get('text', '#eee')};font-size:11.5px;")
            btn = QPushButton("Connect")
            btn.setFixedHeight(22)
            btn.setFixedWidth(64)
            btn.setStyleSheet(
                f"padding:2px 6px;font-size:10.5px;background:{theme.get('win', '#181818')};"
                f"border:1px solid {theme.get('border', '#333')};border-radius:3px;"
                f"color:{theme.get('accent', '#3daee9')};font-weight:bold;"
            )
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _, n=name: self.main_window.connect_profile(n))
            row.addWidget(lbl, 1)
            row.addWidget(btn, 0)
            self.recent_layout.addLayout(row)

    def refresh_stats(self) -> None:
        """Refresh quick status statistics."""
        p_count = len(getattr(self.main_window, "profiles", {}))
        db_count = len(getattr(self.main_window, "db_profiles", {}))
        theme_name = getattr(self.services.theme, "name", getattr(self.services.theme, "current_name", "Breeze Dark"))

        prof_text = f"🐚 SSH: <b>{p_count}</b>"
        db_text = f"🗄️ DBs: <b>{db_count}</b>"
        theme_text = f"🎨 Theme: <b>{theme_name}</b>"

        self.stat_profiles.setText(prof_text)
        self.stat_dbs.setText(db_text)
        self.stat_theme.setText(theme_text)

        self.stat_profiles_sm.setText(prof_text)
        self.stat_dbs_sm.setText(db_text)
        self.stat_theme_sm.setText(theme_text)

    def _open_sftp_quick(self) -> None:
        """Open SFTP browser for selected or chosen profile."""
        name = self.main_window._get_selected_profile_name()
        if not name and self.main_window.profiles:
            names = list(self.main_window.profiles.keys())
            if len(names) == 1:
                name = names[0]
            else:
                chosen, ok = QInputDialog.getItem(
                    self, "SFTP Browser", "Select profile for SFTP session:", names, 0, False
                )
                if ok and chosen:
                    name = chosen
        if name:
            self.main_window.open_sftp(name)
        else:
            self.main_window._notify("Select or add an SSH profile to open SFTP")

    def _open_split_quick(self) -> None:
        """Open Split Terminal for selected or chosen profile."""
        name = self.main_window._get_selected_profile_name()
        if not name and self.main_window.profiles:
            names = list(self.main_window.profiles.keys())
            if len(names) == 1:
                name = names[0]
            else:
                chosen, ok = QInputDialog.getItem(
                    self, "Split Terminal", "Select profile for split terminal:", names, 0, False
                )
                if ok and chosen:
                    name = chosen
        if name and name in self.main_window.profiles:
            data = self.main_window.profiles[name]
            from admin_suite.terminal.split_tab import SplitTerminalTab

            tab = SplitTerminalTab(self.services, name, data)
            for terminal in tab.terminals:
                self.main_window._connect_terminal(terminal)

            original_add_pane = tab.add_pane

            def wrapped_add_pane(orientation):
                original_add_pane(orientation)
                for terminal in tab.terminals:
                    self.main_window._connect_terminal(terminal)

            tab.add_pane = wrapped_add_pane
            index = self.main_window.tabs.addTab(tab, f"⧉ {name}")
            self.main_window.tabs.setCurrentIndex(index)
            self.main_window.add_recent(name)
        else:
            self.main_window._notify("Select or add an SSH profile to open Split Terminal")

    def _open_session_logs(self) -> None:
        from admin_suite.ui.dialogs import SessionLogViewerDialog
        SessionLogViewerDialog(self.main_window, self.services).exec()

    def _open_key_manager(self) -> None:
        from admin_suite.ui.dialogs import KeyManagerDialog
        KeyManagerDialog(self.main_window, self.services).exec()

    def _open_linux_settings(self) -> None:
        from admin_suite.ui.linux_settings_dialog import LinuxSettingsDialog
        LinuxSettingsDialog(self.services, self.main_window).exec()
