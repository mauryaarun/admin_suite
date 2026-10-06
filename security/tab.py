"""
Unified Security Hub Tab for Admin Suite.
Integrates Firewall Management, Fail2ban Intrusion Defense, Port Exposure Auditing,
and SSH Hardening into a single cockpit with local and remote SSH support.
"""

from __future__ import annotations

import shlex
import subprocess
from typing import Any, Callable, Optional

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
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
from admin_suite.security.firewall import FirewallManagerWidget
from admin_suite.security.fail2ban import Fail2banManagerWidget
from admin_suite.security.exposure import PortExposureWidget
from admin_suite.security.auditor import HardeningAuditorWidget


class _LocalExecWorker(QThread):
    finished_output = pyqtSignal(str, str, int)

    def __init__(self, cmd: str, parent=None):
        super().__init__(parent)
        self.cmd = cmd

    def run(self):
        try:
            res = subprocess.run(
                ["bash", "-c", self.cmd],
                capture_output=True,
                text=True,
                timeout=45,
            )
            self.finished_output.emit(res.stdout, res.stderr, res.returncode)
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

        title = QLabel(f"🛡️ Security Hub — {self.profile_name}")
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

        btn_full_audit = QPushButton("🚀 Run Full Audit")
        btn_full_audit.clicked.connect(self.refresh_all)
        head_layout.addWidget(btn_full_audit)

        layout.addWidget(head_frame)

        # Tab Widget
        self.tabs = QTabWidget()

        # 1. Firewall Manager
        self.firewall_tab = FirewallManagerWidget(
            run_cmd=self.execute_command,
            theme=theme,
            parent=self,
        )
        self.tabs.addTab(self.firewall_tab, "🛡️ Firewall (UFW)")

        # 2. Fail2ban Manager
        self.fail2ban_tab = Fail2banManagerWidget(
            run_cmd=self.execute_command,
            theme=theme,
            parent=self,
        )
        self.tabs.addTab(self.fail2ban_tab, "🚨 Intrusion Defense (Fail2ban)")

        # 3. Port Exposure Auditor
        self.exposure_tab = PortExposureWidget(
            run_cmd=self.execute_command,
            theme=theme,
            on_block_port_cb=self._on_block_port_requested,
            parent=self,
        )
        self.tabs.addTab(self.exposure_tab, "🌐 Port Exposure & Sockets")

        # 4. SSH & Server Hardening
        self.auditor_tab = HardeningAuditorWidget(
            run_cmd=self.execute_command,
            theme=theme,
            parent=self,
        )
        self.tabs.addTab(self.auditor_tab, "🔒 SSH & Host Hardening")

        layout.addWidget(self.tabs)

        self.refresh_all()

    def execute_command(self, cmd: str, callback: Callable[[str, str, int], None]):
        """Executes a command either locally or via SSH, with optional sudo elevation."""
        final_cmd = cmd
        if self.sudo_chk.isChecked() and not cmd.strip().startswith("sudo"):
            final_cmd = f"sudo bash -c {shlex.quote(cmd)}"

        if self.profile and self.profile.get("host"):
            worker = RemoteExecThread(self.profile, final_cmd)

            def _on_remote_done(out: str, err: str, code: int):
                if worker in self._workers:
                    self._workers.remove(worker)
                callback(out, err, code)

            worker.finished_output.connect(_on_remote_done)
            self._workers.append(worker)
            worker.start()
        else:
            worker = _LocalExecWorker(final_cmd)

            def _on_local_done(out: str, err: str, code: int):
                if worker in self._workers:
                    self._workers.remove(worker)
                callback(out, err, code)

            worker.finished_output.connect(_on_local_done)
            self._workers.append(worker)
            worker.start()

    def refresh_all(self):
        """Refreshes all security sub-modules and top badges."""
        self.firewall_tab.refresh()
        self.fail2ban_tab.refresh()
        self.exposure_tab.refresh()
        self.auditor_tab.refresh()

        # Update top badges
        self.execute_command(
            "ufw status 2>/dev/null | head -n 1 || echo 'Status: unknown'",
            self._update_fw_badge,
        )
        self.execute_command(
            "systemctl is-active fail2ban 2>/dev/null || echo 'inactive'",
            self._update_f2b_badge,
        )

    def _update_fw_badge(self, stdout: str, stderr: str, code: int):
        st = stdout.lower()
        if "active" in st:
            self.badge_fw.setText("Firewall: ACTIVE")
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
        # Switch to firewall tab and offer confirmation
        self.tabs.setCurrentIndex(0)
        confirm = QMessageBox.question(
            self,
            "Block Port in Firewall",
            f"Add UFW rule to DENY incoming traffic on port {port}/{proto}?\n\n(Command: ufw deny {port}/{proto})",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm == QMessageBox.StandardButton.Yes:
            from admin_suite.security.commands import SecurityCommands
            cmd = SecurityCommands.ufw_add_rule(
                port_or_service=str(port),
                proto=proto,
                action="deny",
                from_ip="any",
                comment=f"Blocked exposed socket {port}",
            )
            self.execute_command(cmd, lambda out, err, code: self.firewall_tab.refresh())
