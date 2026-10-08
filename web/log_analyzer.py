"""
Web Server Log Viewer & Traffic Analyzer for Admin Suite.
Allows users to define custom access/error log paths (for Debian, RHEL, or custom vhosts),
parses combined access log formats, provides real-time status code breakdowns (2xx, 3xx, 4xx, 5xx),
top client IP rankings, and fast search filtering.
"""

from __future__ import annotations

import os
import re
from collections import Counter
from typing import Any, Callable, Optional

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
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
from admin_suite.web.commands import web_logs_cmd

# Combined log format regex: IP - - [date] "METHOD PATH HTTP" STATUS BYTES "REFERRER" "USER-AGENT"
_LOG_REGEX = re.compile(
    r'^(\S+) \S+ \S+ \[([^\]]+)\] "(\S+) ([^"]+) \S+" (\d{3}) (\d+|-) "(.*?)" "(.*?)"'
)


class WebLogAnalyzerWidget(QWidget):
    """Real-time log viewer and status code analyzer for web servers with custom log path support."""

    status_message = pyqtSignal(str)

    def __init__(self, services, exec_fn: Callable[[str, Callable[[str, int], None]], None], parent=None):
        super().__init__(parent)
        self.services = services
        self.exec_fn = exec_fn
        self._parsed_rows: list[dict[str, Any]] = []

        theme = self.services.theme.current

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # Toolbar Row 1: Source & Custom Path
        toolbar_top = QHBoxLayout()
        toolbar_top.setSpacing(6)

        toolbar_top.addWidget(QLabel("Log Preset:"))
        self.log_preset = QComboBox()
        self.log_preset.addItem("Nginx Access (/var/log/nginx/access.log)", "/var/log/nginx/access.log")
        self.log_preset.addItem("Nginx Error (/var/log/nginx/error.log)", "/var/log/nginx/error.log")
        self.log_preset.addItem("Apache Debian Access (/var/log/apache2/access.log)", "/var/log/apache2/access.log")
        self.log_preset.addItem("Apache Debian Error (/var/log/apache2/error.log)", "/var/log/apache2/error.log")
        self.log_preset.addItem("Apache RHEL Access (/var/log/httpd/access_log)", "/var/log/httpd/access_log")
        self.log_preset.addItem("Apache RHEL Error (/var/log/httpd/error_log)", "/var/log/httpd/error_log")
        self.log_preset.addItem("Caddy Logs (/var/log/caddy/access.log)", "/var/log/caddy/access.log")
        self.log_preset.addItem("Custom User Path...", "custom")

        # Load user-saved custom paths from configuration
        saved_custom = self.services.config.get("custom_access_log_paths", [])
        for p in saved_custom:
            self.log_preset.addItem(f"📁 {p}", p)

        self.log_preset.currentIndexChanged.connect(self._on_preset_changed)
        toolbar_top.addWidget(self.log_preset)

        toolbar_top.addWidget(QLabel("Custom Path:"))
        self.custom_path_in = QLineEdit()
        self.custom_path_in.setPlaceholderText("/var/log/nginx/access.log or /var/www/site/logs/access.log")
        self.custom_path_in.setText(self.services.config.get("custom_access_log_path", "/var/log/nginx/access.log"))
        self.custom_path_in.returnPressed.connect(self.refresh)
        toolbar_top.addWidget(self.custom_path_in, 1)

        self.save_path_btn = QPushButton("💾 Save Path")
        self.save_path_btn.setToolTip("Save this custom log path to your persistent presets")
        self.save_path_btn.clicked.connect(self._save_custom_path)
        toolbar_top.addWidget(self.save_path_btn)

        layout.addLayout(toolbar_top)

        # Toolbar Row 2: Lines, Filter, Status filter, Export, Refresh
        toolbar_sub = QHBoxLayout()
        toolbar_sub.setSpacing(6)

        toolbar_sub.addWidget(QLabel("Lines:"))
        self.lines_combo = QComboBox()
        self.lines_combo.addItems(["100", "250", "500", "1000", "2000"])
        self.lines_combo.setCurrentText("250")
        toolbar_sub.addWidget(self.lines_combo)

        toolbar_sub.addWidget(QLabel("Status Filter:"))
        self.status_code_filter = QComboBox()
        self.status_code_filter.addItems(["All Status Codes", "2xx Success", "3xx Redirects", "4xx Client Errors (404)", "5xx Server Errors (500)"])
        self.status_code_filter.currentIndexChanged.connect(self._apply_filter)
        toolbar_sub.addWidget(self.status_code_filter)

        self.filter_in = QLineEdit()
        self.filter_in.setPlaceholderText("🔍 Filter by IP, URL Path, or User-Agent...")
        self.filter_in.textChanged.connect(self._apply_filter)
        toolbar_sub.addWidget(self.filter_in, 1)

        export_btn = QPushButton("📤 Export Logs")
        export_btn.setToolTip("Export structured request table or raw logs to file")
        export_btn.clicked.connect(self._export_logs)
        toolbar_sub.addWidget(export_btn)

        self.refresh_btn = QPushButton("🔄 Refresh")
        self.refresh_btn.clicked.connect(self.refresh)
        toolbar_sub.addWidget(self.refresh_btn)

        layout.addLayout(toolbar_sub)

        # Metrics Card Bar (2xx, 3xx, 4xx, 5xx counters)
        metrics_bar = QHBoxLayout()

        self.card_total = self._make_card("Total Requests", "0", theme.get("accent", "#3daee9"))
        self.card_2xx = self._make_card("2xx Success", "0", theme.get("ok", "#0dbc79"))
        self.card_3xx = self._make_card("3xx Redirects", "0", theme.get("sub", "#9aa2c0"))
        self.card_4xx = self._make_card("4xx Client Error", "0", theme.get("warn", "#f39c12"))
        self.card_5xx = self._make_card("5xx Server Error", "0", theme.get("danger", "#ff5555"))

        metrics_bar.addWidget(self.card_total)
        metrics_bar.addWidget(self.card_2xx)
        metrics_bar.addWidget(self.card_3xx)
        metrics_bar.addWidget(self.card_4xx)
        metrics_bar.addWidget(self.card_5xx)

        layout.addLayout(metrics_bar)

        # Log Content Tabs
        self.tabs = QTabWidget()

        # Tab 1: Structured Requests Table
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Status", "Method", "Request Path", "Client IP", "Timestamp", "User-Agent"])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Interactive)
        self.tabs.addTab(self.table, "📊 Structured Requests")

        # Tab 2: Top Visitor IPs & Top URLs
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

        # Tab 3: Raw Log Output
        self.raw_view = QPlainTextEdit()
        self.raw_view.setReadOnly(True)
        self.raw_view.setFont(QFont("JetBrains Mono, Consolas", 9))
        ReportExporter.attach_export_context_menu(self.raw_view)
        self.tabs.addTab(self.raw_view, "📜 Raw Log Output")

        layout.addWidget(self.tabs, 1)

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

    def _on_preset_changed(self):
        val = self.log_preset.currentData()
        if val and val != "custom":
            self.custom_path_in.setText(val)
            self.refresh()

    def _save_custom_path(self):
        path = self.custom_path_in.text().strip()
        if not path:
            return
        saved = self.services.config.get("custom_access_log_paths", [])
        if path not in saved:
            saved.append(path)
            self.services.config.set("custom_access_log_paths", saved)
            self.services.config.set("custom_access_log_path", path)
            self.services.config.save()
            self.log_preset.addItem(f"📁 {path}", path)
            self.services.notifications.push("ok", "Log Path Saved", path)

    def refresh(self) -> None:
        """Fetch selected log file contents across Debian, RHEL, or custom path."""
        path = self.custom_path_in.text().strip()
        lines = int(self.lines_combo.currentText())

        if path:
            cmd = web_logs_cmd(lines=lines, custom_path=path)
        else:
            preset = self.log_preset.currentText()
            if "Nginx" in preset:
                cmd = web_logs_cmd(server="nginx", lines=lines)
            elif "Apache" in preset:
                cmd = web_logs_cmd(server="apache2", lines=lines)
            elif "Caddy" in preset:
                cmd = web_logs_cmd(server="caddy", lines=lines)
            else:
                cmd = web_logs_cmd(lines=lines)

        self.status_message.emit(f"Fetching logs from {path or 'preset'}...")

        def on_done(out: str, rc: int):
            self.raw_view.setPlainText(out)
            self._parse_log_text(out)
            self.status_message.emit("Logs updated.")

        self.exec_fn(cmd, on_done)

    def _parse_log_text(self, text: str) -> None:
        parsed: list[dict[str, Any]] = []
        c_2xx = c_3xx = c_4xx = c_5xx = 0
        ip_counter = Counter()
        url_counter = Counter()

        for line in text.splitlines():
            line_s = line.strip()
            if not line_s or line_s.startswith("===") or line_s.startswith("Cannot access"):
                continue

            m = _LOG_REGEX.match(line_s)
            if m:
                ip, ts, method, path, status, bytes_sent, ref, ua = m.groups()
                status_code = int(status)

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

                parsed.append({
                    "status": status,
                    "method": method,
                    "path": path,
                    "ip": ip,
                    "time": ts,
                    "ua": ua[:60],
                })

        # Update counter cards
        self._set_card_val(self.card_total, str(len(parsed)))
        self._set_card_val(self.card_2xx, str(c_2xx))
        self._set_card_val(self.card_3xx, str(c_3xx))
        self._set_card_val(self.card_4xx, str(c_4xx))
        self._set_card_val(self.card_5xx, str(c_5xx))

        self._parsed_rows = parsed
        self._render_table(parsed)
        self._render_rankings(ip_counter, url_counter)

    def _set_card_val(self, frame: QFrame, val: str) -> None:
        lbl = frame.findChild(QLabel, "val_lbl")
        if lbl:
            lbl.setText(val)

    def _render_table(self, rows: list[dict[str, Any]]) -> None:
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

        filtered = []
        for r in self._parsed_rows:
            code = int(r["status"]) if r["status"].isdigit() else 200

            # Status filter
            if status_filter_idx == 1 and not (200 <= code < 300):
                continue
            if status_filter_idx == 2 and not (300 <= code < 400):
                continue
            if status_filter_idx == 3 and not (400 <= code < 500):
                continue
            if status_filter_idx == 4 and not (code >= 500):
                continue

            # Text filter
            if text:
                if (text not in r["path"].lower() and text not in r["ip"] and
                    text not in r["status"] and text not in r["method"].lower() and
                    text not in r["ua"].lower()):
                    continue

            filtered.append(r)

        self._render_table(filtered)

    def _export_logs(self) -> None:
        """Export structured requests table or raw log output."""
        menu = QMenu(self)
        csv_act = menu.addAction("📊 Export Requests Table as CSV")
        json_act = menu.addAction("📄 Export Requests Table as JSON")
        raw_act = menu.addAction("📜 Export Raw Logs to File (.log/.txt)")
        pos = self.sender().mapToGlobal(self.sender().rect().bottomLeft()) if self.sender() else self.mapToGlobal(self.pos())
        action = menu.exec(pos)
        if action == csv_act:
            ReportExporter.export_table_csv(self, self.table, "web_access_requests.csv", "Export Requests Table to CSV")
        elif action == json_act:
            ReportExporter.export_table_json(self, self.table, "web_access_requests.json", "Export Requests Table to JSON")
        elif action == raw_act:
            raw_text = self.raw_view.toPlainText()
            ReportExporter.export_text_file(
                self, raw_text, "web_server_logs.log", "Export Raw Web Server Logs",
                "Log Files (*.log);;Text Files (*.txt);;All Files (*)"
            )
