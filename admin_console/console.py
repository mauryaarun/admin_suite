"""
Unified Admin Console for Admin Suite.
Consolidates SysAdmin Dashboard, Security Hub, Web Server Management,
Network & VPN Administration, and VAPT into a comprehensive single-pane workbench
with a persistent live terminal execution output pane.
"""

from __future__ import annotations

import getpass
import shlex
import subprocess
import time
from typing import Any, Callable, Optional

from PyQt6.QtCore import Qt, QSortFilterProxyModel, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QStandardItem, QStandardItemModel
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QTableView,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from admin_suite.core.export import ReportExporter
from admin_suite.ssh.remote_exec import RemoteExecThread
from admin_suite.ui.dialogs import SudoCredentialsDialog, ensure_sudo_credentials
from admin_suite.ui.linux_settings_dialog import LinuxSettingsDialog

# Subsystem modules
from admin_suite.sysadmin.commands import SYSADMIN_CMDS, SYSADMIN_SECTIONS
from admin_suite.sysadmin.presenter import (
    SysAdminContainersWidget,
    SysAdminCronWidget,
    SysAdminJournalWidget,
    SysAdminNetworkWidget,
    SysAdminOverviewWidget,
    SysAdminPackagesWidget,
    SysAdminPerformanceWidget,
    SysAdminSecurityWidget,
    SysAdminStorageWidget,
)

from admin_suite.security.firewall import FirewallManagerWidget
from admin_suite.security.fail2ban import Fail2banManagerWidget
from admin_suite.security.exposure import PortExposureWidget
from admin_suite.security.auditor import HardeningAuditorWidget
from admin_suite.security.malware_scanner import MalwareScannerWidget
from admin_suite.security.ssl_auditor import SSLAuditorWidget

from admin_suite.web.vhosts import VHostManagerWidget
from admin_suite.web.ssl_manager import SSLManagerWidget
from admin_suite.web.runtimes import RuntimesManagerWidget
from admin_suite.web.log_analyzer import WebLogAnalyzerWidget
from admin_suite.web.tools import WebToolsWidget

from admin_suite.vapt.tab import VaptTab

from admin_suite.admin_console.terminal_pane import TerminalOutputPane
from admin_suite.admin_console.network_tools import NetworkAdminWidget
from admin_suite.vpn.dialog import VpnConfigDialog


