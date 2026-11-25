"""
System Log Viewer Sub-Module for Security Hub.
Provides unified system log viewing for /var/log/messages and systemd journalctl 1-day logs by default,
with extensive customization: syslog preset files, custom log paths, journalctl service unit filtering,
user name/UID filtering, severity priority filtering, and since duration.
"""

from __future__ import annotations

import os
from typing import Any, Callable, Optional

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from admin_suite.core.export import ReportExporter
from admin_suite.core.fonts import mono_font
from admin_suite.security.commands import SecurityCommands, SecurityParsers


class SystemLogViewerWidget(QWidget):
    """Unified System Log Viewer for Syslog & systemd Journalctl logs."""

    DEFAULT_SYSLOG_PRESETS = [
        ("/var/log/messages", "RHEL/CentOS System Messages (/var/log/messages)"),
        ("/var/log/syslog", "Debian/Ubuntu Syslog (/var/log/syslog)"),
        ("/var/log/auth.log", "Debian/Ubuntu Auth & Sudo (/var/log/auth.log)"),
        ("/var/log/secure", "RHEL/CentOS Auth & Sudo (/var/log/secure)"),
        ("/var/log/kern.log", "Kernel Messages (/var/log/kern.log)"),
        ("/var/log/cron", "RHEL Cron Daemon (/var/log/cron)"),
        ("/var/log/cron.log", "Debian Cron Daemon (/var/log/cron.log)"),
        ("/var/log/fail2ban.log", "Fail2ban Intrusions (/var/log/fail2ban.log)"),
        ("/var/log/audit/audit.log", "Linux Auditd / SELinux (/var/log/audit/audit.log)"),
        ("/var/log/boot.log", "System Boot Logs (/var/log/boot.log)"),
        ("/var/log/daemon.log", "System Background Daemons (/var/log/daemon.log)"),
        ("/var/log/dmesg", "Boot Kernel Ring Buffer (/var/log/dmesg)"),
    ]

    COMMON_SERVICES = [
        "",
        "sshd",
        "systemd-logind",
        "NetworkManager",
        "auditd",
        "fail2ban",
        "firewalld",
        "ufw",
        "apache2",
        "httpd",
        "nginx",
        "cron",
        "crond",
        "docker",
        "containerd",
        "php-fpm",
    ]

    def __init__(
        self,
        run_cmd: Callable[[str, Callable[[str, str, int], None]], None],
        theme: dict[str, str],
        parent=None,
    ):
        super().__init__(parent)
        self.run_cmd = run_cmd
        self.theme = theme

        self._raw_output: str = ""
        self._is_maximized: bool = False
        self._custom_paths: list[str] = []

        # Auto-refresh timer
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)

        self._has_discovered: bool = False
        self._setup_ui()

    def _setup_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(6, 6, 6, 6)
        main_layout.setSpacing(6)

        # ---------------- Top Header Strip ----------------
        top_frame = QFrame()
        top_frame.setStyleSheet(
            f"background: {self.theme.get('panel2', '#222')}; border-radius: 4px; padding: 4px 8px;"
        )
        top_layout = QHBoxLayout(top_frame)
        top_layout.setContentsMargins(4, 2, 4, 2)
        top_layout.setSpacing(8)

        title = QLabel("📋 System Log Viewer")
        title.setStyleSheet(
            f"font-size: 13px; font-weight: bold; color: {self.theme.get('accent', '#3daee9')};"
        )
        top_layout.addWidget(title)

        self.mode_badge = QLabel("Mode: Combined (/var/log/messages + journalctl 1d)")
        self.mode_badge.setStyleSheet(
            "background: #1b3d4d; color: #80d8ff; padding: 2px 6px; border-radius: 3px; font-size: 11px;"
        )
        top_layout.addWidget(self.mode_badge)

        self.journal_badge = QLabel("journalctl: checking...")
        self.journal_badge.setStyleSheet(
            f"background: #2a2a2a; border: 1px solid {self.theme.get('border', '#444')}; "
            "padding: 2px 6px; border-radius: 3px; font-size: 11px;"
        )
        top_layout.addWidget(self.journal_badge)

        top_layout.addStretch()

        # Auto Refresh
        top_layout.addWidget(QLabel("Auto-Refresh:"))
        self.auto_refresh_combo = QComboBox()
        self.auto_refresh_combo.addItems(["Off", "5s", "10s", "30s", "60s"])
        self.auto_refresh_combo.currentIndexChanged.connect(self._on_auto_refresh_changed)
        top_layout.addWidget(self.auto_refresh_combo)

        # Action Buttons
        self.btn_refresh = QPushButton("🔄 Fetch Logs")
        self.btn_refresh.setToolTip("Fetch fresh logs with current filters")
        self.btn_refresh.clicked.connect(self.refresh)
        top_layout.addWidget(self.btn_refresh)

        btn_copy = QPushButton("📋 Copy All")
        btn_copy.setToolTip("Copy displayed log text to clipboard")
        btn_copy.clicked.connect(self._copy_output)
        top_layout.addWidget(btn_copy)

        btn_export = QPushButton("💾 Export...")
        btn_export.setToolTip("Export logs to file")
        btn_export.clicked.connect(self._export_logs)
        top_layout.addWidget(btn_export)

        btn_clear = QPushButton("🧹 Clear")
        btn_clear.clicked.connect(self._clear_view)
        top_layout.addWidget(btn_clear)

        main_layout.addWidget(top_frame)

        # ---------------- Splitter for Controls & Output ----------------
        self.splitter = QSplitter(Qt.Orientation.Vertical)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(8)

        # Top Section: Filter & Configuration Panel
        self.config_panel = QFrame()
        self.config_panel.setMinimumHeight(140)
        self.config_panel.setStyleSheet(
            f"background: {self.theme.get('panel', '#1e1e1e')}; "
            f"border: 1px solid {self.theme.get('border', '#333')}; border-radius: 4px;"
        )
        config_vbox = QVBoxLayout(self.config_panel)
        config_vbox.setContentsMargins(8, 8, 8, 8)
        config_vbox.setSpacing(8)

        # Row 1: Source Mode & Syslog / Custom selection
        row1 = QHBoxLayout()
        row1.setSpacing(6)

        row1.addWidget(QLabel("Log Source:"))
        self.source_combo = QComboBox()
        self.source_combo.addItem("Combined (Default: /var/log/messages + journalctl 1d)", "combined")
        self.source_combo.addItem("Syslog Files (/var/log/...)", "syslog")
        self.source_combo.addItem("systemd journalctl Command", "journalctl")
        self.source_combo.addItem("Custom Log File Path", "custom")
        self.source_combo.currentIndexChanged.connect(self._on_source_changed)
        row1.addWidget(self.source_combo)

        row1.addWidget(QLabel("Syslog File:"))
        self.syslog_combo = QComboBox()
        for path, label in self.DEFAULT_SYSLOG_PRESETS:
            self.syslog_combo.addItem(label, path)
        self.syslog_combo.currentIndexChanged.connect(self._on_syslog_file_changed)
        row1.addWidget(self.syslog_combo, 1)

        row1.addWidget(QLabel("Custom Path:"))
        self.custom_path_edit = QLineEdit()
        self.custom_path_edit.setPlaceholderText("/var/log/messages, /var/log/syslog, or /path/to/app.log")
        self.custom_path_edit.returnPressed.connect(self.refresh)
        row1.addWidget(self.custom_path_edit, 1)

        btn_browse = QPushButton("📂 Browse...")
        btn_browse.setToolTip("Browse local log file")
        btn_browse.clicked.connect(self._browse_custom_path)
        row1.addWidget(btn_browse)

        btn_save = QPushButton("💾 Save Preset")
        btn_save.setToolTip("Save this custom log path to the preset list")
        btn_save.clicked.connect(self._save_custom_preset)
        row1.addWidget(btn_save)

        config_vbox.addLayout(row1)

        # Row 2: journalctl Specific Filters (Since Duration, Service/Unit, User/UID, Priority)
        row2 = QHBoxLayout()
        row2.setSpacing(6)

        row2.addWidget(QLabel("Since Duration:"))
        self.since_combo = QComboBox()
        self.since_combo.addItem("1 day ago (Default)", "1 day ago")
        self.since_combo.addItem("1 hour ago", "1 hour ago")
        self.since_combo.addItem("6 hours ago", "6 hours ago")
        self.since_combo.addItem("12 hours ago", "12 hours ago")
        self.since_combo.addItem("today", "today")
        self.since_combo.addItem("yesterday", "yesterday")
        self.since_combo.addItem("2 days ago", "2 days ago")
        self.since_combo.addItem("7 days ago", "7 days ago")
        self.since_combo.addItem("30 days ago", "30 days ago")
        self.since_combo.addItem("All time", "all")
        self.since_combo.currentIndexChanged.connect(self.refresh)
        row2.addWidget(self.since_combo)

        row2.addWidget(QLabel("Service / Unit (-u):"))
        self.unit_combo = QComboBox()
        self.unit_combo.setEditable(True)
        self.unit_combo.addItems(self.COMMON_SERVICES)
        self.unit_combo.setToolTip("Select or type systemd service name (e.g., sshd, apache2, httpd, cron)")
        self.unit_combo.currentTextChanged.connect(self._on_filter_control_changed)
        row2.addWidget(self.unit_combo, 1)

        row2.addWidget(QLabel("User / UID:"))
        self.user_edit = QLineEdit()
        self.user_edit.setPlaceholderText("Username or UID (e.g. root, 1000)")
        self.user_edit.setToolTip("Filter by username (e.g. root) or numeric UID (_UID)")
        self.user_edit.returnPressed.connect(self.refresh)
        row2.addWidget(self.user_edit, 1)

        row2.addWidget(QLabel("Priority / Severity (-p):"))
        self.priority_combo = QComboBox()
        self.priority_combo.addItem("All Priorities", "all")
        self.priority_combo.addItem("0..3 (Emergency .. Error)", "0..3")
        self.priority_combo.addItem("0..4 (Emergency .. Warning)", "0..4")
        self.priority_combo.addItem("0..5 (Emergency .. Notice)", "0..5")
        self.priority_combo.addItem("err (Errors only)", "err")
        self.priority_combo.addItem("warning (Warnings only)", "warning")
        self.priority_combo.addItem("info (Info and above)", "info")
        self.priority_combo.addItem("debug (Debug and above)", "debug")
        self.priority_combo.currentIndexChanged.connect(self.refresh)
        row2.addWidget(self.priority_combo)

        row2.addWidget(QLabel("Lines:"))
        self.lines_combo = QComboBox()
        self.lines_combo.addItems(["100", "250", "500", "1000", "2500", "5000"])
        self.lines_combo.setCurrentText("500")
        self.lines_combo.currentIndexChanged.connect(self.refresh)
        row2.addWidget(self.lines_combo)

        config_vbox.addLayout(row2)

        # Row 3: Live Search / Grep Filter Bar
        row3 = QHBoxLayout()
        row3.setSpacing(6)

        self.search_in = QLineEdit()
        self.search_in.setPlaceholderText("🔍 Real-time filter / highlight (type any word, IP, PID, or error code)...")
        self.search_in.textChanged.connect(self._apply_text_filter)
        row3.addWidget(self.search_in, 1)

        self.grep_chk = QCheckBox("Grep via Command (Server-side)")
        self.grep_chk.setToolTip("Pass filter directly to grep / journalctl in execution command")
        row3.addWidget(self.grep_chk)

        config_vbox.addLayout(row3)
        self.splitter.addWidget(self.config_panel)

        # ---------------- Bottom Section: Output Viewer ----------------
        self.output_container = QFrame()
        self.output_container.setMinimumHeight(160)
        self.output_container.setStyleSheet(
            f"background: {self.theme.get('panel', '#1e1e1e')}; "
            f"border: 1px solid {self.theme.get('border', '#333')}; border-radius: 4px;"
        )
        out_vbox = QVBoxLayout(self.output_container)
        out_vbox.setContentsMargins(6, 6, 6, 6)
        out_vbox.setSpacing(4)

        # Viewer toolbar with Quick Height Resizing controls
        view_toolbar = QHBoxLayout()
        view_toolbar.setSpacing(6)

        self.status_lbl = QLabel("Ready")
        self.status_lbl.setStyleSheet(f"color: {self.theme.get('sub', '#888')}; font-size: 11px;")
        view_toolbar.addWidget(self.status_lbl)

        view_toolbar.addStretch()

        view_toolbar.addWidget(QLabel("Height:"))
        btn_compact = QPushButton("Compact (150px)")
        btn_compact.setFixedHeight(22)
        btn_compact.clicked.connect(lambda: self.set_output_height(150))
        view_toolbar.addWidget(btn_compact)

        btn_normal = QPushButton("Normal (300px)")
        btn_normal.setFixedHeight(22)
        btn_normal.clicked.connect(lambda: self.set_output_height(300))
        view_toolbar.addWidget(btn_normal)

        btn_large = QPushButton("Large (500px)")
        btn_large.setFixedHeight(22)
        btn_large.clicked.connect(lambda: self.set_output_height(500))
        view_toolbar.addWidget(btn_large)

        self.btn_maximize = QPushButton("⤢ Maximize")
        self.btn_maximize.setFixedHeight(22)
        self.btn_maximize.setToolTip("Toggle maximize log output area")
        self.btn_maximize.clicked.connect(self._toggle_maximize)
        view_toolbar.addWidget(self.btn_maximize)

        out_vbox.addLayout(view_toolbar)

        # Log Text Viewer
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setFont(mono_font(9))
        self.log_view.setMinimumHeight(80)
        self.log_view.setStyleSheet(
            f"background: {self.theme.get('panel2', '#181818')}; "
            f"color: {self.theme.get('fg', '#ccc')}; "
            f"border: 1px solid {self.theme.get('border', '#444')}; font-family: monospace;"
        )
        ReportExporter.attach_export_context_menu(self.log_view)
        out_vbox.addWidget(self.log_view, 1)

        self.splitter.addWidget(self.output_container)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)

        main_layout.addWidget(self.splitter, 1)

        # Initial state setup
        self._update_control_states()

    def set_output_height(self, height_px: int) -> None:
        """Sets the log viewer pane height smoothly via splitter sizes."""
        if self._is_maximized:
            self._toggle_maximize()
        curr_total = sum(self.splitter.sizes()) or 600
        cfg_h = max(130, curr_total - height_px)
        self.splitter.setSizes([cfg_h, height_px])

    def _toggle_maximize(self) -> None:
        """Toggles maximizing log output viewer by collapsing or restoring configuration controls."""
        if not self._is_maximized:
            self._saved_splitter_sizes = self.splitter.sizes()
            self.config_panel.setVisible(False)
            self._is_maximized = True
            self.btn_maximize.setText("↩ Restore")
        else:
            self.config_panel.setVisible(True)
            self._is_maximized = False
            self.btn_maximize.setText("⤢ Maximize")
            if hasattr(self, "_saved_splitter_sizes") and self._saved_splitter_sizes:
                self.splitter.setSizes(self._saved_splitter_sizes)
            else:
                self.splitter.setSizes([180, 420])

    def _on_auto_refresh_changed(self) -> None:
        text = self.auto_refresh_combo.currentText()
        self._timer.stop()
        if text == "5s":
            self._timer.start(5000)
        elif text == "10s":
            self._timer.start(10000)
        elif text == "30s":
            self._timer.start(30000)
        elif text == "60s":
            self._timer.start(60000)

    def _on_source_changed(self) -> None:
        mode = self.source_combo.currentData()
        if mode == "combined":
            self.mode_badge.setText("Mode: Combined (/var/log/messages + journalctl 1d)")
            self.mode_badge.setStyleSheet("background: #1b3d4d; color: #80d8ff; padding: 2px 6px; border-radius: 3px; font-size: 11px;")
        elif mode == "syslog":
            path = self.syslog_combo.currentData() or "/var/log/messages"
            self.mode_badge.setText(f"Mode: Syslog ({path})")
            self.mode_badge.setStyleSheet("background: #311b4d; color: #ea80fc; padding: 2px 6px; border-radius: 3px; font-size: 11px;")
        elif mode == "journalctl":
            self.mode_badge.setText("Mode: systemd journalctl")
            self.mode_badge.setStyleSheet("background: #1b4d24; color: #44eb74; padding: 2px 6px; border-radius: 3px; font-size: 11px;")
        elif mode == "custom":
            cpath = self.custom_path_edit.text().strip() or "Custom"
            self.mode_badge.setText(f"Mode: Custom Path ({cpath})")
            self.mode_badge.setStyleSheet("background: #4a3615; color: #ffd54f; padding: 2px 6px; border-radius: 3px; font-size: 11px;")

        self._update_control_states()
        self.refresh()

    def _update_control_states(self) -> None:
        mode = self.source_combo.currentData()
        is_combined = mode == "combined"
        is_syslog = mode == "syslog"
        is_journal = mode == "journalctl"
        is_custom = mode == "custom"

        self.syslog_combo.setEnabled(is_syslog)
        self.custom_path_edit.setEnabled(is_custom)
        self.since_combo.setEnabled(is_journal or is_combined)
        self.unit_combo.setEnabled(is_journal)
        self.user_edit.setEnabled(is_journal)
        self.priority_combo.setEnabled(is_journal)

    def _on_syslog_file_changed(self) -> None:
        if self.source_combo.currentData() == "syslog":
            path = self.syslog_combo.currentData()
            self.mode_badge.setText(f"Mode: Syslog ({path})")
            self.refresh()

    def _on_filter_control_changed(self) -> None:
        if self.source_combo.currentData() == "journalctl":
            self.refresh()

    def _browse_custom_path(self) -> None:
        dlg = QFileDialog(self, "Select Syslog File", "/var/log")
        dlg.setFileMode(QFileDialog.FileMode.ExistingFile)
        if dlg.exec():
            selected = dlg.selectedFiles()
            if selected:
                path = selected[0]
                self.custom_path_edit.setText(path)
                self.source_combo.setCurrentIndex(3)  # Custom Path
                self.refresh()

    def _save_custom_preset(self) -> None:
        path = self.custom_path_edit.text().strip()
        if not path:
            return
        if path not in self._custom_paths:
            self._custom_paths.append(path)
            self.syslog_combo.addItem(f"📁 {path}", path)
            QMessageBox.information(self, "Preset Saved", f"Saved '{path}' to syslog preset list.")

    def _discover_logs(self) -> None:
        """Discovers existing syslog files and journalctl availability."""
        cmd = SecurityCommands.discover_system_logs()

        def _on_done(out: str, err: str, code: int):
            parsed = SecurityParsers.parse_discovered_system_logs(out)
            if parsed.get("journalctl_available"):
                self.journal_badge.setText("journalctl: available")
                self.journal_badge.setStyleSheet(
                    "background: #1b4d24; color: #44eb74; padding: 2px 6px; border-radius: 3px; font-size: 11px;"
                )
            else:
                self.journal_badge.setText("journalctl: unavailable")
                self.journal_badge.setStyleSheet(
                    "background: #4d1b1b; color: #eb4444; padding: 2px 6px; border-radius: 3px; font-size: 11px;"
                )

            found_files = {item["path"]: item["size"] for item in parsed.get("files", [])}
            # Annotate sizes in syslog combo
            for i in range(self.syslog_combo.count()):
                p = self.syslog_combo.itemData(i)
                if p in found_files:
                    sz = found_files[p]
                    old_text = self.syslog_combo.itemText(i)
                    if "[" not in old_text:
                        self.syslog_combo.setItemText(i, f"{old_text} [{sz}]")

        self.run_cmd(cmd, _on_done)

    def refresh(self) -> None:
        """Fetches system logs based on current configuration."""
        if not self._has_discovered:
            self._has_discovered = True
            self._discover_logs()

        mode = self.source_combo.currentData()
        lines = int(self.lines_combo.currentText() or "500")
        grep_filter = self.search_in.text().strip() if self.grep_chk.isChecked() else ""

        self.status_lbl.setText("Fetching logs...")
        self.btn_refresh.setEnabled(False)

        if mode == "combined":
            cmd = SecurityCommands.system_logs_combined(lines=lines)
        elif mode == "syslog":
            path = self.syslog_combo.currentData() or "/var/log/messages"
            cmd = SecurityCommands.system_logs_syslog(path=path, lines=lines, grep_filter=grep_filter)
        elif mode == "custom":
            path = self.custom_path_edit.text().strip() or "/var/log/messages"
            cmd = SecurityCommands.system_logs_syslog(path=path, lines=lines, grep_filter=grep_filter)
        elif mode == "journalctl":
            since = self.since_combo.currentData() or "1 day ago"
            unit = self.unit_combo.currentText().strip()
            user = self.user_edit.text().strip()
            priority = self.priority_combo.currentData() or "all"
            cmd = SecurityCommands.system_logs_journalctl(
                since=since,
                unit=unit,
                user=user,
                priority=priority,
                lines=lines,
                grep_filter=grep_filter,
            )
        else:
            cmd = SecurityCommands.system_logs_combined(lines=lines)

        def _on_done(out: str, err: str, code: int):
            self.btn_refresh.setEnabled(True)
            output = out or err or "No logs returned."
            self._raw_output = output
            self._apply_text_filter()
            line_count = len(output.splitlines())
            self.status_lbl.setText(f"Showing {line_count} log entries | Last updated: {os.path.basename(mode)}")

        self.run_cmd(cmd, _on_done)

    def _apply_text_filter(self) -> None:
        """Performs instant client-side text filtering."""
        query = self.search_in.text().strip().lower()
        if not query or self.grep_chk.isChecked():
            self.log_view.setPlainText(self._raw_output)
            # Auto scroll to bottom for newest logs
            self.log_view.verticalScrollBar().setValue(
                self.log_view.verticalScrollBar().maximum()
            )
            return

        filtered_lines = [
            line for line in self._raw_output.splitlines()
            if query in line.lower()
        ]
        self.log_view.setPlainText("\n".join(filtered_lines))
        self.status_lbl.setText(f"Filtered: {len(filtered_lines)} matches found")

    def _copy_output(self) -> None:
        text = self.log_view.toPlainText()
        if text:
            QApplication.clipboard().setText(text)
            self.status_lbl.setText("Logs copied to clipboard!")

    def _clear_view(self) -> None:
        self.log_view.clear()
        self._raw_output = ""
        self.status_lbl.setText("Cleared.")

    def _export_logs(self) -> None:
        text = self.log_view.toPlainText()
        if not text:
            QMessageBox.information(self, "Export", "No logs to export.")
            return

        ReportExporter.export_text_file(
            self,
            text,
            default_name="system_logs.log",
            dialog_title="Export System Logs",
            file_filter="Log Files (*.log);;Text Files (*.txt);;All Files (*)",
        )
