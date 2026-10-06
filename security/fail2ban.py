"""
Fail2ban Intrusion Defense Management Interface for Admin Suite.
Inspects active jails, monitors failed/banned statistics, and provides 1-click IP unbanning/banning.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from admin_suite.security.commands import SecurityCommands, SecurityParsers


class ManualBanDialog(QDialog):
    """Dialog to manually ban an IP address in a specific jail."""

    def __init__(self, jails: list[str], default_jail: str = "sshd", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Manually Ban IP Address")
        self.setMinimumWidth(380)

        layout = QVBoxLayout(self)

        grid = QGridLayout()
        grid.setSpacing(10)

        grid.addWidget(QLabel("Jail:"), 0, 0)
        self.jail_combo = QComboBox()
        self.jail_combo.addItems(jails or ["sshd"])
        if default_jail in jails:
            self.jail_combo.setCurrentText(default_jail)
        grid.addWidget(self.jail_combo, 0, 1)

        grid.addWidget(QLabel("IP Address:"), 1, 0)
        self.ip_edit = QLineEdit()
        self.ip_edit.setPlaceholderText("e.g. 198.51.100.24")
        grid.addWidget(self.ip_edit, 1, 1)

        layout.addLayout(grid)

        btn_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btn_box.accepted.connect(self.accept)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

    def get_data(self) -> tuple[str, str]:
        return self.jail_combo.currentText().strip(), self.ip_edit.text().strip()


class Fail2banManagerWidget(QWidget):
    """Interactive GUI for Fail2ban intrusion prevention."""

    def __init__(
        self,
        run_cmd: Callable[[str, Callable[[str, str, int], None]], None],
        theme: dict[str, str],
        parent=None,
    ):
        super().__init__(parent)
        self.run_cmd = run_cmd
        self.theme = theme

        self._jails: list[str] = []
        self._current_jail = ""
        self._current_stats: dict[str, Any] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        # Header Bar
        top_bar = QHBoxLayout()
        top_bar.setSpacing(8)

        self.status_badge = QLabel("Fail2ban: Checking...")
        self.status_badge.setStyleSheet(
            f"font-weight: bold; padding: 4px 10px; border-radius: 4px; border: 1px solid {theme.get('border', '#444')};"
        )
        top_bar.addWidget(self.status_badge)

        top_bar.addWidget(QLabel("Active Jail:"))
        self.jail_combo = QComboBox()
        self.jail_combo.setMinimumWidth(160)
        self.jail_combo.currentTextChanged.connect(self._on_jail_changed)
        top_bar.addWidget(self.jail_combo)

        top_bar.addStretch()

        self.btn_manual_ban = QPushButton("🚫 Manual Ban IP")
        self.btn_manual_ban.clicked.connect(self._on_manual_ban)
        top_bar.addWidget(self.btn_manual_ban)

        self.btn_reload = QPushButton("🔄 Reload Rules")
        self.btn_reload.clicked.connect(self._on_reload)
        top_bar.addWidget(self.btn_reload)

        self.btn_export = QPushButton("📤 Export Banned IPs")
        self.btn_export.setToolTip("Export banned IPs table to CSV or JSON")
        self.btn_export.clicked.connect(self._on_export)
        top_bar.addWidget(self.btn_export)

        self.btn_refresh = QPushButton("🔄 Refresh")
        self.btn_refresh.clicked.connect(self.refresh)
        top_bar.addWidget(self.btn_refresh)

        layout.addLayout(top_bar)

        # Metrics KPI Cards Frame
        kpi_frame = QFrame()
        kpi_frame.setStyleSheet(
            f"background: {theme.get('panel2', '#222')}; border-radius: 4px; padding: 6px;"
        )
        kpi_layout = QHBoxLayout(kpi_frame)
        kpi_layout.setSpacing(12)

        self.card_curr_banned = self._create_kpi_card("Currently Banned", "0", "#eb4444")
        self.card_total_banned = self._create_kpi_card("Total Banned", "0", "#ff9800")
        self.card_curr_failed = self._create_kpi_card("Currently Failed", "0", "#4fc3f7")
        self.card_total_failed = self._create_kpi_card("Total Failed", "0", "#9e9e9e")

        kpi_layout.addWidget(self.card_curr_banned)
        kpi_layout.addWidget(self.card_total_banned)
        kpi_layout.addWidget(self.card_curr_failed)
        kpi_layout.addWidget(self.card_total_failed)
        kpi_layout.addStretch()

        self.log_file_label = QLabel("Log File: -")
        self.log_file_label.setStyleSheet(f"color: {theme.get('text_dim', '#888')}; font-size: 11px;")
        kpi_layout.addWidget(self.log_file_label)

        layout.addWidget(kpi_frame)

        # Banned IP Table
        table_label = QLabel("🔒 Currently Banned IP Addresses:")
        table_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(table_label)

        self.table = QTableWidget()
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(["Banned IP Address", "Active Jail", "Actions"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)

        layout.addWidget(self.table)

    def _create_kpi_card(self, title: str, value: str, val_color: str) -> QWidget:
        widget = QWidget()
        vbox = QVBoxLayout(widget)
        vbox.setContentsMargins(6, 2, 6, 2)
        vbox.setSpacing(2)

        lbl_title = QLabel(title)
        lbl_title.setStyleSheet(f"color: {self.theme.get('text_dim', '#888')}; font-size: 10px; text-transform: uppercase;")
        lbl_val = QLabel(value)
        lbl_val.setStyleSheet(f"font-size: 16px; font-weight: bold; color: {val_color};")
        lbl_val.setObjectName("kpi_val")

        vbox.addWidget(lbl_title)
        vbox.addWidget(lbl_val)
        return widget

    def _set_kpi_val(self, card: QWidget, val: str):
        lbl = card.findChild(QLabel, "kpi_val")
        if lbl:
            lbl.setText(val)

    def refresh(self):
        """Fetch fail2ban global status and jail list."""
        self.run_cmd(SecurityCommands.fail2ban_status(), self._on_status_result)

    def _on_status_result(self, stdout: str, stderr: str, code: int):
        if "not found" in stderr.lower() or "command not found" in stdout.lower() or code != 0 and not stdout:
            self.status_badge.setText("○ Fail2ban: NOT INSTALLED / INACTIVE")
            self.status_badge.setStyleSheet(
                "background: #4d1b1b; color: #eb4444; font-weight: bold; padding: 4px 10px; border-radius: 4px; border: 1px solid #b71c1c;"
            )
            self.jail_combo.clear()
            self._populate_banned_ips([])
            return

        self.status_badge.setText("● Fail2ban: ACTIVE")
        self.status_badge.setStyleSheet(
            "background: #1b4d24; color: #44eb74; font-weight: bold; padding: 4px 10px; border-radius: 4px; border: 1px solid #2e7d32;"
        )

        jails = SecurityParsers.parse_fail2ban_status(stdout)
        self._jails = jails

        current_selection = self.jail_combo.currentText()
        self.jail_combo.blockSignals(True)
        self.jail_combo.clear()
        self.jail_combo.addItems(jails)
        if current_selection in jails:
            self.jail_combo.setCurrentText(current_selection)
        elif jails:
            self.jail_combo.setCurrentIndex(0)
        self.jail_combo.blockSignals(False)

        selected = self.jail_combo.currentText()
        if selected:
            self._load_jail_status(selected)
        else:
            self._populate_banned_ips([])

    def _on_jail_changed(self, jail: str):
        if jail:
            self._load_jail_status(jail)

    def _load_jail_status(self, jail: str):
        self._current_jail = jail
        self.run_cmd(SecurityCommands.fail2ban_jail_status(jail), self._on_jail_status_result)

    def _on_jail_status_result(self, stdout: str, stderr: str, code: int):
        data = SecurityParsers.parse_fail2ban_jail_status(stdout)
        self._current_stats = data

        self._set_kpi_val(self.card_curr_banned, str(data["currently_banned"]))
        self._set_kpi_val(self.card_total_banned, str(data["total_banned"]))
        self._set_kpi_val(self.card_curr_failed, str(data["currently_failed"]))
        self._set_kpi_val(self.card_total_failed, str(data["total_failed"]))
        self.log_file_label.setText(f"Log: {data.get('file_list', '-')}")

        self._populate_banned_ips(data.get("banned_ips", []))

    def _populate_banned_ips(self, ips: list[str]):
        self.table.setRowCount(len(ips))
        for row, ip in enumerate(ips):
            # IP
            item_ip = QTableWidgetItem(ip)
            item_ip.setFont(QFont("Monospace"))
            self.table.setItem(row, 0, item_ip)

            # Jail
            item_jail = QTableWidgetItem(self._current_jail)
            item_jail.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 1, item_jail)

            # Actions Cell
            actions_widget = QWidget()
            actions_layout = QHBoxLayout(actions_widget)
            actions_layout.setContentsMargins(4, 2, 4, 2)
            actions_layout.setSpacing(6)

            btn_unban = QPushButton("🔓 Unban IP")
            btn_unban.setStyleSheet("padding: 2px 8px; font-size: 11px;")
            btn_unban.clicked.connect(lambda _, ip_addr=ip: self._unban_ip(ip_addr))
            actions_layout.addWidget(btn_unban)

            btn_copy = QPushButton("📋 Copy")
            btn_copy.setStyleSheet("padding: 2px 8px; font-size: 11px;")
            btn_copy.clicked.connect(lambda _, ip_addr=ip: QApplication.clipboard().setText(ip_addr))
            actions_layout.addWidget(btn_copy)

            actions_layout.addStretch()
            self.table.setCellWidget(row, 2, actions_widget)

    def _unban_ip(self, ip: str):
        jail = self._current_jail
        confirm = QMessageBox.question(
            self,
            "Confirm Unban IP",
            f"Are you sure you want to unban {ip} from jail '{jail}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm == QMessageBox.StandardButton.Yes:
            cmd = SecurityCommands.fail2ban_unban(jail, ip)
            self.run_cmd(cmd, lambda out, err, code: self._load_jail_status(jail))

    def _on_manual_ban(self):
        dlg = ManualBanDialog(self._jails, default_jail=self._current_jail, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            jail, ip = dlg.get_data()
            if not ip:
                QMessageBox.warning(self, "Invalid IP", "Please specify a valid IP address.")
                return
            cmd = SecurityCommands.fail2ban_ban(jail, ip)
            self.run_cmd(cmd, lambda out, err, code: self._load_jail_status(jail))

    def _on_reload(self):
        self.run_cmd(SecurityCommands.fail2ban_reload(), lambda out, err, code: self.refresh())

    def _on_export(self):
        from admin_suite.core.export import ReportExporter
        from PyQt6.QtWidgets import QMenu
        from PyQt6.QtGui import QCursor
        menu = QMenu(self)
        csv_act = menu.addAction("📊 Export as CSV File")
        json_act = menu.addAction("📄 Export as JSON File")
        action = menu.exec(QCursor.pos())
        if action == csv_act:
            ReportExporter.export_table_csv(self, self.table, "fail2ban_banned_ips.csv", "Export Banned IPs to CSV")
        elif action == json_act:
            ReportExporter.export_table_json(self, self.table, "fail2ban_banned_ips.json", "Export Banned IPs to JSON")

