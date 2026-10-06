"""
Application Runtime Manager: PHP-FPM Pool tuning and Node.js (PM2) process monitoring.
"""

from __future__ import annotations

import json
import shlex
from typing import Any, Callable, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)


class RuntimesManagerWidget(QWidget):
    """PHP-FPM and Node.js (PM2) Application Runtime manager."""

    status_message = pyqtSignal(str)

    def __init__(self, services, exec_fn: Callable[[str, Callable[[str, int], None]], None], parent=None):
        super().__init__(parent)
        self.services = services
        self.exec_fn = exec_fn

        theme = self.services.theme.current

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        self.tabs = QTabWidget()

        # Tab 1: PHP-FPM
        php_widget = QWidget()
        php_layout = QVBoxLayout(php_widget)

        php_bar = QHBoxLayout()
        php_title = QLabel("🐘 PHP-FPM Pool Management")
        php_title.setStyleSheet(f"font-weight:bold;color:{theme.get('accent', '#3daee9')};")
        php_bar.addWidget(php_title)
        php_bar.addStretch()

        self.php_reload_btn = QPushButton("🔄 Reload PHP-FPM")
        self.php_reload_btn.clicked.connect(self._reload_php)
        php_bar.addWidget(self.php_reload_btn)

        php_refresh_btn = QPushButton("🔄 Refresh Pools")
        php_refresh_btn.clicked.connect(self._refresh_php)
        php_bar.addWidget(php_refresh_btn)

        php_layout.addLayout(php_bar)

        self.php_table = QTableWidget(0, 6)
        self.php_table.setHorizontalHeaderLabels([
            "Pool Config", "User", "Listen Socket", "Process Manager", "Max Children", "Start Servers"
        ])
        self.php_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.php_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.php_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        php_layout.addWidget(self.php_table, 1)

        self.tabs.addTab(php_widget, "🐘 PHP-FPM")

        # Tab 2: Node.js (PM2)
        node_widget = QWidget()
        node_layout = QVBoxLayout(node_widget)

        node_bar = QHBoxLayout()
        node_title = QLabel("🟢 Node.js / PM2 Process Manager")
        node_title.setStyleSheet(f"font-weight:bold;color:{theme.get('accent', '#3daee9')};")
        node_bar.addWidget(node_title)
        node_bar.addStretch()

        self.pm2_start_btn = QPushButton("▶ Start")
        self.pm2_start_btn.clicked.connect(lambda: self._pm2_action("restart"))
        node_bar.addWidget(self.pm2_start_btn)

        self.pm2_stop_btn = QPushButton("⏹ Stop")
        self.pm2_stop_btn.clicked.connect(lambda: self._pm2_action("stop"))
        node_bar.addWidget(self.pm2_stop_btn)

        self.pm2_restart_btn = QPushButton("🔄 Restart")
        self.pm2_restart_btn.clicked.connect(lambda: self._pm2_action("restart"))
        node_bar.addWidget(self.pm2_restart_btn)

        self.pm2_logs_btn = QPushButton("📜 View Logs")
        self.pm2_logs_btn.clicked.connect(self._view_pm2_logs)
        node_bar.addWidget(self.pm2_logs_btn)

        pm2_refresh_btn = QPushButton("🔄 Refresh PM2")
        pm2_refresh_btn.clicked.connect(self._refresh_pm2)
        node_bar.addWidget(pm2_refresh_btn)

        node_layout.addLayout(node_bar)

        self.node_table = QTableWidget(0, 7)
        self.node_table.setHorizontalHeaderLabels([
            "App Name", "ID", "Mode", "Status", "CPU %", "Memory (MB)", "Restarts"
        ])
        self.node_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.node_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.node_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        node_layout.addWidget(self.node_table, 1)

        self.tabs.addTab(node_widget, "🟢 Node.js (PM2)")

        layout.addWidget(self.tabs, 1)

        # Bottom Log Viewer
        self.log_viewer = QPlainTextEdit()
        self.log_viewer.setReadOnly(True)
        self.log_viewer.setMaximumHeight(130)
        self.log_viewer.setFont(QFont("JetBrains Mono, Consolas", 9))
        self.log_viewer.setPlaceholderText("Runtime command and process logs...")
        layout.addWidget(self.log_viewer)

    def refresh(self) -> None:
        self._refresh_php()
        self._refresh_pm2()

    # ------------------------------------------------------------
    # PHP-FPM Methods
    # ------------------------------------------------------------
    def _refresh_php(self) -> None:
        from admin_suite.web.commands import PHP_POOLS_CMD
        self.status_message.emit("Scanning PHP-FPM pools...")

        def on_done(out: str, rc: int):
            self._parse_php_pools(out)
            self.status_message.emit("PHP-FPM pools loaded.")

        self.exec_fn(PHP_POOLS_CMD, on_done)

    def _parse_php_pools(self, out: str) -> None:
        pools: list[dict[str, str]] = []
        curr_pool: dict[str, str] = {}

        for line in out.splitlines():
            line = line.strip()
            if line.startswith("=== POOL:") and line.endswith(" ==="):
                if curr_pool:
                    pools.append(curr_pool)
                curr_pool = {"file": line[9:-4]}
            elif "=" in line and curr_pool:
                k, _, v = line.partition("=")
                curr_pool[k.strip()] = v.strip().strip('"').strip("'")

        if curr_pool:
            pools.append(curr_pool)

        self.php_table.setRowCount(0)
        for row, p in enumerate(pools):
            self.php_table.insertRow(row)
            self.php_table.setItem(row, 0, QTableWidgetItem(p.get("file", "default")))
            self.php_table.setItem(row, 1, QTableWidgetItem(p.get("user", "www-data")))
            self.php_table.setItem(row, 2, QTableWidgetItem(p.get("listen", "socket")))
            self.php_table.setItem(row, 3, QTableWidgetItem(p.get("pm", "dynamic")))
            self.php_table.setItem(row, 4, QTableWidgetItem(p.get("pm.max_children", "5")))
            self.php_table.setItem(row, 5, QTableWidgetItem(p.get("pm.start_servers", "2")))

    def _reload_php(self) -> None:
        # Detect active PHP version and reload
        cmd = "for s in $(systemctl list-units --type=service --state=running --no-legend 2>/dev/null | awk '{print $1}' | grep -E '^php.*fpm'); do systemctl reload $s && echo \"Reloaded $s\"; done"
        self.status_message.emit("Reloading PHP-FPM service...")

        def on_done(out: str, rc: int):
            self.log_viewer.appendPlainText(out or "PHP-FPM reloaded.")
            self.services.notifications.push("ok" if rc == 0 else "error", "PHP-FPM", out.strip() or "Reloaded")

        self.exec_fn(cmd, on_done)

    # ------------------------------------------------------------
    # PM2 Methods
    # ------------------------------------------------------------
    def _refresh_pm2(self) -> None:
        from admin_suite.web.commands import PM2_LIST_CMD
        self.status_message.emit("Scanning PM2 processes...")

        def on_done(out: str, rc: int):
            self._parse_pm2(out)

        self.exec_fn(PM2_LIST_CMD, on_done)

    def _parse_pm2(self, out: str) -> None:
        theme = self.services.theme.current
        self.node_table.setRowCount(0)

        # Parse JSON output from pm2 jlist
        try:
            apps = json.loads(out)
        except Exception:
            apps = []

        if not isinstance(apps, list):
            apps = []

        for row, app in enumerate(apps):
            self.node_table.insertRow(row)

            name = app.get("name", "app")
            pm_id = str(app.get("pm_id", row))
            mode = app.get("exec_mode", "fork")
            status = app.get("pm2_env", {}).get("status", "unknown")
            restarts = str(app.get("pm2_env", {}).get("restart_time", 0))

            monit = app.get("monit", {})
            cpu = f"{monit.get('cpu', 0):.1f}%"
            mem_mb = f"{monit.get('memory', 0) / (1024 * 1024):.1f} MB"

            name_item = QTableWidgetItem(f"🟢 {name}")
            id_item = QTableWidgetItem(pm_id)
            mode_item = QTableWidgetItem(mode)

            status_item = QTableWidgetItem(status)
            if status == "online":
                status_item.setForeground(QColor(theme.get("ok", "#0dbc79")))
            elif status in ("stopped", "errored"):
                status_item.setForeground(QColor(theme.get("danger", "#ff5555")))

            cpu_item = QTableWidgetItem(cpu)
            mem_item = QTableWidgetItem(mem_mb)
            restart_item = QTableWidgetItem(restarts)

            for col, itm in enumerate([name_item, id_item, mode_item, status_item, cpu_item, mem_item, restart_item]):
                itm.setData(Qt.ItemDataRole.UserRole, name)
                self.node_table.setItem(row, col, itm)

    def _get_selected_pm2_app(self) -> Optional[str]:
        selected = self.node_table.selectedItems()
        if not selected:
            QMessageBox.information(self, "Selection", "Select a Node.js / PM2 process first.")
            return None
        return selected[0].data(Qt.ItemDataRole.UserRole)

    def _pm2_action(self, action: str) -> None:
        app_name = self._get_selected_pm2_app()
        if not app_name:
            return

        cmd = f"pm2 {action} {shlex.quote(app_name)}"
        self.status_message.emit(f"Running pm2 {action} {app_name}...")

        def on_done(out: str, rc: int):
            self.log_viewer.appendPlainText(out)
            self._refresh_pm2()

        self.exec_fn(cmd, on_done)

    def _view_pm2_logs(self) -> None:
        app_name = self._get_selected_pm2_app()
        if not app_name:
            return

        cmd = f"pm2 logs {shlex.quote(app_name)} --lines 50 --nostream 2>&1"
        self.status_message.emit(f"Fetching logs for {app_name}...")

        def on_done(out: str, rc: int):
            self.log_viewer.setPlainText(out or f"No logs recorded for {app_name}")

        self.exec_fn(cmd, on_done)
