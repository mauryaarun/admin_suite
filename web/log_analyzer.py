"""
Web Server Log Viewer & Traffic Analyzer.
Parses Nginx and Apache combined log formats, displays HTTP status breakdowns (2xx, 3xx, 4xx, 5xx),
and enables searching through recent client requests.
"""

from __future__ import annotations

import re
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
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

# Combined log format regex: IP - - [date] "METHOD PATH HTTP" STATUS BYTES "REFERRER" "USER-AGENT"
_LOG_REGEX = re.compile(
    r'^(\S+) \S+ \S+ \[([^\]]+)\] "(\S+) ([^"]+) \S+" (\d{3}) (\d+|-) "(.*?)" "(.*?)"'
)


class WebLogAnalyzerWidget(QWidget):
    """Real-time log viewer and status code analyzer for web servers."""

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

        # Toolbar
        toolbar = QHBoxLayout()

        toolbar.addWidget(QLabel("Server Log:"))
        self.log_source = QComboBox()
        self.log_source.addItems([
            "Nginx Access (/var/log/nginx/access.log)",
            "Nginx Error (/var/log/nginx/error.log)",
            "Apache Access (/var/log/apache2/access.log)",
            "Apache Error (/var/log/apache2/error.log)",
        ])
        self.log_source.currentTextChanged.connect(lambda *_: self.refresh())
        toolbar.addWidget(self.log_source)

        toolbar.addWidget(QLabel("Lines:"))
        self.lines_combo = QComboBox()
        self.lines_combo.addItems(["100", "250", "500", "1000"])
        self.lines_combo.setCurrentText("250")
        toolbar.addWidget(self.lines_combo)

        self.filter_in = QLineEdit()
        self.filter_in.setPlaceholderText("🔍 Filter by IP, Path, Status (e.g. 404, /api)...")
        self.filter_in.textChanged.connect(self._apply_filter)
        toolbar.addWidget(self.filter_in, 1)

        self.refresh_btn = QPushButton("🔄 Refresh")
        self.refresh_btn.clicked.connect(self.refresh)
        toolbar.addWidget(self.refresh_btn)

        layout.addLayout(toolbar)

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

        # Log Content Tabs (Structured Table vs Raw Monospace)
        self.tabs = QTabWidget()

        # Tab 1: Structured Requests Table
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Status", "Method", "Request Path", "Client IP", "Timestamp", "User-Agent"])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.tabs.addTab(self.table, "📊 Structured Requests")

        # Tab 2: Raw Log Viewer
        self.raw_view = QPlainTextEdit()
        self.raw_view.setReadOnly(True)
        self.raw_view.setFont(QFont("JetBrains Mono, Consolas", 9))
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

    def refresh(self) -> None:
        """Fetch selected log file contents."""
        source = self.log_source.currentText()
        lines = int(self.lines_combo.currentText())

        if "Nginx Access" in source:
            cmd = f"tail -n {lines} /var/log/nginx/access.log 2>/dev/null || echo 'access.log not accessible'"
        elif "Nginx Error" in source:
            cmd = f"tail -n {lines} /var/log/nginx/error.log 2>/dev/null || echo 'error.log not accessible'"
        elif "Apache Access" in source:
            cmd = f"tail -n {lines} /var/log/apache2/access.log 2>/dev/null || echo 'access.log not accessible'"
        else:
            cmd = f"tail -n {lines} /var/log/apache2/error.log 2>/dev/null || echo 'error.log not accessible'"

        self.status_message.emit("Fetching web server logs...")

        def on_done(out: str, rc: int):
            self.raw_view.setPlainText(out)
            self._parse_log_text(out)
            self.status_message.emit("Logs updated.")

        self.exec_fn(cmd, on_done)

    def _parse_log_text(self, text: str) -> None:
        parsed: list[dict[str, Any]] = []
        c_2xx = c_3xx = c_4xx = c_5xx = 0

        for line in text.splitlines():
            line_s = line.strip()
            if not line_s:
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

    def _set_card_val(self, frame: QFrame, val: str) -> None:
        lbl = frame.findChild(QLabel, "val_lbl")
        if lbl:
            lbl.setText(val)

    def _render_table(self, rows: list[dict[str, Any]]) -> None:
        theme = self.services.theme.current
        self.table.setRowCount(0)

        # Show newest entries first
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

    def _apply_filter(self, text: str) -> None:
        text = text.lower().strip()
        if not text:
            self._render_table(self._parsed_rows)
            return

        filtered = [
            r for r in self._parsed_rows
            if text in r["path"].lower() or text in r["ip"] or text in r["status"] or text in r["method"].lower()
        ]
        self._render_table(filtered)
