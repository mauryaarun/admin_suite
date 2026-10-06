"""
Unified Web Server & Hosting Management Tab.
Brings together Virtual Hosts, SSL/TLS, Application Runtimes, and Log Analytics.
"""

from __future__ import annotations

import shlex
import subprocess
from typing import Any, Callable, Optional

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from admin_suite.ssh.remote_exec import RemoteExecThread
from admin_suite.web.vhosts import VHostManagerWidget
from admin_suite.web.ssl_manager import SSLManagerWidget
from admin_suite.web.runtimes import RuntimesManagerWidget
from admin_suite.web.log_analyzer import WebLogAnalyzerWidget
from admin_suite.web.tools import WebToolsWidget


class WebManagerTab(QWidget):
    """Main Web Server and Website Hosting management tab."""

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
        self.profile = profile  # None -> local execution

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

        title = QLabel(f"🌐 Web Hosting Manager — {self.profile_name}")
        title.setStyleSheet(f"font-size: 14px; font-weight: bold; color: {theme.get('accent', '#3daee9')};")
        head_layout.addWidget(title)

        # Service Status Badges
        self.badge_nginx = QLabel("Nginx: ?")
        self.badge_apache = QLabel("Apache: ?")
        self.badge_php = QLabel("PHP-FPM: ?")
        self.badge_certbot = QLabel("Certbot: ?")
        self.badge_pm2 = QLabel("PM2: ?")

        for b in (self.badge_nginx, self.badge_apache, self.badge_php, self.badge_certbot, self.badge_pm2):
            b.setStyleSheet(
                f"border: 1px solid {theme.get('border', '#444')}; padding: 2px 6px; border-radius: 3px; font-size: 11px;"
            )
            head_layout.addWidget(b)

        head_layout.addStretch()

        self.sudo_chk = QCheckBox("Use sudo")
        self.sudo_chk.setChecked(True)
        head_layout.addWidget(self.sudo_chk)

        test_syntax_btn = QPushButton("🩺 Test Syntax")
        test_syntax_btn.clicked.connect(self._test_all_syntax)
        head_layout.addWidget(test_syntax_btn)

        refresh_all_btn = QPushButton("🔄 Refresh All")
        refresh_all_btn.clicked.connect(self.refresh_all)
        head_layout.addWidget(refresh_all_btn)

        layout.addWidget(head_frame)

        # Sub-Tabs
        self.tabs = QTabWidget()

        # 1. Virtual Hosts
        self.vhost_mgr = VHostManagerWidget(self.services, self.execute_command, self)
        self.vhost_mgr.status_message.connect(self._set_status)
        self.tabs.addTab(self.vhost_mgr, "🌐 Virtual Hosts")

        # 2. SSL/TLS Certificates
        self.ssl_mgr = SSLManagerWidget(self.services, self.execute_command, self)
        self.ssl_mgr.status_message.connect(self._set_status)
        self.tabs.addTab(self.ssl_mgr, "🔒 SSL / TLS (Certbot)")

        # 3. Application Runtimes
        self.runtimes_mgr = RuntimesManagerWidget(self.services, self.execute_command, self)
        self.runtimes_mgr.status_message.connect(self._set_status)
        self.tabs.addTab(self.runtimes_mgr, "⚡ Runtimes (PHP / PM2)")

        # 4. Logs & Analytics
        self.logs_mgr = WebLogAnalyzerWidget(self.services, self.execute_command, self)
        self.logs_mgr.status_message.connect(self._set_status)
        self.tabs.addTab(self.logs_mgr, "📜 Logs & Analytics")

        # 5. Web Tools & SELinux Policies (RHEL & Debian)
        self.tools_mgr = WebToolsWidget(self.services, self.execute_command, self)
        self.tools_mgr.status_message.connect(self._set_status)
        self.tabs.addTab(self.tools_mgr, "🛠️ Web Tools & SELinux")

        layout.addWidget(self.tabs, 1)

        # Status Bar
        self.status_bar = QLabel("Ready")
        self.status_bar.setStyleSheet(f"color: {theme.get('sub', '#888')}; padding: 2px;")
        layout.addWidget(self.status_bar)

        self.tabs.currentChanged.connect(self._on_subtab_changed)

        # Initial probe
        self._probe_web_services()
        self.vhost_mgr.refresh()

    def _set_status(self, msg: str) -> None:
        self.status_bar.setText(msg)

    def _on_subtab_changed(self, idx: int) -> None:
        widget = self.tabs.widget(idx)
        if hasattr(widget, "refresh"):
            widget.refresh()

    def refresh_all(self) -> None:
        self._probe_web_services()
        widget = self.tabs.currentWidget()
        if hasattr(widget, "refresh"):
            widget.refresh()

    def execute_command(self, cmd: str, callback: Callable[[str, int], None]) -> None:
        """Run command remotely over SSH or locally via subprocess, with optional sudo."""
        if self.sudo_chk.isChecked() and not cmd.strip().startswith("sudo"):
            escaped = cmd.replace("'", "'\\''")
            cmd = f"sudo bash -c '{escaped}'"

        self._cleanup_workers()

        worker = RemoteExecThread(
            profile=self.profile,
            cmd=cmd,
            timeout=60,
        )
        self._workers.append(worker)

        def on_finished(out: str, rc: int):
            callback(out, rc)

        worker.finished_cmd.connect(on_finished)
        worker.start()

    def _cleanup_workers(self) -> None:
        self._workers = [w for w in self._workers if w.isRunning()]

    def _probe_web_services(self) -> None:
        """Probe installed web servers, PHP-FPM, Certbot, and PM2."""
        from admin_suite.web.commands import WEB_DISCOVERY_CMD

        def on_done(out: str, rc: int):
            self._update_service_badges(out)

        self.execute_command(WEB_DISCOVERY_CMD, on_done)

    def _update_service_badges(self, out: str) -> None:
        theme = self.services.theme.current
        ok_color = theme.get("ok", "#0dbc79")
        sub_color = theme.get("sub", "#888888")

        for line in out.splitlines():
            line = line.strip()
            if "|" in line:
                parts = line.split("|")
                srv = parts[0]
                if srv in ("nginx", "apache2", "httpd", "caddy"):
                    installed = len(parts) > 1 and parts[1] == "installed"
                    active = len(parts) > 2 and parts[2] == "active"

                    target_badge = self.badge_nginx if srv == "nginx" else self.badge_apache
                    display_name = "Apache" if srv in ("apache2", "httpd") else srv.capitalize()
                    if active:
                        target_badge.setText(f"{display_name}: ● Active")
                        target_badge.setStyleSheet(f"border:1px solid {ok_color}; color:{ok_color}; padding:2px 6px; border-radius:3px;")
                    elif installed:
                        target_badge.setText(f"{display_name}: Inactive")
                        target_badge.setStyleSheet(f"border:1px solid {sub_color}; color:{sub_color}; padding:2px 6px; border-radius:3px;")
                    else:
                        target_badge.setText(f"{display_name}: Not Installed")
                elif "php" in srv and "fpm" in srv:
                    st = parts[1] if len(parts) > 1 else "inactive"
                    if st == "active":
                        self.badge_php.setText(f"PHP-FPM: ● Active")
                        self.badge_php.setStyleSheet(f"border:1px solid {ok_color}; color:{ok_color}; padding:2px 6px; border-radius:3px;")
                    elif self.badge_php.text() == "PHP-FPM: ?":
                        self.badge_php.setText(f"PHP-FPM: Inactive")
                        self.badge_php.setStyleSheet(f"border:1px solid {sub_color}; color:{sub_color}; padding:2px 6px; border-radius:3px;")
            elif line == "certbot_installed":
                self.badge_certbot.setText("Certbot: ● Ready")
                self.badge_certbot.setStyleSheet(f"border:1px solid {ok_color}; color:{ok_color}; padding:2px 6px; border-radius:3px;")
            elif line == "pm2_installed":
                self.badge_pm2.setText("PM2: ● Ready")
                self.badge_pm2.setStyleSheet(f"border:1px solid {ok_color}; color:{ok_color}; padding:2px 6px; border-radius:3px;")

    def _test_all_syntax(self) -> None:
        from admin_suite.web.commands import test_all_syntax_cmd
        cmd = test_all_syntax_cmd()
        self._set_status("Running pre-flight syntax checks...")

        def on_done(out: str, rc: int):
            self.tabs.setCurrentWidget(self.tools_mgr)
            self.tools_mgr.syntax_output.setPlainText(out)
            self.services.notifications.push("ok", "Syntax Checked", "Report displayed in Web Tools.")

        self.execute_command(cmd, on_done)

