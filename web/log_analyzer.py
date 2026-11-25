"""
Web Server Log Viewer & Traffic Analyzer for Admin Suite.
Allows users to auto-populate latest Apache/httpd and Nginx logs, define custom access and
error log paths (for Debian, RHEL, or custom vhosts), parses combined access log and
error log formats, provides real-time status code breakdowns (2xx, 3xx, 4xx, 5xx),
top client IP rankings, error event tracking, and fast search filtering.
"""

from __future__ import annotations

import os
import re
from collections import Counter
from typing import Any, Callable, Optional

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from admin_suite.core.export import ReportExporter
from admin_suite.core.fonts import mono_font
from admin_suite.web.commands import discover_web_logs_cmd, web_logs_cmd

# Combined log format regex (supports standard combined and vhost:port combined):
_ACCESS_LOG_REGEX = re.compile(
    r'^(?:(?P<vhost>\S+:\d+|\S+\.\S+) )?(?P<ip>\S+) \S+ \S+ \[(?P<time>[^\]]+)\] "(?P<method>\S+) (?P<path>[^"]+) \S+" (?P<status>\d{3}) (?P<bytes>\d+|-) "(?P<ref>.*?)" "(?P<ua>.*?)"'
)

# Apache 2.4 and standard error log regex:
_APACHE_ERR_REGEX = re.compile(
    r'^\[(?P<time>[^\]]+)\] \[(?:(?P<module>[^:\]]+):)?(?P<level>[a-zA-Z0-9_]+)\] (?:\[pid (?P<pid>\d+)(?::tid (?P<tid>\d+))?\] )?(?:\[client (?P<client>[^\]]+)\] )?(?P<msg>.*)'
)

# Nginx error log regex:
_NGINX_ERR_REGEX = re.compile(
    r'^(?P<time>\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}) \[(?P<level>[a-zA-Z]+)\] (?P<pid>\d+)#\d+: (?P<msg>.*)'
)


