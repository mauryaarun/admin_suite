"""
Firewall Management Interface for Admin Suite.
Supports both UFW (Debian / Ubuntu) and Firewalld (RedHat / CentOS / AlmaLinux / Rocky / Fedora),
with rule tables, quick service presets, IP restrictions, and live actions.
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
    """Wizard for adding a new firewall rule supporting both UFW and Firewalld."""

    SERVICE_PRESETS = [
        ("Custom Port...", ""),
        ("SSH (22)", "22"),
        ("HTTP Web (80)", "80"),
        ("HTTPS Web (443)", "443"),
        ("HTTP & HTTPS (80, 443)", "80,443"),
        ("MySQL / MariaDB (3306)", "3306"),
        ("PostgreSQL (5432)", "5432"),
        ("Redis (6379)", "6379"),
        ("MongoDB (27017)", "27017"),
        ("DNS (53)", "53"),
        ("FTP (21)", "21"),
    ]

    FIREWALLD_SERVICES = [
        "ssh", "http", "https", "cockpit", "dns", "ftp", "mysql", "postgresql",
        "redis", "smtp", "smtps", "imap", "imaps", "openvpn", "wireguard", "nfs", "samba",
    ]

    def __init__(self, backend: str, theme: dict[str, str], zone: str = "public", parent=None):
        super().__init__(parent)
        self.backend = backend.lower()
        self.theme = theme
        self.zone = zone
        self.setWindowTitle(f"Add Firewall Rule ({self.backend.upper()})")
        self.setMinimumWidth(500)

        layout = QVBoxLayout(self)

        grid = QGridLayout()
        grid.setSpacing(10)

        # Mode selection for Firewalld: Port vs Named Service
        row = 0
        if "firewalld" in self.backend:
            grid.addWidget(QLabel("Rule Type:"), row, 0)
            self.type_combo = QComboBox()
            self.type_combo.addItems(["Open Port / Range", "Named Service Preset", "Custom Rich Rule"])
            self.type_combo.currentIndexChanged.connect(self._on_type_changed)
            grid.addWidget(self.type_combo, row, 1)
            row += 1

            grid.addWidget(QLabel("Zone:"), row, 0)
            self.zone_edit = QLineEdit(self.zone)
            grid.addWidget(self.zone_edit, row, 1)
            row += 1

            grid.addWidget(QLabel("Service Name:"), row, 0)
            self.service_combo = QComboBox()
            self.service_combo.setEditable(True)
            self.service_combo.addItems(self.FIREWALLD_SERVICES)
            self.service_combo.setEnabled(False)
            self.service_combo.currentTextChanged.connect(self._update_preview)
            grid.addWidget(self.service_combo, row, 1)
            row += 1

        # Quick Preset for Port
        grid.addWidget(QLabel("Service Preset:"), row, 0)
        self.preset_combo = QComboBox()
        for label, port in self.SERVICE_PRESETS:
            self.preset_combo.addItem(label, port)
        self.preset_combo.currentIndexChanged.connect(self._on_preset_changed)
        grid.addWidget(self.preset_combo, row, 1)
        row += 1

        # Port / Range
        grid.addWidget(QLabel("Port or Range:"), row, 0)
        self.port_edit = QLineEdit()
        self.port_edit.setPlaceholderText("e.g. 22 or 8000:8080")
        grid.addWidget(self.port_edit, row, 1)
        row += 1

        # Protocol
        grid.addWidget(QLabel("Protocol:"), row, 0)
        self.proto_combo = QComboBox()
        self.proto_combo.addItems(["tcp", "udp", "any"])
        grid.addWidget(self.proto_combo, row, 1)
        row += 1

        # Action
        grid.addWidget(QLabel("Action:"), row, 0)
        self.action_combo = QComboBox()
        self.action_combo.addItems(["allow", "deny", "reject", "limit"])
        grid.addWidget(self.action_combo, row, 1)
        row += 1

        # Source IP
        grid.addWidget(QLabel("Source IP / CIDR:"), row, 0)
        self.source_edit = QLineEdit("any")
        self.source_edit.setPlaceholderText("any or 192.168.1.0/24 or 203.0.113.5")
        grid.addWidget(self.source_edit, row, 1)
        row += 1

        # Comment (for UFW) / Note
        grid.addWidget(QLabel("Comment / Note:"), row, 0)
        self.comment_edit = QLineEdit()
        self.comment_edit.setPlaceholderText("e.g. Office VPN or DB replication")
        grid.addWidget(self.comment_edit, row, 1)
        row += 1

        layout.addLayout(grid)

        # Preview label
        self.preview_lbl = QLabel()
        self.preview_lbl.setStyleSheet(f"color: {theme.get('accent', '#3daee9')}; font-family: monospace; padding: 6px;")
        self.preview_lbl.setWordWrap(True)
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

    def _on_type_changed(self, idx: int):
        is_service = idx == 1
        is_rich = idx == 2
        if hasattr(self, "service_combo"):
            self.service_combo.setEnabled(is_service)
        self.port_edit.setEnabled(not is_service)
        self.preset_combo.setEnabled(not is_service and not is_rich)
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
        self.preview_lbl.setText(f"Command:\n{cmd}")

    def get_command(self) -> str:
        port = self.port_edit.text().strip()
        proto = self.proto_combo.currentText()
        action = self.action_combo.currentText()
        source = self.source_edit.text().strip() or "any"
        comment = self.comment_edit.text().strip()

        if "firewalld" in self.backend:
            zone = self.zone_edit.text().strip() if hasattr(self, "zone_edit") else self.zone
            rule_type_idx = self.type_combo.currentIndex() if hasattr(self, "type_combo") else 0

            if rule_type_idx == 1:
                # Named service
                srv = self.service_combo.currentText().strip()
                return SecurityCommands.firewalld_add_service(srv, zone=zone)
            elif rule_type_idx == 2 or (source != "any" and source != ""):
                # Rich rule
                act_str = "accept" if action == "allow" else ("drop" if action == "deny" else "reject")
                proto_clean = "tcp" if proto == "any" else proto
                port_part = f' port port="{port}" protocol="{proto_clean}"' if port else ""
                src_part = f' source address="{source}"' if (source and source != "any") else ""
                rich = f'rule{src_part}{port_part} {act_str}'
                return SecurityCommands.firewalld_add_rich_rule(rich, zone=zone)
            else:
                if action in ("deny", "reject"):
                    return SecurityCommands.firewalld_deny_port(port, proto=proto if proto != "any" else "tcp", zone=zone)
                return SecurityCommands.firewalld_add_port(port, proto=proto if proto != "any" else "tcp", zone=zone)
        else:
            return SecurityCommands.ufw_add_rule(
                port_or_service=port,
                proto=proto,
                action=action,
                from_ip=source,
                comment=comment,
            )


class FirewallManagerWidget(QWidget):
    """Interactive GUI for inspecting and managing Linux firewalls (UFW & Firewalld)."""

    def __init__(
        self,
        run_cmd: Callable[[str, Callable[[str, str, int], None]], None],
        theme: dict[str, str],
        parent=None,
    ):
        super().__init__(parent)
        self.run_cmd = run_cmd
        self.theme = theme
        self.active_backend: str = "auto"
        self.detected_backend: str = "ufw"
        self.current_zone: str = "public"
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

        # Backend Selector
        top_bar.addWidget(QLabel("Backend:"))
        self.backend_combo = QComboBox()
        self.backend_combo.addItem("Auto-Detect", "auto")
        self.backend_combo.addItem("UFW (Ubuntu/Debian)", "ufw")
        self.backend_combo.addItem("Firewalld (RHEL/CentOS)", "firewalld")
        self.backend_combo.addItem("iptables (Legacy)", "iptables")
        self.backend_combo.currentIndexChanged.connect(self._on_backend_changed)
        top_bar.addWidget(self.backend_combo)

        self.policy_label = QLabel("Zone / Policy: ?")
        self.policy_label.setStyleSheet(f"color: {theme.get('text_dim', '#888')}; font-size: 11px;")
        top_bar.addWidget(self.policy_label)

        top_bar.addStretch()

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("🔍 Filter rules...")
        self.search_edit.setFixedWidth(180)
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
            "Details / Comment",
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

    def _on_backend_changed(self):
        self.active_backend = self.backend_combo.currentData()
        self.refresh()

    def refresh(self):
        """Detect and fetch firewall status and rules."""
        if self.active_backend == "auto":
            # Detect whether firewalld or ufw or iptables is present
            self.run_cmd(SecurityCommands.detect_firewall(), self._on_detection_result)
        elif self.active_backend == "firewalld":
            self.detected_backend = "firewalld"
            self.run_cmd(SecurityCommands.firewalld_status(self.current_zone), self._on_firewalld_result)
        elif self.active_backend == "iptables":
            self.detected_backend = "iptables"
            self.run_cmd(SecurityCommands.iptables_status(), self._on_iptables_result)
        else:
            self.detected_backend = "ufw"
            self.run_cmd(SecurityCommands.get_ufw_status(), self._on_ufw_result)

    def _on_detection_result(self, stdout: str, stderr: str, code: int):
        out = stdout.lower()
        if "type:firewalld" in out:
            self.detected_backend = "firewalld"
            self.run_cmd(SecurityCommands.firewalld_status(self.current_zone), self._on_firewalld_result)
        elif "type:ufw" in out:
            self.detected_backend = "ufw"
            self.run_cmd(SecurityCommands.get_ufw_status(), self._on_ufw_result)
        elif "type:iptables" in out:
            self.detected_backend = "iptables"
            self.run_cmd(SecurityCommands.iptables_status(), self._on_iptables_result)
        else:
            self.detected_backend = "ufw"
            self.run_cmd(SecurityCommands.get_ufw_status(), self._on_ufw_result)

    def _on_ufw_result(self, stdout: str, stderr: str, code: int):
        data = SecurityParsers.parse_ufw_status(stdout)
        is_active = data["active"]

        if is_active:
            self.status_badge.setText("● Firewall: ACTIVE (UFW)")
            self.status_badge.setStyleSheet(
                "background: #1b4d24; color: #44eb74; font-weight: bold; padding: 4px 10px; border-radius: 4px; border: 1px solid #2e7d32;"
            )
            self.btn_toggle_fw.setText("⛔ Disable Firewall")
        else:
            self.status_badge.setText("○ Firewall: INACTIVE (UFW)")
            self.status_badge.setStyleSheet(
                "background: #4d1b1b; color: #eb4444; font-weight: bold; padding: 4px 10px; border-radius: 4px; border: 1px solid #b71c1c;"
            )
            self.btn_toggle_fw.setText("⚡ Enable Firewall")

        in_pol = data.get("default_incoming", "unknown")
        out_pol = data.get("default_outgoing", "unknown")
        self.policy_label.setText(f"Default Policies: Incoming: {in_pol} | Outgoing: {out_pol}")

        self._rules_data = data["rules"]
        self._populate_table(self._rules_data)

    def _on_firewalld_result(self, stdout: str, stderr: str, code: int):
        data = SecurityParsers.parse_firewalld_status(stdout)
        is_active = data["active"]
        self.current_zone = data.get("zone", "public")

        if is_active:
            self.status_badge.setText(f"● Firewall: ACTIVE (Firewalld - {self.current_zone})")
            self.status_badge.setStyleSheet(
                "background: #1b4d24; color: #44eb74; font-weight: bold; padding: 4px 10px; border-radius: 4px; border: 1px solid #2e7d32;"
            )
            self.btn_toggle_fw.setText("⛔ Disable Firewall")
        else:
            self.status_badge.setText("○ Firewall: INACTIVE (Firewalld)")
            self.status_badge.setStyleSheet(
                "background: #4d1b1b; color: #eb4444; font-weight: bold; padding: 4px 10px; border-radius: 4px; border: 1px solid #b71c1c;"
            )
            self.btn_toggle_fw.setText("⚡ Enable Firewall")

        active_zones_str = ", ".join(data.get("active_zones", [self.current_zone]))
        self.policy_label.setText(f"Active Zones: {active_zones_str} | Services: {len(data['services'])} | Ports: {len(data['ports'])}")

        self._rules_data = data["rules"]
        self._populate_table(self._rules_data)

    def _on_iptables_result(self, stdout: str, stderr: str, code: int):
        self.status_badge.setText("● Firewall: iptables")
        self.status_badge.setStyleSheet(
            "background: #333; color: #4fc3f7; font-weight: bold; padding: 4px 10px; border-radius: 4px; border: 1px solid #555;"
        )
        self.policy_label.setText("Legacy packet filter rules")

        # Parse basic iptables lines
        rules = []
        rule_num = 1
        for line in stdout.splitlines():
            line_s = line.strip()
            if not line_s or line_s.startswith("Chain") or line_s.startswith("pkts") or line_s.startswith("num"):
                continue
            parts = line_s.split()
            if len(parts) >= 6:
                act = parts[2] if len(parts) > 2 else "ACCEPT"
                proto = parts[1] if len(parts) > 1 else "all"
                src = parts[7] if len(parts) > 7 else "anywhere"
                rules.append({
                    "num": rule_num,
                    "action": act,
                    "to": proto,
                    "from": src,
                    "ipv6": False,
                    "comment": line_s,
                    "raw_target": line_s,
                })
                rule_num += 1

        self._rules_data = rules
        self._populate_table(rules)

    def _populate_table(self, rules: list[dict[str, Any]]):
        self.table.setRowCount(len(rules))
        for row, r in enumerate(rules):
            # 0. Num
            item_num = QTableWidgetItem(str(r["num"]))
            item_num.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 0, item_num)

            # 1. Action
            action_str = r.get("action", "")
            item_act = QTableWidgetItem(action_str)
            item_act.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if "ALLOW" in action_str or "ACCEPT" in action_str:
                item_act.setForeground(QColor("#44eb74"))
            elif "DENY" in action_str or "REJECT" in action_str or "DROP" in action_str:
                item_act.setForeground(QColor("#eb4444"))
            elif "LIMIT" in action_str or "RICH" in action_str:
                item_act.setForeground(QColor("#ffa000"))
            self.table.setItem(row, 1, item_act)

            # 2. To
            self.table.setItem(row, 2, QTableWidgetItem(r.get("to", "")))

            # 3. From
            self.table.setItem(row, 3, QTableWidgetItem(r.get("from", "")))

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
            if q in str(r.get("num", ""))
            or q in r.get("action", "").lower()
            or q in r.get("to", "").lower()
            or q in r.get("from", "").lower()
            or q in r.get("comment", "").lower()
        ]
        self._populate_table(filtered)

    def _on_add_rule(self):
        backend = self.detected_backend
        dlg = AddFirewallRuleDialog(backend=backend, theme=self.theme, zone=self.current_zone, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            cmd = dlg.get_command()
            self.run_cmd(cmd, self._after_mutation)

    def _on_delete_rule(self):
        curr_row = self.table.currentRow()
        if curr_row < 0:
            QMessageBox.warning(self, "No Rule Selected", "Please select a rule to delete.")
            return

        rule_record = self._rules_data[curr_row] if curr_row < len(self._rules_data) else {}
        rule_num_str = self.table.item(curr_row, 0).text()
        rule_desc = f"{self.table.item(curr_row, 1).text()} {self.table.item(curr_row, 2).text()}"

        confirm = QMessageBox.question(
            self,
            "Confirm Delete Rule",
            f"Are you sure you want to remove rule #{rule_num_str}?\n\n({rule_desc})",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm == QMessageBox.StandardButton.Yes:
            if self.detected_backend == "firewalld":
                rule_type = rule_record.get("type", "")
                raw_target = rule_record.get("raw_target", "")
                if rule_type == "service":
                    cmd = SecurityCommands.firewalld_remove_service(raw_target, zone=self.current_zone)
                elif rule_type == "port":
                    cmd = SecurityCommands.firewalld_remove_port(raw_target, zone=self.current_zone)
                elif rule_type == "rich":
                    cmd = SecurityCommands.firewalld_remove_rich_rule(raw_target, zone=self.current_zone)
                else:
                    cmd = SecurityCommands.firewalld_remove_port(raw_target, zone=self.current_zone)
                self.run_cmd(cmd, self._after_mutation)
            else:
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

        if self.detected_backend == "firewalld":
            cmd = SecurityCommands.firewalld_disable() if is_active else SecurityCommands.firewalld_enable()
        else:
            cmd = SecurityCommands.ufw_disable() if is_active else SecurityCommands.ufw_enable()
        self.run_cmd(cmd, self._after_mutation)

    def _on_reload(self):
        if self.detected_backend == "firewalld":
            cmd = SecurityCommands.firewalld_reload()
        else:
            cmd = SecurityCommands.ufw_reload()
        self.run_cmd(cmd, self._after_mutation)

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
