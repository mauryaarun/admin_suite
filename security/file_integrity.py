"""
File Integrity Monitoring (AIDE) and Application Whitelisting (fapolicyd) Management Interface.
Provides baseline database creation, scheduled cron integrity audits, trust database management,
and real-time execution denial tracking, with distribution adaptation for RHEL and Debian families.
"""

from __future__ import annotations

import re
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
from admin_suite.security.commands import SecurityCommands, SecurityParsers


class WhitelistBinaryDialog(QDialog):
    """Dialog to whitelist a custom binary or path in fapolicyd."""

    def __init__(self, default_path: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Whitelist Application in fapolicyd")
        self.setMinimumWidth(440)

        layout = QVBoxLayout(self)

        info = QLabel(
            "Add a binary, script, or directory path to the fapolicyd trust list.\n"
            "The trust database will be updated automatically to permit execution."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        form = QGridLayout()
        form.addWidget(QLabel("Target Path:"), 0, 0)
        self.path_edit = QLineEdit(default_path)
        self.path_edit.setPlaceholderText("/opt/app/bin/tool or /usr/local/bin/custom")
        form.addWidget(self.path_edit, 0, 1)

        layout.addLayout(form)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def get_path(self) -> str:
        return self.path_edit.text().strip()


class FileIntegrityWidget(QWidget):
    """
    Unified File Integrity & Application Whitelisting management interface.
    Integrates AIDE (baseline database & cron auditing) and fapolicyd (kernel application whitelisting).
    """

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
        self.aide_info: dict[str, Any] = {}
        self.fapol_info: dict[str, Any] = {}

        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        # ---------------- Top Bar ----------------
        top_frame = QFrame()
        top_frame.setStyleSheet(
            f"background: {self.theme.get('panel2', '#222')}; border-radius: 4px; padding: 6px;"
        )
        top_layout = QHBoxLayout(top_frame)
        top_layout.setContentsMargins(8, 4, 8, 4)
        top_layout.setSpacing(10)

        self.title_lbl = QLabel("🛡️ File Integrity & Application Whitelisting")
        self.title_lbl.setStyleSheet(
            f"font-size: 13px; font-weight: bold; color: {self.theme.get('accent', '#3daee9')};"
        )
        top_layout.addWidget(self.title_lbl)

        # Distribution Badge
        self.distro_badge = QLabel("Distro: On Demand")
        self.distro_badge.setStyleSheet(
            f"background: #2a2a2a; border: 1px solid {self.theme.get('border', '#444')}; "
            f"padding: 2px 8px; border-radius: 3px; font-size: 11px;"
        )
        top_layout.addWidget(self.distro_badge)

        # Quick Badges
        self.badge_aide = QLabel("AIDE: On Demand")
        self.badge_fapol = QLabel("fapolicyd: On Demand")
        for b in (self.badge_aide, self.badge_fapol):
            b.setStyleSheet(
                f"border: 1px solid {self.theme.get('border', '#444')}; padding: 2px 6px; "
                f"border-radius: 3px; font-size: 11px;"
            )
            top_layout.addWidget(b)

        top_layout.addStretch()

        self.btn_install = QPushButton("📦 Install Tools")
        self.btn_install.setToolTip("Install missing AIDE / fapolicyd packages for this distribution")
        self.btn_install.clicked.connect(self._install_missing_tools)
        top_layout.addWidget(self.btn_install)

        self.btn_refresh = QPushButton("🔍 Audit Status Now")
        self.btn_refresh.setToolTip("Probe host distribution, AIDE file integrity, and fapolicyd whitelisting on demand")
        self.btn_refresh.clicked.connect(self.refresh)
        top_layout.addWidget(self.btn_refresh)

        layout.addWidget(top_frame)

        # ---------------- Sub-Tabs: AIDE vs fapolicyd ----------------
        self.sub_tabs = QTabWidget()

        # Tab 1: AIDE
        self.sub_tabs.addTab(self._build_aide_tab(), "📁 AIDE (File Integrity Monitoring)")

        # Tab 2: fapolicyd
        self.sub_tabs.addTab(self._build_fapolicyd_tab(), "🛡️ fapolicyd (Application Whitelisting)")

        layout.addWidget(self.sub_tabs)

    # ------------------------------------------------------------
    # 1. AIDE Sub-Tab UI
    # ------------------------------------------------------------
    def _build_aide_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)

        # Info & Status Card
        card = QFrame()
        card.setStyleSheet(
            f"background: {self.theme.get('panel2', '#222')}; border-radius: 4px; padding: 6px;"
        )
        card_layout = QGridLayout(card)
        card_layout.setSpacing(8)

        self.aide_status_lbl = QLabel("Binary: On Demand")
        self.aide_db_lbl = QLabel("Database: On Demand")
        self.aide_cron_lbl = QLabel("Daily Cron: On Demand")
        self.aide_log_lbl = QLabel("Audit Log: On Demand")

        for lbl in (self.aide_status_lbl, self.aide_db_lbl, self.aide_cron_lbl, self.aide_log_lbl):
            lbl.setStyleSheet("font-size: 11px;")

        card_layout.addWidget(QLabel("<b>AIDE Binary:</b>"), 0, 0)
        card_layout.addWidget(self.aide_status_lbl, 0, 1)
        card_layout.addWidget(QLabel("<b>Baseline DB:</b>"), 0, 2)
        card_layout.addWidget(self.aide_db_lbl, 0, 3)

        card_layout.addWidget(QLabel("<b>Cron Automation:</b>"), 1, 0)
        card_layout.addWidget(self.aide_cron_lbl, 1, 1)
        card_layout.addWidget(QLabel("<b>Check Log:</b>"), 1, 2)
        card_layout.addWidget(self.aide_log_lbl, 1, 3)

        layout.addWidget(card)

        # Actions Toolbar
        btn_bar = QHBoxLayout()
        btn_bar.setSpacing(6)

        self.btn_aide_init = QPushButton("⚡ Initialize Baseline DB")
        self.btn_aide_init.setToolTip("Create initial cryptographic database of system files")
        self.btn_aide_init.clicked.connect(self._init_aide_db)
        btn_bar.addWidget(self.btn_aide_init)

        self.btn_aide_check = QPushButton("🔍 Run Integrity Check Now")
        self.btn_aide_check.setToolTip("Compare current system files against baseline database")
        self.btn_aide_check.clicked.connect(self._run_aide_check)
        btn_bar.addWidget(self.btn_aide_check)

        self.btn_aide_update = QPushButton("💾 Update Baseline DB")
        self.btn_aide_update.setToolTip("Update database to accept current system modifications as new baseline")
        self.btn_aide_update.clicked.connect(self._update_aide_db)
        btn_bar.addWidget(self.btn_aide_update)

        self.btn_aide_cron = QPushButton("⏰ Setup Daily Cron")
        self.btn_aide_cron.setToolTip("Install automated daily integrity verification via /etc/cron.daily/aide-check")
        self.btn_aide_cron.clicked.connect(self._toggle_aide_cron)
        btn_bar.addWidget(self.btn_aide_cron)

        self.btn_aide_view_log = QPushButton("📄 View AIDE Log")
        self.btn_aide_view_log.clicked.connect(self._view_aide_log)
        btn_bar.addWidget(self.btn_aide_view_log)

        btn_bar.addStretch()

        self.btn_aide_export = QPushButton("📋 Export Report")
        self.btn_aide_export.clicked.connect(self._export_aide_results)
        btn_bar.addWidget(self.btn_aide_export)

        layout.addLayout(btn_bar)

        # Check Result Summary Bar
        self.aide_summary_bar = QLabel("Run an integrity check to inspect system files.")
        self.aide_summary_bar.setStyleSheet(
            "padding: 4px 8px; border-radius: 3px; background: #252526; font-size: 12px; font-weight: bold;"
        )
        layout.addWidget(self.aide_summary_bar)

        # Splitter: Table of File Modifications vs Raw Output
        splitter = QSplitter(Qt.Orientation.Vertical)

        # Table of modified files
        table_container = QWidget()
        t_layout = QVBoxLayout(table_container)
        t_layout.setContentsMargins(0, 0, 0, 0)

        t_lbl = QLabel("<b>Detected File Changes (Differences from Authorized Baseline):</b>")
        t_layout.addWidget(t_lbl)

        self.aide_table = QTableWidget(0, 3)
        self.aide_table.setHorizontalHeaderLabels(["Status", "File Path", "Modification Details"])
        self.aide_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.aide_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        self.aide_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.aide_table.horizontalHeader().resizeSection(1, 320)
        self.aide_table.setAlternatingRowColors(True)
        self.aide_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        t_layout.addWidget(self.aide_table)
        splitter.addWidget(table_container)

        # Raw Output Editor
        raw_container = QWidget()
        r_layout = QVBoxLayout(raw_container)
        r_layout.setContentsMargins(0, 0, 0, 0)
        r_lbl = QLabel("<b>Raw AIDE Check Output / System Log:</b>")
        r_layout.addWidget(r_lbl)

        self.aide_raw_edit = QPlainTextEdit()
        self.aide_raw_edit.setFont(QFont("Monospace", 9))
        self.aide_raw_edit.setReadOnly(True)
        r_layout.addWidget(self.aide_raw_edit)
        splitter.addWidget(raw_container)

        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter)

        return widget

    # ------------------------------------------------------------
    # 2. fapolicyd Sub-Tab UI
    # ------------------------------------------------------------
    def _build_fapolicyd_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)

        # RHEL / Distro compatibility banner
        self.fapol_distro_banner = QLabel(
            "ℹ️ <b>fapolicyd</b> is RHEL's file access policy daemon (RedHat / CentOS / Rocky / Alma / Fedora). "
            "It intercepts system calls via fanotify to block execution of untrusted or unauthorized binaries."
        )
        self.fapol_distro_banner.setWordWrap(True)
        self.fapol_distro_banner.setStyleSheet(
            "background: #1e3a5f; color: #90caf9; padding: 6px 10px; border-radius: 4px; font-size: 11px;"
        )
        layout.addWidget(self.fapol_distro_banner)

        # Status & Mode Frame
        card = QFrame()
        card.setStyleSheet(
            f"background: {self.theme.get('panel2', '#222')}; border-radius: 4px; padding: 6px;"
        )
        card_layout = QGridLayout(card)
        card_layout.setSpacing(8)

        self.fapol_state_lbl = QLabel("Service: On Demand")
        self.fapol_mode_lbl = QLabel("Mode: On Demand")
        self.fapol_integrity_lbl = QLabel("Integrity: On Demand")
        self.fapol_rules_lbl = QLabel("Trust DB: On Demand")

        for lbl in (self.fapol_state_lbl, self.fapol_mode_lbl, self.fapol_integrity_lbl, self.fapol_rules_lbl):
            lbl.setStyleSheet("font-size: 11px;")

        card_layout.addWidget(QLabel("<b>Daemon Service:</b>"), 0, 0)
        card_layout.addWidget(self.fapol_state_lbl, 0, 1)
        card_layout.addWidget(QLabel("<b>Enforcement Mode:</b>"), 0, 2)
        card_layout.addWidget(self.fapol_mode_lbl, 0, 3)

        card_layout.addWidget(QLabel("<b>Hash Integrity:</b>"), 1, 0)
        card_layout.addWidget(self.fapol_integrity_lbl, 1, 1)
        card_layout.addWidget(QLabel("<b>Trust DB Status:</b>"), 1, 2)
        card_layout.addWidget(self.fapol_rules_lbl, 1, 3)

        layout.addWidget(card)

        # Action Buttons Bar
        btn_bar = QHBoxLayout()
        btn_bar.setSpacing(6)

        self.btn_fapol_toggle_srv = QPushButton("⚡ Enable & Start fapolicyd")
        self.btn_fapol_toggle_srv.clicked.connect(self._toggle_fapol_service)
        btn_bar.addWidget(self.btn_fapol_toggle_srv)

        self.btn_fapol_enforce = QPushButton("🛡️ Switch to Enforcing Mode")
        self.btn_fapol_enforce.setToolTip("Block all untrusted/unauthorized application executions")
        self.btn_fapol_enforce.clicked.connect(lambda: self._set_fapol_mode(permissive=False))
        btn_bar.addWidget(self.btn_fapol_enforce)

        self.btn_fapol_permissive = QPushButton("👁️ Permissive (Audit Only)")
        self.btn_fapol_permissive.setToolTip("Log unauthorized executions to audit log without blocking")
        self.btn_fapol_permissive.clicked.connect(lambda: self._set_fapol_mode(permissive=True))
        btn_bar.addWidget(self.btn_fapol_permissive)

        self.btn_fapol_update_db = QPushButton("🔄 Update Trust DB")
        self.btn_fapol_update_db.setToolTip("Rescan RPM database and custom trust files")
        self.btn_fapol_update_db.clicked.connect(self._update_fapol_db)
        btn_bar.addWidget(self.btn_fapol_update_db)

        self.btn_fapol_add_trust = QPushButton("➕ Whitelist Binary / Path")
        self.btn_fapol_add_trust.clicked.connect(self._add_custom_trust)
        btn_bar.addWidget(self.btn_fapol_add_trust)

        btn_bar.addStretch()

        self.btn_fapol_refresh_denials = QPushButton("🚨 Refresh Blocked Executions")
        self.btn_fapol_refresh_denials.clicked.connect(self._fetch_fapol_denials)
        btn_bar.addWidget(self.btn_fapol_refresh_denials)

        layout.addLayout(btn_bar)

        # Splitter: Blocked Executions Table vs Details/Trust List
        splitter = QSplitter(Qt.Orientation.Vertical)

        # Denials Table
        d_container = QWidget()
        d_layout = QVBoxLayout(d_container)
        d_layout.setContentsMargins(0, 0, 0, 0)
        d_layout.addWidget(QLabel("<b>Blocked Execution Events (Fanotify Denials):</b>"))

        self.fapol_denials_table = QTableWidget(0, 6)
        self.fapol_denials_table.setHorizontalHeaderLabels(
            ["Timestamp", "Blocked Binary", "Process (comm)", "PID", "UID", "Action"]
        )
        self.fapol_denials_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.fapol_denials_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.fapol_denials_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.fapol_denials_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.fapol_denials_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.fapol_denials_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        self.fapol_denials_table.setAlternatingRowColors(True)
        self.fapol_denials_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        d_layout.addWidget(self.fapol_denials_table)
        splitter.addWidget(d_container)

        # Trust List / Log Viewer
        log_container = QWidget()
        l_layout = QVBoxLayout(log_container)
        l_layout.setContentsMargins(0, 0, 0, 0)

        l_head = QHBoxLayout()
        l_head.addWidget(QLabel("<b>fapolicyd Status & Trust Database Dump:</b>"))
        btn_dump_trust = QPushButton("📋 Dump Trust List")
        btn_dump_trust.clicked.connect(self._dump_fapol_trust)
        l_head.addWidget(btn_dump_trust)
        l_head.addStretch()
        l_layout.addLayout(l_head)

        self.fapol_raw_edit = QPlainTextEdit()
        self.fapol_raw_edit.setFont(QFont("Monospace", 9))
        self.fapol_raw_edit.setReadOnly(True)
        l_layout.addWidget(self.fapol_raw_edit)
        splitter.addWidget(log_container)

        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter)

        return widget

    # ------------------------------------------------------------
    # Refresh & Probe Handlers
    # ------------------------------------------------------------
    def refresh(self) -> None:
        """Probes distribution, AIDE status, and fapolicyd status."""
        self.run_cmd(SecurityCommands.detect_os(), self._on_os_detected)
        self.run_cmd(SecurityCommands.aide_probe(), self._on_aide_probe)
        self.run_cmd(SecurityCommands.fapolicyd_probe(), self._on_fapolicyd_probe)

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
            self.fapol_distro_banner.setText(
                f"✅ <b>RHEL Ecosystem Detected ({name})</b>: fapolicyd is natively supported as the primary application whitelisting subsystem."
            )
            self.fapol_distro_banner.setStyleSheet(
                "background: #1b4d24; color: #44eb74; padding: 6px 10px; border-radius: 4px; font-size: 11px;"
            )
        elif fam == "DEBIAN":
            self.distro_badge.setStyleSheet(
                "background: #311b4d; color: #ea80fc; border: 1px solid #7b1fa2; padding: 2px 8px; border-radius: 3px; font-size: 11px;"
            )
            self.fapol_distro_banner.setText(
                f"ℹ️ <b>Debian/Ubuntu Ecosystem Detected ({name})</b>: AIDE is the cross-distro standard for file integrity. "
                "fapolicyd is primarily developed for RHEL/CentOS/Fedora systems."
            )
            self.fapol_distro_banner.setStyleSheet(
                "background: #3e2723; color: #ffccbc; padding: 6px 10px; border-radius: 4px; font-size: 11px;"
            )

    def _on_aide_probe(self, stdout: str, stderr: str, code: int) -> None:
        self.aide_info = SecurityParsers.parse_aide_probe(stdout)

        # Status Badge
        if self.aide_info.get("installed"):
            if self.aide_info.get("db_exists"):
                self.badge_aide.setText("AIDE: ● Baseline Ready")
                self.badge_aide.setStyleSheet(
                    "background: #1b4d24; color: #44eb74; padding: 2px 6px; border-radius: 3px; font-size: 11px;"
                )
            else:
                self.badge_aide.setText("AIDE: ⚠️ No Database")
                self.badge_aide.setStyleSheet(
                    "background: #4d381b; color: #ebaa44; padding: 2px 6px; border-radius: 3px; font-size: 11px;"
                )
        else:
            self.badge_aide.setText("AIDE: ○ Not Installed")
            self.badge_aide.setStyleSheet(
                "background: #4d1b1b; color: #eb4444; padding: 2px 6px; border-radius: 3px; font-size: 11px;"
            )

        # Card Labels
        inst_txt = "Installed" if self.aide_info.get("installed") else "Not Installed"
        self.aide_status_lbl.setText(f"{inst_txt} (Config: {self.aide_info.get('config', 'none')})")

        if self.aide_info.get("db_exists"):
            db_p = self.aide_info.get("db_path", "")
            stat_t = self.aide_info.get("db_stat", "")
            self.aide_db_lbl.setText(f"Active ({db_p} {stat_t})")
        elif self.aide_info.get("db_new_exists"):
            self.aide_db_lbl.setText("Unconfirmed DB (aide.db.new.gz exists - click Update DB)")
        else:
            self.aide_db_lbl.setText("None (Click 'Initialize Baseline DB')")

        cron_sched = self.aide_info.get("cron_scheduled")
        cron_f = self.aide_info.get("cron_file", "")
        self.aide_cron_lbl.setText(f"Active ({cron_f})" if cron_sched else "Disabled / Not Configured")
        self.btn_aide_cron.setText("❌ Remove Daily Cron" if cron_sched else "⏰ Setup Daily Cron")

        log_ex = self.aide_info.get("log_exists")
        self.aide_log_lbl.setText(f"Present ({self.aide_info.get('log_stat', '')})" if log_ex else "No log yet")

    def _on_fapolicyd_probe(self, stdout: str, stderr: str, code: int) -> None:
        self.fapol_info = SecurityParsers.parse_fapolicyd_probe(stdout)

        inst = self.fapol_info.get("installed")
        state = self.fapol_info.get("service_state", "inactive")
        perm = self.fapol_info.get("permissive", 0)

        # Top Badge
        if inst:
            if state == "active":
                mode_str = "Permissive" if perm == 1 else "Enforcing"
                self.badge_fapol.setText(f"fapolicyd: ● {mode_str}")
                self.badge_fapol.setStyleSheet(
                    "background: #1b4d24; color: #44eb74; padding: 2px 6px; border-radius: 3px; font-size: 11px;"
                )
            else:
                self.badge_fapol.setText("fapolicyd: ○ Inactive")
                self.badge_fapol.setStyleSheet(
                    "background: #4d1b1b; color: #eb4444; padding: 2px 6px; border-radius: 3px; font-size: 11px;"
                )
        else:
            self.badge_fapol.setText("fapolicyd: ○ Not Installed")
            self.badge_fapol.setStyleSheet(
                "background: #333333; color: #888888; padding: 2px 6px; border-radius: 3px; font-size: 11px;"
            )

        # Card Labels
        self.fapol_state_lbl.setText(f"{state.upper()} (Enabled: {self.fapol_info.get('service_enabled', 'disabled')})")
        self.btn_fapol_toggle_srv.setText("⛔ Stop & Disable fapolicyd" if state == "active" else "⚡ Enable & Start fapolicyd")

        if perm == 1:
            self.fapol_mode_lbl.setText("Permissive / Audit Only (logs violations without blocking)")
            self.fapol_mode_lbl.setStyleSheet("color: #e67e22; font-weight: bold; font-size: 11px;")
        else:
            self.fapol_mode_lbl.setText("Enforcing (blocks untrusted / unauthorized execution)")
            self.fapol_mode_lbl.setStyleSheet("color: #0dbc79; font-weight: bold; font-size: 11px;")

        self.fapol_integrity_lbl.setText(f"{self.fapol_info.get('integrity', 'none').upper()}")
        self.fapol_rules_lbl.setText("Loaded" if inst and state == "active" else "Unknown")

        if self.fapol_info.get("status_text"):
            self.fapol_raw_edit.setPlainText(self.fapol_info["status_text"])

    # ------------------------------------------------------------
    # Install Missing Tools
    # ------------------------------------------------------------
    def _install_missing_tools(self) -> None:
        fam = self.distro_info.get("family", "generic")
        pkg_m = self.distro_info.get("pkg_manager", "auto")

        if fam == "rhel":
            pkgs = ["aide", "fapolicyd"]
        else:
            pkgs = ["aide"]

        cmd = SecurityCommands.install_packages_cmd(pkgs, pkg_manager=pkg_m)

        confirm = QMessageBox.question(
            self,
            "Install Security Tools",
            f"Install the following security packages for {self.distro_info.get('pretty_name', 'Linux')}?\n\n"
            f"Packages: {', '.join(pkgs)}\nCommand: {cmd}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm == QMessageBox.StandardButton.Yes:
            self.aide_summary_bar.setText("Installing packages... Please wait.")
            self.run_cmd(cmd, lambda out, err, code: self.refresh())

    # ------------------------------------------------------------
    # AIDE Actions
    # ------------------------------------------------------------
    def _init_aide_db(self) -> None:
        self.aide_summary_bar.setText("Initializing AIDE baseline cryptographic database... This may take a minute.")
        self.aide_summary_bar.setStyleSheet("background: #1e3a5f; color: #90caf9; padding: 4px 8px; font-weight: bold;")
        self.btn_aide_init.setEnabled(False)

        def _on_init_done(out: str, err: str, code: int):
            self.btn_aide_init.setEnabled(True)
            self.aide_raw_edit.setPlainText(out + ("\n" + err if err else ""))
            if code == 0:
                self.aide_summary_bar.setText("✅ AIDE baseline database successfully initialized and activated.")
                self.aide_summary_bar.setStyleSheet("background: #1b4d24; color: #44eb74; padding: 4px 8px; font-weight: bold;")
            else:
                self.aide_summary_bar.setText(f"⚠️ AIDE init finished with code {code}.")
            self.refresh()

        self.run_cmd(SecurityCommands.aide_init(self.distro_info.get("family", "auto")), _on_init_done)

    def _run_aide_check(self) -> None:
        self.aide_summary_bar.setText("Running AIDE file integrity check against baseline... Scanning core binaries.")
        self.aide_summary_bar.setStyleSheet("background: #1e3a5f; color: #90caf9; padding: 4px 8px; font-weight: bold;")
        self.btn_aide_check.setEnabled(False)

        def _on_check_done(out: str, err: str, code: int):
            self.btn_aide_check.setEnabled(True)
            raw = out + ("\n" + err if err else "")
            self.aide_raw_edit.setPlainText(raw)

            data = SecurityParsers.parse_aide_check_output(raw)
            if data["clean"]:
                self.aide_summary_bar.setText("✅ No Differences: All monitored core system files match the authorized baseline.")
                self.aide_summary_bar.setStyleSheet("background: #1b4d24; color: #44eb74; padding: 4px 8px; font-weight: bold;")
            else:
                msg = (
                    f"⚠️ Unauthorized Modifications Detected! "
                    f"Added: {data['added']} | Removed: {data['removed']} | Changed: {data['changed']}"
                )
                self.aide_summary_bar.setText(msg)
                self.aide_summary_bar.setStyleSheet("background: #4d1b1b; color: #eb4444; padding: 4px 8px; font-weight: bold;")

            self._populate_aide_table(data["entries"])

        self.run_cmd(SecurityCommands.aide_check(), _on_check_done)

    def _populate_aide_table(self, entries: list[dict[str, Any]]) -> None:
        self.aide_table.setRowCount(len(entries))
        for row, item in enumerate(entries):
            st = item.get("status", "")
            item_st = QTableWidgetItem(st)
            item_st.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if st == "Added":
                item_st.setForeground(QColor("#44eb74"))
            elif st == "Removed":
                item_st.setForeground(QColor("#eb4444"))
            else:
                item_st.setForeground(QColor("#e67e22"))
            self.aide_table.setItem(row, 0, item_st)

            item_path = QTableWidgetItem(item.get("path", ""))
            self.aide_table.setItem(row, 1, item_path)

            item_det = QTableWidgetItem(item.get("details", ""))
            self.aide_table.setItem(row, 2, item_det)

    def _update_aide_db(self) -> None:
        confirm = QMessageBox.question(
            self,
            "Update Baseline Database",
            "This will accept all current filesystem changes as the new trusted cryptographic baseline.\n\n"
            "Proceed with updating the AIDE database?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm == QMessageBox.StandardButton.Yes:
            self.aide_summary_bar.setText("Updating AIDE database to accept modifications...")
            self.run_cmd(SecurityCommands.aide_update(), lambda o, e, c: self.refresh())

    def _toggle_aide_cron(self) -> None:
        if self.aide_info.get("cron_scheduled"):
            self.run_cmd(SecurityCommands.aide_remove_cron(), lambda o, e, c: self.refresh())
        else:
            self.run_cmd(SecurityCommands.aide_setup_cron(), lambda o, e, c: self.refresh())

    def _view_aide_log(self) -> None:
        self.run_cmd(
            SecurityCommands.aide_read_log(150),
            lambda out, err, code: self.aide_raw_edit.setPlainText(out or "No log content."),
        )

    def _export_aide_results(self) -> None:
        content = self.aide_raw_edit.toPlainText()
        ReportExporter.export_text_file(
            self,
            content,
            default_filename="aide-integrity-report.txt",
            title="Export AIDE Integrity Report",
        )

    # ------------------------------------------------------------
    # fapolicyd Actions
    # ------------------------------------------------------------
    def _toggle_fapol_service(self) -> None:
        cur_state = self.fapol_info.get("service_state", "inactive")
        if cur_state == "active":
            cmd = SecurityCommands.fapolicyd_disable()
        else:
            cmd = SecurityCommands.fapolicyd_enable()
        self.run_cmd(cmd, lambda o, e, c: self.refresh())

    def _set_fapol_mode(self, permissive: bool) -> None:
        mode_label = "Permissive (Audit Only)" if permissive else "Enforcing (Block Untrusted)"
        confirm = QMessageBox.question(
            self,
            "Change fapolicyd Enforcement Mode",
            f"Set fapolicyd enforcement to {mode_label} and restart the service?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm == QMessageBox.StandardButton.Yes:
            self.run_cmd(SecurityCommands.fapolicyd_set_mode(permissive=permissive), lambda o, e, c: self.refresh())

    def _update_fapol_db(self) -> None:
        self.fapol_raw_edit.setPlainText("Updating fapolicyd trust database... Please wait.")
        self.run_cmd(
            SecurityCommands.fapolicyd_update_db(),
            lambda out, err, code: self.fapol_raw_edit.setPlainText(out + ("\n" + err if err else "")),
        )

    def _dump_fapol_trust(self) -> None:
        self.run_cmd(
            SecurityCommands.fapolicyd_list_trust(),
            lambda out, err, code: self.fapol_raw_edit.setPlainText(out or "No trust list entries."),
        )

    def _add_custom_trust(self, default_path: str = "") -> None:
        dlg = WhitelistBinaryDialog(default_path=default_path, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            p = dlg.get_path()
            if p:
                self.run_cmd(
                    SecurityCommands.fapolicyd_trust_file(p),
                    lambda out, err, code: self.refresh(),
                )

    def _fetch_fapol_denials(self) -> None:
        def _on_denials(out: str, err: str, code: int):
            denials = SecurityParsers.parse_fapolicyd_denials(out)
            self._populate_fapol_denials(denials)

        self.run_cmd(SecurityCommands.fapolicyd_get_denials(60), _on_denials)

    def _populate_fapol_denials(self, denials: list[dict[str, str]]) -> None:
        self.fapol_denials_table.setRowCount(len(denials))
        for row, d in enumerate(denials):
            # 0. Timestamp
            item_t = QTableWidgetItem(d.get("timestamp", ""))
            item_t.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.fapol_denials_table.setItem(row, 0, item_t)

            # 1. Binary Path
            exe_p = d.get("exe", "")
            item_exe = QTableWidgetItem(exe_p)
            item_exe.setForeground(QColor("#eb4444"))
            self.fapol_denials_table.setItem(row, 1, item_exe)

            # 2. Comm
            item_comm = QTableWidgetItem(d.get("comm", ""))
            self.fapol_denials_table.setItem(row, 2, item_comm)

            # 3. PID
            item_pid = QTableWidgetItem(d.get("pid", ""))
            item_pid.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.fapol_denials_table.setItem(row, 3, item_pid)

            # 4. UID
            item_uid = QTableWidgetItem(d.get("uid", ""))
            item_uid.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.fapol_denials_table.setItem(row, 4, item_uid)

            # 5. Whitelist button
            btn_wl = QPushButton("➕ Whitelist")
            btn_wl.setFixedHeight(22)
            btn_wl.clicked.connect(lambda checked, p=exe_p: self._add_custom_trust(p))
            self.fapol_denials_table.setCellWidget(row, 5, btn_wl)