class _ConsoleLocalExecThread(QThread):
    finished_cmd = pyqtSignal(str, str, int)  # stdout, stderr, return_code

    def __init__(self, cmd: str, timeout: int = 60, sudo_password: Optional[str] = None, parent=None):
        super().__init__(parent)
        self.cmd = cmd
        self.timeout = timeout
        self.sudo_password = sudo_password

    def run(self) -> None:
        try:
            p = subprocess.Popen(
                ["bash", "-c", self.cmd],
                stdin=subprocess.PIPE if self.sudo_password else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            out, err = p.communicate(
                input=(self.sudo_password + "\n") if self.sudo_password else None,
                timeout=self.timeout,
            )
            self.finished_cmd.emit(out or "", err or "", p.returncode if p.returncode is not None else 0)
        except subprocess.TimeoutExpired:
            self.finished_cmd.emit("", "[Command timed out]\n", 124)
        except Exception as exc:
            self.finished_cmd.emit("", f"[Execution error] {exc}\n", 1)


class AdminConsoleTab(QWidget):
    """
    Unified Admin Console combining:
      1. 🖥️ System Administration (Overview, Services, Processes, Packages, Storage, Journal, Cron, Users, Containers, Performance)
      2. 🛡️ Security Center (Firewall, Fail2ban, Port Exposure, Hardening, Malware Scanner, SSL Auditor)
      3. 🌐 Network & VPN (Interfaces, Routing, DNS, Sockets, Diagnostics, WireGuard Server Setup)
      4. 🌍 Web & Hosting (VHosts, SSL/Certbot, Runtimes, Web Logs, Web Tools)
      5. 🔍 VAPT & Vulnerability Audit (Reconnaissance, Security Headers, WHOIS, DNSSEC, Reports)
      6. 📟 Persistent Live Terminal Output Pane with ad-hoc interactive command prompt!
    """

    def __init__(
        self,
        services,
        profile_name: str = "Local",
        profile: Optional[dict[str, Any]] = None,
        initial_category: Optional[str] = None,
        auto_refresh: bool = True,
        parent=None,
    ):
        super().__init__(parent)
        self.services = services
        self.profile_name = profile_name or "Local"
        self.profile = profile
        self.theme = self.services.theme.current
        self.auto_refresh = auto_refresh

        self._workers: list[QThread] = []

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(4, 4, 4, 4)
        main_layout.setSpacing(4)

        # ------------------------------------------------------------
        # Top Header & Status Toolbar
        # ------------------------------------------------------------
        header_frame = QFrame()
        header_frame.setStyleSheet(
            f"background: {self.theme.get('panel2', '#1e222b')}; border-radius: 5px; padding: 4px;"
        )
        h_layout = QHBoxLayout(header_frame)
        h_layout.setContentsMargins(8, 4, 8, 4)
        h_layout.setSpacing(8)

        is_local_display = " (Localhost)" if not self._is_remote() else ""
        title = QLabel(f"🖥️ Admin Console — {self.profile_name}{is_local_display}")
        title.setStyleSheet(f"font-size: 14px; font-weight: bold; color: {self.theme.get('accent', '#3daee9')};")
        h_layout.addWidget(title)

        # Badges
        self.badge_fw = QLabel("FW: ?")
        self.badge_f2b = QLabel("F2B: ?")
        self.badge_web = QLabel("Web: ?")
        for b in (self.badge_fw, self.badge_f2b, self.badge_web):
            b.setStyleSheet(
                f"border: 1px solid {self.theme.get('border', '#444')}; padding: 2px 5px; border-radius: 3px; font-size: 11px;"
            )
            h_layout.addWidget(b)

        h_layout.addStretch()

        self.sudo_chk = QCheckBox("Use sudo")
        self.sudo_chk.setChecked(True)
        self.sudo_chk.setToolTip("Elevate commands using sudo")
        h_layout.addWidget(self.sudo_chk)

        self.sudo_btn = QPushButton("🔑")
        self.sudo_btn.setToolTip("Configure Root / Sudo credentials for this profile")
        self.sudo_btn.setFixedWidth(28)
        self.sudo_btn.clicked.connect(self._configure_sudo_credentials)
        h_layout.addWidget(self.sudo_btn)

        btn_vpn_config = QPushButton("🔒 VPN Manager")
        btn_vpn_config.setToolTip("Configure VPN Profiles and WireGuard Server")
        btn_vpn_config.clicked.connect(self._open_vpn_config)
        h_layout.addWidget(btn_vpn_config)

        btn_settings = QPushButton("⚙️ Distro")
        btn_settings.setToolTip("Linux distro settings, firewall backend, and log paths")
        btn_settings.clicked.connect(self._open_settings)
        h_layout.addWidget(btn_settings)

        self.btn_toggle_term = QPushButton("📟 Terminal Output")
        self.btn_toggle_term.setToolTip("Toggle terminal output pane")
        self.btn_toggle_term.clicked.connect(self._toggle_terminal_pane)
        h_layout.addWidget(self.btn_toggle_term)

        btn_refresh_all = QPushButton("🚀 Run Audit / Refresh")
        btn_refresh_all.clicked.connect(self.refresh_all)
        h_layout.addWidget(btn_refresh_all)

        main_layout.addWidget(header_frame)

        # ------------------------------------------------------------
        # Vertical Splitter: Workspaces (Top) + Terminal Pane (Bottom)
        # ------------------------------------------------------------
        self.splitter = QSplitter(Qt.Orientation.Vertical)
        self.splitter.setChildrenCollapsible(False)

        # Top Container: Master Tabs
        self.master_tabs = QTabWidget()

        # Category 1: 🖥️ System Administration
        self.sysadmin_widget = self._build_sysadmin_subsystem()
        self.master_tabs.addTab(self.sysadmin_widget, "🖥️ System")

        # Category 2: 🛡️ Security Center
        self.security_widget = self._build_security_subsystem()
        self.master_tabs.addTab(self.security_widget, "🛡️ Security")

        # Category 3: 🌐 Network & VPN Admin
        self.network_widget = NetworkAdminWidget(run_cmd_fn=self.execute_command, theme=self.theme, parent=self)
        self.master_tabs.addTab(self.network_widget, "🌐 Network & VPN")

        # Category 4: 🌍 Web & Hosting
        self.web_widget = self._build_web_subsystem()
        self.master_tabs.addTab(self.web_widget, "🌍 Web & Hosting")

        # Category 5: 🔍 VAPT & Vulnerability Audit
        self.vapt_widget = self._build_vapt_subsystem()
        self.master_tabs.addTab(self.vapt_widget, "🔍 VAPT & Recon")

        self.splitter.addWidget(self.master_tabs)

        # Bottom Container: Terminal Output Pane
        self.terminal_pane = TerminalOutputPane(self.services, host_name=self.profile_name, parent=self)
        self.terminal_pane.execute_requested.connect(self._on_terminal_adhoc_cmd)
        self.terminal_pane.visibility_toggle_requested.connect(self._on_terminal_pane_collapse_toggled)
        self.splitter.addWidget(self.terminal_pane)

        # Splitter sizing: 70% top, 30% bottom
        self.splitter.setSizes([550, 220])
        main_layout.addWidget(self.splitter, 1)

        # Select initial category if requested
        if initial_category:
            cat_map = {
                "system": 0, "sysadmin": 0,
                "security": 1,
                "network": 2, "vpn": 2,
                "web": 3, "hosting": 3,
                "vapt": 4, "recon": 4,
            }
            idx = cat_map.get(initial_category.lower(), 0)
            self.master_tabs.setCurrentIndex(idx)

        # Initial background status load
        if self.auto_refresh:
            self.refresh_all()

    # ------------------------------------------------------------
    # Helpers & Remote execution
    # ------------------------------------------------------------
    def _is_remote(self) -> bool:
        if not self.profile:
            return False
        if bool(self.profile.get("is_local")):
            return False
        if self.profile_name.lower() in ("localhost", "local", "127.0.0.1"):
            return False
        host = self.profile.get("ssh_host") or self.profile.get("host")
        if host in ("localhost", "127.0.0.1", "::1") and bool(self.profile.get("use_local_exec", True)):
            return False
        return True

    def _get_sudo_password(self) -> Optional[str]:
        """
        Retrieve sudo password for current profile:
        1. From in-memory profile dict ('sudo_pass')
        2. From session cache or keyring via self.services.get_sudo_password(self.profile_name)
        3. Localhost aliases ('localhost', 'Localhost', 'Local') for local profiles
        """
        if self.profile and self.profile.get("sudo_pass"):
            pw = self.profile["sudo_pass"]
            self.services.set_sudo_password(self.profile_name, pw)
            return pw

        pw = self.services.get_sudo_password(self.profile_name)
        if pw is not None:
            return pw

        if not self._is_remote() or self.profile_name.lower() in ("localhost", "local", "127.0.0.1"):
            for alias in ("localhost", "Localhost", "Local"):
                pw = self.services.get_sudo_password(alias)
                if pw is not None:
                    return pw

        return None

    def execute_command(
        self,
        cmd: str,
        callback: Optional[Callable[[str, str, int], None]] = None,
        use_sudo: bool = True,
        timeout: int = 60,
    ) -> None:
        """
        Universal command execution pipeline.
        Logs command execution and raw output to TerminalOutputPane in real time!
        """
        if not getattr(self, "auto_refresh", True):
            if callback:
                callback("", "", 0)
            return

        should_sudo = use_sudo and self.sudo_chk.isChecked()
        self.terminal_pane.log_command_start(cmd, use_sudo=should_sudo)

        sudo_pw = self._get_sudo_password() if should_sudo else None

        if not self._is_remote() and should_sudo and sudo_pw is None:
            try:
                chk = subprocess.run(["sudo", "-n", "true"], capture_output=True, timeout=3)
                if chk.returncode == 0:
                    self.services.set_sudo_password(self.profile_name, "")
                    sudo_pw = ""
            except Exception:
                pass

        final_cmd = cmd.strip()
        if should_sudo:
            if final_cmd.startswith("sudo "):
                final_cmd = final_cmd[5:].strip()

            escaped = final_cmd.replace("'", "'\\''")
            if sudo_pw:
                final_cmd = f"sudo -S -p '' bash -c '{escaped}'"
            elif sudo_pw == "":
                final_cmd = f"sudo -n bash -c '{escaped}'"
            else:
                final_cmd = f"sudo -S -p '' bash -c '{escaped}'"

        start_time = time.perf_counter()

        if self._is_remote():
            # Remote SSH execution
            worker = RemoteExecThread(
                self.profile or {},
                final_cmd,
                sudo_password=sudo_pw,
                timeout=timeout,
                parent=self,
            )

            def on_remote_done(output: str, rc: int):
                elapsed = time.perf_counter() - start_time
                err = output if rc != 0 else ""
                if "sudo: a password is required" in output or "incorrect password attempt" in output:
                    self.services.clear_sudo_password(self.profile_name)
                    if hasattr(self.services, "notifications"):
                        self.services.notifications.push(
                            "error",
                            "Admin Console (Sudo Auth Failed)",
                            f"Root/sudo authentication failed for {self.profile_name}.",
                        )
                self.terminal_pane.log_command_output(output, err, rc, elapsed=elapsed)
                if callback:
                    callback(output, err, rc)

            worker.finished_cmd.connect(on_remote_done)
            self._workers.append(worker)
            worker.start()

        else:
            # Localhost execution
            worker = _ConsoleLocalExecThread(final_cmd, timeout=timeout, sudo_password=sudo_pw, parent=self)

            def on_local_done(stdout: str, stderr: str, rc: int):
                elapsed = time.perf_counter() - start_time
                combined = stdout + "\n" + stderr
                if "sudo: a password is required" in combined or "incorrect password attempt" in combined:
                    self.services.clear_sudo_password(self.profile_name)
                    if hasattr(self.services, "notifications"):
                        self.services.notifications.push(
                            "error",
                            "Admin Console (Sudo Auth Failed)",
                            f"Root/sudo authentication failed for {self.profile_name}.",
                        )
                self.terminal_pane.log_command_output(stdout, stderr, rc, elapsed=elapsed)
                if callback:
                    callback(stdout, stderr, rc)

            worker.finished_cmd.connect(on_local_done)
            self._workers.append(worker)
            worker.start()

    def _on_terminal_adhoc_cmd(self, cmd: str, use_sudo: bool):
        """Executed when user types directly in Terminal Output Pane."""
        self.execute_command(cmd, use_sudo=use_sudo)

    def _toggle_terminal_pane(self):
        cur_sizes = self.splitter.sizes()
        if cur_sizes[1] < 40:
            self.splitter.setSizes([500, 220])
        else:
            self.splitter.setSizes([700, 36])

    def _on_terminal_pane_collapse_toggled(self):
        cur_sizes = self.splitter.sizes()
        if cur_sizes[1] > 50:
            self.splitter.setSizes([700, 36])
        else:
            self.splitter.setSizes([500, 220])

    def _configure_sudo_credentials(self) -> None:
        user = ""
        if self.profile:
            user = self.profile.get("ssh_user") or self.profile.get("user") or ""
        if not user:
            user = getpass.getuser() if not self._is_remote() else ""
        cur_pw = self._get_sudo_password() or ""
        dlg = SudoCredentialsDialog(self, self.services, self.profile_name, user, current_password=cur_pw)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            pw = dlg.get_password()
            self.services.set_sudo_password(self.profile_name, pw, persist=dlg.should_save_to_profile())
            if self.profile is not None:
                self.profile["sudo_pass"] = pw
            if hasattr(self.services, "notifications"):
                self.services.notifications.push(
                    "ok",
                    "Root/Sudo Credentials",
                    f"Updated elevation credentials for {self.profile_name}",
                )
            self.refresh_all()

    def _open_settings(self):
        dlg = LinuxSettingsDialog(self.services, self)
        dlg.settings_applied.connect(self.refresh_all)
        dlg.exec()

    def _open_vpn_config(self):
        dlg = VpnConfigDialog(self.services, parent=self)
        dlg.exec()

    # ------------------------------------------------------------
    # 1. System Administration Subsystem
    # ------------------------------------------------------------
    def _build_sysadmin_subsystem(self) -> QWidget:
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(2, 2, 2, 2)

        # Left Nav List
        left = QVBoxLayout()
        self.sys_nav = QListWidget()
        self.sys_nav.setFixedWidth(150)
        for sec in SYSADMIN_SECTIONS:
            self.sys_nav.addItem(sec)
        self.sys_nav.currentTextChanged.connect(self._on_sysadmin_section_selected)
        left.addWidget(self.sys_nav)

        btn_sys_refresh = QPushButton("🔄 Refresh Section")
        btn_sys_refresh.clicked.connect(self._refresh_current_sysadmin_section)
        left.addWidget(btn_sys_refresh)
        layout.addLayout(left)

        # Right Content Stack
        self.sys_stack = QStackedWidget()
        self.sys_views: dict[str, Any] = {}
        self.sys_raw_views: dict[str, QPlainTextEdit] = {}

        presenter_classes = {
            "Overview": SysAdminOverviewWidget,
            "Storage": SysAdminStorageWidget,
            "Network": SysAdminNetworkWidget,
            "Journal": SysAdminJournalWidget,
            "Cron": SysAdminCronWidget,
            "Security": SysAdminSecurityWidget,
            "Packages": SysAdminPackagesWidget,
            "Containers": SysAdminContainersWidget,
            "Performance": SysAdminPerformanceWidget,
        }

        for sec in SYSADMIN_SECTIONS:
            sec_widget = QWidget()
            sw_layout = QVBoxLayout(sec_widget)
            sw_layout.setContentsMargins(0, 0, 0, 0)

            p_cls = presenter_classes.get(sec)
            if p_cls:
                if sec in ("Journal", "Packages", "Containers"):
                    pres = p_cls(theme=self.theme, run_cmd_fn=self.execute_command, parent=self)
                else:
                    pres = p_cls(theme=self.theme, parent=self)
                self.sys_views[sec] = pres
                sw_layout.addWidget(pres, 1)
            else:
                raw_edit = QPlainTextEdit()
                raw_edit.setReadOnly(True)
                raw_edit.setFont(QFont("Monospace", 10))
                self.sys_raw_views[sec] = raw_edit
                sw_layout.addWidget(raw_edit, 1)

            self.sys_stack.addWidget(sec_widget)

        layout.addWidget(self.sys_stack, 1)
        self.sys_nav.blockSignals(True)
        self.sys_nav.setCurrentRow(0)
        self.sys_nav.blockSignals(False)
        return widget

    def _on_sysadmin_section_selected(self, section: str):
        if not section:
            return
        idx = SYSADMIN_SECTIONS.index(section) if section in SYSADMIN_SECTIONS else 0
        if hasattr(self, "sys_stack"):
            self.sys_stack.setCurrentIndex(idx)
        if getattr(self, "auto_refresh", True):
            self._refresh_sysadmin_section(section)

    def _refresh_current_sysadmin_section(self):
        sec = self.sys_nav.currentItem().text() if self.sys_nav.currentItem() else "Overview"
        self._refresh_sysadmin_section(sec)

    def _refresh_sysadmin_section(self, section: str):
        if not getattr(self, "auto_refresh", True):
            return
        cmd = SYSADMIN_CMDS.get(section, "")
        if isinstance(cmd, dict):
            cmd = cmd.get("cmd", "")
        if not cmd:
            return

        def on_done(stdout, stderr, rc):
            out = stdout if rc == 0 else (stdout + "\n" + stderr)
            pres = self.sys_views.get(section)
            if pres and hasattr(pres, "update_data"):
                try:
                    pres.update_data(out)
                except Exception as exc:
                    self.services.emit_log("ADMIN", f"Failed updating view for {section}: {exc}", "WARNING")
            elif section in self.sys_raw_views:
                self.sys_raw_views[section].setPlainText(out)

        self.execute_command(cmd, on_done)

    # ------------------------------------------------------------
    # 2. Security Subsystem
    # ------------------------------------------------------------
    def _build_security_subsystem(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(2, 2, 2, 2)

        sec_tabs = QTabWidget()

        # Firewall
        self.firewall_tab = FirewallManagerWidget(run_cmd=self.execute_command, theme=self.theme, parent=self)
        sec_tabs.addTab(self.firewall_tab, "🛡️ Firewall (UFW / Firewalld)")

        # Fail2ban
        self.fail2ban_tab = Fail2banManagerWidget(run_cmd=self.execute_command, theme=self.theme, parent=self)
        sec_tabs.addTab(self.fail2ban_tab, "🚨 Intrusion Defense (Fail2ban)")

        # Port Exposure
        self.exposure_tab = PortExposureWidget(
            run_cmd=self.execute_command,
            theme=self.theme,
            on_block_port_cb=lambda p, proto: self.firewall_tab.add_rule(p, proto, "deny"),
            parent=self,
        )
        sec_tabs.addTab(self.exposure_tab, "🌐 Port Exposure & Connections")

        # Hardening
        self.auditor_tab = HardeningAuditorWidget(run_cmd=self.execute_command, theme=self.theme, parent=self)
        sec_tabs.addTab(self.auditor_tab, "🔒 SSH & Host Hardening")

        # Malware & Rootkits
        self.malware_tab = MalwareScannerWidget(run_cmd=self.execute_command, theme=self.theme, parent=self)
        sec_tabs.addTab(self.malware_tab, "🦠 Malware & Rootkits")

        # SSL Auditor
        self.ssl_tab = SSLAuditorWidget(run_cmd=self.execute_command, theme=self.theme, parent=self)
        sec_tabs.addTab(self.ssl_tab, "📜 SSL / TLS Auditor")

        layout.addWidget(sec_tabs)
        return widget

    def _execute_web_command(self, cmd: str, callback: Optional[Callable[[str, int], None]] = None) -> None:
        def on_done(out, err, rc):
            if callback:
                callback(out or err, rc)
        self.execute_command(cmd, on_done)

    # ------------------------------------------------------------
    # 3. Web Hosting Subsystem
    # ------------------------------------------------------------
    def _build_web_subsystem(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(2, 2, 2, 2)

        web_tabs = QTabWidget()

        self.vhosts_tab = VHostManagerWidget(self.services, self._execute_web_command, self)
        web_tabs.addTab(self.vhosts_tab, "🌐 Virtual Hosts (Nginx/Apache)")

        self.ssl_mgr_tab = SSLManagerWidget(self.services, self._execute_web_command, self)
        web_tabs.addTab(self.ssl_mgr_tab, "🔒 SSL Certificates (Certbot)")

        self.runtimes_tab = RuntimesManagerWidget(self.services, self._execute_web_command, self)
        web_tabs.addTab(self.runtimes_tab, "⚡ Application Runtimes (PHP/PM2)")

        self.logs_tab = WebLogAnalyzerWidget(self.services, self._execute_web_command, self)
        web_tabs.addTab(self.logs_tab, "📜 Web Log Analytics")

        self.tools_tab = WebToolsWidget(self.services, self._execute_web_command, self)
        web_tabs.addTab(self.tools_tab, "🛠️ Web Server Tools")

        layout.addWidget(web_tabs)
        return widget

    # ------------------------------------------------------------
    # 4. VAPT Subsystem
    # ------------------------------------------------------------
    def _build_vapt_subsystem(self) -> QWidget:
        initial_host = ""
        if getattr(self, "auto_refresh", True) and self.profile:
            host_candidate = self.profile.get("ssh_host") or self.profile.get("host") or ""
            if host_candidate and host_candidate not in ("localhost", "127.0.0.1"):
                initial_host = host_candidate

        return VaptTab(self.services, initial_target=initial_host, parent=self)

    # ------------------------------------------------------------
    # Refresh All
    # ------------------------------------------------------------
    def refresh_all(self):
        """Perform comprehensive health & security refresh."""
        if self.sudo_chk.isChecked():
            sudo_pw = self._get_sudo_password()
            if sudo_pw is None and not self._is_remote():
                try:
                    chk = subprocess.run(["sudo", "-n", "true"], capture_output=True, timeout=3)
                    if chk.returncode == 0:
                        self.services.set_sudo_password(self.profile_name, "")
                        sudo_pw = ""
                except Exception:
                    pass
            if sudo_pw is None and getattr(self, "isVisible", lambda: False)():
                user = ""
                if self.profile:
                    user = self.profile.get("ssh_user") or self.profile.get("user") or ""
                if not user:
                    user = getpass.getuser() if not self._is_remote() else ""
                dlg = SudoCredentialsDialog(self, self.services, self.profile_name, user)
                if dlg.exec() == QDialog.DialogCode.Accepted:
                    pw = dlg.get_password()
                    self.services.set_sudo_password(self.profile_name, pw, persist=dlg.should_save_to_profile())
                    if self.profile is not None:
                        self.profile["sudo_pass"] = pw

        try:
            self._refresh_current_sysadmin_section()
        except Exception as exc:
            self.services.emit_log("ADMIN", f"Error refreshing sysadmin section: {exc}", "WARNING")

        try:
            self.firewall_tab.refresh()
        except Exception as exc:
            self.services.emit_log("ADMIN", f"Error refreshing firewall: {exc}", "WARNING")

        try:
            self.fail2ban_tab.refresh()
        except Exception as exc:
            self.services.emit_log("ADMIN", f"Error refreshing fail2ban: {exc}", "WARNING")

        try:
            self.exposure_tab.refresh()
        except Exception as exc:
            self.services.emit_log("ADMIN", f"Error refreshing port exposure: {exc}", "WARNING")

        try:
            self.auditor_tab.refresh()
        except Exception as exc:
            self.services.emit_log("ADMIN", f"Error refreshing security auditor: {exc}", "WARNING")

        try:
            self.vhosts_tab.refresh()
        except Exception as exc:
            self.services.emit_log("ADMIN", f"Error refreshing virtual hosts: {exc}", "WARNING")

        try:
            self.network_widget.refresh_interfaces()
        except Exception as exc:
            self.services.emit_log("ADMIN", f"Error refreshing network interfaces: {exc}", "WARNING")

        # Update summary badges
        def on_badge_check(out, err, rc):
            fw_state = "UFW: Active" if "Status: active" in out else ("Firewalld: Active" if "running" in out else "FW: Inactive")
            f2b_state = "F2B: Active" if "active (running)" in out else "F2B: Inactive"
            web_state = "Nginx/Apache" if ("nginx.service" in out or "apache2.service" in out) else "Web: None"

            self.badge_fw.setText(fw_state)
            self.badge_f2b.setText(f2b_state)
            self.badge_web.setText(web_state)

        badge_cmd = "ufw status 2>/dev/null || true; firewall-cmd --state 2>/dev/null || true; systemctl status fail2ban 2>/dev/null || true; systemctl is-active nginx apache2 httpd 2>/dev/null || true"
        self.execute_command(badge_cmd, on_badge_check, use_sudo=False)
