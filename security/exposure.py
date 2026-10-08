"""
Network Port Exposure, Listening Sockets, and Active Connections Auditor for Admin Suite.
Audits open network sockets, detects publicly exposed databases/internal services,
monitors active established remote connections, and provides 1-click firewall mitigations.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from admin_suite.security.commands import SecurityCommands, SecurityParsers


class PortExposureWidget(QWidget):
    """Interactive GUI for discovering exposed services, listening sockets, and active connections."""

    def __init__(
        self,
        run_cmd: Callable[[str, Callable[[str, str, int], None]], None],
        theme: dict[str, str],
        on_block_port_cb: Optional[Callable[[int, str], None]] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.run_cmd = run_cmd
        self.theme = theme
        self.on_block_port_cb = on_block_port_cb

        self._all_sockets: list[dict[str, Any]] = []
        self._active_connections: list[dict[str, Any]] = []
        self._top_ips: list[dict[str, Any]] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        # Header Bar
        top_bar = QHBoxLayout()
        top_bar.setSpacing(8)

        title = QLabel("🌐 Network Exposure & Active Connections")
        title.setStyleSheet(f"font-size: 13px; font-weight: bold; color: {theme.get('accent', '#3daee9')};")
        top_bar.addWidget(title)

        top_bar.addStretch()

        self.risk_filter_combo = QComboBox()
        self.risk_filter_combo.addItems(["All Risks", "High & Critical Only", "Medium & Up", "Safe Only"])
        self.risk_filter_combo.currentIndexChanged.connect(self._filter_table)
        top_bar.addWidget(self.risk_filter_combo)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("🔍 Search port, process, IP...")
        self.search_edit.setFixedWidth(200)
        self.search_edit.textChanged.connect(self._filter_table)
        top_bar.addWidget(self.search_edit)

        self.btn_refresh = QPushButton("🔄 Audit Now")
        self.btn_refresh.clicked.connect(self.refresh)
        top_bar.addWidget(self.btn_refresh)

        self.btn_export = QPushButton("📤 Export Sockets")
        self.btn_export.setToolTip("Export exposed sockets table to CSV or JSON")
        self.btn_export.clicked.connect(self._on_export)
        top_bar.addWidget(self.btn_export)

        layout.addLayout(top_bar)

        # KPI Metrics Cards
        kpi_frame = QFrame()
        kpi_frame.setStyleSheet(
            f"background: {theme.get('panel2', '#222')}; border-radius: 4px; padding: 6px;"
        )
        kpi_layout = QHBoxLayout(kpi_frame)
        kpi_layout.setSpacing(16)

        self.card_total = self._create_kpi_card("Total Sockets", "0", theme.get("fg", "#eee"))
        self.card_critical = self._create_kpi_card("🔴 Critical / High Risk", "0", "#eb4444")
        self.card_medium = self._create_kpi_card("🟡 Medium Risk", "0", "#ff9800")
        self.card_safe = self._create_kpi_card("🟢 Safe / Localhost", "0", "#44eb74")
        self.card_conn = self._create_kpi_card("📡 Active Conns", "0", "#29b6f6")

        kpi_layout.addWidget(self.card_total)
        kpi_layout.addWidget(self.card_critical)
        kpi_layout.addWidget(self.card_medium)
        kpi_layout.addWidget(self.card_safe)
        kpi_layout.addWidget(self.card_conn)
        kpi_layout.addStretch()

        layout.addWidget(kpi_frame)

        # Tabs for Listening Sockets vs Active Remote Connections
        self.sub_tabs = QTabWidget()

        # ---------------- Sub-tab 1: Listening Sockets ----------------
        sockets_widget = QWidget()
        s_layout = QVBoxLayout(sockets_widget)
        s_layout.setContentsMargins(0, 0, 0, 0)

        self.table = QTableWidget()
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels([
            "Risk",
            "Proto",
            "Local Address & Port",
            "Process (PID)",
            "Assessment & Exposure Analysis",
            "Mitigation",
        ])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        s_layout.addWidget(self.table)
        self.sub_tabs.addTab(sockets_widget, "🌐 Listening Sockets & Port Risks")

        # ---------------- Sub-tab 2: Active Remote Connections ----------------
        conn_widget = QWidget()
        c_layout = QHBoxLayout(conn_widget)
        c_layout.setContentsMargins(0, 0, 0, 0)

        # Left: Top Remote IPs Table
        left_box = QWidget()
        l_vbox = QVBoxLayout(left_box)
        l_vbox.setContentsMargins(0, 0, 0, 0)
        l_vbox.addWidget(QLabel("🔝 Top Remote IPs (Connection Count):"))

        self.top_ip_table = QTableWidget()
        self.top_ip_table.setColumnCount(3)
        self.top_ip_table.setHorizontalHeaderLabels(["Hits / Conns", "Remote IP", "Actions"])
        self.top_ip_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.top_ip_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.top_ip_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.top_ip_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        l_vbox.addWidget(self.top_ip_table)
        c_layout.addWidget(left_box, 1)

        # Right: Established Connections Table
        right_box = QWidget()
        r_vbox = QVBoxLayout(right_box)
        r_vbox.setContentsMargins(0, 0, 0, 0)
        r_vbox.addWidget(QLabel("📡 Active Established Sockets:"))

        self.conn_table = QTableWidget()
        self.conn_table.setColumnCount(5)
        self.conn_table.setHorizontalHeaderLabels(["Proto", "State", "Local Address", "Remote Address", "Process"])
        self.conn_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.conn_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.conn_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        self.conn_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        self.conn_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.conn_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        r_vbox.addWidget(self.conn_table)
        c_layout.addWidget(right_box, 2)

        self.sub_tabs.addTab(conn_widget, "📡 Active Connections & Top Remote IPs")

        layout.addWidget(self.sub_tabs, 1)

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
        """Fetch listening sockets and active connections."""
        self.run_cmd(SecurityCommands.list_listening_sockets(), self._on_sockets_result)
        self.run_cmd(SecurityCommands.list_active_connections(), self._on_active_conn_result)

    def _on_sockets_result(self, stdout: str, stderr: str, code: int):
        self._all_sockets = SecurityParsers.parse_listening_sockets(stdout)

        crit_count = sum(1 for s in self._all_sockets if s["risk"] in ("critical", "high"))
        med_count = sum(1 for s in self._all_sockets if s["risk"] == "medium")
        safe_count = sum(1 for s in self._all_sockets if s["risk"] in ("safe", "info"))

        self._set_kpi_val(self.card_total, str(len(self._all_sockets)))
        self._set_kpi_val(self.card_critical, str(crit_count))
        self._set_kpi_val(self.card_medium, str(med_count))
        self._set_kpi_val(self.card_safe, str(safe_count))

        self._filter_table()

    def _on_active_conn_result(self, stdout: str, stderr: str, code: int):
        data = SecurityParsers.parse_active_connections(stdout)
        self._active_connections = data.get("connections", [])
        self._top_ips = data.get("top_ips", [])

        self._set_kpi_val(self.card_conn, str(len(self._active_connections)))
        self._populate_active_connections()

    def _populate_active_connections(self):
        # 1. Top IPs
        self.top_ip_table.setRowCount(len(self._top_ips))
        for row, item in enumerate(self._top_ips):
            cnt_item = QTableWidgetItem(str(item["count"]))
            cnt_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.top_ip_table.setItem(row, 0, cnt_item)

            ip_item = QTableWidgetItem(item["ip"])
            ip_item.setFont(QFont("Monospace"))
            self.top_ip_table.setItem(row, 1, ip_item)

            # Actions cell
            ip_addr = item["ip"]
            act_w = QWidget()
            act_l = QHBoxLayout(act_w)
            act_l.setContentsMargins(2, 2, 2, 2)
            act_l.setSpacing(4)

            btn_f2b = QPushButton("🚫 Ban")
            btn_f2b.setStyleSheet("padding: 2px 6px; font-size: 10px;")
            btn_f2b.clicked.connect(lambda _, ip=ip_addr: self._ban_ip_action(ip))
            act_l.addWidget(btn_f2b)

            btn_copy = QPushButton("📋")
            btn_copy.setStyleSheet("padding: 2px 6px; font-size: 10px;")
            btn_copy.clicked.connect(lambda _, ip=ip_addr: QApplication.clipboard().setText(ip))
            act_l.addWidget(btn_copy)

            self.top_ip_table.setCellWidget(row, 2, act_w)

        # 2. Connections Table
        self.conn_table.setRowCount(len(self._active_connections))
        for row, conn in enumerate(self._active_connections):
            self.conn_table.setItem(row, 0, QTableWidgetItem(conn.get("proto", "")))
            self.conn_table.setItem(row, 1, QTableWidgetItem(conn.get("state", "")))
            self.conn_table.setItem(row, 2, QTableWidgetItem(conn.get("local", "")))
            self.conn_table.setItem(row, 3, QTableWidgetItem(conn.get("remote", "")))
            self.conn_table.setItem(row, 4, QTableWidgetItem(conn.get("process", "")))

    def _ban_ip_action(self, ip: str):
        confirm = QMessageBox.question(
            self,
            "Ban Suspicious IP",
            f"Add {ip} to Fail2ban sshd jail or drop via firewall?\n\n(fail2ban-client set sshd banip {ip})",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm == QMessageBox.StandardButton.Yes:
            cmd = SecurityCommands.fail2ban_ban("sshd", ip)
            self.run_cmd(cmd, lambda out, err, code: self.refresh())

    def _filter_table(self):
        filter_mode = self.risk_filter_combo.currentIndex()
        query = self.search_edit.text().strip().lower()

        filtered = []
        for s in self._all_sockets:
            if filter_mode == 1 and s["risk"] not in ("critical", "high"):
                continue
            if filter_mode == 2 and s["risk"] not in ("critical", "high", "medium"):
                continue
            if filter_mode == 3 and s["risk"] not in ("safe", "info"):
                continue

            if query:
                q_match = (
                    query in str(s["port"])
                    or query in s["host"].lower()
                    or query in s["process_name"].lower()
                    or query in s["pid"]
                    or query in s["risk_reason"].lower()
                )
                if not q_match:
                    continue

            filtered.append(s)

        self._populate_table(filtered)

    def _populate_table(self, sockets: list[dict[str, Any]]):
        self.table.setRowCount(len(sockets))

        for row, s in enumerate(sockets):
            # 0. Risk Badge
            risk = s["risk"]
            if risk == "critical":
                risk_text = "🚨 CRITICAL"
                risk_color = "#ff1744"
            elif risk == "high":
                risk_text = "🔴 HIGH RISK"
                risk_color = "#eb4444"
            elif risk == "medium":
                risk_text = "🟡 CAUTION"
                risk_color = "#ff9800"
            elif risk == "info":
                risk_text = "ℹ️ INFO"
                risk_color = "#29b6f6"
            else:
                risk_text = "🟢 SAFE"
                risk_color = "#44eb74"

            item_risk = QTableWidgetItem(risk_text)
            item_risk.setForeground(QColor(risk_color))
            item_risk.setFont(QFont("Sans", weight=QFont.Weight.Bold))
            item_risk.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 0, item_risk)

            # 1. Proto
            item_proto = QTableWidgetItem(s["protocol"])
            item_proto.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 1, item_proto)

            # 2. Local Address : Port
            addr_str = f"{s['host']}:{s['port']}"
            item_addr = QTableWidgetItem(addr_str)
            item_addr.setFont(QFont("Monospace"))
            if s.get("is_wildcard") and risk in ("critical", "high"):
                item_addr.setForeground(QColor("#ff5252"))
            self.table.setItem(row, 2, item_addr)

            # 3. Process (PID)
            proc = s.get("process_name") or "-"
            pid = s.get("pid")
            proc_display = f"{proc} ({pid})" if pid else proc
            self.table.setItem(row, 3, QTableWidgetItem(proc_display))

            # 4. Assessment & Exposure Analysis
            item_desc = QTableWidgetItem(s.get("risk_reason", ""))
            self.table.setItem(row, 4, item_desc)

            # 5. Quick Mitigation Action
            port_num = s["port"]
            proto_val = s["protocol"].lower()

            action_widget = QWidget()
            action_layout = QHBoxLayout(action_widget)
            action_layout.setContentsMargins(4, 2, 4, 2)
            action_layout.setSpacing(4)

            btn_mitigate = QPushButton("🛡️ Block Port")
            btn_mitigate.setStyleSheet("padding: 2px 8px; font-size: 11px;")
            btn_mitigate.clicked.connect(lambda _, p=port_num, pr=proto_val: self._on_block_clicked(p, pr))
            action_layout.addWidget(btn_mitigate)

            self.table.setCellWidget(row, 5, action_widget)

    def _on_block_clicked(self, port: int, proto: str):
        if self.on_block_port_cb:
            self.on_block_port_cb(port, proto)
        else:
            confirm = QMessageBox.question(
                self,
                "Block Port in Firewall",
                f"Block incoming traffic on port {port}/{proto} from all external addresses?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if confirm == QMessageBox.StandardButton.Yes:
                # Use UFW or Firewalld based on detection
                cmd = f"ufw deny {port}/{proto} 2>/dev/null || firewall-cmd --permanent --add-rich-rule='rule port port=\"{port}\" protocol=\"{proto}\" reject' && firewall-cmd --reload"
                self.run_cmd(cmd, lambda out, err, code: self.refresh())

    def _on_export(self):
        from admin_suite.core.export import ReportExporter
        from PyQt6.QtWidgets import QMenu
        from PyQt6.QtGui import QCursor
        menu = QMenu(self)
        csv_act = menu.addAction("📊 Export as CSV File")
        json_act = menu.addAction("📄 Export as JSON File")
        action = menu.exec(QCursor.pos())
        if action == csv_act:
            ReportExporter.export_table_csv(self, self.table, "exposed_sockets_audit.csv", "Export Exposed Sockets to CSV")
        elif action == json_act:
            ReportExporter.export_table_json(self, self.table, "exposed_sockets_audit.json", "Export Exposed Sockets to JSON")
