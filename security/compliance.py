"""
Compliance Automation (OpenSCAP) and Auditing (Auditd) Interface for Admin Suite.
Audits systems against CIS Benchmarks, DISA STIG, and PCI-DSS standards,
and configures Linux Auditd to track user account modifications, permission changes,
and sensitive file access across RHEL and Debian distributions.
"""

from __future__ import annotations

import os
import re
import shlex
from typing import Any, Callable, Dict, List, Optional

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QColor, QDesktopServices, QFont
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from admin_suite.core.export import ReportExporter
from admin_suite.security.commands import SecurityCommands, SecurityParsers


class ComplianceAuditorWidget(QWidget):
    """
    Automated Compliance Verification & Linux Auditing subsystem widget.
    Integrates OpenSCAP (CIS / STIG / PCI-DSS XCCDF evaluation & HTML reporting)
    and Auditd (Linux Audit daemon tracking account, permission, and sensitive file modifications).
    """

    DEFAULT_PROFILES = [
        ("CIS Benchmark — Level 1 / Server (Standard)", "xccdf_org.ssgproject.content_profile_cis"),
        ("CIS Benchmark — Level 1 / Workstation", "xccdf_org.ssgproject.content_profile_cis_workstation_l1"),
        ("DISA STIG Benchmark (DoD)", "xccdf_org.ssgproject.content_profile_stig"),
        ("PCI-DSS v3.2.1 / v4.0 Compliance Profile", "xccdf_org.ssgproject.content_profile_pci-dss"),
        ("Standard System Hardening Profile", "xccdf_org.ssgproject.content_profile_standard"),
        ("HIPAA Security Rule Profile", "xccdf_org.ssgproject.content_profile_hipaa"),
        ("Protection Profile for General Purpose OS (OSPP)", "xccdf_org.ssgproject.content_profile_ospp"),
    ]

    def __init__(
        self,
        run_cmd: Callable[[str, Callable[[str, str, int], None]], None],
        theme: dict[str, str],
        parent=None,
    ):
        super().__init__(parent)
        self.run_cmd = run_cmd
        self.theme = theme

        self.distro_info: dict[str, str] = {
            "id": "unknown",
            "family": "generic",
            "pkg_manager": "auto",
            "pretty_name": "Linux",
        }
        self.oscap_info: dict[str, Any] = {}
        self.auditd_info: dict[str, Any] = {}

        # Default report directory: ~/reports
        reports_dir = os.path.expanduser("~/reports")
        try:
            os.makedirs(reports_dir, exist_ok=True)
        except Exception:
            pass
        self._last_report_path = os.path.join(reports_dir, "scap-compliance-report.html")
        self._last_fix_path = os.path.join(reports_dir, "remediation-fix.sh")
        self._current_loaded_ds: str = ""
        self._oscap_is_maximized: bool = False
        self._audit_is_maximized: bool = False

        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        # ---------------- Top Header Bar (Clutter-free Status Strip) ----------------
        top_frame = QFrame()
        top_frame.setStyleSheet(
            f"background: {self.theme.get('panel2', '#222')}; border-radius: 4px; padding: 4px 8px;"
        )
        top_layout = QHBoxLayout(top_frame)
        top_layout.setContentsMargins(4, 2, 4, 2)
        top_layout.setSpacing(8)

        title = QLabel("⚖️ Compliance & Auditing")
        title.setStyleSheet(
            f"font-size: 13px; font-weight: bold; color: {self.theme.get('accent', '#3daee9')};"
        )
        top_layout.addWidget(title)

        # Distro Badge
        self.distro_badge = QLabel("Distro: On Demand")
        self.distro_badge.setStyleSheet(
            f"background: #2a2a2a; border: 1px solid {self.theme.get('border', '#444')}; "
            f"padding: 2px 8px; border-radius: 3px; font-size: 11px;"
        )
        top_layout.addWidget(self.distro_badge)

        # Status Badges
        self.badge_oscap = QLabel("OpenSCAP: On Demand")
        self.badge_auditd = QLabel("Auditd: On Demand")
        for b in (self.badge_oscap, self.badge_auditd):
            b.setStyleSheet(
                f"border: 1px solid {self.theme.get('border', '#444')}; padding: 2px 8px; "
                f"border-radius: 3px; font-size: 11px;"
            )
            top_layout.addWidget(b)

        top_layout.addStretch()

        self.btn_open_reports_dir = QPushButton("📁 Reports Folder")
        self.btn_open_reports_dir.setToolTip("Open ~/reports directory in file manager")
        self.btn_open_reports_dir.setStyleSheet("font-size: 11px; padding: 3px 8px;")
        self.btn_open_reports_dir.clicked.connect(self._open_reports_folder)
        top_layout.addWidget(self.btn_open_reports_dir)

        self.btn_install = QPushButton("📦 Install Compliance Tools")
        self.btn_install.setToolTip("Install OpenSCAP scanner, Security Guide, and Auditd")
        self.btn_install.setStyleSheet("font-size: 11px; padding: 3px 8px;")
        self.btn_install.clicked.connect(self._install_compliance_tools)
        top_layout.addWidget(self.btn_install)

        self.btn_refresh = QPushButton("🔍 Audit Status Now")
        self.btn_refresh.setToolTip("Probe OpenSCAP benchmarks and Auditd service configuration on demand")
        self.btn_refresh.setStyleSheet("font-size: 11px; font-weight: bold; padding: 3px 10px;")
        self.btn_refresh.clicked.connect(self.refresh)
        top_layout.addWidget(self.btn_refresh)

        layout.addWidget(top_frame)

        # ---------------- Tabs: OpenSCAP vs Auditd ----------------
        self.sub_tabs = QTabWidget()

        # Tab 1: OpenSCAP
        self.sub_tabs.addTab(self._build_openscap_tab(), "📋 OpenSCAP (CIS / STIG Benchmark Scanner)")

        # Tab 2: Auditd
        self.sub_tabs.addTab(self._build_auditd_tab(), "📜 Auditd (Event Tracking & Hardening Rules)")

        layout.addWidget(self.sub_tabs, 1)

    # ------------------------------------------------------------
    # 1. OpenSCAP Sub-Tab UI (Clutter-free & Streamlined)
    # ------------------------------------------------------------
    def _build_openscap_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)

        # Compact banner / subtitle for backward compatibility with external inspections
        self.oscap_banner = QLabel(
            "OpenSCAP compliance scanner automates CIS Benchmarks, DISA STIG, and PCI-DSS audits."
        )
        self.oscap_banner.setStyleSheet(
            f"color: {self.theme.get('sub', '#888')}; font-size: 11px; padding: 0px 2px;"
        )
        layout.addWidget(self.oscap_banner)

        # Scan Config Card
        cfg_box = QGroupBox("Audit Scan Parameters")
        cfg_layout = QGridLayout(cfg_box)
        cfg_layout.setContentsMargins(8, 6, 8, 6)
        cfg_layout.setSpacing(6)

        # Row 0: SCAP Datastream selector + Browse XML button + OS match pill
        cfg_layout.addWidget(QLabel("<b>SCAP Datastream:</b>"), 0, 0)
        ds_row = QHBoxLayout()
        ds_row.setSpacing(6)
        self.ds_combo = QComboBox()
        self.ds_combo.setEditable(True)
        self.ds_combo.setMinimumWidth(320)
        self.ds_combo.currentIndexChanged.connect(self._on_datastream_changed)
        self.ds_combo.currentTextChanged.connect(self._on_datastream_changed)
        ds_row.addWidget(self.ds_combo, 1)

        self.btn_browse_ds = QPushButton("📂 Browse XML...")
        self.btn_browse_ds.setToolTip("Select a custom SCAP Datastream or Benchmark XML file from disk")
        self.btn_browse_ds.clicked.connect(self._browse_datastream_file)
        ds_row.addWidget(self.btn_browse_ds)

        self.lbl_ds_match = QLabel("OS: Auto-detecting")
        self.lbl_ds_match.setStyleSheet("color: #888888; font-size: 11px;")
        ds_row.addWidget(self.lbl_ds_match)
        cfg_layout.addLayout(ds_row, 0, 1)

        # Row 1: Compliance Profile selector + Refresh Profiles button
        cfg_layout.addWidget(QLabel("<b>Compliance Profile:</b>"), 1, 0)
        prof_row = QHBoxLayout()
        prof_row.setSpacing(6)
        self.profile_combo = QComboBox()
        self.profile_combo.setEditable(True)
        for label, pid in self.DEFAULT_PROFILES:
            self.profile_combo.addItem(f"{label} ({pid})", pid)
        prof_row.addWidget(self.profile_combo, 1)

        self.btn_reload_profiles = QPushButton("🔄 Reload Profiles")
        self.btn_reload_profiles.setToolTip("Reload compliance profiles from the selected XML file")
        self.btn_reload_profiles.clicked.connect(lambda: self._on_datastream_changed(force=True))
        prof_row.addWidget(self.btn_reload_profiles)
        cfg_layout.addLayout(prof_row, 1, 1)

        # Row 2: Report Output Path in ~/reports
        cfg_layout.addWidget(QLabel("<b>Report Output Path:</b>"), 2, 0)
        rep_row = QHBoxLayout()
        rep_row.setSpacing(6)
        self.report_path_edit = QLineEdit(self._last_report_path)
        self.report_path_edit.setPlaceholderText(self._last_report_path)
        rep_row.addWidget(self.report_path_edit, 1)

        self.btn_browse_report = QPushButton("📂 Browse...")
        self.btn_browse_report.setToolTip("Choose output location in ~/reports")
        self.btn_browse_report.clicked.connect(self._browse_report_path)
        rep_row.addWidget(self.btn_browse_report)

        self.btn_open_reports_btn = QPushButton("📁 ~/reports")
        self.btn_open_reports_btn.setToolTip("Open reports folder in desktop file manager")
        self.btn_open_reports_btn.clicked.connect(self._open_reports_folder)
        rep_row.addWidget(self.btn_open_reports_btn)
        cfg_layout.addLayout(rep_row, 2, 1)

        # Row 3: Remediation Fix Script Option
        cfg_layout.addWidget(QLabel("<b>Fix Remediation:</b>"), 3, 0)
        fix_opt_row = QHBoxLayout()
        fix_opt_row.setSpacing(10)

        self.chk_auto_gen_fix = QCheckBox("Automatically generate remediation fix script on scan completion")
        self.chk_auto_gen_fix.setToolTip(
            "When checked, Admin Suite automatically generates a remediation fix script in ~/reports upon scan completion"
        )
        self.chk_auto_gen_fix.setChecked(True)
        fix_opt_row.addWidget(self.chk_auto_gen_fix)

        fix_opt_row.addWidget(QLabel("Format:"))
        self.fix_format_combo = QComboBox()
        self.fix_format_combo.addItems(["Bash script (.sh)", "Ansible playbook (.yml)"])
        self.fix_format_combo.setToolTip("Select remediation script engine")
        fix_opt_row.addWidget(self.fix_format_combo)
        fix_opt_row.addStretch()
        cfg_layout.addLayout(fix_opt_row, 3, 1)

        layout.addWidget(cfg_box)

        # Action Toolbar Buttons
        btn_bar = QHBoxLayout()
        btn_bar.setSpacing(8)

        self.btn_run_scan = QPushButton("🚀 Run Audit Scan & Generate HTML Report")
        self.btn_run_scan.setStyleSheet(
            f"font-weight: bold; padding: 5px 14px; background: {self.theme.get('accent', '#3daee9')}; color: white;"
        )
        self.btn_run_scan.clicked.connect(self._run_openscap_scan)
        btn_bar.addWidget(self.btn_run_scan)

        self.btn_gen_fix = QPushButton("🛠️ Generate Remediation Fix Script")
        self.btn_gen_fix.setToolTip("Generate standalone bash/ansible remediation script to ~/reports")
        self.btn_gen_fix.clicked.connect(self._generate_fix_script)
        btn_bar.addWidget(self.btn_gen_fix)

        self.btn_open_report = QPushButton("🌐 Open HTML Report in Browser")
        self.btn_open_report.clicked.connect(self._open_html_report)
        btn_bar.addWidget(self.btn_open_report)

        btn_bar.addStretch()

        self.btn_export_scap = QPushButton("📋 Export Summary")
        self.btn_export_scap.clicked.connect(self._export_openscap_summary)
        btn_bar.addWidget(self.btn_export_scap)

        layout.addLayout(btn_bar)

        # Score & Summary Status Pill Bar
        self.oscap_score_card = QFrame()
        self.oscap_score_card.setStyleSheet(
            f"background: {self.theme.get('panel2', '#222')}; border-radius: 4px; padding: 4px 8px;"
        )
        s_layout = QHBoxLayout(self.oscap_score_card)
        s_layout.setContentsMargins(6, 2, 6, 2)
        s_layout.setSpacing(10)

        self.score_gauge = QLabel("Compliance Score: On Demand")
        self.score_gauge.setStyleSheet("font-size: 12px; font-weight: bold; color: #3daee9;")
        s_layout.addWidget(self.score_gauge)

        self.badge_pass = QLabel("Passed: 0")
        self.badge_pass.setStyleSheet("color: #44eb74; font-weight: bold; font-size: 11px;")
        s_layout.addWidget(self.badge_pass)

        self.badge_fail = QLabel("Failed: 0")
        self.badge_fail.setStyleSheet("color: #eb4444; font-weight: bold; font-size: 11px;")
        s_layout.addWidget(self.badge_fail)

        self.badge_na = QLabel("Not Applicable: 0")
        self.badge_na.setStyleSheet("color: #888888; font-size: 11px;")
        s_layout.addWidget(self.badge_na)

        self.badge_err = QLabel("Errors: 0")
        self.badge_err.setStyleSheet("color: #e67e22; font-size: 11px;")
        s_layout.addWidget(self.badge_err)

        s_layout.addStretch()
        layout.addWidget(self.oscap_score_card)

        # Splitter: Rule Evaluation Table vs Terminal Output
        self.oscap_splitter = QSplitter(Qt.Orientation.Vertical)
        self.oscap_splitter.setChildrenCollapsible(False)
        self.oscap_splitter.setHandleWidth(8)
        self.oscap_splitter.setStyleSheet(
            f"QSplitter::handle:vertical {{"
            f"  background: {self.theme.get('border', '#444')};"
            f"  height: 8px;"
            f"  border-radius: 2px;"
            f"}}"
            f"QSplitter::handle:vertical:hover {{"
            f"  background: {self.theme.get('accent', '#3daee9')};"
            f"}}"
        )

        # Evaluated Rules Table
        t_container = QWidget()
        t_container.setMinimumHeight(100)
        t_layout = QVBoxLayout(t_container)
        t_layout.setContentsMargins(0, 0, 0, 0)
        t_layout.setSpacing(4)
        t_layout.addWidget(QLabel("<b>Benchmark Rule Evaluations:</b>"))

        self.oscap_rules_table = QTableWidget(0, 2)
        self.oscap_rules_table.setHorizontalHeaderLabels(["Result", "Benchmark Rule Identifier"])
        self.oscap_rules_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.oscap_rules_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.oscap_rules_table.setAlternatingRowColors(True)
        self.oscap_rules_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        t_layout.addWidget(self.oscap_rules_table)
        self.oscap_splitter.addWidget(t_container)

        # Raw Output & Remediation Script Viewer with Height Controls
        r_container = QWidget()
        r_container.setMinimumHeight(60)
        r_layout = QVBoxLayout(r_container)
        r_layout.setContentsMargins(0, 0, 0, 0)
        r_layout.setSpacing(4)

        self.oscap_raw_edit = QPlainTextEdit()
        self.oscap_raw_edit.setFont(QFont("Monospace", 9))
        self.oscap_raw_edit.setReadOnly(True)
        self.oscap_raw_edit.setMinimumHeight(40)

        r_head = QHBoxLayout()
        r_head.setContentsMargins(0, 0, 0, 0)
        r_head.setSpacing(6)

        r_title = QLabel("<b>Scan Terminal Output / Remediation Script:</b>")
        r_head.addWidget(r_title)
        r_head.addStretch()

        lbl_h = QLabel("↕ Output Height:")
        lbl_h.setStyleSheet("font-size: 11px; color: #888888;")
        r_head.addWidget(lbl_h)

        btn_h_compact = QPushButton("Compact")
        btn_h_compact.setStyleSheet("font-size: 10px; padding: 2px 6px;")
        btn_h_compact.setToolTip("Set terminal output to compact height (120px)")
        btn_h_compact.clicked.connect(lambda: self._set_output_height(self.oscap_splitter, "compact"))
        r_head.addWidget(btn_h_compact)

        btn_h_normal = QPushButton("Normal")
        btn_h_normal.setStyleSheet("font-size: 10px; padding: 2px 6px;")
        btn_h_normal.setToolTip("Set terminal output to standard height (240px)")
        btn_h_normal.clicked.connect(lambda: self._set_output_height(self.oscap_splitter, "normal"))
        r_head.addWidget(btn_h_normal)

        btn_h_large = QPushButton("Large")
        btn_h_large.setStyleSheet("font-size: 10px; padding: 2px 6px;")
        btn_h_large.setToolTip("Set terminal output to expanded height (400px)")
        btn_h_large.clicked.connect(lambda: self._set_output_height(self.oscap_splitter, "large"))
        r_head.addWidget(btn_h_large)

        self.btn_oscap_max = QPushButton("⤢ Maximize")
        self.btn_oscap_max.setStyleSheet("font-size: 10px; padding: 2px 6px;")
        self.btn_oscap_max.setToolTip("Toggle maximize / restore terminal output")
        self.btn_oscap_max.clicked.connect(self._toggle_oscap_maximize)
        r_head.addWidget(self.btn_oscap_max)

        btn_copy = QPushButton("📋 Copy")
        btn_copy.setStyleSheet("font-size: 10px; padding: 2px 6px;")
        btn_copy.clicked.connect(lambda: QApplication.clipboard().setText(self.oscap_raw_edit.toPlainText()))
        r_head.addWidget(btn_copy)

        btn_clear = QPushButton("🗑️ Clear")
        btn_clear.setStyleSheet("font-size: 10px; padding: 2px 6px;")
        btn_clear.clicked.connect(self.oscap_raw_edit.clear)
        r_head.addWidget(btn_clear)

        r_layout.addLayout(r_head)
        r_layout.addWidget(self.oscap_raw_edit)
        self.oscap_splitter.addWidget(r_container)

        self.oscap_splitter.setStretchFactor(0, 3)
        self.oscap_splitter.setStretchFactor(1, 2)
        self.oscap_splitter.setSizes([320, 220])
        layout.addWidget(self.oscap_splitter, 1)

        return widget

    # ------------------------------------------------------------
    # 2. Auditd Sub-Tab UI (Clutter-free & Streamlined)
    # ------------------------------------------------------------
    def _build_auditd_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)

        # Status & Controls Card
        card = QFrame()
        card.setStyleSheet(
            f"background: {self.theme.get('panel2', '#222')}; border-radius: 4px; padding: 6px;"
        )
        card_layout = QGridLayout(card)
        card_layout.setContentsMargins(8, 6, 8, 6)
        card_layout.setSpacing(8)

        self.auditd_state_lbl = QLabel("Service: On Demand")
        self.auditd_rules_lbl = QLabel("Loaded Rules: On Demand")
        self.auditd_status_lbl = QLabel("Kernel Daemon: On Demand")

        for lbl in (self.auditd_state_lbl, self.auditd_rules_lbl, self.auditd_status_lbl):
            lbl.setStyleSheet("font-size: 11px;")

        card_layout.addWidget(QLabel("<b>Audit Service:</b>"), 0, 0)
        card_layout.addWidget(self.auditd_state_lbl, 0, 1)
        card_layout.addWidget(QLabel("<b>Active Rules:</b>"), 0, 2)
        card_layout.addWidget(self.auditd_rules_lbl, 0, 3)

        card_layout.addWidget(QLabel("<b>Daemon State:</b>"), 1, 0)
        card_layout.addWidget(self.auditd_status_lbl, 1, 1, 1, 3)

        layout.addWidget(card)

        # Service Control Buttons Bar
        s_bar = QHBoxLayout()
        s_bar.setSpacing(6)

        self.btn_auditd_toggle = QPushButton("⚡ Enable & Start Auditd")
        self.btn_auditd_toggle.clicked.connect(self._toggle_auditd_service)
        s_bar.addWidget(self.btn_auditd_toggle)

        self.btn_auditd_reload = QPushButton("🔄 Reload Rules (augenrules --load)")
        self.btn_auditd_reload.clicked.connect(self._reload_auditd_rules)
        s_bar.addWidget(self.btn_auditd_reload)

        self.btn_auditd_view_rules = QPushButton("📋 View Active Rules")
        self.btn_auditd_view_rules.clicked.connect(self._view_loaded_rules)
        s_bar.addWidget(self.btn_auditd_view_rules)

        s_bar.addStretch()
        layout.addLayout(s_bar)

        # Targeted Security Rule Configuration GroupBox
        rule_box = QGroupBox("Targeted Security-Relevant Audit Rules (Configure & Deploy)")
        rule_layout = QVBoxLayout(rule_box)
        rule_layout.setContentsMargins(8, 6, 8, 6)
        rule_layout.setSpacing(4)

        # 1. User Accounts & Auth
        self.chk_rule_accounts = QCheckBox(
            "👤 <b>Track User Account & Authentication Modifications</b> "
            "(/etc/passwd, /etc/shadow, /etc/group, /etc/gshadow, /var/log/faillog, /var/log/lastlog)"
        )
        self.chk_rule_accounts.setChecked(True)
        rule_layout.addWidget(self.chk_rule_accounts)

        # 2. Permission Modifications
        self.chk_rule_perms = QCheckBox(
            "🔑 <b>Track File Permission & Ownership Modifications</b> "
            "(chmod, fchmod, fchmodat, chown, fchown, lchown, setxattr, removexattr syscalls)"
        )
        self.chk_rule_perms.setChecked(True)
        rule_layout.addWidget(self.chk_rule_perms)

        # 3. Access to Sensitive Files
        self.chk_rule_sensitive = QCheckBox(
            "📁 <b>Track Access to Sensitive Files & Configurations</b> "
            "(/etc/sudoers, /etc/sudoers.d, /etc/ssh/sshd_config, /etc/pam.d, /etc/crontab)"
        )
        self.chk_rule_sensitive.setChecked(True)
        rule_layout.addWidget(self.chk_rule_sensitive)

        # 4. Full CIS Preset
        self.chk_rule_cis = QCheckBox(
            "🛡️ <b>Include CIS Benchmark Kernel & System Clock Audits</b> "
            "(kernel module insertions init_module/delete_module, adjtimex, settimeofday)"
        )
        self.chk_rule_cis.setChecked(False)
        rule_layout.addWidget(self.chk_rule_cis)

        # Deploy Button
        dep_bar = QHBoxLayout()
        self.btn_deploy_rules = QPushButton("🚀 Deploy Selected Audit Rules to /etc/audit/rules.d/99-admin-suite.rules")
        self.btn_deploy_rules.setStyleSheet("font-weight: bold; padding: 4px 10px;")
        self.btn_deploy_rules.clicked.connect(self._deploy_selected_audit_rules)
        dep_bar.addWidget(self.btn_deploy_rules)
        dep_bar.addStretch()
        rule_layout.addLayout(dep_bar)

        layout.addWidget(rule_box)

        # Event Search & Forensic Log Inspector
        ev_box = QGroupBox("Audit Log Inspector & Forensic Search (ausearch / aureport)")
        ev_layout = QVBoxLayout(ev_box)
        ev_layout.setContentsMargins(8, 6, 8, 6)
        ev_layout.setSpacing(6)

        filter_bar = QHBoxLayout()
        filter_bar.setSpacing(6)

        btn_f_acc = QPushButton("👤 User Accounts")
        btn_f_acc.clicked.connect(lambda: self._search_audit_events(key="user_account_changes"))
        filter_bar.addWidget(btn_f_acc)

        btn_f_perm = QPushButton("🔑 Permission Mods")
        btn_f_perm.clicked.connect(lambda: self._search_audit_events(key="perm_mod"))
        filter_bar.addWidget(btn_f_perm)

        btn_f_sens = QPushButton("📁 Sensitive Files")
        btn_f_sens.clicked.connect(lambda: self._search_audit_events(key="sudoers_changes"))
        filter_bar.addWidget(btn_f_sens)

        btn_f_auth = QPushButton("🚨 Failed Logins")
        btn_f_auth.clicked.connect(lambda: self._search_audit_events(report="failed"))
        filter_bar.addWidget(btn_f_auth)

        btn_f_sum = QPushButton("📊 Summary Report")
        btn_f_sum.clicked.connect(lambda: self._search_audit_events(report="summary"))
        filter_bar.addWidget(btn_f_sum)

        filter_bar.addStretch()

        filter_bar.addWidget(QLabel("Time Range:"))
        self.time_combo = QComboBox()
        self.time_combo.addItems(["today", "recent", "this-week", "all"])
        filter_bar.addWidget(self.time_combo)

        ev_layout.addLayout(filter_bar)

        # Splitter: Audit Events Table vs Raw Inspector
        self.audit_splitter = QSplitter(Qt.Orientation.Vertical)
        self.audit_splitter.setChildrenCollapsible(False)
        self.audit_splitter.setHandleWidth(8)
        self.audit_splitter.setStyleSheet(
            f"QSplitter::handle:vertical {{"
            f"  background: {self.theme.get('border', '#444')};"
            f"  height: 8px;"
            f"  border-radius: 2px;"
            f"}}"
            f"QSplitter::handle:vertical:hover {{"
            f"  background: {self.theme.get('accent', '#3daee9')};"
            f"}}"
        )

        ev_table_container = QWidget()
        ev_table_container.setMinimumHeight(100)
        ev_t_layout = QVBoxLayout(ev_table_container)
        ev_t_layout.setContentsMargins(0, 0, 0, 0)

        self.audit_events_table = QTableWidget(0, 6)
        self.audit_events_table.setHorizontalHeaderLabels(
            ["Timestamp", "Type", "Key / Tag", "Executable / Syscall", "Target Name", "Result"]
        )
        self.audit_events_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.audit_events_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.audit_events_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.audit_events_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.audit_events_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.audit_events_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        self.audit_events_table.setAlternatingRowColors(True)
        self.audit_events_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        ev_t_layout.addWidget(self.audit_events_table)
        self.audit_splitter.addWidget(ev_table_container)

        # Audit Raw Output Container with Height Controls
        a_container = QWidget()
        a_container.setMinimumHeight(60)
        a_layout = QVBoxLayout(a_container)
        a_layout.setContentsMargins(0, 0, 0, 0)
        a_layout.setSpacing(4)

        self.auditd_raw_edit = QPlainTextEdit()
        self.auditd_raw_edit.setFont(QFont("Monospace", 9))
        self.auditd_raw_edit.setReadOnly(True)
        self.auditd_raw_edit.setMinimumHeight(40)

        a_head = QHBoxLayout()
        a_head.setContentsMargins(0, 0, 0, 0)
        a_head.setSpacing(6)
        a_head.addWidget(QLabel("<b>Raw Audit Inspector Output:</b>"))
        a_head.addStretch()

        lbl_ah = QLabel("↕ Output Height:")
        lbl_ah.setStyleSheet("font-size: 11px; color: #888888;")
        a_head.addWidget(lbl_ah)

        btn_ah_compact = QPushButton("Compact")
        btn_ah_compact.setStyleSheet("font-size: 10px; padding: 2px 6px;")
        btn_ah_compact.clicked.connect(lambda: self._set_output_height(self.audit_splitter, "compact"))
        a_head.addWidget(btn_ah_compact)

        btn_ah_normal = QPushButton("Normal")
        btn_ah_normal.setStyleSheet("font-size: 10px; padding: 2px 6px;")
        btn_ah_normal.clicked.connect(lambda: self._set_output_height(self.audit_splitter, "normal"))
        a_head.addWidget(btn_ah_normal)

        btn_ah_large = QPushButton("Large")
        btn_ah_large.setStyleSheet("font-size: 10px; padding: 2px 6px;")
        btn_ah_large.clicked.connect(lambda: self._set_output_height(self.audit_splitter, "large"))
        a_head.addWidget(btn_ah_large)

        self.btn_audit_max = QPushButton("⤢ Maximize")
        self.btn_audit_max.setStyleSheet("font-size: 10px; padding: 2px 6px;")
        self.btn_audit_max.clicked.connect(self._toggle_audit_maximize)
        a_head.addWidget(self.btn_audit_max)

        btn_acopy = QPushButton("📋 Copy")
        btn_acopy.setStyleSheet("font-size: 10px; padding: 2px 6px;")
        btn_acopy.clicked.connect(lambda: QApplication.clipboard().setText(self.auditd_raw_edit.toPlainText()))
        a_head.addWidget(btn_acopy)

        btn_aclear = QPushButton("🗑️ Clear")
        btn_aclear.setStyleSheet("font-size: 10px; padding: 2px 6px;")
        btn_aclear.clicked.connect(self.auditd_raw_edit.clear)
        a_head.addWidget(btn_aclear)

        a_layout.addLayout(a_head)
        a_layout.addWidget(self.auditd_raw_edit)
        self.audit_splitter.addWidget(a_container)

        self.audit_splitter.setStretchFactor(0, 3)
        self.audit_splitter.setStretchFactor(1, 2)
        self.audit_splitter.setSizes([300, 200])
        ev_layout.addWidget(self.audit_splitter, 1)

        layout.addWidget(ev_box, 1)

        return widget

    # ------------------------------------------------------------
    # Terminal Output Height Control Handlers
    # ------------------------------------------------------------
    def _set_output_height(self, splitter: QSplitter, mode: str) -> None:
        total = sum(splitter.sizes()) or 540
        if mode == "compact":
            splitter.setSizes([max(100, total - 120), 120])
        elif mode == "normal":
            splitter.setSizes([max(100, total - 240), 240])
        elif mode == "large":
            splitter.setSizes([max(100, total - 400), 400])

    def _toggle_oscap_maximize(self) -> None:
        total = sum(self.oscap_splitter.sizes()) or 540
        if getattr(self, "_oscap_is_maximized", False):
            self.oscap_splitter.setSizes([int(total * 0.6), int(total * 0.4)])
            self._oscap_is_maximized = False
            self.btn_oscap_max.setText("⤢ Maximize")
        else:
            self.oscap_splitter.setSizes([70, max(100, total - 70)])
            self._oscap_is_maximized = True
            self.btn_oscap_max.setText("⤡ Restore")

    def _toggle_audit_maximize(self) -> None:
        total = sum(self.audit_splitter.sizes()) or 500
        if getattr(self, "_audit_is_maximized", False):
            self.audit_splitter.setSizes([int(total * 0.6), int(total * 0.4)])
            self._audit_is_maximized = False
            self.btn_audit_max.setText("⤢ Maximize")
        else:
            self.audit_splitter.setSizes([70, max(100, total - 70)])
            self._audit_is_maximized = True
            self.btn_audit_max.setText("⤡ Restore")

    # ------------------------------------------------------------
    # Refresh & Discovery Handlers
    # ------------------------------------------------------------
    def refresh(self) -> None:
        """Probes host OS, OpenSCAP datastreams, and Auditd status."""
        self.run_cmd(SecurityCommands.detect_os(), self._on_os_detected)
        self.run_cmd(SecurityCommands.openscap_probe(), self._on_openscap_probe)
        self.run_cmd(SecurityCommands.auditd_probe(), self._on_auditd_probe)

    def _on_os_detected(self, stdout: str, stderr: str, code: int) -> None:
        self.distro_info = SecurityParsers.parse_os_release(stdout)
        fam = self.distro_info.get("family", "generic").upper()
        name = self.distro_info.get("pretty_name") or self.distro_info.get("name") or "Linux"
        pkg_m = self.distro_info.get("pkg_manager", "auto")

        self.distro_badge.setText(f"OS: {name} ({fam}) | Pkg: {pkg_m}")
        if fam == "RHEL":
            self.distro_badge.setStyleSheet(
                "background: #4a1515; color: #ff8a80; border: 1px solid #c62828; padding: 2px 8px; border-radius: 3px; font-size: 11px;"
            )
        elif fam == "DEBIAN":
            self.distro_badge.setStyleSheet(
                "background: #311b4d; color: #ea80fc; border: 1px solid #7b1fa2; padding: 2px 8px; border-radius: 3px; font-size: 11px;"
            )
        else:
            self.distro_badge.setStyleSheet(
                "background: #2a2a2a; border: 1px solid #555; padding: 2px 8px; border-radius: 3px; font-size: 11px;"
            )

        # Trigger intelligent auto-selection of the datastream matching host OS
        self._auto_select_best_datastream()

    def _on_openscap_probe(self, stdout: str, stderr: str, code: int) -> None:
        self.oscap_info = SecurityParsers.parse_openscap_probe(stdout)

        inst = self.oscap_info.get("installed")
        ds_list = list(self.oscap_info.get("datastreams", []))

        # Also search local SCAP directories if accessible on disk
        local_search_dirs = [
            "/usr/share/xml/scap/ssg/content",
            "/usr/share/scap-security-guide",
            "/usr/share/xml/scap",
            "/usr/share/openscap",
            "/var/lib/openscap",
        ]
        for sdir in local_search_dirs:
            if os.path.isdir(sdir):
                try:
                    for root, _, files in os.walk(sdir):
                        for f in files:
                            if f.endswith(".xml") and ("-ds" in f or "xccdf" in f or "datastream" in f):
                                full_p = os.path.join(root, f)
                                if full_p not in ds_list:
                                    ds_list.append(full_p)
                except Exception:
                    pass

        ds_list = sorted(set(ds_list))

        # Top Badge
        if inst:
            self.badge_oscap.setText(f"OpenSCAP: ● Ready ({len(ds_list)} DS)")
            self.badge_oscap.setStyleSheet(
                "background: #1b4d24; color: #44eb74; padding: 2px 8px; border-radius: 3px; font-size: 11px;"
            )
        else:
            self.badge_oscap.setText("OpenSCAP: ○ Not Installed")
            self.badge_oscap.setStyleSheet(
                "background: #4d1b1b; color: #eb4444; padding: 2px 8px; border-radius: 3px; font-size: 11px;"
            )

        # Update Datastream ComboBox
        cur_ds = self.ds_combo.currentText().strip()
        self.ds_combo.blockSignals(True)
        self.ds_combo.clear()

        # Add all detected datastreams
        for ds in ds_list:
            self.ds_combo.addItem(ds)

        # Fallback defaults if list was empty
        if not ds_list:
            fallback_ds = [
                "/usr/share/xml/scap/ssg/content/ssg-debian12-ds.xml",
                "/usr/share/xml/scap/ssg/content/ssg-debian11-ds.xml",
                "/usr/share/xml/scap/ssg/content/ssg-ubuntu2204-ds.xml",
                "/usr/share/xml/scap/ssg/content/ssg-rhel9-ds.xml",
                "/usr/share/xml/scap/ssg/content/ssg-rhel8-ds.xml",
                "/usr/share/xml/scap/ssg/content/ssg-rocky9-ds.xml",
                "/usr/share/xml/scap/ssg/content/ssg-fedora-ds.xml",
            ]
            for ds in fallback_ds:
                self.ds_combo.addItem(ds)

        self.ds_combo.blockSignals(False)

        # Restore previous user selection or auto-select best candidate
        if cur_ds and self.ds_combo.findText(cur_ds) >= 0:
            self.ds_combo.setCurrentText(cur_ds)
            self._on_datastream_changed()
        else:
            self._auto_select_best_datastream()

        # Ensure default reports directory in ~/reports
        reports_dir = os.path.expanduser("~/reports")
        try:
            os.makedirs(reports_dir, exist_ok=True)
        except Exception:
            pass
        cur_p = self.report_path_edit.text().strip()
        if not cur_p or "/var/www/html" in cur_p or "/tmp" in cur_p:
            self.report_path_edit.setText(f"{reports_dir}/scap-compliance-report.html")

    def _auto_select_best_datastream(self) -> None:
        """Selects the best matching SCAP datastream for the detected distribution."""
        dist_id = (self.distro_info.get("id") or "").lower()
        dist_fam = (self.distro_info.get("family") or "").lower()
        dist_ver = (self.distro_info.get("version_id") or "").lower()
        ver_major = dist_ver.split(".")[0] if dist_ver else ""
        ver_clean = dist_ver.replace(".", "")

        candidates = []
        if dist_id:
            if ver_major:
                candidates.append(f"ssg-{dist_id}{ver_major}-ds.xml")
            if ver_clean:
                candidates.append(f"ssg-{dist_id}{ver_clean}-ds.xml")
            candidates.append(f"ssg-{dist_id}-ds.xml")
            candidates.append(f"-{dist_id}")
        if dist_fam and dist_fam != dist_id:
            if ver_major:
                candidates.append(f"ssg-{dist_fam}{ver_major}-ds.xml")
            candidates.append(f"ssg-{dist_fam}-ds.xml")

        matched_idx = -1
        for cand in candidates:
            for idx in range(self.ds_combo.count()):
                text = self.ds_combo.itemText(idx).lower()
                if cand in text:
                    matched_idx = idx
                    break
            if matched_idx >= 0:
                break

        if matched_idx >= 0:
            self.ds_combo.setCurrentIndex(matched_idx)
            matched_name = os.path.basename(self.ds_combo.itemText(matched_idx))
            self.lbl_ds_match.setText(f"🎯 Auto-matched: {matched_name}")
            self.lbl_ds_match.setStyleSheet("color: #44eb74; font-size: 11px;")
        elif self.ds_combo.count() > 0:
            self.ds_combo.setCurrentIndex(0)
            self.lbl_ds_match.setText("ℹ️ Default datastream selected")
            self.lbl_ds_match.setStyleSheet("color: #888888; font-size: 11px;")

        # Populate compliance profiles for the selected XML file
        self._on_datastream_changed()

    def _on_auditd_probe(self, stdout: str, stderr: str, code: int) -> None:
        self.auditd_info = SecurityParsers.parse_auditd_probe(stdout)

        inst = self.auditd_info.get("installed")
        state = self.auditd_info.get("service_state", "inactive")
        rcount = self.auditd_info.get("rules_count", 0)

        # Top Badge
        if inst:
            if state == "active":
                self.badge_auditd.setText(f"Auditd: ● Active ({rcount} rules)")
                self.badge_auditd.setStyleSheet(
                    "background: #1b4d24; color: #44eb74; padding: 2px 8px; border-radius: 3px; font-size: 11px;"
                )
            else:
                self.badge_auditd.setText("Auditd: ○ Inactive")
                self.badge_auditd.setStyleSheet(
                    "background: #4d1b1b; color: #eb4444; padding: 2px 8px; border-radius: 3px; font-size: 11px;"
                )
        else:
            self.badge_auditd.setText("Auditd: ○ Not Installed")
            self.badge_auditd.setStyleSheet(
                "background: #333333; color: #888888; padding: 2px 8px; border-radius: 3px; font-size: 11px;"
            )

        # Card Labels
        self.auditd_state_lbl.setText(f"{state.upper()} (Enabled: {self.auditd_info.get('service_enabled', 'disabled')})")
        self.btn_auditd_toggle.setText("⛔ Stop Auditd" if state == "active" else "⚡ Enable & Start Auditd")
        self.auditd_rules_lbl.setText(f"{rcount} loaded rules")

        st_text = self.auditd_info.get("status_text", "").strip() or "Daemon status available via auditctl -s"
        self.auditd_status_lbl.setText(st_text.splitlines()[0] if st_text else "Ready")

    def _on_datastream_changed(self, force: bool = False) -> None:
        """Triggered when the user selects or types a SCAP datastream XML file."""
        ds = self.ds_combo.currentText().strip()
        if not ds:
            return

        if not force and getattr(self, "_current_loaded_ds", None) == ds:
            return
        self._current_loaded_ds = ds

        self.profile_combo.blockSignals(True)
        self.profile_combo.clear()
        self.profile_combo.addItem("⏳ Loading compliance profiles from XML...", "")
        self.profile_combo.blockSignals(False)

        cmd = SecurityCommands.openscap_list_profiles(ds)
        self.run_cmd(cmd, self._on_profiles_listed)

    def _on_profiles_listed(self, stdout: str, stderr: str, code: int) -> None:
        profs = SecurityParsers.parse_openscap_profiles(stdout)
        self.profile_combo.blockSignals(True)
        self.profile_combo.clear()

        if profs:
            for p in profs:
                self.profile_combo.addItem(f"{p['title']} ({p['id']})", p["id"])

            # Recommended default preselection: CIS Server or standard profile
            sel_idx = 0
            for i in range(self.profile_combo.count()):
                t = self.profile_combo.itemText(i).lower()
                data = str(self.profile_combo.itemData(i) or "").lower()
                if "cis" in t and "server" in t:
                    sel_idx = i
                    break
                elif "cis" in t or "cis" in data:
                    sel_idx = i
                elif "standard" in t and sel_idx == 0:
                    sel_idx = i

            self.profile_combo.setCurrentIndex(sel_idx)
        else:
            # Fallback to defaults
            for label, pid in self.DEFAULT_PROFILES:
                self.profile_combo.addItem(f"{label} ({pid})", pid)

        self.profile_combo.blockSignals(False)

    def _browse_datastream_file(self) -> None:
        """Opens a file dialog to pick any SCAP XML datastream on disk."""
        init_dir = "/usr/share/xml/scap" if os.path.isdir("/usr/share/xml/scap") else os.path.expanduser("~")
        chosen, _ = QFileDialog.getOpenFileName(
            self,
            "Select SCAP Datastream XML File",
            init_dir,
            "SCAP Datastreams (*.xml);;All Files (*)",
        )
        if chosen:
            if self.ds_combo.findText(chosen) < 0:
                self.ds_combo.insertItem(0, chosen)
            self.ds_combo.setCurrentText(chosen)
            self.lbl_ds_match.setText(f"📁 Custom: {os.path.basename(chosen)}")
            self.lbl_ds_match.setStyleSheet("color: #3daee9; font-size: 11px;")
            self._on_datastream_changed(force=True)

    def _browse_report_path(self) -> None:
        """Opens a file save dialog for HTML report in ~/reports."""
        init_path = self.report_path_edit.text().strip() or os.path.expanduser("~/reports/scap-compliance-report.html")
        chosen, _ = QFileDialog.getSaveFileName(
            self,
            "Save SCAP Compliance HTML Report",
            init_path,
            "HTML Report Files (*.html);;All Files (*)",
        )
        if chosen:
            self.report_path_edit.setText(chosen)

    def _open_reports_folder(self) -> None:
        """Opens ~/reports directory in user's file manager."""
        rep_dir = os.path.expanduser("~/reports")
        try:
            os.makedirs(rep_dir, exist_ok=True)
        except Exception:
            pass

        if os.path.exists(rep_dir):
            QDesktopServices.openUrl(QUrl.fromLocalFile(rep_dir))
        else:
            self.run_cmd(
                f"mkdir -p ~/reports && (xdg-open ~/reports 2>/dev/null || open ~/reports 2>/dev/null || true)",
                lambda o, e, c: None,
            )

    # ------------------------------------------------------------
    # Install Missing Packages
    # ------------------------------------------------------------
    def _install_compliance_tools(self) -> None:
        fam = self.distro_info.get("family", "generic").lower()
        pkg_m = self.distro_info.get("pkg_manager", "auto")

        if fam == "rhel":
            pkgs = ["scap-security-guide", "openscap-scanner", "audit", "audit-libs"]
        else:
            pkgs = ["openscap-scanner", "ssg-debian", "auditd", "audispd-plugins"]

        cmd = SecurityCommands.install_packages_cmd(pkgs, pkg_manager=pkg_m)

        confirm = QMessageBox.question(
            self,
            "Install Compliance Tools",
            f"Install OpenSCAP and Auditd packages for {self.distro_info.get('pretty_name', 'Linux')}?\n\n"
            f"Packages: {', '.join(pkgs)}\nCommand: {cmd}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm == QMessageBox.StandardButton.Yes:
            self.score_gauge.setText("Installing tools... Please wait.")
            self.run_cmd(cmd, lambda o, e, c: self.refresh())

    # ------------------------------------------------------------
    # OpenSCAP Actions
    # ------------------------------------------------------------
    def _run_openscap_scan(self) -> None:
        ds = self.ds_combo.currentText().strip()
        if not ds:
            QMessageBox.warning(self, "OpenSCAP Scan", "Please specify a SCAP datastream XML file.")
            return

        profile = self.profile_combo.currentData() or self.profile_combo.currentText().strip()
        if "(" in profile and ")" in profile:
            profile = profile.split("(")[-1].strip(")")
        if not profile:
            profile = "xccdf_org.ssgproject.content_profile_cis"

        rep_path = self.report_path_edit.text().strip() or os.path.expanduser("~/reports/scap-compliance-report.html")
        self._last_report_path = rep_path

        cmd = SecurityCommands.openscap_eval(profile=profile, datastream_path=ds, report_path=rep_path)

        self.score_gauge.setText("Scanning system against compliance benchmark... Please wait.")
        self.btn_run_scan.setEnabled(False)

        def _on_scan_done(out: str, err: str, code: int):
            self.btn_run_scan.setEnabled(True)
            raw = out + ("\n" + err if err else "")
            self.oscap_raw_edit.setPlainText(raw)

            data = SecurityParsers.parse_openscap_eval_output(raw)
            score = data.get("score", 0.0)
            passed = data.get("passed", 0)
            failed = data.get("failed", 0)
            na = data.get("notapplicable", 0)
            err_count = data.get("error", 0)

            score_color = "#44eb74" if score >= 80 else ("#ebaa44" if score >= 50 else "#eb4444")
            self.score_gauge.setText(f"Compliance Score: {score}%")
            self.score_gauge.setStyleSheet(f"font-size: 12px; font-weight: bold; color: {score_color};")

            self.badge_pass.setText(f"Passed: {passed}")
            self.badge_fail.setText(f"Failed: {failed}")
            self.badge_na.setText(f"Not Applicable: {na}")
            self.badge_err.setText(f"Errors: {err_count}")

            self._populate_openscap_rules(data.get("rules", []))

            # Auto-generate remediation fix script if option is selected
            fix_msg = ""
            if self.chk_auto_gen_fix.isChecked():
                fmt_text = self.fix_format_combo.currentText().lower()
                fix_type = "ansible" if "ansible" in fmt_text else "bash"
                fix_ext = "yml" if fix_type == "ansible" else "sh"
                reports_dir = os.path.expanduser("~/reports")
                fix_out = os.path.join(reports_dir, f"remediation-fix.{fix_ext}")
                self._last_fix_path = fix_out

                fix_cmd = SecurityCommands.openscap_generate_fix(
                    profile=profile,
                    datastream_path=ds,
                    fix_type=fix_type,
                    output_path=fix_out,
                )

                def _on_fix_done(f_out: str, f_err: str, f_code: int):
                    if f_out and not self.oscap_raw_edit.toPlainText().startswith("# Remediation"):
                        # Append remediation preview to raw output
                        current = self.oscap_raw_edit.toPlainText()
                        self.oscap_raw_edit.setPlainText(
                            current + f"\n\n{'='*50}\n# Generated Remediation Script ({fix_out})\n{'='*50}\n" + f_out
                        )

                self.run_cmd(fix_cmd, _on_fix_done)
                fix_msg = f"\n\n🛠️ Remediation script saved to:\n{fix_out}"

            QMessageBox.information(
                self,
                "OpenSCAP Scan Complete",
                f"Compliance scan completed.\n\nScore: {score}%\n"
                f"Passed: {passed} | Failed: {failed} | N/A: {na}\n\n"
                f"HTML Report generated at:\n{rep_path}{fix_msg}",
            )

        self.run_cmd(cmd, _on_scan_done)

    def _populate_openscap_rules(self, rules: list[dict[str, str]]) -> None:
        self.oscap_rules_table.setRowCount(len(rules))
        for row, r in enumerate(rules):
            res = r.get("result", "")
            item_res = QTableWidgetItem(res.upper())
            item_res.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if res == "pass":
                item_res.setForeground(QColor("#44eb74"))
            elif res == "fail":
                item_res.setForeground(QColor("#eb4444"))
            elif res in ("notapplicable", "na"):
                item_res.setForeground(QColor("#888888"))
            else:
                item_res.setForeground(QColor("#e67e22"))
            self.oscap_rules_table.setItem(row, 0, item_res)

            item_id = QTableWidgetItem(r.get("id", ""))
            self.oscap_rules_table.setItem(row, 1, item_id)

    def _open_html_report(self) -> None:
        p = self.report_path_edit.text().strip() or self._last_report_path
        expanded = os.path.expanduser(p)
        if os.path.exists(expanded):
            QDesktopServices.openUrl(QUrl.fromLocalFile(expanded))
        else:
            self.run_cmd(
                f"xdg-open {shlex.quote(expanded)} 2>/dev/null || open {shlex.quote(expanded)} 2>/dev/null || echo 'Open in browser: file://{expanded}'",
                lambda o, e, c: None,
            )
            QMessageBox.information(
                self,
                "HTML Report",
                f"HTML report location on target system:\nfile://{expanded}\n\n"
                "If running remotely via SSH, you can download the report from ~/reports/ or your configured path.",
            )

    def _generate_fix_script(self) -> None:
        ds = self.ds_combo.currentText().strip()
        profile = self.profile_combo.currentData() or self.profile_combo.currentText().strip()
        if "(" in profile and ")" in profile:
            profile = profile.split("(")[-1].strip(")")

        fmt_text = self.fix_format_combo.currentText().lower()
        fix_type = "ansible" if "ansible" in fmt_text else "bash"
        fix_ext = "yml" if fix_type == "ansible" else "sh"
        reports_dir = os.path.expanduser("~/reports")
        try:
            os.makedirs(reports_dir, exist_ok=True)
        except Exception:
            pass
        out_path = os.path.join(reports_dir, f"remediation-fix.{fix_ext}")
        self._last_fix_path = out_path

        self.oscap_raw_edit.setPlainText(
            f"Generating {fix_type.upper()} remediation script from profile: {profile}...\nSaving to: {out_path}\nPlease wait..."
        )

        def _on_fix_done(out: str, err: str, code: int):
            content = out + ("\n" + err if err else "")
            self.oscap_raw_edit.setPlainText(content)
            QMessageBox.information(
                self,
                "Remediation Script Generated",
                f"Successfully generated {fix_type.upper()} remediation script.\n\n"
                f"Saved to:\n{out_path}\n\n"
                "You can inspect or copy the full script in the terminal viewer below.",
            )

        self.run_cmd(
            SecurityCommands.openscap_generate_fix(
                profile=profile,
                datastream_path=ds,
                fix_type=fix_type,
                output_path=out_path,
            ),
            _on_fix_done,
        )

    def _export_openscap_summary(self) -> None:
        content = self.oscap_raw_edit.toPlainText()
        ReportExporter.export_text_file(
            self,
            content,
            default_filename="openscap-audit-report.txt",
            title="Export OpenSCAP Audit Report",
        )

    # ------------------------------------------------------------
    # Auditd Actions
    # ------------------------------------------------------------
    def _toggle_auditd_service(self) -> None:
        st = self.auditd_info.get("service_state", "inactive")
        cmd = SecurityCommands.auditd_disable() if st == "active" else SecurityCommands.auditd_enable()
        self.run_cmd(cmd, lambda o, e, c: self.refresh())

    def _reload_auditd_rules(self) -> None:
        self.run_cmd(SecurityCommands.auditd_reload(), lambda o, e, c: self.refresh())

    def _view_loaded_rules(self) -> None:
        def _on_rules(out: str, err: str, code: int):
            rules = SecurityParsers.parse_auditd_rules(out)
            self.audit_events_table.setColumnCount(4)
            self.audit_events_table.setHorizontalHeaderLabels(["Type", "Target (File / Syscall)", "Permissions", "Key Tag"])
            self.audit_events_table.setRowCount(len(rules))
            for row, r in enumerate(rules):
                self.audit_events_table.setItem(row, 0, QTableWidgetItem(r.get("type", "")))
                self.audit_events_table.setItem(row, 1, QTableWidgetItem(r.get("target", "")))
                self.audit_events_table.setItem(row, 2, QTableWidgetItem(r.get("permissions", "")))
                self.audit_events_table.setItem(row, 3, QTableWidgetItem(r.get("key", "")))
            self.auditd_raw_edit.setPlainText(out or "No loaded rules.")

        self.run_cmd(SecurityCommands.auditd_list_rules(), _on_rules)

    def _deploy_selected_audit_rules(self) -> None:
        rules_to_deploy: list[str] = []

        # 1. Accounts
        if self.chk_rule_accounts.isChecked():
            rules_to_deploy.extend(SecurityCommands.AUDITD_USER_ACCOUNT_RULES)

        # 2. Permissions
        if self.chk_rule_perms.isChecked():
            rules_to_deploy.extend(SecurityCommands.AUDITD_PERMISSION_MOD_RULES)

        # 3. Sensitive Files
        if self.chk_rule_sensitive.isChecked():
            rules_to_deploy.extend(SecurityCommands.AUDITD_SENSITIVE_FILE_RULES)

        # 4. CIS Hardening
        if self.chk_rule_cis.isChecked():
            rules_to_deploy.extend(SecurityCommands.AUDITD_CIS_HARDENING_RULES)

        if not rules_to_deploy:
            QMessageBox.warning(self, "Deploy Rules", "Please select at least one audit rule category to deploy.")
            return

        unique_rules = list(dict.fromkeys(rules_to_deploy))

        confirm = QMessageBox.question(
            self,
            "Deploy Auditd Hardening Rules",
            f"Deploy {len(unique_rules)} security tracking rules to /etc/audit/rules.d/99-admin-suite.rules?\n\n"
            "This will configure kernel-level tracking of user accounts, file permissions, and sensitive files.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm == QMessageBox.StandardButton.Yes:
            self.run_cmd(
                SecurityCommands.auditd_deploy_preset_rules(unique_rules),
                lambda out, err, code: self.refresh(),
            )

    def _search_audit_events(self, key: str = "", report: str = "") -> None:
        time_r = self.time_combo.currentText().strip() or "today"

        if report:
            cmd = SecurityCommands.auditd_aureport(report_type=report, time_range=time_r)
            def _on_report_done(out: str, err: str, code: int):
                self.auditd_raw_edit.setPlainText(out or "No audit events found for specified filter.")
            self.run_cmd(cmd, _on_report_done)
        else:
            cmd = SecurityCommands.auditd_ausearch(filter_key=key, time_range=time_r, lines=60)
            def _on_events_done(out: str, err: str, code: int):
                self.auditd_raw_edit.setPlainText(out or "No audit events found for specified filter.")
                events = SecurityParsers.parse_ausearch_events(out)
                self._populate_audit_events(events)
            self.run_cmd(cmd, _on_events_done)

    def _populate_audit_events(self, events: list[dict[str, str]]) -> None:
        self.audit_events_table.setColumnCount(6)
        self.audit_events_table.setHorizontalHeaderLabels(
            ["Timestamp", "Type", "Key / Tag", "Executable / Syscall", "Target Name", "Result"]
        )
        self.audit_events_table.setRowCount(len(events))
        for row, ev in enumerate(events):
            self.audit_events_table.setItem(row, 0, QTableWidgetItem(ev.get("timestamp", "")))
            self.audit_events_table.setItem(row, 1, QTableWidgetItem(ev.get("type", "")))
            self.audit_events_table.setItem(row, 2, QTableWidgetItem(ev.get("key", "")))
            self.audit_events_table.setItem(row, 3, QTableWidgetItem(ev.get("comm") or ev.get("exe", "")))
            self.audit_events_table.setItem(row, 4, QTableWidgetItem(ev.get("name", "")))

            succ = ev.get("success", "unknown")
            item_s = QTableWidgetItem(succ.upper())
            item_s.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if succ == "yes":
                item_s.setForeground(QColor("#44eb74"))
            elif succ == "no":
                item_s.setForeground(QColor("#eb4444"))
            self.audit_events_table.setItem(row, 5, item_s)