class WebLogAnalyzerWidget(QWidget):
    """Real-time log viewer and status code analyzer for web servers with custom log path support."""

    status_message = pyqtSignal(str)

    def __init__(self, services, exec_fn: Callable[[str, Callable[[str, int], None]], None], parent=None):
        super().__init__(parent)
        self.services = services
        self.exec_fn = exec_fn

        self._parsed_access_rows: list[dict[str, Any]] = []
        self._parsed_error_rows: list[dict[str, Any]] = []
        self._raw_output: str = ""
        self._is_maximized: bool = False
        self._discovered_files: list[tuple[str, str]] = []

        theme = self.services.theme.current

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # ---------------- Vertical Splitter ----------------
        self.splitter = QSplitter(Qt.Orientation.Vertical)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(8)

        # Top Control Panel
        self.control_panel = QFrame()
        self.control_panel.setMinimumHeight(170)
        self.control_panel.setStyleSheet(
            f"background: {theme.get('panel', '#1e1e1e')}; "
            f"border: 1px solid {theme.get('border', '#333')}; border-radius: 4px;"
        )
        ctrl_layout = QVBoxLayout(self.control_panel)
        ctrl_layout.setContentsMargins(8, 8, 8, 8)
        ctrl_layout.setSpacing(6)

        # Row 1: Log Target View & Presets (Populates latest Apache/httpd/Nginx logs)
        row1 = QHBoxLayout()
        row1.setSpacing(6)

        row1.addWidget(QLabel("Log View:"))
        self.log_target_combo = QComboBox()
        self.log_target_combo.addItem("Both (Access & Error Logs)", "both")
        self.log_target_combo.addItem("Access Log Only", "access")
        self.log_target_combo.addItem("Error Log Only", "error")
        self.log_target_combo.currentIndexChanged.connect(self._on_target_changed)
        row1.addWidget(self.log_target_combo)

        row1.addWidget(QLabel("Preset Log:"))
        self.log_preset = QComboBox()
        self.log_preset.addItem("⚡ Latest Apache/httpd Logs (Auto)", "apache_auto")
        self.log_preset.addItem("⚡ Latest Nginx Logs (Auto)", "nginx_auto")
        self.log_preset.addItem("Apache Debian Access (/var/log/apache2/access.log)", "/var/log/apache2/access.log")
        self.log_preset.addItem("Apache Debian Error (/var/log/apache2/error.log)", "/var/log/apache2/error.log")
        self.log_preset.addItem("Apache RHEL Access (/var/log/httpd/access_log)", "/var/log/httpd/access_log")
        self.log_preset.addItem("Apache RHEL Error (/var/log/httpd/error_log)", "/var/log/httpd/error_log")
        self.log_preset.addItem("Nginx Access (/var/log/nginx/access.log)", "/var/log/nginx/access.log")
        self.log_preset.addItem("Nginx Error (/var/log/nginx/error.log)", "/var/log/nginx/error.log")
        self.log_preset.addItem("Caddy Logs (/var/log/caddy/access.log)", "/var/log/caddy/access.log")
        self.log_preset.addItem("Custom User Paths...", "custom")

        # Load saved custom paths
        saved_acc = self.services.config.get("custom_access_log_paths", [])
        for p in saved_acc:
            self.log_preset.addItem(f"📁 [Access] {p}", p)
        saved_err = self.services.config.get("custom_error_log_paths", [])
        for p in saved_err:
            self.log_preset.addItem(f"📁 [Error] {p}", p)

        self.log_preset.currentIndexChanged.connect(self._on_preset_changed)
        row1.addWidget(self.log_preset, 1)

        btn_discover = QPushButton("🔍 Discover Logs")
        btn_discover.setToolTip("Scan host for active Apache, httpd, and Nginx log files")
        btn_discover.clicked.connect(self._discover_apache_logs)
        row1.addWidget(btn_discover)

        ctrl_layout.addLayout(row1)

        # Row 2: Custom Access & Custom Error Log Paths (with Browse & Save)
        row2 = QHBoxLayout()
        row2.setSpacing(6)

        row2.addWidget(QLabel("Custom Access Log:"))
        self.custom_access_in = QLineEdit()
        self.custom_access_in.setPlaceholderText("/var/log/apache2/access.log or /var/www/site/logs/access.log")
        saved_acc_path = self.services.config.get("custom_access_log_path", "")
        self.custom_access_in.setText(saved_acc_path)
        self.custom_access_in.returnPressed.connect(self.refresh)
        row2.addWidget(self.custom_access_in, 1)

        btn_browse_acc = QPushButton("📂 Browse...")
        btn_browse_acc.setToolTip("Browse for access log file")
        btn_browse_acc.clicked.connect(self._browse_access_path)
        row2.addWidget(btn_browse_acc)

        btn_save_acc = QPushButton("💾 Save")
        btn_save_acc.setToolTip("Save this custom access log path to presets")
        btn_save_acc.clicked.connect(self._save_custom_access_path)
        row2.addWidget(btn_save_acc)

        ctrl_layout.addLayout(row2)

        row2_err = QHBoxLayout()
        row2_err.setSpacing(6)

        row2_err.addWidget(QLabel("Custom Error Log:"))
        self.custom_error_in = QLineEdit()
        self.custom_error_in.setPlaceholderText("/var/log/apache2/error.log or /var/www/site/logs/error.log")
        saved_err_path = self.services.config.get("custom_error_log_path", "")
        self.custom_error_in.setText(saved_err_path)
        self.custom_error_in.returnPressed.connect(self.refresh)
        row2_err.addWidget(self.custom_error_in, 1)

        btn_browse_err = QPushButton("📂 Browse...")
        btn_browse_err.setToolTip("Browse for error log file")
        btn_browse_err.clicked.connect(self._browse_error_path)
        row2_err.addWidget(btn_browse_err)

        btn_save_err = QPushButton("💾 Save")
        btn_save_err.setToolTip("Save this custom error log path to presets")
        btn_save_err.clicked.connect(self._save_custom_error_path)
        row2_err.addWidget(btn_save_err)

        ctrl_layout.addLayout(row2_err)

        # Row 3: Lines, Status Filter, Search Filter, Actions
        row3 = QHBoxLayout()
        row3.setSpacing(6)

        row3.addWidget(QLabel("Lines:"))
        self.lines_combo = QComboBox()
        self.lines_combo.addItems(["100", "250", "500", "1000", "2000", "5000"])
        self.lines_combo.setCurrentText("250")
        self.lines_combo.currentIndexChanged.connect(self.refresh)
        row3.addWidget(self.lines_combo)

        row3.addWidget(QLabel("Filter:"))
        self.status_code_filter = QComboBox()
        self.status_code_filter.addItems([
            "All Events / Codes",
            "2xx Success",
            "3xx Redirects",
            "4xx Client Errors (404)",
            "5xx Server Errors (500)",
            "⚠️ Error Log Entries Only",
        ])
        self.status_code_filter.currentIndexChanged.connect(self._apply_filter)
        row3.addWidget(self.status_code_filter)

        self.filter_in = QLineEdit()
        self.filter_in.setPlaceholderText("🔍 Filter by IP, URL Path, Error Message, or User-Agent...")
        self.filter_in.textChanged.connect(self._apply_filter)
        row3.addWidget(self.filter_in, 1)

        export_btn = QPushButton("📤 Export Logs")
        export_btn.setToolTip("Export structured request table, error events, or raw logs")
        export_btn.clicked.connect(self._export_logs)
        row3.addWidget(export_btn)

        self.refresh_btn = QPushButton("🔄 Refresh")
        self.refresh_btn.clicked.connect(self.refresh)
        row3.addWidget(self.refresh_btn)

        ctrl_layout.addLayout(row3)
        self.splitter.addWidget(self.control_panel)

        # ---------------- Bottom Section: Metrics & Tabs ----------------
        bottom_container = QWidget()
        bottom_layout = QVBoxLayout(bottom_container)
        bottom_layout.setContentsMargins(0, 0, 0, 0)
        bottom_layout.setSpacing(4)

        # Metrics Card Bar (Total, 2xx, 3xx, 4xx, 5xx, Errors)
        metrics_bar = QHBoxLayout()
        metrics_bar.setSpacing(6)

        self.card_total = self._make_card("Total Requests", "0", theme.get("accent", "#3daee9"))
        self.card_2xx = self._make_card("2xx Success", "0", theme.get("ok", "#0dbc79"))
        self.card_3xx = self._make_card("3xx Redirects", "0", theme.get("sub", "#9aa2c0"))
        self.card_4xx = self._make_card("4xx Client Error", "0", theme.get("warn", "#f39c12"))
        self.card_5xx = self._make_card("5xx Server Error", "0", theme.get("danger", "#ff5555"))
        self.card_err = self._make_card("⚠️ Errors Logged", "0", "#ff8a80")

        metrics_bar.addWidget(self.card_total)
        metrics_bar.addWidget(self.card_2xx)
        metrics_bar.addWidget(self.card_3xx)
        metrics_bar.addWidget(self.card_4xx)
        metrics_bar.addWidget(self.card_5xx)
        metrics_bar.addWidget(self.card_err)

        bottom_layout.addLayout(metrics_bar)

        # Log Content Tabs
        self.tabs = QTabWidget()

        # Tab 1: Structured Requests Table (Access Log)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Status", "Method", "Request Path", "Client IP", "Timestamp", "User-Agent"])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Interactive)
        self.tabs.addTab(self.table, "📊 Access Requests")

        # Tab 2: Structured Error Events (Apache / httpd / Nginx Error Log)
        self.error_table = QTableWidget(0, 5)
        self.error_table.setHorizontalHeaderLabels(["Level", "Client IP", "Timestamp", "Module / PID", "Message"])
        self.error_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.error_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.error_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        self.error_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        self.error_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        self.error_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.tabs.addTab(self.error_table, "⚠️ Error Events")

        # Tab 3: Top Visitor IPs & Top URLs
        stats_widget = QWidget()
        stats_layout = QHBoxLayout(stats_widget)
        stats_layout.setContentsMargins(4, 4, 4, 4)

        # Left: Top IPs
        ip_box = QWidget()
        ip_vbox = QVBoxLayout(ip_box)
        ip_vbox.setContentsMargins(0, 0, 0, 0)
        ip_vbox.addWidget(QLabel("🔝 Top Client IPs:"))
        self.top_ip_table = QTableWidget(0, 2)
        self.top_ip_table.setHorizontalHeaderLabels(["Requests", "Client IP"])
        self.top_ip_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.top_ip_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        ip_vbox.addWidget(self.top_ip_table)
        stats_layout.addWidget(ip_box, 1)

        # Right: Top URLs
        url_box = QWidget()
        url_vbox = QVBoxLayout(url_box)
        url_vbox.setContentsMargins(0, 0, 0, 0)
        url_vbox.addWidget(QLabel("🔗 Top Requested Paths:"))
        self.top_url_table = QTableWidget(0, 2)
        self.top_url_table.setHorizontalHeaderLabels(["Hits", "URL Path"])
        self.top_url_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.top_url_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        url_vbox.addWidget(self.top_url_table)
        stats_layout.addWidget(url_box, 2)

        self.tabs.addTab(stats_widget, "🔝 Top Clients & Paths")

        # Tab 4: Raw Log Output with quick height resizing toolbar
        raw_box = QWidget()
        raw_layout = QVBoxLayout(raw_box)
        raw_layout.setContentsMargins(2, 2, 2, 2)
        raw_layout.setSpacing(2)

        raw_toolbar = QHBoxLayout()
        raw_toolbar.addWidget(QLabel("Output Height:"))

        btn_compact = QPushButton("Compact (150px)")
        btn_compact.setFixedHeight(22)
        btn_compact.clicked.connect(lambda: self.set_output_height(150))
        raw_toolbar.addWidget(btn_compact)

        btn_normal = QPushButton("Normal (300px)")
        btn_normal.setFixedHeight(22)
        btn_normal.clicked.connect(lambda: self.set_output_height(300))
        raw_toolbar.addWidget(btn_normal)

        btn_large = QPushButton("Large (500px)")
        btn_large.setFixedHeight(22)
        btn_large.clicked.connect(lambda: self.set_output_height(500))
        raw_toolbar.addWidget(btn_large)

        self.btn_maximize = QPushButton("⤢ Maximize")
        self.btn_maximize.setFixedHeight(22)
        self.btn_maximize.clicked.connect(self._toggle_maximize)
        raw_toolbar.addWidget(self.btn_maximize)

        raw_toolbar.addStretch()

        btn_copy = QPushButton("📋 Copy")
        btn_copy.setFixedHeight(22)
        btn_copy.clicked.connect(self._copy_raw)
        raw_toolbar.addWidget(btn_copy)

        btn_clear = QPushButton("🧹 Clear")
        btn_clear.setFixedHeight(22)
        btn_clear.clicked.connect(self._clear_raw)
        raw_toolbar.addWidget(btn_clear)

        raw_layout.addLayout(raw_toolbar)

        self.raw_view = QPlainTextEdit()
        self.raw_view.setReadOnly(True)
        self.raw_view.setFont(mono_font(9))
        self.raw_view.setMinimumHeight(100)
        ReportExporter.attach_export_context_menu(self.raw_view)
        raw_layout.addWidget(self.raw_view, 1)

        self.tabs.addTab(raw_box, "📜 Raw Log Output")

        bottom_layout.addWidget(self.tabs, 1)
        self.splitter.addWidget(bottom_container)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)

        layout.addWidget(self.splitter, 1)

        self._has_discovered: bool = False

    def set_output_height(self, height_px: int) -> None:
        """Sets the log viewer pane height smoothly via splitter sizes."""
        if self._is_maximized:
            self._toggle_maximize()
        curr_total = sum(self.splitter.sizes()) or 600
        ctrl_h = max(140, curr_total - height_px)
        self.splitter.setSizes([ctrl_h, height_px])

    def _toggle_maximize(self) -> None:
        """Toggles maximizing log viewer area by hiding or showing the upper control panel."""
        if not self._is_maximized:
            self._saved_splitter_sizes = self.splitter.sizes()
            self.control_panel.setVisible(False)
            self._is_maximized = True
            self.btn_maximize.setText("↩ Restore")
        else:
            self.control_panel.setVisible(True)
            self._is_maximized = False
            self.btn_maximize.setText("⤢ Maximize")
            if hasattr(self, "_saved_splitter_sizes") and self._saved_splitter_sizes:
                self.splitter.setSizes(self._saved_splitter_sizes)
            else:
                self.splitter.setSizes([180, 420])

    def _make_card(self, title: str, val: str, color: str) -> QFrame:
        frame = QFrame()
        frame.setStyleSheet(
            f"border: 1px solid {self.services.theme.current.get('border', '#444')};"
            f"background: {self.services.theme.current.get('panel2', '#222')};"
            "border-radius: 4px; padding: 4px;"
        )
        l = QVBoxLayout(frame)
        l.setContentsMargins(6, 4, 6, 4)
        l.setSpacing(2)

        t_lbl = QLabel(title)
        t_lbl.setStyleSheet("font-size: 10px; color: #aaa;")
        v_lbl = QLabel(val)
        v_lbl.setObjectName("val_lbl")
        v_lbl.setStyleSheet(f"font-size: 16px; font-weight: bold; color: {color};")

        l.addWidget(t_lbl)
        l.addWidget(v_lbl)
        return frame

    def _on_target_changed(self) -> None:
        self.refresh()

    def _on_preset_changed(self) -> None:
        val = self.log_preset.currentData()
        if not val:
            return
        if val == "custom":
            return
        elif val == "apache_auto":
            # Auto populate latest apache logs
            self.custom_access_in.clear()
            self.custom_error_in.clear()
            self.refresh()
        elif val == "nginx_auto":
            self.custom_access_in.clear()
            self.custom_error_in.clear()
            self.refresh()
        elif "error" in val.lower():
            self.custom_error_in.setText(val)
            self.log_target_combo.setCurrentIndex(2)  # Error Log Only
            self.refresh()
        else:
            self.custom_access_in.setText(val)
            self.log_target_combo.setCurrentIndex(1)  # Access Log Only
            self.refresh()

    def _browse_access_path(self) -> None:
        dlg = QFileDialog(self, "Select Web Access Log", "/var/log")
        dlg.setFileMode(QFileDialog.FileMode.ExistingFile)
        if dlg.exec():
            selected = dlg.selectedFiles()
            if selected:
                path = selected[0]
                self.custom_access_in.setText(path)
                self.refresh()

    def _browse_error_path(self) -> None:
        dlg = QFileDialog(self, "Select Web Error Log", "/var/log")
        dlg.setFileMode(QFileDialog.FileMode.ExistingFile)
        if dlg.exec():
            selected = dlg.selectedFiles()
            if selected:
                path = selected[0]
                self.custom_error_in.setText(path)
                self.refresh()

    def _save_custom_access_path(self) -> None:
        path = self.custom_access_in.text().strip()
        if not path:
            return
        saved = self.services.config.get("custom_access_log_paths", [])
        if path not in saved:
            saved.append(path)
            self.services.config.set("custom_access_log_paths", saved)
            self.services.config.set("custom_access_log_path", path)
            self.services.config.save()
            self.log_preset.addItem(f"📁 [Access] {path}", path)
            self.services.notifications.push("ok", "Access Log Path Saved", path)

    def _save_custom_error_path(self) -> None:
        path = self.custom_error_in.text().strip()
        if not path:
            return
        saved = self.services.config.get("custom_error_log_paths", [])
        if path not in saved:
            saved.append(path)
            self.services.config.set("custom_error_log_paths", saved)
            self.services.config.set("custom_error_log_path", path)
            self.services.config.save()
            self.log_preset.addItem(f"📁 [Error] {path}", path)
            self.services.notifications.push("ok", "Error Log Path Saved", path)

    def _discover_apache_logs(self) -> None:
        """Discovers existing and recent Apache/httpd and Nginx log files."""
        cmd = discover_web_logs_cmd()

        def _on_done(out: str, rc: int):
            files: list[tuple[str, str]] = []
            for line in out.splitlines():
                line_s = line.strip()
                if not line_s or line_s.startswith("===") or line_s == "none":
                    continue
                parts = line_s.split("|")
                if len(parts) >= 2:
                    files.append((parts[0], parts[1]))
                elif len(parts) == 1 and parts[0].startswith("/"):
                    files.append((parts[0], ""))

            self._discovered_files = files
            # Populate preset dropdown with newly discovered files
            for p, sz in files:
                sz_str = f" [{sz}]" if sz else ""
                label = f"⚡ Discovered: {p}{sz_str}"
                # Check if already present in preset combo
                exists = False
                for i in range(self.log_preset.count()):
                    if self.log_preset.itemData(i) == p:
                        exists = True
                        break
                if not exists:
                    self.log_preset.addItem(label, p)

            self.status_message.emit(f"Discovered {len(files)} web log files on host.")

        self.exec_fn(cmd, _on_done)

    def refresh(self) -> None:
        """Fetch web server access and error logs based on user configuration."""
        if not self._has_discovered:
            self._has_discovered = True
            self._discover_apache_logs()

        lines = int(self.lines_combo.currentText() or "250")
        target_mode = self.log_target_combo.currentData() or "both"
        custom_acc = self.custom_access_in.text().strip()
        custom_err = self.custom_error_in.text().strip()
        preset = self.log_preset.currentData() or ""

        # Determine server preset type if custom paths are not both supplied
        server = "apache2"
        if preset == "nginx_auto" or "nginx" in str(preset).lower():
            server = "nginx"
        elif preset == "apache_auto" or "apache" in str(preset).lower() or "httpd" in str(preset).lower():
            server = "apache2"
        elif preset == "/var/log/caddy/access.log":
            server = "caddy"

        cmd = web_logs_cmd(
            server=server,
            lines=lines,
            custom_access_path=custom_acc,
            custom_error_path=custom_err,
            log_type=target_mode,
        )

        self.status_message.emit("Fetching latest web logs...")

        def _on_done(out: str, rc: int):
            self._raw_output = out
            self.raw_view.setPlainText(out)
            self._parse_log_text(out)
            self.status_message.emit("Web logs updated.")

        self.exec_fn(cmd, _on_done)

    def _parse_log_text(self, text: str) -> None:
        """Parses combined text stream containing access and error logs."""
        parsed_acc: list[dict[str, Any]] = []
        parsed_err: list[dict[str, Any]] = []

        c_2xx = c_3xx = c_4xx = c_5xx = 0
        ip_counter = Counter()
        url_counter = Counter()

        for line in text.splitlines():
            line_s = line.strip()
            if not line_s or line_s.startswith("===") or line_s.startswith("Cannot access"):
                continue

            # 1. Try parsing as access log
            m_acc = _ACCESS_LOG_REGEX.match(line_s)
            if m_acc:
                gd = m_acc.groupdict()
                ip = gd.get("ip") or ""
                ts = gd.get("time") or ""
                method = gd.get("method") or ""
                path = gd.get("path") or ""
                status = gd.get("status") or "200"
                ua = gd.get("ua") or ""
                status_code = int(status) if status.isdigit() else 200

                if 200 <= status_code < 300:
                    c_2xx += 1
                elif 300 <= status_code < 400:
                    c_3xx += 1
                elif 400 <= status_code < 500:
                    c_4xx += 1
                elif status_code >= 500:
                    c_5xx += 1

                ip_counter[ip] += 1
                url_counter[path] += 1

                parsed_acc.append({
                    "status": status,
                    "method": method,
                    "path": path,
                    "ip": ip,
                    "time": ts,
                    "ua": ua[:60],
                })
                continue

            # 2. Try parsing as Apache Error log
            m_ap_err = _APACHE_ERR_REGEX.match(line_s)
            if m_ap_err:
                gd = m_ap_err.groupdict()
                parsed_err.append({
                    "level": gd.get("level") or "error",
                    "client": gd.get("client") or "",
                    "time": gd.get("time") or "",
                    "module": f"{gd.get('module') or ''} pid:{gd.get('pid') or ''}".strip(),
                    "msg": gd.get("msg") or "",
                })
                continue

            # 3. Try parsing as Nginx Error log
            m_ng_err = _NGINX_ERR_REGEX.match(line_s)
            if m_ng_err:
                gd = m_ng_err.groupdict()
                parsed_err.append({
                    "level": gd.get("level") or "error",
                    "client": "",
                    "time": gd.get("time") or "",
                    "module": f"pid:{gd.get('pid') or ''}",
                    "msg": gd.get("msg") or "",
                })
                continue

            # Fallback if line contains error keywords
            if "[error]" in line_s or "[warn]" in line_s or "failed" in line_s.lower():
                parsed_err.append({
                    "level": "warn" if "[warn]" in line_s else "error",
                    "client": "",
                    "time": "",
                    "module": "",
                    "msg": line_s,
                })

        # Update counter cards
        self._set_card_val(self.card_total, str(len(parsed_acc)))
        self._set_card_val(self.card_2xx, str(c_2xx))
        self._set_card_val(self.card_3xx, str(c_3xx))
        self._set_card_val(self.card_4xx, str(c_4xx))
        self._set_card_val(self.card_5xx, str(c_5xx))
        self._set_card_val(self.card_err, str(len(parsed_err)))

        self._parsed_access_rows = parsed_acc
        self._parsed_error_rows = parsed_err

        self._render_access_table(parsed_acc)
        self._render_error_table(parsed_err)
        self._render_rankings(ip_counter, url_counter)

    def _set_card_val(self, frame: QFrame, val: str) -> None:
        lbl = frame.findChild(QLabel, "val_lbl")
        if lbl:
            lbl.setText(val)

    def _render_access_table(self, rows: list[dict[str, Any]]) -> None:
        theme = self.services.theme.current
        self.table.setRowCount(0)

        for row_idx, r in enumerate(reversed(rows)):
            self.table.insertRow(row_idx)

            status_str = r["status"]
            status_item = QTableWidgetItem(status_str)
            code = int(status_str) if status_str.isdigit() else 200

            if 200 <= code < 300:
                status_item.setForeground(QColor(theme.get("ok", "#0dbc79")))
            elif 300 <= code < 400:
                status_item.setForeground(QColor(theme.get("accent", "#3daee9")))
            elif 400 <= code < 500:
                status_item.setForeground(QColor(theme.get("warn", "#f39c12")))
            else:
                status_item.setForeground(QColor(theme.get("danger", "#ff5555")))

            method_item = QTableWidgetItem(r["method"])
            path_item = QTableWidgetItem(r["path"])
            ip_item = QTableWidgetItem(r["ip"])
            time_item = QTableWidgetItem(r["time"])
            ua_item = QTableWidgetItem(r["ua"])

            for col, itm in enumerate([status_item, method_item, path_item, ip_item, time_item, ua_item]):
                self.table.setItem(row_idx, col, itm)

    def _render_error_table(self, rows: list[dict[str, Any]]) -> None:
        theme = self.services.theme.current
        self.error_table.setRowCount(0)

        for row_idx, r in enumerate(reversed(rows)):
            self.error_table.insertRow(row_idx)

            lvl = r["level"].lower()
            lvl_item = QTableWidgetItem(r["level"])
            if lvl in ("notice", "info"):
                lvl_item.setForeground(QColor(theme.get("accent", "#3daee9")))
            elif lvl in ("warn", "warning"):
                lvl_item.setForeground(QColor(theme.get("warn", "#f39c12")))
            else:
                lvl_item.setForeground(QColor(theme.get("danger", "#ff5555")))

            client_item = QTableWidgetItem(r["client"])
            time_item = QTableWidgetItem(r["time"])
            mod_item = QTableWidgetItem(r["module"])
            msg_item = QTableWidgetItem(r["msg"])

            for col, itm in enumerate([lvl_item, client_item, time_item, mod_item, msg_item]):
                self.error_table.setItem(row_idx, col, itm)

    def _render_rankings(self, ip_counter: Counter, url_counter: Counter) -> None:
        # Top IPs
        top_ips = ip_counter.most_common(25)
        self.top_ip_table.setRowCount(len(top_ips))
        for row, (ip, count) in enumerate(top_ips):
            c_item = QTableWidgetItem(str(count))
            c_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.top_ip_table.setItem(row, 0, c_item)
            self.top_ip_table.setItem(row, 1, QTableWidgetItem(ip))

        # Top URLs
        top_urls = url_counter.most_common(25)
        self.top_url_table.setRowCount(len(top_urls))
        for row, (url, count) in enumerate(top_urls):
            c_item = QTableWidgetItem(str(count))
            c_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.top_url_table.setItem(row, 0, c_item)
            self.top_url_table.setItem(row, 1, QTableWidgetItem(url))

    def _apply_filter(self) -> None:
        text = self.filter_in.text().lower().strip()
        status_filter_idx = self.status_code_filter.currentIndex()

        # Filter access rows
        filtered_acc = []
        for r in self._parsed_access_rows:
            code = int(r["status"]) if r["status"].isdigit() else 200

            if status_filter_idx == 1 and not (200 <= code < 300):
                continue
            if status_filter_idx == 2 and not (300 <= code < 400):
                continue
            if status_filter_idx == 3 and not (400 <= code < 500):
                continue
            if status_filter_idx == 4 and not (code >= 500):
                continue
            if status_filter_idx == 5:
                # Error log entries only
                continue

            if text:
                if (text not in r["path"].lower() and text not in r["ip"] and
                    text not in r["status"] and text not in r["method"].lower() and
                    text not in r["ua"].lower()):
                    continue

            filtered_acc.append(r)

        self._render_access_table(filtered_acc)

        # Filter error rows
        filtered_err = []
        for r in self._parsed_error_rows:
            if status_filter_idx in (1, 2, 3, 4):
                # Specific HTTP status codes don't apply to pure error events
                continue
            if text:
                if (text not in r["msg"].lower() and text not in r["client"] and
                    text not in r["level"].lower() and text not in r["module"].lower()):
                    continue
            filtered_err.append(r)

        self._render_error_table(filtered_err)

    def _copy_raw(self) -> None:
        text = self.raw_view.toPlainText()
        if text:
            QApplication.clipboard().setText(text)
            self.status_message.emit("Raw logs copied to clipboard.")

    def _clear_raw(self) -> None:
        self.raw_view.clear()
        self._raw_output = ""
        self.status_message.emit("Log viewer cleared.")

    def _export_logs(self) -> None:
        """Export structured requests table, error table, or raw log output."""
        menu = QMenu(self)
        acc_csv_act = menu.addAction("📊 Export Access Requests Table as CSV")
        acc_json_act = menu.addAction("📄 Export Access Requests Table as JSON")
        err_csv_act = menu.addAction("⚠️ Export Error Events Table as CSV")
        err_json_act = menu.addAction("📄 Export Error Events Table as JSON")
        raw_act = menu.addAction("📜 Export Raw Logs to File (.log/.txt)")

        pos = self.sender().mapToGlobal(self.sender().rect().bottomLeft()) if self.sender() else self.mapToGlobal(self.pos())
        action = menu.exec(pos)

        if action == acc_csv_act:
            ReportExporter.export_table_csv(self, self.table, "web_access_requests.csv", "Export Access Requests to CSV")
        elif action == acc_json_act:
            ReportExporter.export_table_json(self, self.table, "web_access_requests.json", "Export Access Requests to JSON")
        elif action == err_csv_act:
            ReportExporter.export_table_csv(self, self.error_table, "web_error_events.csv", "Export Error Events to CSV")
        elif action == err_json_act:
            ReportExporter.export_table_json(self, self.error_table, "web_error_events.json", "Export Error Events to JSON")
        elif action == raw_act:
            raw_text = self.raw_view.toPlainText()
            ReportExporter.export_text_file(
                self, raw_text, "web_server_logs.log", "Export Raw Web Server Logs",
                "Log Files (*.log);;Text Files (*.txt);;All Files (*)"
            )
