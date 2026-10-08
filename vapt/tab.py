"""
Unified VAPT, Web Application Security & Domain Audit Tab for Admin Suite.
Interactive GUI for comprehensive vulnerability assessment, technology detection,
security headers analysis, WHOIS intelligence, SSL inspection, subdomain discovery, and report export.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from admin_suite.core.export import ReportExporter
from admin_suite.vapt.engine import VaptEngine
from admin_suite.vapt.reporter import VaptReporter


class _VaptWorkerThread(QThread):
    """Background worker for asynchronous non-blocking security audit execution."""

    progress_updated = pyqtSignal(str, int)
    audit_completed = pyqtSignal(dict)
    audit_failed = pyqtSignal(str)

    def __init__(self, target_input: str, options: dict[str, bool], parent=None):
        super().__init__(parent)
        self.target_input = target_input
        self.options = options
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    def run(self):
        try:
            res = VaptEngine.run_audit(
                self.target_input,
                options=self.options,
                progress_cb=lambda msg, pct: self.progress_updated.emit(msg, pct),
                is_cancelled=lambda: self._is_cancelled,
            )
            self.audit_completed.emit(res)
        except InterruptedError:
            self.audit_failed.emit("Audit cancelled by user.")
        except Exception as ex:
            self.audit_failed.emit(f"Audit encountered an error: {ex}")


class VaptTab(QWidget):
    """Full-featured VAPT, Web Application Audit & Domain Reconnaissance GUI Tab."""

    def __init__(self, services, initial_target: str = "", parent=None):
        super().__init__(parent)
        self.services = services
        self.initial_target = initial_target
        self._worker: Optional[_VaptWorkerThread] = None
        self._current_data: Optional[dict[str, Any]] = None

        theme = self.services.theme.current
        self.theme = theme

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(6, 6, 6, 6)
        main_layout.setSpacing(6)

        # 1. Top Target Input & Action Bar
        top_bar = self._build_target_bar(theme)
        main_layout.addWidget(top_bar)

        # 2. Progress Indicator (Hidden when idle)
        self.progress_frame = self._build_progress_frame(theme)
        main_layout.addWidget(self.progress_frame)

        # 3. KPI Score & Overview Cards
        self.kpi_frame = self._build_kpi_frame(theme)
        main_layout.addWidget(self.kpi_frame)

        # 4. Central Multi-Tab Notebook
        self.audit_tabs = QTabWidget()
        self.audit_tabs.setDocumentMode(True)

        self.tab_summary = self._build_summary_tab(theme)
        self.tab_headers = self._build_headers_tab(theme)
        self.tab_tech = self._build_tech_tab(theme)
        self.tab_whois = self._build_whois_tab(theme)
        self.tab_dns = self._build_dns_tab(theme)
        self.tab_ssl = self._build_ssl_tab(theme)
        self.tab_subdomains = self._build_subdomains_tab(theme)
        self.tab_exposure = self._build_exposure_tab(theme)
        self.tab_report = self._build_report_tab(theme)

        self.audit_tabs.addTab(self.tab_summary, "📊 Executive Summary")
        self.audit_tabs.addTab(self.tab_headers, "🛡️ Security Headers")
        self.audit_tabs.addTab(self.tab_tech, "💻 Tech Stack")
        self.audit_tabs.addTab(self.tab_whois, "📋 WHOIS")
        self.audit_tabs.addTab(self.tab_dns, "🌐 DNS & Email Security")
        self.audit_tabs.addTab(self.tab_ssl, "🔒 SSL / TLS")
        self.audit_tabs.addTab(self.tab_subdomains, "🔎 Subdomains")
        self.audit_tabs.addTab(self.tab_exposure, "⚠️ Web Exposure & Cookies")
        self.audit_tabs.addTab(self.tab_report, "📄 Full Report & Export")

        main_layout.addWidget(self.audit_tabs, 1)

        # Auto start if initial target provided
        if self.initial_target:
            self.target_input.setText(self.initial_target)
            self.start_audit()

    def _build_target_bar(self, theme: dict[str, str]) -> QFrame:
        frame = QFrame()
        frame.setStyleSheet(
            f"background: {theme.get('panel2', '#222')}; border-radius: 6px; padding: 6px;"
        )
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(8)

        icon_lbl = QLabel("🛡️")
        icon_lbl.setFont(QFont("Segoe UI", 14))
        layout.addWidget(icon_lbl)

        target_lbl = QLabel("Target URL / Domain:")
        target_lbl.setStyleSheet(f"font-weight: bold; color: {theme.get('accent', '#3daee9')};")
        layout.addWidget(target_lbl)

        self.target_input = QLineEdit()
        self.target_input.setPlaceholderText("e.g. https://example.com or company.org")
        self.target_input.returnPressed.connect(self.start_audit)
        layout.addWidget(self.target_input, 1)

        self.profile_combo = QComboBox()
        self.profile_combo.addItems(["Full VAPT Audit", "Fast Web Audit", "Recon Only"])
        self.profile_combo.setToolTip("Select scan profile depth")
        layout.addWidget(self.profile_combo)

        self.btn_start = QPushButton("🚀 Run Audit")
        self.btn_start.setStyleSheet(
            f"background: {theme.get('accent', '#3daee9')}; color: white; font-weight: bold; padding: 6px 14px;"
        )
        self.btn_start.clicked.connect(self.start_audit)
        layout.addWidget(self.btn_start)

        self.btn_cancel = QPushButton("🛑 Stop")
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self.cancel_audit)
        layout.addWidget(self.btn_cancel)

        # Export Report Button
        self.btn_export_quick = QPushButton("📤 Export Report")
        self.btn_export_quick.setEnabled(False)
        self.btn_export_quick.clicked.connect(self.export_html_report)
        layout.addWidget(self.btn_export_quick)

        return frame

    def _build_progress_frame(self, theme: dict[str, str]) -> QFrame:
        frame = QFrame()
        frame.setStyleSheet(
            f"background: {theme.get('panel', '#1e1e1e')}; border: 1px solid {theme.get('border', '#333')}; border-radius: 4px; padding: 4px;"
        )
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(10)

        self.progress_lbl = QLabel("Ready to audit target.")
        self.progress_lbl.setStyleSheet(f"color: {theme.get('sub', '#aaa')}; font-size: 12px;")
        layout.addWidget(self.progress_lbl, 1)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFixedHeight(14)
        self.progress_bar.setFixedWidth(260)
        layout.addWidget(self.progress_bar)

        frame.setVisible(False)
        return frame

    def _build_kpi_frame(self, theme: dict[str, str]) -> QFrame:
        frame = QFrame()
        frame.setStyleSheet(
            f"background: {theme.get('panel2', '#222')}; border-radius: 6px; padding: 6px;"
        )
        layout = QHBoxLayout(frame)
        layout.setSpacing(14)

        # Grade Badge
        self.kpi_grade = self._create_kpi_card("Overall Grade", "-", "#888", font_size=24)
        self.kpi_score = self._create_kpi_card("Security Score", "0 / 100", theme.get("fg", "#eee"))
        self.kpi_crit = self._create_kpi_card("🔴 Critical Issues", "0", "#eb4444")
        self.kpi_high = self._create_kpi_card("🟠 High Issues", "0", "#ff9800")
        self.kpi_warn = self._create_kpi_card("🟡 Warnings", "0", "#ffca28")
        self.kpi_subs = self._create_kpi_card("🟢 Subdomains", "0", "#44eb74")
        self.kpi_time = self._create_kpi_card("⏱ Scan Time", "0s", theme.get("sub", "#aaa"))

        layout.addWidget(self.kpi_grade)
        layout.addWidget(self.kpi_score)
        layout.addWidget(self.kpi_crit)
        layout.addWidget(self.kpi_high)
        layout.addWidget(self.kpi_warn)
        layout.addWidget(self.kpi_subs)
        layout.addWidget(self.kpi_time)
        layout.addStretch()

        return frame

    def _create_kpi_card(self, title: str, val: str, val_color: str, font_size: int = 16) -> QFrame:
        card = QFrame()
        card.setStyleSheet(
            f"background: {self.theme.get('panel', '#1b1e23')}; border: 1px solid {self.theme.get('border', '#333')}; border-radius: 5px; padding: 4px 10px;"
        )
        lay = QVBoxLayout(card)
        lay.setContentsMargins(4, 2, 4, 2)
        lay.setSpacing(1)

        val_lbl = QLabel(val)
        val_lbl.setFont(QFont("Segoe UI, Inter", font_size, QFont.Weight.Bold))
        val_lbl.setStyleSheet(f"color: {val_color};")
        val_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

        title_lbl = QLabel(title)
        title_lbl.setStyleSheet(f"font-size: 10px; color: {self.theme.get('sub', '#888')}; text-transform: uppercase;")
        title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

        lay.addWidget(val_lbl)
        lay.addWidget(title_lbl)

        card._val_lbl = val_lbl  # store reference
        return card

    # -------------------------------------------------------------------------
    # Sub-tab Builders
    # -------------------------------------------------------------------------

    def _build_summary_tab(self, theme: dict[str, str]) -> QWidget:
        widget = QWidget()
        lay = QVBoxLayout(widget)
        lay.setContentsMargins(6, 6, 6, 6)

        header_lbl = QLabel("⚠️ Identified Security Vulnerabilities & Audit Findings")
        header_lbl.setStyleSheet(f"font-weight: bold; font-size: 13px; color: {theme.get('accent', '#3daee9')};")
        lay.addWidget(header_lbl)

        self.summary_table = QTableWidget()
        self.summary_table.setColumnCount(4)
        self.summary_table.setHorizontalHeaderLabels(["Severity", "Vulnerability / Issue", "Technical Details", "Actionable Remediation"])
        self.summary_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.summary_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.summary_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.summary_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.summary_table.verticalHeader().setVisible(False)
        lay.addWidget(self.summary_table)

        return widget

    def _build_headers_tab(self, theme: dict[str, str]) -> QWidget:
        widget = QWidget()
        lay = QVBoxLayout(widget)
        lay.setContentsMargins(6, 6, 6, 6)

        top_info = QHBoxLayout()
        self.headers_grade_lbl = QLabel("Security Headers Compliance: Not Scanned")
        self.headers_grade_lbl.setStyleSheet(f"font-weight: bold; font-size: 13px; color: {theme.get('accent', '#3daee9')};")
        top_info.addWidget(self.headers_grade_lbl)
        top_info.addStretch()
        lay.addLayout(top_info)

        splitter = QSplitter(Qt.Orientation.Vertical)

        # Headers table
        self.headers_table = QTableWidget()
        self.headers_table.setColumnCount(5)
        self.headers_table.setHorizontalHeaderLabels(["Header Name", "Status", "Detected Value", "Earned / Weight", "Recommended Directive"])
        self.headers_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.headers_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.headers_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.headers_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.headers_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.headers_table.verticalHeader().setVisible(False)
        splitter.addWidget(self.headers_table)

        # Remediation Config box
        config_box = QFrame()
        config_lay = QVBoxLayout(config_box)
        config_lay.setContentsMargins(0, 4, 0, 0)
        lbl = QLabel("🔧 Recommended Server Hardening Snippet (Nginx / Apache):")
        lbl.setStyleSheet(f"font-weight: bold; color: {theme.get('fg', '#eee')};")
        config_lay.addWidget(lbl)

        self.headers_config_text = QPlainTextEdit()
        self.headers_config_text.setReadOnly(True)
        self.headers_config_text.setPlaceholderText("Hardening configuration will be generated after audit.")
        self.headers_config_text.setFont(QFont("Monospace", 9))
        config_lay.addWidget(self.headers_config_text)
        splitter.addWidget(config_box)

        splitter.setSizes([320, 160])
        lay.addWidget(splitter)
        return widget

    def _build_tech_tab(self, theme: dict[str, str]) -> QWidget:
        widget = QWidget()
        lay = QVBoxLayout(widget)
        lay.setContentsMargins(6, 6, 6, 6)

        title = QLabel("💻 Applications, Frameworks & Web Server Fingerprints")
        title.setStyleSheet(f"font-weight: bold; font-size: 13px; color: {theme.get('accent', '#3daee9')};")
        lay.addWidget(title)

        self.tech_table = QTableWidget()
        self.tech_table.setColumnCount(4)
        self.tech_table.setHorizontalHeaderLabels(["Technology / Application", "Category", "Detected Version", "Confidence"])
        self.tech_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.tech_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.tech_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.tech_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.tech_table.verticalHeader().setVisible(False)
        lay.addWidget(self.tech_table)

        return widget

    def _build_whois_tab(self, theme: dict[str, str]) -> QWidget:
        widget = QWidget()
        lay = QVBoxLayout(widget)
        lay.setContentsMargins(6, 6, 6, 6)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left: Key parsed properties
        left_frame = QFrame()
        left_lay = QVBoxLayout(left_frame)
        left_lay.setContentsMargins(0, 0, 4, 0)

        title = QLabel("📋 Parsed Registration & Domain Intelligence")
        title.setStyleSheet(f"font-weight: bold; color: {theme.get('accent', '#3daee9')};")
        left_lay.addWidget(title)

        self.whois_table = QTableWidget()
        self.whois_table.setColumnCount(2)
        self.whois_table.setHorizontalHeaderLabels(["Property", "Value"])
        self.whois_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.whois_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.whois_table.verticalHeader().setVisible(False)
        left_lay.addWidget(self.whois_table)
        splitter.addWidget(left_frame)

        # Right: Raw WHOIS text
        right_frame = QFrame()
        right_lay = QVBoxLayout(right_frame)
        right_lay.setContentsMargins(4, 0, 0, 0)

        raw_title = QLabel("📄 Raw WHOIS Server Response")
        raw_title.setStyleSheet(f"font-weight: bold; color: {theme.get('fg', '#eee')};")
        right_lay.addWidget(raw_title)

        self.whois_raw_text = QPlainTextEdit()
        self.whois_raw_text.setReadOnly(True)
        self.whois_raw_text.setFont(QFont("Monospace", 9))
        right_lay.addWidget(self.whois_raw_text)
        splitter.addWidget(right_frame)

        splitter.setSizes([380, 500])
        lay.addWidget(splitter)
        return widget

    def _build_dns_tab(self, theme: dict[str, str]) -> QWidget:
        widget = QWidget()
        lay = QVBoxLayout(widget)
        lay.setContentsMargins(6, 6, 6, 6)

        splitter = QSplitter(Qt.Orientation.Vertical)

        # Email Security Posture
        email_sec_frame = QFrame()
        email_sec_lay = QVBoxLayout(email_sec_frame)
        email_sec_lay.setContentsMargins(0, 0, 0, 6)

        email_title = QLabel("📧 Email Spoofing Defense Posture (SPF & DMARC)")
        email_title.setStyleSheet(f"font-weight: bold; color: {theme.get('accent', '#3daee9')};")
        email_sec_lay.addWidget(email_title)

        self.email_sec_table = QTableWidget()
        self.email_sec_table.setColumnCount(4)
        self.email_sec_table.setHorizontalHeaderLabels(["Protocol", "Status", "Policy Enforced", "Raw Record / Evaluation"])
        self.email_sec_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.email_sec_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.email_sec_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.email_sec_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.email_sec_table.verticalHeader().setVisible(False)
        email_sec_lay.addWidget(self.email_sec_table)
        splitter.addWidget(email_sec_frame)

        # DNS Records
        dns_frame = QFrame()
        dns_lay = QVBoxLayout(dns_frame)
        dns_lay.setContentsMargins(0, 6, 0, 0)

        dns_title = QLabel("🌐 Authoritative DNS Records (A, AAAA, MX, NS, TXT, SOA, CAA)")
        dns_title.setStyleSheet(f"font-weight: bold; color: {theme.get('accent', '#3daee9')};")
        dns_lay.addWidget(dns_title)

        self.dns_table = QTableWidget()
        self.dns_table.setColumnCount(3)
        self.dns_table.setHorizontalHeaderLabels(["Record Type", "Target Domain", "Record Value / Target"])
        self.dns_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.dns_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.dns_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.dns_table.verticalHeader().setVisible(False)
        dns_lay.addWidget(self.dns_table)
        splitter.addWidget(dns_frame)

        splitter.setSizes([200, 320])
        lay.addWidget(splitter)
        return widget

    def _build_ssl_tab(self, theme: dict[str, str]) -> QWidget:
        widget = QWidget()
        lay = QVBoxLayout(widget)
        lay.setContentsMargins(6, 6, 6, 6)

        title = QLabel("🔒 SSL / TLS Certificate Validity & Cipher Handshake")
        title.setStyleSheet(f"font-weight: bold; font-size: 13px; color: {theme.get('accent', '#3daee9')};")
        lay.addWidget(title)

        self.ssl_table = QTableWidget()
        self.ssl_table.setColumnCount(2)
        self.ssl_table.setHorizontalHeaderLabels(["Certificate / TLS Property", "Inspected Value"])
        self.ssl_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.ssl_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.ssl_table.verticalHeader().setVisible(False)
        lay.addWidget(self.ssl_table)

        return widget

    def _build_subdomains_tab(self, theme: dict[str, str]) -> QWidget:
        widget = QWidget()
        lay = QVBoxLayout(widget)
        lay.setContentsMargins(6, 6, 6, 6)

        top_bar = QHBoxLayout()
        self.subs_title = QLabel("🔎 Subdomain Enumeration & Active Resolvers: 0 Discovered")
        self.subs_title.setStyleSheet(f"font-weight: bold; font-size: 13px; color: {theme.get('accent', '#3daee9')};")
        top_bar.addWidget(self.subs_title)
        top_bar.addStretch()

        self.subs_filter = QLineEdit()
        self.subs_filter.setPlaceholderText("🔍 Filter subdomains...")
        self.subs_filter.setFixedWidth(200)
        self.subs_filter.textChanged.connect(self._filter_subdomains)
        top_bar.addWidget(self.subs_filter)

        lay.addLayout(top_bar)

        self.subdomains_table = QTableWidget()
        self.subdomains_table.setColumnCount(5)
        self.subdomains_table.setHorizontalHeaderLabels(["Subdomain Hostname", "Resolved IP", "HTTP Status", "Liveness", "Discovery Source"])
        self.subdomains_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.subdomains_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.subdomains_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.subdomains_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.subdomains_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.subdomains_table.verticalHeader().setVisible(False)
        lay.addWidget(self.subdomains_table)

        return widget

    def _build_exposure_tab(self, theme: dict[str, str]) -> QWidget:
        widget = QWidget()
        lay = QVBoxLayout(widget)
        lay.setContentsMargins(6, 6, 6, 6)

        splitter = QSplitter(Qt.Orientation.Vertical)

        # Cookie flags
        cookie_frame = QFrame()
        cookie_lay = QVBoxLayout(cookie_frame)
        cookie_lay.setContentsMargins(0, 0, 0, 6)

        cookie_title = QLabel("🍪 Cookie Security Flags Audit (Secure, HttpOnly, SameSite)")
        cookie_title.setStyleSheet(f"font-weight: bold; color: {theme.get('accent', '#3daee9')};")
        cookie_lay.addWidget(cookie_title)

        self.cookies_table = QTableWidget()
        self.cookies_table.setColumnCount(5)
        self.cookies_table.setHorizontalHeaderLabels(["Cookie Name", "Secure Flag", "HttpOnly Flag", "SameSite Attribute", "Evaluation"])
        self.cookies_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.cookies_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.cookies_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.cookies_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.cookies_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.cookies_table.verticalHeader().setVisible(False)
        cookie_lay.addWidget(self.cookies_table)
        splitter.addWidget(cookie_frame)

        # Endpoints & files exposure
        expo_frame = QFrame()
        expo_lay = QVBoxLayout(expo_frame)
        expo_lay.setContentsMargins(0, 6, 0, 0)

        expo_title = QLabel("⚠️ Sensitive Endpoint & Configuration Exposure Checks")
        expo_title.setStyleSheet(f"font-weight: bold; color: {theme.get('accent', '#3daee9')};")
        expo_lay.addWidget(expo_title)

        self.exposure_table = QTableWidget()
        self.exposure_table.setColumnCount(3)
        self.exposure_table.setHorizontalHeaderLabels(["Endpoint / Path", "Probe Response Status", "Risk Severity"])
        self.exposure_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.exposure_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.exposure_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.exposure_table.verticalHeader().setVisible(False)
        expo_lay.addWidget(self.exposure_table)
        splitter.addWidget(expo_frame)

        splitter.setSizes([240, 240])
        lay.addWidget(splitter)
        return widget

    def _build_report_tab(self, theme: dict[str, str]) -> QWidget:
        widget = QWidget()
        lay = QVBoxLayout(widget)
        lay.setContentsMargins(6, 6, 6, 6)

        btn_bar = QHBoxLayout()
        btn_bar.setSpacing(8)

        self.btn_export_html = QPushButton("📄 Export HTML Report")
        self.btn_export_html.clicked.connect(self.export_html_report)
        btn_bar.addWidget(self.btn_export_html)

        self.btn_export_json = QPushButton("📊 Export JSON Report")
        self.btn_export_json.clicked.connect(self.export_json_report)
        btn_bar.addWidget(self.btn_export_json)

        self.btn_export_md = QPushButton("📝 Export Markdown Report")
        self.btn_export_md.clicked.connect(self.export_markdown_report)
        btn_bar.addWidget(self.btn_export_md)

        self.btn_copy_summary = QPushButton("📋 Copy Summary")
        self.btn_copy_summary.clicked.connect(self.copy_summary_to_clipboard)
        btn_bar.addWidget(self.btn_copy_summary)

        btn_bar.addStretch()
        lay.addLayout(btn_bar)

        self.report_preview = QTextEdit()
        self.report_preview.setReadOnly(True)
        self.report_preview.setFont(QFont("Monospace", 9))
        self.report_preview.setPlaceholderText("Audit report preview will be rendered here upon completion.")
        lay.addWidget(self.report_preview)

        return widget

    # -------------------------------------------------------------------------
    # Audit Execution Control
    # -------------------------------------------------------------------------

    def start_audit(self):
        target = self.target_input.text().strip()
        if not target:
            QMessageBox.warning(self, "Invalid Target", "Please enter a valid target URL or domain name.")
            return

        mode = self.profile_combo.currentText()
        options = {
            "whois": True,
            "dns": True,
            "ssl": True,
            "headers": True,
            "tech": True,
            "subdomains": mode != "Fast Web Audit",
            "exposure": mode != "Recon Only",
        }

        self.btn_start.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.btn_export_quick.setEnabled(False)
        self.progress_frame.setVisible(True)
        self.progress_bar.setValue(5)
        self.progress_lbl.setText(f"Initiating security audit for {target}...")

        self.services.emit_log("VAPT", f"Started VAPT security audit on target: {target} (Mode: {mode})", "INFO")

        self._worker = _VaptWorkerThread(target, options, self)
        self._worker.progress_updated.connect(self._on_progress)
        self._worker.audit_completed.connect(self._on_audit_completed)
        self._worker.audit_failed.connect(self._on_audit_failed)
        self._worker.start()

    def cancel_audit(self):
        if self._worker and self._worker.isRunning():
            self._worker.cancel()
            self.progress_lbl.setText("Cancelling audit...")
            self.btn_cancel.setEnabled(False)

    def _on_progress(self, msg: str, pct: int):
        self.progress_lbl.setText(msg)
        self.progress_bar.setValue(pct)

    def _on_audit_completed(self, data: dict[str, Any]):
        self._current_data = data
        self.btn_start.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        self.btn_export_quick.setEnabled(True)
        self.progress_bar.setValue(100)
        self.progress_lbl.setText(f"✓ Audit completed successfully in {data.get('duration', 0)}s!")

        # Update KPI Cards
        score = data.get("score", 0)
        grade = data.get("grade", "F")
        counts = data.get("finding_counts", {})

        grade_colors = {
            "A+": "#00e676", "A": "#00e676",
            "B": "#29b6f6", "C": "#ffca28",
            "D": "#ff9800", "F": "#f44336",
        }
        g_col = grade_colors.get(grade, "#f44336")

        self.kpi_grade._val_lbl.setText(grade)
        self.kpi_grade._val_lbl.setStyleSheet(f"color: {g_col};")
        self.kpi_score._val_lbl.setText(f"{score} / 100")
        self.kpi_crit._val_lbl.setText(str(counts.get("CRITICAL", 0)))
        self.kpi_high._val_lbl.setText(str(counts.get("HIGH", 0)))
        self.kpi_warn._val_lbl.setText(str(counts.get("MEDIUM", 0)))
        self.kpi_subs._val_lbl.setText(str(len(data.get("subdomains", []))))
        self.kpi_time._val_lbl.setText(f"{data.get('duration', 0)}s")

        # Populate tables
        self._populate_summary_table(data)
        self._populate_headers_table(data)
        self._populate_tech_table(data)
        self._populate_whois_table(data)
        self._populate_dns_table(data)
        self._populate_ssl_table(data)
        self._populate_subdomains_table(data)
        self._populate_exposure_table(data)
        self._populate_report_tab(data)

        self.services.emit_log(
            "VAPT",
            f"Completed audit for {data.get('domain')}: Grade {grade} ({score}/100) with {len(data.get('all_findings', []))} findings.",
            "SUCCESS",
        )
        self.services.notifications.push(
            f"VAPT Audit Complete: {data.get('domain')} (Grade {grade}, Score {score}/100)",
            level="success" if score >= 75 else "warning",
        )

    def _on_audit_failed(self, err_msg: str):
        self.btn_start.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        self.progress_lbl.setText(f"❌ {err_msg}")
        QMessageBox.critical(self, "Audit Failed", err_msg)
        self.services.emit_log("VAPT", err_msg, "ERROR")

    # -------------------------------------------------------------------------
    # Table Population Helpers
    # -------------------------------------------------------------------------

    def _populate_summary_table(self, data: dict[str, Any]):
        findings = data.get("all_findings", [])
        self.summary_table.setRowCount(len(findings))

        sev_colors = {
            "CRITICAL": QColor("#eb4444"),
            "HIGH": QColor("#ff5722"),
            "MEDIUM": QColor("#ffa000"),
            "LOW": QColor("#29b6f6"),
        }

        for row, f in enumerate(findings):
            sev = f.get("severity", "LOW")
            sev_item = QTableWidgetItem(f" {sev} ")
            sev_item.setBackground(sev_colors.get(sev, QColor("#888")))
            sev_item.setForeground(QColor("#ffffff"))
            sev_item.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
            sev_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            issue_item = QTableWidgetItem(f.get("issue", ""))
            issue_item.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))

            detail_item = QTableWidgetItem(f.get("detail", ""))
            remed_item = QTableWidgetItem(f.get("remediation", ""))

            self.summary_table.setItem(row, 0, sev_item)
            self.summary_table.setItem(row, 1, issue_item)
            self.summary_table.setItem(row, 2, detail_item)
            self.summary_table.setItem(row, 3, remed_item)

    def _populate_headers_table(self, data: dict[str, Any]):
        sec_h = data.get("security_headers", {})
        score = sec_h.get("score", 0)
        grade = sec_h.get("grade", "F")
        self.headers_grade_lbl.setText(f"Security Headers Compliance: Grade {grade} ({score}/100)")

        items = sec_h.get("items", [])
        self.headers_table.setRowCount(len(items))

        status_colors = {
            "PASS": QColor("#00c853"),
            "WARN": QColor("#ffb300"),
            "FAIL": QColor("#e53935"),
        }

        for row, item in enumerate(items):
            h_item = QTableWidgetItem(item.get("header", ""))
            h_item.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))

            status = item.get("status", "FAIL")
            st_item = QTableWidgetItem(f" {status} ")
            st_item.setBackground(status_colors.get(status, QColor("#888")))
            st_item.setForeground(QColor("#ffffff"))
            st_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            val_item = QTableWidgetItem(item.get("value", ""))
            val_item.setFont(QFont("Monospace", 9))

            score_str = f"{item.get('earned', 0)} / {item.get('weight', 0)}"
            score_item = QTableWidgetItem(score_str)
            score_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            rec_item = QTableWidgetItem(item.get("recommended", ""))

            self.headers_table.setItem(row, 0, h_item)
            self.headers_table.setItem(row, 1, st_item)
            self.headers_table.setItem(row, 2, val_item)
            self.headers_table.setItem(row, 3, score_item)
            self.headers_table.setItem(row, 4, rec_item)

        cfg_text = sec_h.get("nginx_config", "") + "\n\n" + sec_h.get("apache_config", "")
        self.headers_config_text.setPlainText(cfg_text)

    def _populate_tech_table(self, data: dict[str, Any]):
        techs = data.get("tech_stack", [])
        self.tech_table.setRowCount(len(techs))

        for row, t in enumerate(techs):
            icon = t.get("icon", "📦")
            name_item = QTableWidgetItem(f"{icon}  {t.get('name', '')}")
            name_item.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))

            cat_item = QTableWidgetItem(t.get("category", ""))
            ver_item = QTableWidgetItem(t.get("version", "-") or "-")
            ver_item.setFont(QFont("Monospace", 9))
            conf_item = QTableWidgetItem(t.get("confidence", "High"))

            self.tech_table.setItem(row, 0, name_item)
            self.tech_table.setItem(row, 1, cat_item)
            self.tech_table.setItem(row, 2, ver_item)
            self.tech_table.setItem(row, 3, conf_item)

    def _populate_whois_table(self, data: dict[str, Any]):
        whois = data.get("whois", {})
        self.whois_raw_text.setPlainText(whois.get("raw", "No raw WHOIS data available."))

        props = [
            ("Target Domain", whois.get("domain", data.get("domain"))),
            ("Registrar", whois.get("registrar")),
            ("Registrar IANA ID", whois.get("registrar_iana_id")),
            ("Registrar URL", whois.get("registrar_url")),
            ("Creation Date", whois.get("creation_date")),
            ("Expiration Date", whois.get("expiration_date")),
            ("Days Until Expiry", f"{whois.get('days_until_expiry')} days" if whois.get("days_until_expiry") is not None else "-"),
            ("Updated Date", whois.get("updated_date")),
            ("Registrant Organization", whois.get("registrant_org")),
            ("Registrant Country", whois.get("registrant_country")),
            ("DNSSEC Status", whois.get("dnssec")),
            ("Name Servers", ", ".join(whois.get("name_servers", []))),
            ("Domain Status", ", ".join(whois.get("status", []))),
        ]

        self.whois_table.setRowCount(len(props))
        for row, (k, v) in enumerate(props):
            k_item = QTableWidgetItem(k)
            k_item.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
            v_item = QTableWidgetItem(str(v or "Unknown / Redacted"))
            self.whois_table.setItem(row, 0, k_item)
            self.whois_table.setItem(row, 1, v_item)

    def _populate_dns_table(self, data: dict[str, Any]):
        dns_info = data.get("dns", {})

        # Email Security table
        spf = dns_info.get("spf", {})
        dmarc = dns_info.get("dmarc", {})

        email_items = [
            ("SPF (Sender Policy Framework)", spf.get("status", "Missing"), str(spf.get("policy", "None")), spf.get("raw", "No SPF record found.")),
            ("DMARC (Domain-based Auth)", dmarc.get("status", "Missing"), str(dmarc.get("policy", "None")), dmarc.get("raw", "No DMARC record found at _dmarc.")),
        ]
        self.email_sec_table.setRowCount(len(email_items))
        for row, (proto, status, policy, raw) in enumerate(email_items):
            p_item = QTableWidgetItem(proto)
            p_item.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))

            st_item = QTableWidgetItem(status)
            if "Missing" in status:
                st_item.setBackground(QColor("#e53935"))
                st_item.setForeground(QColor("#fff"))
            else:
                st_item.setBackground(QColor("#00c853"))
                st_item.setForeground(QColor("#fff"))

            pol_item = QTableWidgetItem(policy)
            raw_item = QTableWidgetItem(raw)
            raw_item.setFont(QFont("Monospace", 9))

            self.email_sec_table.setItem(row, 0, p_item)
            self.email_sec_table.setItem(row, 1, st_item)
            self.email_sec_table.setItem(row, 2, pol_item)
            self.email_sec_table.setItem(row, 3, raw_item)

        # DNS Records table
        records = dns_info.get("records", {})
        total_rows = sum(len(v) for v in records.values())
        self.dns_table.setRowCount(total_rows)

        r_idx = 0
        domain = data.get("domain", "")
        for rtype, rvalues in records.items():
            for val in rvalues:
                t_item = QTableWidgetItem(rtype)
                t_item.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
                d_item = QTableWidgetItem(domain)
                v_item = QTableWidgetItem(val)
                v_item.setFont(QFont("Monospace", 9))

                self.dns_table.setItem(r_idx, 0, t_item)
                self.dns_table.setItem(r_idx, 1, d_item)
                self.dns_table.setItem(r_idx, 2, v_item)
                r_idx += 1

    def _populate_ssl_table(self, data: dict[str, Any]):
        ssl_info = data.get("ssl", {})
        props = [
            ("SSL / TLS Configured", "Yes (Port 443 active)" if ssl_info.get("has_ssl") else "No SSL Detected"),
            ("Security Grade", ssl_info.get("grade", "F")),
            ("Issuer Organization", ssl_info.get("issuer", {}).get("organizationName", "-")),
            ("Issuer Common Name (CA)", ssl_info.get("issuer", {}).get("commonName", "-")),
            ("Subject Common Name", ssl_info.get("subject", {}).get("commonName", "-")),
            ("Valid From", ssl_info.get("valid_from", "-")),
            ("Expiration Date", ssl_info.get("valid_to", "-")),
            ("Days Until Expiry", f"{ssl_info.get('days_left')} days" if ssl_info.get("days_left") is not None else "-"),
            ("Is Self-Signed", "Yes (Untrusted)" if ssl_info.get("is_self_signed") else "No (Trusted CA)"),
            ("TLS Protocol Version", ssl_info.get("protocol", "-")),
            ("Negotiated Cipher Suite", ssl_info.get("cipher", "-")),
            ("Cipher Strength", f"{ssl_info.get('cipher_bits')} bits" if ssl_info.get("cipher_bits") else "-"),
            ("Subject Alternative Names (SANs)", ", ".join(ssl_info.get("sans", [])) or "None"),
        ]

        self.ssl_table.setRowCount(len(props))
        for row, (k, v) in enumerate(props):
            k_item = QTableWidgetItem(k)
            k_item.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
            v_item = QTableWidgetItem(str(v))
            if k == "Days Until Expiry" and ssl_info.get("is_expiring_soon"):
                v_item.setForeground(QColor("#ff9800"))
            elif k == "Is Self-Signed" and ssl_info.get("is_self_signed"):
                v_item.setForeground(QColor("#eb4444"))

            self.ssl_table.setItem(row, 0, k_item)
            self.ssl_table.setItem(row, 1, v_item)

    def _populate_subdomains_table(self, data: dict[str, Any]):
        subdomains = data.get("subdomains", [])
        self.subs_title.setText(f"🔎 Subdomain Enumeration: {len(subdomains)} Discovered")
        self.subdomains_table.setRowCount(len(subdomains))

        for row, s in enumerate(subdomains):
            sub_item = QTableWidgetItem(s.get("subdomain", ""))
            sub_item.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))

            ip_item = QTableWidgetItem(s.get("ip", "-"))
            ip_item.setFont(QFont("Monospace", 9))

            st_item = QTableWidgetItem(str(s.get("status_code", "-")))
            st_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            is_alive = s.get("is_alive", False)
            live_item = QTableWidgetItem("🟢 Alive" if is_alive else "⚪ Inactive")
            live_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            src_item = QTableWidgetItem(", ".join(s.get("sources", [])))

            self.subdomains_table.setItem(row, 0, sub_item)
            self.subdomains_table.setItem(row, 1, ip_item)
            self.subdomains_table.setItem(row, 2, st_item)
            self.subdomains_table.setItem(row, 3, live_item)
            self.subdomains_table.setItem(row, 4, src_item)

    def _filter_subdomains(self, text: str):
        query = text.strip().lower()
        for row in range(self.subdomains_table.rowCount()):
            item = self.subdomains_table.item(row, 0)
            if item:
                match = query in item.text().lower()
                self.subdomains_table.setRowHidden(row, not match)

    def _populate_exposure_table(self, data: dict[str, Any]):
        # Cookies table
        cookies_info = data.get("cookies", {})
        cookie_list = cookies_info.get("cookies", [])
        self.cookies_table.setRowCount(len(cookie_list))

        for row, c in enumerate(cookie_list):
            name_item = QTableWidgetItem(c.get("name", ""))
            name_item.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))

            sec_item = QTableWidgetItem("✓ Yes" if c.get("has_secure") else "✗ Missing")
            sec_item.setForeground(QColor("#00c853") if c.get("has_secure") else QColor("#e53935"))

            http_item = QTableWidgetItem("✓ Yes" if c.get("has_httponly") else "✗ Missing")
            http_item.setForeground(QColor("#00c853") if c.get("has_httponly") else QColor("#e53935"))

            same_item = QTableWidgetItem(str(c.get("samesite", "None")))

            eval_str = "Clean" if c.get("status") == "PASS" else ", ".join(c.get("flaws", []))
            eval_item = QTableWidgetItem(eval_str)
            if c.get("status") != "PASS":
                eval_item.setForeground(QColor("#ffa000"))

            self.cookies_table.setItem(row, 0, name_item)
            self.cookies_table.setItem(row, 1, sec_item)
            self.cookies_table.setItem(row, 2, http_item)
            self.cookies_table.setItem(row, 3, same_item)
            self.cookies_table.setItem(row, 4, eval_item)

        # Exposure files table
        expo_info = data.get("web_exposure", {})
        sensitive_items = expo_info.get("sensitive_files", {}).get("items", [])
        self.exposure_table.setRowCount(len(sensitive_items))

        for row, item in enumerate(sensitive_items):
            p_item = QTableWidgetItem(item.get("path", ""))
            p_item.setFont(QFont("Monospace", 9, QFont.Weight.Bold))

            st_item = QTableWidgetItem(item.get("status", ""))
            sev = item.get("severity", "NONE")
            sev_item = QTableWidgetItem(sev)
            if sev == "CRITICAL":
                sev_item.setBackground(QColor("#eb4444"))
                sev_item.setForeground(QColor("#fff"))
            elif sev == "HIGH":
                sev_item.setBackground(QColor("#ff5722"))
                sev_item.setForeground(QColor("#fff"))
            else:
                sev_item.setForeground(QColor("#00c853"))

            self.exposure_table.setItem(row, 0, p_item)
            self.exposure_table.setItem(row, 1, st_item)
            self.exposure_table.setItem(row, 2, sev_item)

    def _populate_report_tab(self, data: dict[str, Any]):
        md_text = VaptReporter.generate_markdown(data)
        self.report_preview.setPlainText(md_text)

    # -------------------------------------------------------------------------
    # Report Export Handlers
    # -------------------------------------------------------------------------

    def export_html_report(self):
        if not self._current_data:
            QMessageBox.information(self, "Export Report", "Run an audit first before exporting.")
            return

        html_content = VaptReporter.generate_html(self._current_data)
        domain = self._current_data.get("domain", "audit")
        filename = f"vapt_report_{domain}.html"

        path = ReportExporter.export_text_file(
            self,
            html_content,
            default_filename=filename,
            title="Export VAPT HTML Report",
            filter_str="HTML Files (*.html);;All Files (*)",
        )
        if path:
            self.services.emit_log("VAPT", f"Exported HTML audit report to {path}", "INFO")

    def export_json_report(self):
        if not self._current_data:
            QMessageBox.information(self, "Export Report", "Run an audit first before exporting.")
            return

        json_content = VaptReporter.generate_json(self._current_data)
        domain = self._current_data.get("domain", "audit")
        filename = f"vapt_audit_{domain}.json"

        path = ReportExporter.export_text_file(
            self,
            json_content,
            default_filename=filename,
            title="Export VAPT JSON Data",
            filter_str="JSON Files (*.json);;All Files (*)",
        )
        if path:
            self.services.emit_log("VAPT", f"Exported JSON audit data to {path}", "INFO")

    def export_markdown_report(self):
        if not self._current_data:
            QMessageBox.information(self, "Export Report", "Run an audit first before exporting.")
            return

        md_content = VaptReporter.generate_markdown(self._current_data)
        domain = self._current_data.get("domain", "audit")
        filename = f"vapt_report_{domain}.md"

        path = ReportExporter.export_text_file(
            self,
            md_content,
            default_filename=filename,
            title="Export VAPT Markdown Report",
            filter_str="Markdown Files (*.md);;Text Files (*.txt);;All Files (*)",
        )
        if path:
            self.services.emit_log("VAPT", f"Exported Markdown audit report to {path}", "INFO")

    def copy_summary_to_clipboard(self):
        if not self._current_data:
            return
        summary = VaptReporter.generate_markdown(self._current_data)
        QApplication.clipboard().setText(summary)
        QMessageBox.information(self, "Copied", "VAPT audit summary copied to clipboard.")
