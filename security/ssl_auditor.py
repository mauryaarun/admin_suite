"""
SSL/TLS Certificate & Cryptography Auditor for Admin Suite.
Inspects all local system certificates (Let's Encrypt, RHEL PKI, OpenSSL),
tracks expiration dates, and audits remote endpoint TLS handshake protocols.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from admin_suite.core.export import ReportExporter
from admin_suite.security.commands import SecurityCommands, SecurityParsers


class SSLAuditorWidget(QWidget):
    """Interactive GUI for SSL certificate inventory, expiration monitoring, and TLS probes."""

    def __init__(
        self,
        run_cmd: Callable[[str, Callable[[str, str, int], None]], None],
        theme: dict[str, str],
        parent=None,
    ):
        super().__init__(parent)
        self.run_cmd = run_cmd
        self.theme = theme

        self._certs: list[dict[str, Any]] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        # Header Bar
        top_bar = QHBoxLayout()
        top_bar.setSpacing(8)

        title = QLabel("📜 SSL / TLS Certificate Security & Expiry Auditor")
        title.setStyleSheet(f"font-size: 13px; font-weight: bold; color: {theme.get('accent', '#3daee9')};")
        top_bar.addWidget(title)

        top_bar.addStretch()

        self.btn_export = QPushButton("📤 Export Certs")
        self.btn_export.clicked.connect(self._on_export)
        top_bar.addWidget(self.btn_export)

        self.btn_refresh = QPushButton("🔄 Audit Certs")
        self.btn_refresh.clicked.connect(self.refresh)
        top_bar.addWidget(self.btn_refresh)

        layout.addLayout(top_bar)

        # KPI Metrics Cards
        kpi_frame = QFrame()
        kpi_frame.setStyleSheet(f"background: {theme.get('panel2', '#222')}; border-radius: 4px; padding: 6px;")
        kpi_layout = QHBoxLayout(kpi_frame)
        kpi_layout.setSpacing(16)

        self.card_total = self._create_kpi_card("Total Certs", "0", theme.get("fg", "#eee"))
        self.card_valid = self._create_kpi_card("🟢 Valid (>30d)", "0", "#44eb74")
        self.card_warn = self._create_kpi_card("🟡 Expiring Soon", "0", "#ff9800")
        self.card_crit = self._create_kpi_card("🔴 Expired / Critical", "0", "#eb4444")

        kpi_layout.addWidget(self.card_total)
        kpi_layout.addWidget(self.card_valid)
        kpi_layout.addWidget(self.card_warn)
        kpi_layout.addWidget(self.card_crit)
        kpi_layout.addStretch()

        layout.addWidget(kpi_frame)

        # Certificate Inventory Table
        self.table = QTableWidget()
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels([
            "Status",
            "Domain / Subject",
            "Days Left",
            "Expiry Date",
            "Key Size & Algo",
            "Certificate File Path",
        ])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)

        layout.addWidget(self.table, 1)

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
        """Audit all SSL certificates across Debian and RHEL paths."""
        self.run_cmd(SecurityCommands.audit_all_ssl_certs(), self._on_certs_result)

    def _on_certs_result(self, stdout: str, stderr: str, code: int):
        certs = SecurityParsers.parse_ssl_certificates(stdout)
        self._certs = certs

        valid_cnt = sum(1 for c in certs if c["status"] == "VALID")
        warn_cnt = sum(1 for c in certs if c["status"] == "WARNING")
        crit_cnt = sum(1 for c in certs if c["status"] in ("CRITICAL", "EXPIRED"))

        self._set_kpi_val(self.card_total, str(len(certs)))
        self._set_kpi_val(self.card_valid, str(valid_cnt))
        self._set_kpi_val(self.card_warn, str(warn_cnt))
        self._set_kpi_val(self.card_crit, str(crit_cnt))

        self.table.setRowCount(len(certs))
        for row, c in enumerate(certs):
            # 0. Status
            st = c["status"]
            if st == "VALID":
                item_st = QTableWidgetItem("🟢 VALID")
                item_st.setForeground(QColor("#44eb74"))
            elif st == "WARNING":
                item_st = QTableWidgetItem("🟡 EXPIRING")
                item_st.setForeground(QColor("#ff9800"))
            else:
                item_st = QTableWidgetItem("🔴 " + st)
                item_st.setForeground(QColor("#ff5252"))
            item_st.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 0, item_st)

            # 1. Domain
            self.table.setItem(row, 1, QTableWidgetItem(c["domain"]))

            # 2. Days Left
            days = c["days_left"]
            item_days = QTableWidgetItem(f"{days} days" if days >= 0 else "Expired")
            item_days.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 2, item_days)

            # 3. Expiry Date
            self.table.setItem(row, 3, QTableWidgetItem(c["enddate"]))

            # 4. Key Size
            self.table.setItem(row, 4, QTableWidgetItem(c.get("keysize", "-")))

            # 5. Path
            item_p = QTableWidgetItem(c["path"])
            item_p.setFont(QFont("Monospace", 9))
            self.table.setItem(row, 5, item_p)

    def _on_export(self):
        menu = ReportExporter.create_export_menu(
            self,
            on_csv=lambda: ReportExporter.export_table_csv(self, self.table, "ssl_certificates_audit.csv", "Export SSL Certs to CSV"),
            on_json=lambda: ReportExporter.export_table_json(self, self.table, "ssl_certificates_audit.json", "Export SSL Certs to JSON"),
        )
        pos = self.btn_export.mapToGlobal(self.btn_export.rect().bottomLeft())
        menu.exec(pos)
