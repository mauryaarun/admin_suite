"""
Firewall Management Interface for Admin Suite.
Supports UFW with rule table, quick service presets, IP restrictions, and live actions.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
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
    QRadioButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from admin_suite.security.commands import SecurityCommands, SecurityParsers


class AddFirewallRuleDialog(QDialog):
    """Wizard for adding a new firewall rule with presets."""

    SERVICE_PRESETS = [
        ("Custom...", ""),
        ("SSH (22)", "22"),
        ("HTTP (80)", "80"),
        ("HTTPS (443)", "443"),
        ("HTTP & HTTPS (80, 443)", "80,443"),
        ("MySQL / MariaDB (3306)", "3306"),
        ("PostgreSQL (5432)", "5432"),
        ("Redis (6379)", "6379"),
        ("MongoDB (27017)", "27017"),
        ("DNS (53)", "53"),
        ("FTP (21)", "21"),
    ]

    def __init__(self, theme: dict[str, str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add Firewall Rule")
        self.setMinimumWidth(480)
        self.theme = theme

        layout = QVBoxLayout(self)

        grid = QGridLayout()
        grid.setSpacing(10)

        # Quick Preset
        grid.addWidget(QLabel("Service Preset:"), 0, 0)
        self.preset_combo = QComboBox()
        for label, port in self.SERVICE_PRESETS:
            self.preset_combo.addItem(label, port)
        self.preset_combo.currentIndexChanged.connect(self._on_preset_changed)
        grid.addWidget(self.preset_combo, 0, 1)

        # Port / Range
        grid.addWidget(QLabel("Port or Range:"), 1, 0)
        self.port_edit = QLineEdit()
        self.port_edit.setPlaceholderText("e.g. 22 or 8000:8080")
        grid.addWidget(self.port_edit, 1, 1)

        # Protocol
        grid.addWidget(QLabel("Protocol:"), 2, 0)
        self.proto_combo = QComboBox()
        self.proto_combo.addItems(["tcp", "udp", "any"])
        grid.addWidget(self.proto_combo, 2, 1)

        # Action
        grid.addWidget(QLabel("Action:"), 3, 0)
        self.action_combo = QComboBox()
        self.action_combo.addItems(["allow", "deny", "reject", "limit"])
        grid.addWidget(self.action_combo, 3, 1)

        # Source IP
        grid.addWidget(QLabel("Source IP / CIDR:"), 4, 0)
        self.source_edit = QLineEdit("any")
        self.source_edit.setPlaceholderText("any or 192.168.1.0/24 or 203.0.113.5")
        grid.addWidget(self.source_edit, 4, 1)

        # Comment
        grid.addWidget(QLabel("Comment / Note:"), 5, 0)
        self.comment_edit = QLineEdit()
        self.comment_edit.setPlaceholderText("e.g. Office VPN or DB replication")
        grid.addWidget(self.comment_edit, 5, 1)

        layout.addLayout(grid)

        # Preview label
        self.preview_lbl = QLabel()
        self.preview_lbl.setStyleSheet(f"color: {theme.get('accent', '#3daee9')}; font-family: monospace; padding: 6px;")
        layout.addWidget(self.preview_lbl)

        # Dialog Buttons
        self.btn_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.btn_box.accepted.connect(self.accept)
        self.btn_box.rejected.connect(self.reject)
        layout.addWidget(self.btn_box)

        self.port_edit.textChanged.connect(self._update_preview)
        self.proto_combo.currentTextChanged.connect(self._update_preview)
        self.action_combo.currentTextChanged.connect(self._update_preview)
        self.source_edit.textChanged.connect(self._update_preview)
        self.comment_edit.textChanged.connect(self._update_preview)

        self._update_preview()

    def _on_preset_changed(self):
        port = self.preset_combo.currentData()
        if port:
            self.port_edit.setText(port)
            if port == "53":
                self.proto_combo.setCurrentText("any")
            else:
                self.proto_combo.setCurrentText("tcp")
            desc = self.preset_combo.currentText()
            if "(" in desc:
                self.comment_edit.setText(desc.split("(")[0].strip())

    def _update_preview(self):
        cmd = self.get_command()
        self.preview_lbl.setText(f"Command: {cmd}")

    def get_command(self) -> str:
        port = self.port_edit.text().strip()
        proto = self.proto_combo.currentText()
        action = self.action_combo.currentText()
        source = self.source_edit.text().strip() or "any"
        comment = self.comment_edit.text().strip()
        return SecurityCommands.ufw_add_rule(
            port_or_service=port,
            proto=proto,
            action=action,
            from_ip=source,
            comment=comment,
        )


class FirewallManagerWidget(QWidget):
    """Interactive GUI for inspecting and managing Linux firewalls."""

    def __init__(self, run_cmd: Callable[[str, Callable[[str, str, int], None]], None], theme: dict[str, str], parent=None):
        super().__init__(parent)
        self.run_cmd = run_cmd
        self.theme = theme
        self._rules_data: list[dict[str, Any]] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        # Top Control & Status Bar
        top_bar = QHBoxLayout()
        top_bar.setSpacing(8)

        self.status_badge = QLabel("Firewall: Checking...")
        self.status_badge.setStyleSheet(
            f"font-weight: bold; padding: 4px 10px; border-radius: 4px; border: 1px solid {theme.get('border', '#444')};"
        )
        top_bar.addWidget(self.status_badge)

        self.policy_label = QLabel("Default Policy: Incoming: ? | Outgoing: ?")
        self.policy_label.setStyleSheet(f"color: {theme.get('text_dim', '#888')}; font-size: 11px;")
        top_bar.addWidget(self.policy_label)

        top_bar.addStretch()

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("🔍 Filter rules...")
        self.search_edit.setFixedWidth(200)
        self.search_edit.textChanged.connect(self._filter_table)
        top_bar.addWidget(self.search_edit)

        self.btn_add_rule = QPushButton("➕ Add Rule")
        self.btn_add_rule.clicked.connect(self._on_add_rule)
        top_bar.addWidget(self.btn_add_rule)

        self.btn_del_rule = QPushButton("🗑️ Delete Selected")
        self.btn_del_rule.clicked.connect(self._on_delete_rule)
        top_bar.addWidget(self.btn_del_rule)

        self.btn_toggle_fw = QPushButton("⚡ Enable Firewall")
        self.btn_toggle_fw.clicked.connect(self._on_toggle_firewall)
        top_bar.addWidget(self.btn_toggle_fw)

        self.btn_reload = QPushButton("🔄 Reload")
        self.btn_reload.clicked.connect(self._on_reload)
        top_bar.addWidget(self.btn_reload)

        self.btn_export = QPushButton("📤 Export Rules")
        self.btn_export.setToolTip("Export rules table to CSV or JSON")
        self.btn_export.clicked.connect(self._on_export)
        top_bar.addWidget(self.btn_export)

        layout.addLayout(top_bar)

        # Rules Table
        self.table = QTableWidget()
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels([
            "#",
            "Action",
            "To (Port / Service)",
            "From (Source IP)",
            "IP Version",
            "Comment / Note",
        ])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)

        layout.addWidget(self.table)

    def refresh(self):
        """Fetch firewall status and rules."""
        self.run_cmd(SecurityCommands.get_ufw_status(), self._on_status_result)

    def _on_status_result(self, stdout: str, stderr: str, code: int):
        data = SecurityParsers.parse_ufw_status(stdout)
        is_active = data["active"]

        if is_active:
            self.status_badge.setText("● Firewall: ACTIVE (UFW)")
            self.status_badge.setStyleSheet(
                "background: #1b4d24; color: #44eb74; font-weight: bold; padding: 4px 10px; border-radius: 4px; border: 1px solid #2e7d32;"
            )
            self.btn_toggle_fw.setText("⛔ Disable Firewall")
        else:
            self.status_badge.setText("○ Firewall: INACTIVE")
            self.status_badge.setStyleSheet(
                "background: #4d1b1b; color: #eb4444; font-weight: bold; padding: 4px 10px; border-radius: 4px; border: 1px solid #b71c1c;"
            )
            self.btn_toggle_fw.setText("⚡ Enable Firewall")

        in_pol = data.get("default_incoming", "unknown")
        out_pol = data.get("default_outgoing", "unknown")
        self.policy_label.setText(f"Default Policies: Incoming: {in_pol} | Outgoing: {out_pol}")

        self._rules_data = data["rules"]
        self._populate_table(self._rules_data)

    def _populate_table(self, rules: list[dict[str, Any]]):
        self.table.setRowCount(len(rules))
        for row, r in enumerate(rules):
            # 0. Num
            item_num = QTableWidgetItem(str(r["num"]))
            item_num.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 0, item_num)

            # 1. Action
            action_str = r["action"]
            item_act = QTableWidgetItem(action_str)
            item_act.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if "ALLOW" in action_str:
                item_act.setForeground(QColor("#44eb74"))
            elif "DENY" in action_str or "REJECT" in action_str:
                item_act.setForeground(QColor("#eb4444"))
            elif "LIMIT" in action_str:
                item_act.setForeground(QColor("#ffa000"))
            self.table.setItem(row, 1, item_act)

            # 2. To
            self.table.setItem(row, 2, QTableWidgetItem(r["to"]))

            # 3. From
            self.table.setItem(row, 3, QTableWidgetItem(r["from"]))

            # 4. IP Version
            v_str = "IPv6" if r.get("ipv6") else "IPv4"
            item_v = QTableWidgetItem(v_str)
            item_v.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 4, item_v)

            # 5. Comment
            self.table.setItem(row, 5, QTableWidgetItem(r.get("comment", "")))

    def _filter_table(self, query: str):
        q = query.strip().lower()
        if not q:
            self._populate_table(self._rules_data)
            return

        filtered = [
            r for r in self._rules_data
            if q in str(r["num"])
            or q in r["action"].lower()
            or q in r["to"].lower()
            or q in r["from"].lower()
            or q in r.get("comment", "").lower()
        ]
        self._populate_table(filtered)

    def _on_add_rule(self):
        dlg = AddFirewallRuleDialog(self.theme, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            cmd = dlg.get_command()
            self.run_cmd(cmd, self._after_mutation)

    def _on_delete_rule(self):
        curr_row = self.table.currentRow()
        if curr_row < 0:
            QMessageBox.warning(self, "No Rule Selected", "Please select a rule to delete.")
            return

        rule_num_str = self.table.item(curr_row, 0).text()
        rule_desc = f"{self.table.item(curr_row, 1).text()} {self.table.item(curr_row, 2).text()} from {self.table.item(curr_row, 3).text()}"

        confirm = QMessageBox.question(
            self,
            "Confirm Delete Rule",
            f"Are you sure you want to delete rule #{rule_num_str}?\n\n({rule_desc})",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm == QMessageBox.StandardButton.Yes:
            try:
                num = int(rule_num_str)
                cmd = SecurityCommands.ufw_delete_rule_num(num)
                self.run_cmd(cmd, self._after_mutation)
            except ValueError:
                pass

    def _on_toggle_firewall(self):
        is_active = "ACTIVE" in self.status_badge.text()
        action_name = "disable" if is_active else "enable"

        if not is_active:
            confirm = QMessageBox.question(
                self,
                "Enable Firewall Warning",
                "Enabling the firewall may disconnect active SSH sessions if port 22 is not allowed.\n\nAre you sure you want to proceed?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if confirm != QMessageBox.StandardButton.Yes:
                return

        cmd = SecurityCommands.ufw_disable() if is_active else SecurityCommands.ufw_enable()
        self.run_cmd(cmd, self._after_mutation)

    def _on_reload(self):
        self.run_cmd(SecurityCommands.ufw_reload(), self._after_mutation)

    def _after_mutation(self, stdout: str, stderr: str, code: int):
        self.refresh()

    def _on_export(self):
        from admin_suite.core.export import ReportExporter
        from PyQt6.QtWidgets import QMenu
        from PyQt6.QtGui import QCursor
        menu = QMenu(self)
        csv_act = menu.addAction("📊 Export as CSV File")
        json_act = menu.addAction("📄 Export as JSON File")
        action = menu.exec(QCursor.pos())
        if action == csv_act:
            ReportExporter.export_table_csv(self, self.table, "firewall_rules.csv", "Export Firewall Rules to CSV")
        elif action == json_act:
            ReportExporter.export_table_json(self, self.table, "firewall_rules.json", "Export Firewall Rules to JSON")

