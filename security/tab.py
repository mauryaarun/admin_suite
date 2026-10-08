"""
Unified Security Hub Tab for Admin Suite.
Integrates Multi-Linux Firewall Management (UFW & Firewalld), Fail2ban Intrusion Defense,
Port Exposure & Active Connections, SSH Hardening, Malware/Rootkit Auditing, and SSL/TLS Auditor.
"""

from __future__ import annotations

import shlex
import subprocess
from typing import Any, Callable, Optional

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from admin_suite.ssh.remote_exec import RemoteExecThread
from admin_suite.ui.dialogs import SudoCredentialsDialog, ensure_sudo_credentials
from admin_suite.security.firewall import FirewallManagerWidget
from admin_suite.security.fail2ban import Fail2banManagerWidget
from admin_suite.security.exposure import PortExposureWidget
from admin_suite.security.auditor import HardeningAuditorWidget
from admin_suite.security.malware_scanner import MalwareScannerWidget
from admin_suite.security.ssl_auditor import SSLAuditorWidget
from admin_suite.security.commands import SecurityCommands
from admin_suite.ui.linux_settings_dialog import LinuxSettingsDialog


class _LocalExecWorker(QThread):
    finished_output = pyqtSignal(str, str, int)

    def __init__(self, cmd: str, sudo_password: Optional[str] = None, parent=None):
        super().__init__(parent)
        self.cmd = cmd
        self.sudo_password = sudo_password

    def run(self):
        try:
            res = subprocess.Popen(
                ["bash", "-c", self.cmd],
                stdin=subprocess.PIPE if self.sudo_password is not None else None,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            out, err = res.communicate(
                input=(self.sudo_password + "\n") if self.sudo_password else None,
                timeout=60,
            )
            self.finished_output.emit(out, err or "", res.returncode)
        except Exception as ex:
            self.finished_output.emit("", str(ex), -1)


class SecurityHubTab(QWidget):
    """Unified Server, Network, and Hosting Security Hub tab."""

    def __init__(
        self,
        services,
        profile_name: str = "Local",
        profile: Optional[dict[str, Any]] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.services = services
        self.profile_name = profile_name or "Local"
        self.profile = profile

        self._workers: list[QThread] = []

        theme = self.services.theme.current

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # Header Bar
        head_frame = QFrame()
        head_frame.setStyleSheet(
            f"background: {theme.get('panel2', '#222')}; border-radius: 4px; padding: 4px;"
        )
        head_layout = QHBoxLayout(head_frame)
        head_layout.setContentsMargins(8, 4, 8, 4)

        is_local_display = (
            " (Localhost)"
            if (
                not self.profile
                or bool(self.profile.get("is_local"))
                or self.profile_name.lower() == "localhost"
                or (
                    (self.profile.get("ssh_host") or self.profile.get("host")) in ("localhost", "127.0.0.1")
                    and bool(self.profile.get("use_local_exec", True))
                )
            )
            else ""
        )
        title = QLabel(f"🛡️ Security Hub — {self.profile_name}{is_local_display}")
        title.setStyleSheet(f"font-size: 14px; font-weight: bold; color: {theme.get('accent', '#3daee9')};")
        head_layout.addWidget(title)

        # Quick Status Badges
        self.badge_fw = QLabel("Firewall: ?")
        self.badge_f2b = QLabel("Fail2ban: ?")
        self.badge_audit = QLabel("Posture: ?")

        for b in (self.badge_fw, self.badge_f2b, self.badge_audit):
            b.setStyleSheet(
                f"border: 1px solid {theme.get('border', '#444')}; padding: 2px 6px; border-radius: 3px; font-size: 11px;"
            )
            head_layout.addWidget(b)

        head_layout.addStretch()

        self.sudo_chk = QCheckBox("Use sudo")
        self.sudo_chk.setChecked(True)
        head_layout.addWidget(self.sudo_chk)

        self.sudo_btn = QPushButton("🔑")
        self.sudo_btn.setToolTip("Configure Root / Sudo credentials for this profile")
        self.sudo_btn.setFixedWidth(28)
        self.sudo_btn.clicked.connect(self._configure_sudo_credentials)
        head_layout.addWidget(self.sudo_btn)

        btn_settings = QPushButton("⚙️ Linux / Distro Settings")
        btn_settings.setToolTip("Configure Linux distro preference, firewall backend (UFW vs Firewalld), and log paths")
        btn_settings.clicked.connect(self._open_settings)
        head_layout.addWidget(btn_settings)

        btn_full_audit = QPushButton("🚀 Run Full Audit")
        btn_full_audit.clicked.connect(self.refresh_all)
        head_layout.addWidget(btn_full_audit)

        layout.addWidget(head_frame)

        # Tab Widget
        self.tabs = QTabWidget()

        # 1. Firewall Manager (UFW & Firewalld)
        self.firewall_tab = FirewallManagerWidget(
            run_cmd=self.execute_command,
            theme=theme,
            parent=self,
        )
        self.tabs.addTab(self.firewall_tab, "🛡️ Firewall (UFW / Firewalld)")

        # 2. Fail2ban Manager
        self.fail2ban_tab = Fail2banManagerWidget(
            run_cmd=self.execute_command,
            theme=theme,
            parent=self,
        )
        self.tabs.addTab(self.fail2ban_tab, "🚨 Intrusion Defense (Fail2ban)")

        # 3. Port Exposure & Active Connections Auditor
        self.exposure_tab = PortExposureWidget(
            run_cmd=self.execute_command,
            theme=theme,
            on_block_port_cb=self._on_block_port_requested,
            parent=self,
        )
        self.tabs.addTab(self.exposure_tab, "🌐 Port Exposure & Connections")

        # 4. SSH & Server Hardening
        self.auditor_tab = HardeningAuditorWidget(
            run_cmd=self.execute_command,
            theme=theme,
            parent=self,
        )
        self.tabs.addTab(self.auditor_tab, "🔒 SSH & Host Hardening")

        # 5. Malware, Rootkits & SUID Auditor
        self.malware_tab = MalwareScannerWidget(
            run_cmd=self.execute_command,
            theme=theme,
            parent=self,
        )
        self.tabs.addTab(self.malware_tab, "🦠 Malware & Rootkits")

        # 6. SSL/TLS Certificate Auditor
        self.ssl_tab = SSLAuditorWidget(
            run_cmd=self.execute_command,
            theme=theme,
            parent=self,
        )
        self.tabs.addTab(self.ssl_tab, "📜 SSL / TLS Auditor")

        # 7. VAPT & Web Application Security Audit
        from admin_suite.vapt.tab import VaptTab
        initial_host = ""
        if self.profile:
            host_candidate = self.profile.get("ssh_host") or self.profile.get("host") or ""
            if host_candidate and host_candidate not in ("localhost", "127.0.0.1"):
                initial_host = host_candidate
        self.vapt_tab = VaptTab(
            self.services,
            initial_target=initial_host,
            parent=self,
        )
        self.tabs.addTab(self.vapt_tab, "🔍 VAPT & Web Audit")

        layout.addWidget(self.tabs)

        self.refresh_all()

    def _open_settings(self):
        dlg = LinuxSettingsDialog(self.services, self)
        dlg.settings_applied.connect(self.refresh_all)
        dlg.exec()

    def _configure_sudo_credentials(self) -> None:
        import getpass
        user = ""
        if self.profile:
            user = self.profile.get("ssh_user") or self.profile.get("user") or ""
        if not user:
            is_local = (
                not self.profile
                or bool(self.profile.get("is_local"))
                or self.profile_name.lower() == "localhost"
            )
            user = getpass.getuser() if is_local else ""
        cur_pw = self.services.get_sudo_password(self.profile_name) or ""
        dlg = SudoCredentialsDialog(
            self,
            self.services,
            self.profile_name,
            user,
            current_password=cur_pw,
        )
        if dlg.exec() == QDialog.DialogCode.Accepted:
            pw = dlg.get_password()
            self.services.set_sudo_password(
                self.profile_name,
                pw,
                persist=dlg.should_save_to_profile(),
            )
            if dlg.should_save_to_profile() and self.profile is not None:
                self.profile["sudo_pass"] = pw
            self.services.notifications.push(
                "ok",
                "Root/Sudo Credentials",
                f"Updated elevation credentials for {self.profile_name}",
            )

    def execute_command(self, cmd: str, callback: Callable[[str, str, int], None]):
        """Executes a command either locally or via SSH, with optional sudo elevation."""
        sudo_pw = None
        if self.sudo_chk.isChecked() and not cmd.strip().startswith("sudo"):
            sudo_pw, ok = ensure_sudo_credentials(
                self, self.services, self.profile_name, self.profile
            )
            if not ok:
                callback("", "Execution cancelled: Root/sudo elevation required", 1)
                return

            if sudo_pw:
                escaped = cmd.replace("'", "'\\''")
                final_cmd = f"sudo -S -p '' bash -c '{escaped}'"
            else:
                final_cmd = f"sudo bash -c {shlex.quote(cmd)}"
        else:
            final_cmd = cmd

        is_remote = (
            self.profile
            and not bool(self.profile.get("is_local"))
            and self.profile_name.lower() != "localhost"
            and (self.profile.get("ssh_host") or self.profile.get("host")) not in ("localhost", "127.0.0.1", "::1")
        )

        if is_remote:
            worker = RemoteExecThread(self.profile, final_cmd, sudo_password=sudo_pw)
        else:
            worker = _LocalExecWorker(final_cmd, sudo_password=sudo_pw)

        def _on_done(out: str, err: str, code: int):
            if worker in self._workers:
                self._workers.remove(worker)
            if "sudo: a password is required" in (out + err) or "incorrect password attempt" in (out + err):
                self.services.clear_sudo_password(self.profile_name)
                self.services.notifications.push(
                    "error",
                    "Security Hub (Sudo Auth Failed)",
                    "Root/sudo authentication failed. Invalid password.",
                )
            callback(out, err, code)

        worker.finished_output.connect(_on_done)
        self._workers.append(worker)
        worker.start()

    def refresh_all(self):
        """Refreshes all security sub-modules and top badges."""
        self.firewall_tab.refresh()
        self.fail2ban_tab.refresh()
        self.exposure_tab.refresh()
        self.auditor_tab.refresh()
        self.malware_tab.refresh()
        self.ssl_tab.refresh()

        # Update top badges dynamically for both UFW and Firewalld
        check_fw_cmd = (
            "if which firewall-cmd >/dev/null 2>&1 && firewall-cmd --state >/dev/null 2>&1; then "
            "  echo 'firewalld: active'; "
            "elif which ufw >/dev/null 2>&1 && ufw status | grep -q 'Status: active'; then "
            "  echo 'ufw: active'; "
            "else "
            "  echo 'inactive'; "
            "fi"
        )
        self.execute_command(check_fw_cmd, self._update_fw_badge)
        self.execute_command(
            "systemctl is-active fail2ban 2>/dev/null || echo 'inactive'",
            self._update_f2b_badge,
        )

    def _update_fw_badge(self, stdout: str, stderr: str, code: int):
        st = stdout.lower().strip()
        if "firewalld: active" in st:
            self.badge_fw.setText("Firewall: ACTIVE (Firewalld)")
            self.badge_fw.setStyleSheet("background: #1b4d24; color: #44eb74; padding: 2px 6px; border-radius: 3px; font-size: 11px;")
        elif "ufw: active" in st:
            self.badge_fw.setText("Firewall: ACTIVE (UFW)")
            self.badge_fw.setStyleSheet("background: #1b4d24; color: #44eb74; padding: 2px 6px; border-radius: 3px; font-size: 11px;")
        else:
            self.badge_fw.setText("Firewall: INACTIVE")
            self.badge_fw.setStyleSheet("background: #4d1b1b; color: #eb4444; padding: 2px 6px; border-radius: 3px; font-size: 11px;")

    def _update_f2b_badge(self, stdout: str, stderr: str, code: int):
        st = stdout.strip().lower()
        if st == "active":
            self.badge_f2b.setText("Fail2ban: ACTIVE")
            self.badge_f2b.setStyleSheet("background: #1b4d24; color: #44eb74; padding: 2px 6px; border-radius: 3px; font-size: 11px;")
        else:
            self.badge_f2b.setText("Fail2ban: INACTIVE")
            self.badge_f2b.setStyleSheet("background: #4d1b1b; color: #eb4444; padding: 2px 6px; border-radius: 3px; font-size: 11px;")

    def _on_block_port_requested(self, port: int, proto: str):
        self.tabs.setCurrentIndex(0)
        confirm = QMessageBox.question(
            self,
            "Block Port in Firewall",
            f"Add firewall rule to DENY incoming traffic on port {port}/{proto}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm == QMessageBox.StandardButton.Yes:
            if self.firewall_tab.detected_backend == "firewalld":
                cmd = SecurityCommands.firewalld_deny_port(str(port), proto=proto)
            else:
                cmd = SecurityCommands.ufw_add_rule(
                    port_or_service=str(port),
                    proto=proto,
                    action="deny",
                    from_ip="any",
                    comment=f"Blocked exposed socket {port}",
                )
            self.execute_command(cmd, lambda out, err, code: self.firewall_tab.refresh())
