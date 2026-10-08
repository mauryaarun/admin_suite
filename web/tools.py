"""
Web Tools, SELinux Policies & Diagnostic Cockpit.
Provides SELinux Web Booleans manager for RHEL/CentOS/Fedora,
PHP Configuration & OPcache Inspector, Web Server Modules Inspector,
HTTP Endpoint Latency Benchmark, Permissions Fixer, DNS Diagnostic resolver, and Syntax Validator.
"""

from __future__ import annotations

import re
import shlex
from typing import Any, Callable, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
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


class WebToolsWidget(QWidget):
    """Integrated Web Server Diagnostics, SELinux Manager, PHP/Module Inspectors, and Network Probes."""

    status_message = pyqtSignal(str)

    def __init__(self, services, exec_fn: Callable[[str, Callable[[str, int], None]], None], parent=None):
        super().__init__(parent)
        self.services = services
        self.exec_fn = exec_fn
        theme = self.services.theme.current

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        tabs = QTabWidget()

        # ------------------------------------------------------------
        # Tab 1: SELinux Web Booleans (RHEL / CentOS / Alma / Rocky)
        # ------------------------------------------------------------
        selinux_tab = QWidget()
        selinux_layout = QVBoxLayout(selinux_tab)

        se_bar = QHBoxLayout()
        se_title = QLabel("🛡️ SELinux Web Hosting Policies (RHEL / CentOS / Fedora)")
        se_title.setStyleSheet(f"font-weight:bold;color:{theme.get('accent', '#3daee9')};")
        se_bar.addWidget(se_title)
        se_bar.addStretch()

        self.se_status_badge = QLabel("Status: Checking...")
        self.se_status_badge.setStyleSheet("padding: 2px 8px; border-radius: 4px; font-weight: bold; background: #333;")
        se_bar.addWidget(self.se_status_badge)

        se_refresh_btn = QPushButton("🔄 Refresh Booleans")
        se_refresh_btn.clicked.connect(self._refresh_selinux)
        se_bar.addWidget(se_refresh_btn)

        selinux_layout.addLayout(se_bar)

        se_desc = QLabel(
            "Configure SELinux booleans required for web servers to proxy to application ports, "
            "connect to remote databases, or read user directories. Changes are applied with -P (persistent)."
        )
        se_desc.setWordWrap(True)
        se_desc.setStyleSheet(f"color: {theme.get('sub', '#888')}; margin-bottom: 4px;")
        selinux_layout.addWidget(se_desc)

        self.se_booleans_group = QGroupBox("Web Hosting Booleans")
        se_grid = QGridLayout(self.se_booleans_group)

        self.bool_switches: dict[str, QCheckBox] = {}
        booleans_info = [
            ("httpd_can_network_connect", "Allow Reverse Proxy / Connect to upstream ports (Node.js, Python, Ruby, Go)"),
            ("httpd_can_network_connect_db", "Allow Database Connections (MySQL, PostgreSQL, MariaDB from web server/PHP)"),
            ("httpd_unified", "Unified Content (Allow unified full read/write/exec for web scripts)"),
            ("httpd_read_user_content", "User Content (Allow web server to read user home public_html)"),
            ("httpd_enable_homedirs", "Home Directories (Allow access to /home directories)"),
        ]

        for idx, (b_name, b_desc) in enumerate(booleans_info):
            chk = QCheckBox(f"{b_name} — {b_desc}")
            chk.setEnabled(False)
            self.bool_switches[b_name] = chk
            apply_btn = QPushButton("Apply")
            apply_btn.setMaximumWidth(80)
            apply_btn.clicked.connect(lambda _, n=b_name, c=chk: self._apply_boolean(n, c.isChecked()))
            se_grid.addWidget(chk, idx, 0)
            se_grid.addWidget(apply_btn, idx, 1)

        selinux_layout.addWidget(self.se_booleans_group)

        # Output pane for SELinux actions
        self.se_output = QPlainTextEdit()
        self.se_output.setReadOnly(True)
        self.se_output.setMaximumHeight(120)
        self.se_output.setFont(QFont("JetBrains Mono, Consolas", 9))
        self.se_output.setPlaceholderText("SELinux status details and command logs...")
        ReportExporter.attach_export_context_menu(self.se_output)
        selinux_layout.addWidget(self.se_output)

        tabs.addTab(selinux_tab, "🛡️ SELinux Policies")

        # ------------------------------------------------------------
        # Tab 2: PHP Configuration & OPcache Inspector
        # ------------------------------------------------------------
        php_tab = QWidget()
        php_layout = QVBoxLayout(php_tab)

        php_bar = QHBoxLayout()
        php_title = QLabel("🐘 PHP Configuration, php.ini & OPcache Inspector")
        php_title.setStyleSheet(f"font-weight:bold;color:{theme.get('accent', '#3daee9')};")
        php_bar.addWidget(php_title)
        php_bar.addStretch()

        php_refresh_btn = QPushButton("🔄 Inspect PHP")
        php_refresh_btn.clicked.connect(self._inspect_php)
        php_bar.addWidget(php_refresh_btn)

        php_export_btn = QPushButton("📤 Export PHP Info")
        php_export_btn.clicked.connect(lambda: ReportExporter.export_text_file(
            self, self.php_output.toPlainText(), "php_config_report.txt", "Export PHP Report"
        ))
        php_bar.addWidget(php_export_btn)
        php_layout.addLayout(php_bar)

        self.php_output = QPlainTextEdit()
        self.php_output.setReadOnly(True)
        self.php_output.setFont(QFont("JetBrains Mono, Consolas", 10))
        self.php_output.setPlaceholderText("Inspects PHP CLI version, upload_max_filesize, memory_limit, extensions, and OPcache...")
        ReportExporter.attach_export_context_menu(self.php_output)
        php_layout.addWidget(self.php_output, 1)

        tabs.addTab(php_tab, "🐘 PHP & OPcache")

        # ------------------------------------------------------------
        # Tab 3: Web Server Modules Inspector
        # ------------------------------------------------------------
        modules_tab = QWidget()
        modules_layout = QVBoxLayout(modules_tab)

        mod_bar = QHBoxLayout()
        mod_title = QLabel("🧩 Loaded Web Server Modules (Apache & Nginx)")
        mod_title.setStyleSheet(f"font-weight:bold;color:{theme.get('accent', '#3daee9')};")
        mod_bar.addWidget(mod_title)
        mod_bar.addStretch()

        mod_refresh_btn = QPushButton("🔄 Inspect Modules")
        mod_refresh_btn.clicked.connect(self._inspect_modules)
        mod_bar.addWidget(mod_refresh_btn)

        mod_export_btn = QPushButton("📤 Export Modules")
        mod_export_btn.clicked.connect(lambda: ReportExporter.export_text_file(
            self, self.modules_output.toPlainText(), "web_modules_report.txt", "Export Web Modules Report"
        ))
        mod_bar.addWidget(mod_export_btn)
        modules_layout.addLayout(mod_bar)

        self.modules_output = QPlainTextEdit()
        self.modules_output.setReadOnly(True)
        self.modules_output.setFont(QFont("JetBrains Mono, Consolas", 10))
        self.modules_output.setPlaceholderText("Lists compiled Nginx modules (nginx -V) and loaded Apache modules (apache2ctl -M / httpd -M)...")
        ReportExporter.attach_export_context_menu(self.modules_output)
        modules_layout.addWidget(self.modules_output, 1)

        tabs.addTab(modules_tab, "🧩 Server Modules")

        # ------------------------------------------------------------
        # Tab 4: HTTP Latency & Network Probe
        # ------------------------------------------------------------
        probe_tab = QWidget()
        probe_layout = QVBoxLayout(probe_tab)

        probe_bar = QHBoxLayout()
        probe_bar.addWidget(QLabel("Target URL:"))
        self.url_in = QLineEdit("http://127.0.0.1")
        self.url_in.setPlaceholderText("http://localhost:80 or https://example.com")
        self.url_in.returnPressed.connect(self._run_http_probe)
        probe_bar.addWidget(self.url_in, 1)

        probe_btn = QPushButton("🚀 Send HTTP Probe")
        probe_btn.clicked.connect(self._run_http_probe)
        probe_bar.addWidget(probe_btn)

        bench_btn = QPushButton("⏱️ Latency Breakdown")
        bench_btn.setToolTip("Break down DNS, TCP, TLS handshake, TTFB, and transfer time using curl")
        bench_btn.clicked.connect(self._run_latency_benchmark)
        probe_bar.addWidget(bench_btn)

        export_probe_btn = QPushButton("📤 Export Output")
        export_probe_btn.clicked.connect(lambda: ReportExporter.export_text_file(
            self, self.probe_output.toPlainText(), "http_probe_results.txt", "Export Probe Results"
        ))
        probe_bar.addWidget(export_probe_btn)

        probe_layout.addLayout(probe_bar)

        self.probe_output = QPlainTextEdit()
        self.probe_output.setReadOnly(True)
        self.probe_output.setFont(QFont("JetBrains Mono, Consolas", 10))
        self.probe_output.setPlaceholderText("HTTP Response headers, latency breakdown, and status codes will appear here...")
        ReportExporter.attach_export_context_menu(self.probe_output)
        probe_layout.addWidget(self.probe_output, 1)

        tabs.addTab(probe_tab, "🌐 HTTP & Latency Probe")

        # ------------------------------------------------------------
        # Tab 5: Web Root Permissions Fixer
        # ------------------------------------------------------------
        perm_tab = QWidget()
        perm_layout = QVBoxLayout(perm_tab)

        perm_bar = QHBoxLayout()
        perm_bar.addWidget(QLabel("Document Root:"))
        self.perm_dir_in = QLineEdit("/var/www/html")
        perm_bar.addWidget(self.perm_dir_in, 1)

        fix_btn = QPushButton("🔧 Fix Permissions (755 Dirs / 644 Files)")
        fix_btn.setToolTip("Sets directories to 755, files to 644, and ownership to www-data (Debian) or apache/nginx (RHEL)")
        fix_btn.clicked.connect(self._fix_permissions)
        perm_bar.addWidget(fix_btn)

        perm_layout.addLayout(perm_bar)

        perm_note = QLabel(
            "Safely applies standard web server permissions:\n"
            "• Directories: chmod 755 (drwxr-xr-x)\n"
            "• Files: chmod 644 (-rw-r--r--)\n"
            "• Ownership: www-data:www-data on Debian/Ubuntu, or apache:apache / nginx:nginx on RHEL/CentOS."
        )
        perm_note.setStyleSheet(f"color: {theme.get('sub', '#888')}; padding: 4px;")
        perm_layout.addWidget(perm_note)

        self.perm_output = QPlainTextEdit()
        self.perm_output.setReadOnly(True)
        self.perm_output.setFont(QFont("JetBrains Mono, Consolas", 10))
        self.perm_output.setPlaceholderText("Permission fix execution logs will appear here...")
        perm_layout.addWidget(self.perm_output, 1)

        tabs.addTab(perm_tab, "🔧 Permissions Fixer")

        # ------------------------------------------------------------
        # Tab 6: DNS Diagnostics
        # ------------------------------------------------------------
        dns_tab = QWidget()
        dns_layout = QVBoxLayout(dns_tab)

        dns_bar = QHBoxLayout()
        dns_bar.addWidget(QLabel("Domain Name:"))
        self.domain_in = QLineEdit("localhost")
        self.domain_in.setPlaceholderText("example.com")
        self.domain_in.returnPressed.connect(self._run_dns_probe)
        dns_bar.addWidget(self.domain_in, 1)

        dns_btn = QPushButton("🔍 Resolve DNS Records")
        dns_btn.clicked.connect(self._run_dns_probe)
        dns_bar.addWidget(dns_btn)

        export_dns_btn = QPushButton("📤 Export DNS Report")
        export_dns_btn.clicked.connect(lambda: ReportExporter.export_text_file(
            self, self.dns_output.toPlainText(), "dns_resolution_report.txt", "Export DNS Report"
        ))
        dns_bar.addWidget(export_dns_btn)

        dns_layout.addLayout(dns_bar)

        self.dns_output = QPlainTextEdit()
        self.dns_output.setReadOnly(True)
        self.dns_output.setFont(QFont("JetBrains Mono, Consolas", 10))
        self.dns_output.setPlaceholderText("A, AAAA, MX, and TXT DNS records resolution output...")
        ReportExporter.attach_export_context_menu(self.dns_output)
        dns_layout.addWidget(self.dns_output, 1)

        tabs.addTab(dns_tab, "🔎 DNS Diagnostics")

        # ------------------------------------------------------------
        # Tab 7: Multi-Server Syntax Validator
        # ------------------------------------------------------------
        syntax_tab = QWidget()
        syntax_layout = QVBoxLayout(syntax_tab)

        syntax_bar = QHBoxLayout()
        syntax_title = QLabel("🩺 Multi-Server Syntax Validator")
        syntax_title.setStyleSheet(f"font-weight:bold;color:{theme.get('accent', '#3daee9')};")
        syntax_bar.addWidget(syntax_title)
        syntax_bar.addStretch()

        check_syntax_btn = QPushButton("🩺 Run Syntax Check on All Servers")
        check_syntax_btn.clicked.connect(self._run_all_syntax_checks)
        syntax_bar.addWidget(check_syntax_btn)

        export_syntax_btn = QPushButton("📤 Export Report")
        export_syntax_btn.clicked.connect(lambda: ReportExporter.export_text_file(
            self, self.syntax_output.toPlainText(), "syntax_validation_report.txt", "Export Syntax Report"
        ))
        syntax_bar.addWidget(export_syntax_btn)

        syntax_layout.addLayout(syntax_bar)

        self.syntax_output = QPlainTextEdit()
        self.syntax_output.setReadOnly(True)
        self.syntax_output.setFont(QFont("JetBrains Mono, Consolas", 10))
        self.syntax_output.setPlaceholderText("Validates syntax for Nginx, Apache2 / HTTPD, and Caddy...")
        ReportExporter.attach_export_context_menu(self.syntax_output)
        syntax_layout.addWidget(self.syntax_output, 1)

        tabs.addTab(syntax_tab, "🩺 Syntax Validator")

        layout.addWidget(tabs, 1)

    def refresh(self) -> None:
        self._refresh_selinux()

    def _refresh_selinux(self) -> None:
        from admin_suite.web.commands import SELINUX_WEB_CMD
        self.status_message.emit("Inspecting SELinux status and booleans...")

        def on_done(out: str, rc: int):
            self.se_output.setPlainText(out)

            # Parse status
            is_enforcing = "enforcing" in out.lower()
            is_permissive = "permissive" in out.lower()
            is_disabled = "disabled" in out.lower() or "not installed" in out.lower()

            if is_enforcing:
                self.se_status_badge.setText("● SELinux: Enforcing")
                self.se_status_badge.setStyleSheet("padding: 2px 8px; border-radius: 4px; font-weight: bold; background: #0dbc79; color: black;")
            elif is_permissive:
                self.se_status_badge.setText("⚠️ SELinux: Permissive")
                self.se_status_badge.setStyleSheet("padding: 2px 8px; border-radius: 4px; font-weight: bold; background: #f39c12; color: black;")
            else:
                self.se_status_badge.setText("○ SELinux: Disabled / Deb-AppArmor")
                self.se_status_badge.setStyleSheet("padding: 2px 8px; border-radius: 4px; font-weight: bold; background: #555; color: white;")

            # Parse booleans
            bool_enabled_state = not is_disabled
            for b_name, chk in self.bool_switches.items():
                chk.setEnabled(bool_enabled_state)
                m = re.search(rf"{b_name}\s+(?:-->\s+)?(on|off)", out)
                if m:
                    chk.setChecked(m.group(1) == "on")
                else:
                    chk.setChecked(False)

            self.status_message.emit("SELinux status updated.")

        self.exec_fn(SELINUX_WEB_CMD, on_done)

    def _apply_boolean(self, bool_name: str, enable: bool) -> None:
        from admin_suite.web.commands import set_selinux_boolean_cmd
        cmd = set_selinux_boolean_cmd(bool_name, enable)
        self.status_message.emit(f"Applying SELinux boolean {bool_name}...")
        self.se_output.appendPlainText(f"$ {cmd}")

        def on_done(out: str, rc: int):
            if rc == 0:
                self.services.notifications.push("ok", "SELinux Boolean Updated", f"{bool_name} = {'on' if enable else 'off'}")
                self.se_output.appendPlainText("Successfully updated boolean.")
            else:
                self.services.notifications.push("error", "SELinux Update Failed", out[:100] or "Permission denied")
                self.se_output.appendPlainText(f"Error: {out}")
            self._refresh_selinux()

        self.exec_fn(cmd, on_done)

    def _inspect_php(self) -> None:
        from admin_suite.web.commands import php_config_inspector_cmd
        cmd = php_config_inspector_cmd()
        self.status_message.emit("Inspecting PHP configuration and OPcache...")
        self.php_output.setPlainText("Querying PHP runtime information...\n")

        def on_done(out: str, rc: int):
            self.php_output.setPlainText(out)
            self.status_message.emit("PHP inspection completed.")

        self.exec_fn(cmd, on_done)

    def _inspect_modules(self) -> None:
        from admin_suite.web.commands import web_modules_inspector_cmd
        cmd = web_modules_inspector_cmd()
        self.status_message.emit("Inspecting web server modules...")
        self.modules_output.setPlainText("Querying loaded modules...\n")

        def on_done(out: str, rc: int):
            self.modules_output.setPlainText(out)
            self.status_message.emit("Modules inspection completed.")

        self.exec_fn(cmd, on_done)

    def _run_http_probe(self) -> None:
        url = self.url_in.text().strip()
        if not url:
            return
        if not url.startswith("http://") and not url.startswith("https://"):
            url = f"http://{url}"
            self.url_in.setText(url)

        from admin_suite.web.commands import http_probe_cmd
        cmd = http_probe_cmd(url)
        self.status_message.emit(f"Probing {url}...")
        self.probe_output.setPlainText(f"$ {cmd}\n\nWaiting for response...\n")

        def on_done(out: str, rc: int):
            self.probe_output.setPlainText(f"$ {cmd}\n\n{out}")
            self.status_message.emit("HTTP probe completed.")

        self.exec_fn(cmd, on_done)

    def _run_latency_benchmark(self) -> None:
        url = self.url_in.text().strip()
        if not url:
            return
        if not url.startswith("http://") and not url.startswith("https://"):
            url = f"http://{url}"
            self.url_in.setText(url)

        from admin_suite.web.commands import http_latency_benchmark_cmd
        cmd = http_latency_benchmark_cmd(url)
        self.status_message.emit(f"Profiling latency for {url}...")
        self.probe_output.setPlainText(f"$ Running detailed latency breakdown for {url}...\n\n")

        def on_done(out: str, rc: int):
            self.probe_output.setPlainText(out)
            self.status_message.emit("Latency benchmark finished.")

        self.exec_fn(cmd, on_done)

    def _fix_permissions(self) -> None:
        doc_root = self.perm_dir_in.text().strip()
        if not doc_root:
            return
        from admin_suite.web.commands import web_permissions_fix_cmd
        os_fam = self.services.config.get("os_family", "auto")
        cmd = web_permissions_fix_cmd(doc_root, os_fam)
        self.status_message.emit(f"Fixing permissions for {doc_root}...")
        self.perm_output.setPlainText(f"$ {cmd}\n\nApplying permissions...\n")

        def on_done(out: str, rc: int):
            self.perm_output.setPlainText(out)
            self.status_message.emit("Permissions updated.")
            self.services.notifications.push("ok" if rc == 0 else "error", "Permissions Fix", out.strip()[:100])

        self.exec_fn(cmd, on_done)

    def _run_dns_probe(self) -> None:
        domain = self.domain_in.text().strip()
        if not domain:
            return

        from admin_suite.web.commands import dns_probe_cmd
        cmd = dns_probe_cmd(domain)
        self.status_message.emit(f"Resolving DNS for {domain}...")
        self.dns_output.setPlainText(f"$ Resolving {domain}...\n\n")

        def on_done(out: str, rc: int):
            self.dns_output.setPlainText(out)
            self.status_message.emit("DNS query completed.")

        self.exec_fn(cmd, on_done)

    def _run_all_syntax_checks(self) -> None:
        from admin_suite.web.commands import test_all_syntax_cmd
        cmd = test_all_syntax_cmd()
        self.status_message.emit("Testing configuration syntax across web servers...")
        self.syntax_output.setPlainText("Running syntax validation checks...\n")

        def on_done(out: str, rc: int):
            self.syntax_output.setPlainText(out)
            self.status_message.emit("Syntax validation finished.")
            if "syntax is ok" in out.lower() or "syntax ok" in out.lower():
                self.services.notifications.push("ok", "Syntax Valid", "Web server syntax check passed.")
            elif "fail" in out.lower() or "error" in out.lower():
                self.services.notifications.push("warn", "Syntax Warnings/Errors", "Check syntax validator report.")

        self.exec_fn(cmd, on_done)
