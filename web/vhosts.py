"""
Virtual Host / Server Block Manager for Nginx, Apache, and Caddy.
Includes visual list, enable/disable toggling, config editor, and VHost Generator Wizard.
"""

from __future__ import annotations

import os
import shlex
from typing import Any, Callable, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QTextCursor
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
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

from admin_suite.ssh.remote_exec import RemoteExecThread
from admin_suite.core.export import ReportExporter


def generate_nginx_vhost(
    domain: str,
    aliases: str = "",
    site_type: str = "Static",
    doc_root: str = "/var/www/html",
    proxy_pass: str = "http://127.0.0.1:3000",
    php_socket: str = "unix:/run/php/php8.2-fpm.sock",
    enable_gzip: bool = True,
    enable_websockets: bool = True,
    enable_cors: bool = False,
    block_hidden: bool = True,
    sec_headers: bool = True,
) -> str:
    """Generate production-ready Nginx server block configuration."""
    server_names = domain
    if aliases:
        server_names += f" {aliases}"

    conf = [
        "server {",
        "    listen 80;",
        "    listen [::]:80;",
        f"    server_name {server_names};",
        "",
    ]

    if sec_headers:
        conf.extend([
            "    # Security headers",
            '    add_header X-Frame-Options "SAMEORIGIN" always;',
            '    add_header X-Content-Type-Options "nosniff" always;',
            '    add_header X-XSS-Protection "1; mode=block" always;',
            '    add_header Referrer-Policy "strict-origin-when-cross-origin" always;',
            "",
        ])

    if enable_gzip:
        conf.extend([
            "    # Gzip compression",
            "    gzip on;",
            "    gzip_types text/plain text/css application/json application/javascript text/xml application/xml+rss text/javascript image/svg+xml;",
            "    gzip_min_length 1024;",
            "",
        ])

    if block_hidden:
        conf.extend([
            "    # Block hidden files (.git, .env, etc.)",
            "    location ~ /\\.(?!well-known) {",
            "        deny all;",
            "        access_log off;",
            "        log_not_found off;",
            "    }",
            "",
        ])

    if site_type == "Static":
        conf.extend([
            f"    root {doc_root};",
            "    index index.html index.htm;",
            "",
            "    location / {",
            "        try_files $uri $uri/ =404;",
            "    }",
        ])
    elif site_type == "PHP-FPM":
        conf.extend([
            f"    root {doc_root};",
            "    index index.php index.html;",
            "",
            "    location / {",
            "        try_files $uri $uri/ /index.php?$query_string;",
            "    }",
            "",
            "    location ~ \\.php$ {",
            "        include snippets/fastcgi-php.conf;",
            f"        fastcgi_pass {php_socket};",
            "        fastcgi_read_timeout 300;",
            "    }",
        ])
    elif site_type in ("Reverse Proxy", "Node.js"):
        conf.extend([
            "    location / {",
            f"        proxy_pass {proxy_pass};",
            "        proxy_http_version 1.1;",
            "        proxy_set_header Host $host;",
            "        proxy_set_header X-Real-IP $remote_addr;",
            "        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;",
            "        proxy_set_header X-Forwarded-Proto $scheme;",
        ])
        if enable_websockets:
            conf.extend([
                "        proxy_set_header Upgrade $http_upgrade;",
                '        proxy_set_header Connection "upgrade";',
            ])
        if enable_cors:
            conf.extend([
                '        add_header Access-Control-Allow-Origin "*" always;',
                '        add_header Access-Control-Allow-Methods "GET, POST, OPTIONS, PUT, DELETE" always;',
                '        add_header Access-Control-Allow-Headers "Origin, X-Requested-With, Content-Type, Accept, Authorization" always;',
            ])
        conf.append("    }")

    conf.extend([
        "",
        f"    access_log /var/log/nginx/{domain}.access.log;",
        f"    error_log /var/log/nginx/{domain}.error.log warn;",
        "}",
    ])

    return "\n".join(conf)


class NewVHostDialog(QDialog):
    """Wizard for generating and deploying a new Virtual Host configuration."""

    def __init__(self, parent=None, default_server: str = "nginx"):
        super().__init__(parent)
        self.setWindowTitle("Create New Virtual Host")
        self.resize(720, 600)

        layout = QVBoxLayout(self)

        self.tabs = QTabWidget()

        # Settings tab
        settings_widget = QWidget()
        form = QFormLayout(settings_widget)

        self.server_type = QComboBox()
        self.server_type.addItems(["nginx", "apache2 / httpd", "caddy"])
        if "apache" in default_server or "httpd" in default_server:
            self.server_type.setCurrentText("apache2 / httpd")
        elif "caddy" in default_server:
            self.server_type.setCurrentText("caddy")
        else:
            self.server_type.setCurrentText("nginx")
        self.server_type.currentTextChanged.connect(self._on_server_changed)

        self.distro_target = QComboBox()
        self.distro_target.addItems(["Debian / Ubuntu (/etc/.../sites-available)", "RedHat / CentOS / Rocky (/etc/.../conf.d)"])
        self.distro_target.currentTextChanged.connect(self._update_preview)

        self.domain_in = QLineEdit()
        self.domain_in.setPlaceholderText("example.com")
        self.domain_in.textChanged.connect(self._update_preview)

        self.aliases_in = QLineEdit()
        self.aliases_in.setPlaceholderText("www.example.com api.example.com")
        self.aliases_in.textChanged.connect(self._update_preview)

        self.site_type = QComboBox()
        self.site_type.addItems(["Static", "PHP-FPM", "Reverse Proxy", "Node.js"])
        self.site_type.currentTextChanged.connect(self._on_type_changed)

        self.doc_root_in = QLineEdit("/var/www/example.com/html")
        self.doc_root_in.textChanged.connect(self._update_preview)

        self.access_log_in = QLineEdit()
        self.access_log_in.setPlaceholderText("Custom access log path (optional, leave blank for default)")
        self.access_log_in.textChanged.connect(self._update_preview)

        self.error_log_in = QLineEdit()
        self.error_log_in.setPlaceholderText("Custom error log path (optional, leave blank for default)")
        self.error_log_in.textChanged.connect(self._update_preview)

        self.proxy_pass_in = QLineEdit("http://127.0.0.1:3000")
        self.proxy_pass_in.textChanged.connect(self._update_preview)

        self.php_socket_in = QLineEdit("unix:/run/php/php8.2-fpm.sock")
        self.php_socket_in.textChanged.connect(self._update_preview)

        self.gzip_chk = QCheckBox("Enable Gzip compression")
        self.gzip_chk.setChecked(True)
        self.gzip_chk.stateChanged.connect(self._update_preview)

        self.ws_chk = QCheckBox("Support WebSockets upgrade headers")
        self.ws_chk.setChecked(True)
        self.ws_chk.stateChanged.connect(self._update_preview)

        self.cors_chk = QCheckBox("Allow Cross-Origin Resource Sharing (CORS)")
        self.cors_chk.setChecked(False)
        self.cors_chk.stateChanged.connect(self._update_preview)

        self.sec_headers_chk = QCheckBox("Inject security headers (X-Frame, nosniff, XSS)")
        self.sec_headers_chk.setChecked(True)
        self.sec_headers_chk.stateChanged.connect(self._update_preview)

        self.block_hidden_chk = QCheckBox("Block access to dotfiles (.env, .git)")
        self.block_hidden_chk.setChecked(True)
        self.block_hidden_chk.stateChanged.connect(self._update_preview)

        form.addRow("Web Server:", self.server_type)
        form.addRow("Target OS Layout:", self.distro_target)
        form.addRow("Primary Domain:", self.domain_in)
        form.addRow("Domain Aliases:", self.aliases_in)
        form.addRow("Application Type:", self.site_type)
        form.addRow("Document Root:", self.doc_root_in)
        form.addRow("Access Log Path:", self.access_log_in)
        form.addRow("Error Log Path:", self.error_log_in)
        form.addRow("Upstream Proxy URL:", self.proxy_pass_in)
        form.addRow("PHP FastCGI Socket:", self.php_socket_in)
        form.addRow("", self.gzip_chk)
        form.addRow("", self.ws_chk)
        form.addRow("", self.cors_chk)
        form.addRow("", self.sec_headers_chk)
        form.addRow("", self.block_hidden_chk)

        self.tabs.addTab(settings_widget, "⚙️ Configuration")

        # Preview tab
        preview_widget = QWidget()
        preview_layout = QVBoxLayout(preview_widget)
        self.preview_edit = QPlainTextEdit()
        self.preview_edit.setFont(QFont("JetBrains Mono, Consolas", 10))
        preview_layout.addWidget(self.preview_edit)
        self.tabs.addTab(preview_widget, "📄 Generated Config Preview")

        layout.addWidget(self.tabs, 1)

        # Buttons
        btn_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btn_box.accepted.connect(self._validate_and_accept)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

        self._on_type_changed(self.site_type.currentText())
        self._update_preview()

    def _on_server_changed(self, text: str):
        is_nginx = "nginx" in text
        self.block_hidden_chk.setEnabled(is_nginx)
        self.gzip_chk.setEnabled(is_nginx)
        self._update_preview()

    def _on_type_changed(self, text: str) -> None:
        is_proxy = text in ("Reverse Proxy", "Node.js")
        is_php = text == "PHP-FPM"
        is_static = text == "Static"

        self.proxy_pass_in.setEnabled(is_proxy)
        self.ws_chk.setEnabled(is_proxy)
        self.cors_chk.setEnabled(is_proxy)

        self.php_socket_in.setEnabled(is_php)
        self.doc_root_in.setEnabled(is_static or is_php)
        self._update_preview()

    def _update_preview(self) -> None:
        from admin_suite.web.commands import generate_apache_vhost, generate_caddy_vhost

        domain = self.domain_in.text().strip() or "example.com"
        server = self.server_type.currentText()
        aliases = self.aliases_in.text().strip()
        site_type = self.site_type.currentText()
        doc_root = self.doc_root_in.text().strip()
        proxy_pass = self.proxy_pass_in.text().strip()
        php_socket = self.php_socket_in.text().strip()
        sec_headers = self.sec_headers_chk.isChecked()
        access_log = self.access_log_in.text().strip()
        error_log = self.error_log_in.text().strip()

        if "apache" in server:
            config_text = generate_apache_vhost(
                domain=domain,
                aliases=aliases,
                site_type=site_type,
                doc_root=doc_root,
                proxy_pass=proxy_pass,
                php_socket=php_socket,
                access_log=access_log,
                error_log=error_log,
                sec_headers=sec_headers,
            )
        elif "caddy" in server:
            config_text = generate_caddy_vhost(
                domain=domain,
                aliases=aliases,
                site_type=site_type,
                doc_root=doc_root,
                proxy_pass=proxy_pass,
            )
        else:
            config_text = generate_nginx_vhost(
                domain=domain,
                aliases=aliases,
                site_type=site_type,
                doc_root=doc_root,
                proxy_pass=proxy_pass,
                php_socket=php_socket,
                enable_gzip=self.gzip_chk.isChecked(),
                enable_websockets=self.ws_chk.isChecked(),
                enable_cors=self.cors_chk.isChecked(),
                block_hidden=self.block_hidden_chk.isChecked(),
                sec_headers=sec_headers,
            )
            # If custom logs given for nginx, replace standard logs
            if access_log:
                config_text = config_text.replace(f"/var/log/nginx/{domain}.access.log", access_log)
            if error_log:
                config_text = config_text.replace(f"/var/log/nginx/{domain}.error.log", error_log)

        self.preview_edit.setPlainText(config_text)

    def _validate_and_accept(self) -> None:
        if not self.domain_in.text().strip():
            QMessageBox.warning(self, "Validation", "Primary domain name is required.")
            return
        self.accept()

    def get_result(self) -> dict[str, Any]:
        server_choice = "nginx"
        if "apache" in self.server_type.currentText():
            server_choice = "apache2"
        elif "caddy" in self.server_type.currentText():
            server_choice = "caddy"

        return {
            "server": server_choice,
            "domain": self.domain_in.text().strip(),
            "config": self.preview_edit.toPlainText(),
            "is_rhel": "RedHat" in self.distro_target.currentText(),
        }


class VHostManagerWidget(QWidget):
    """Virtual Host list, editor, and control panel."""

    status_message = pyqtSignal(str)

    def __init__(self, services, exec_fn: Callable[[str, Callable[[str, int], None]], None], parent=None):
        super().__init__(parent)
        self.services = services
        self.exec_fn = exec_fn  # Callback to run command remotely or locally

        self._active_file_path: Optional[str] = None
        self._vhost_entries: list[dict[str, str]] = []

        theme = self.services.theme.current

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # Toolbar
        toolbar = QHBoxLayout()

        self.filter_in = QLineEdit()
        self.filter_in.setPlaceholderText("🔍 Filter Virtual Hosts...")
        self.filter_in.textChanged.connect(self._apply_filter)
        toolbar.addWidget(self.filter_in, 1)

        new_btn = QPushButton("➕ New Virtual Host")
        new_btn.clicked.connect(self._create_new_vhost)
        toolbar.addWidget(new_btn)

        toggle_btn = QPushButton("⚡ Enable/Disable")
        toggle_btn.clicked.connect(self._toggle_selected_site)
        toolbar.addWidget(toggle_btn)

        test_btn = QPushButton("🩺 Test Syntax")
        test_btn.setToolTip("Run nginx -t or apachectl configtest")
        test_btn.clicked.connect(self._test_syntax)
        toolbar.addWidget(test_btn)

        reload_btn = QPushButton("🔄 Reload Server")
        reload_btn.setToolTip("Graceful zero-downtime reload (systemctl reload)")
        reload_btn.clicked.connect(self._reload_server)
        toolbar.addWidget(reload_btn)

        export_btn = QPushButton("📤 Export Sites")
        export_btn.setToolTip("Export virtual hosts list to CSV or JSON")
        export_btn.clicked.connect(self._export_sites_list)
        toolbar.addWidget(export_btn)

        refresh_btn = QPushButton("🔄 Refresh List")
        refresh_btn.clicked.connect(self.refresh)
        toolbar.addWidget(refresh_btn)

        layout.addLayout(toolbar)

        # Splitter: Left Table | Right Editor
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Table
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Server", "Site / Domain", "Status", "Path"])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        splitter.addWidget(self.table)

        # Editor Panel
        editor_panel = QWidget()
        editor_layout = QVBoxLayout(editor_panel)
        editor_layout.setContentsMargins(0, 0, 0, 0)

        editor_head = QHBoxLayout()
        self.editor_title = QLabel("Select a Virtual Host to edit")
        self.editor_title.setStyleSheet(f"font-weight:bold;color:{theme.get('accent', '#3daee9')};")
        editor_head.addWidget(self.editor_title, 1)

        self.save_btn = QPushButton("💾 Save Config")
        self.save_btn.setEnabled(False)
        self.save_btn.clicked.connect(self._save_config)
        editor_head.addWidget(self.save_btn)

        editor_layout.addLayout(editor_head)

        self.config_editor = QPlainTextEdit()
        self.config_editor.setFont(QFont("JetBrains Mono, Consolas", 10))
        ReportExporter.attach_export_context_menu(self.config_editor)
        editor_layout.addWidget(self.config_editor, 1)

        splitter.addWidget(editor_panel)
        splitter.setSizes([450, 550])

        layout.addWidget(splitter, 1)

    def refresh(self) -> None:
        """Scan available and enabled sites."""
        from admin_suite.web.commands import VHOST_DISCOVERY_CMD
        self.status_message.emit("Scanning Virtual Hosts...")

        def on_done(out: str, rc: int):
            self._parse_vhost_discovery(out)
            self.status_message.emit(f"Found {len(self._vhost_entries)} Virtual Host(s)")

        self.exec_fn(VHOST_DISCOVERY_CMD, on_done)

    def _parse_vhost_discovery(self, out: str) -> None:
        sections: dict[str, list[str]] = {
            "NGINX_AVAILABLE": [],
            "NGINX_ENABLED": [],
            "NGINX_CONFD": [],
            "NGINX_CONFD_DISABLED": [],
            "APACHE_DEBIAN_AVAILABLE": [],
            "APACHE_DEBIAN_ENABLED": [],
            "APACHE_RHEL_CONFD": [],
            "APACHE_RHEL_CONFD_DISABLED": [],
            "CADDY_FILE": [],
        }
        curr = None
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("=== ") and line.endswith(" ==="):
                curr = line[4:-4]
            elif curr and line and curr in sections:
                sections[curr].append(line)

        entries = []

        # 1. Nginx Debian (sites-available / sites-enabled)
        nginx_enabled = set(sections["NGINX_ENABLED"])
        for name in sections["NGINX_AVAILABLE"]:
            entries.append({
                "server": "nginx",
                "name": name,
                "enabled": name in nginx_enabled,
                "mode": "symlink",
                "path": f"/etc/nginx/sites-available/{name}",
                "enabled_path": f"/etc/nginx/sites-enabled/{name}",
            })

        # 2. Nginx RHEL (conf.d)
        for name in sections["NGINX_CONFD"]:
            entries.append({
                "server": "nginx",
                "name": name,
                "enabled": True,
                "mode": "confd",
                "path": f"/etc/nginx/conf.d/{name}",
                "enabled_path": f"/etc/nginx/conf.d/{name}",
            })
        for name in sections["NGINX_CONFD_DISABLED"]:
            clean_name = name.removesuffix(".disabled").removesuffix(".bak")
            entries.append({
                "server": "nginx",
                "name": clean_name,
                "enabled": False,
                "mode": "confd",
                "path": f"/etc/nginx/conf.d/{name}",
                "enabled_path": f"/etc/nginx/conf.d/{clean_name}",
            })

        # 3. Apache Debian (apache2 sites-available / sites-enabled)
        apache_enabled = set(sections["APACHE_DEBIAN_ENABLED"])
        for name in sections["APACHE_DEBIAN_AVAILABLE"]:
            entries.append({
                "server": "apache2",
                "name": name,
                "enabled": name in apache_enabled,
                "mode": "a2enmod",
                "path": f"/etc/apache2/sites-available/{name}",
                "enabled_path": f"/etc/apache2/sites-enabled/{name}",
            })

        # 4. Apache RHEL (httpd /etc/httpd/conf.d)
        for name in sections["APACHE_RHEL_CONFD"]:
            entries.append({
                "server": "httpd",
                "name": name,
                "enabled": True,
                "mode": "confd",
                "path": f"/etc/httpd/conf.d/{name}",
                "enabled_path": f"/etc/httpd/conf.d/{name}",
            })
        for name in sections["APACHE_RHEL_CONFD_DISABLED"]:
            clean_name = name.removesuffix(".disabled").removesuffix(".bak")
            entries.append({
                "server": "httpd",
                "name": clean_name,
                "enabled": False,
                "mode": "confd",
                "path": f"/etc/httpd/conf.d/{name}",
                "enabled_path": f"/etc/httpd/conf.d/{clean_name}",
            })

        # 5. Caddy
        if "exists" in sections.get("CADDY_FILE", []):
            entries.append({
                "server": "caddy",
                "name": "Caddyfile",
                "enabled": True,
                "mode": "single",
                "path": "/etc/caddy/Caddyfile",
                "enabled_path": "/etc/caddy/Caddyfile",
            })

        self._vhost_entries = entries
        self._render_table(entries)

    def _render_table(self, entries: list[dict[str, Any]]) -> None:
        theme = self.services.theme.current
        self.table.setRowCount(0)
        for row, item in enumerate(entries):
            self.table.insertRow(row)

            server_item = QTableWidgetItem(item["server"])
            name_item = QTableWidgetItem(item["name"])

            status_str = "● Enabled" if item["enabled"] else "Disabled"
            status_item = QTableWidgetItem(status_str)
            if item["enabled"]:
                status_item.setForeground(QColor(theme.get("ok", "#0dbc79")))
            else:
                status_item.setForeground(QColor(theme.get("sub", "#888888")))

            path_item = QTableWidgetItem(item["path"])

            for col, it in enumerate([server_item, name_item, status_item, path_item]):
                it.setData(Qt.ItemDataRole.UserRole, item)
                self.table.setItem(row, col, it)

    def _apply_filter(self, text: str) -> None:
        text = text.lower().strip()
        filtered = [
            e for e in self._vhost_entries
            if text in e["name"].lower() or text in e["server"].lower()
        ]
        self._render_table(filtered)

    def _on_selection_changed(self) -> None:
        selected = self.table.selectedItems()
        if not selected:
            return
        item_data = selected[0].data(Qt.ItemDataRole.UserRole)
        if not item_data:
            return

        path = item_data["path"]
        self._active_file_path = path
        self.editor_title.setText(f"Editing: {path}")
        self.status_message.emit(f"Loading {path}...")

        def on_done(out: str, rc: int):
            self.config_editor.setPlainText(out)
            self.save_btn.setEnabled(True)
            self.status_message.emit(f"Loaded {path}")

        self.exec_fn(f"cat {shlex.quote(path)} 2>/dev/null", on_done)

    def _save_config(self) -> None:
        if not self._active_file_path:
            return

        content = self.config_editor.toPlainText()
        import base64
        b64 = base64.b64encode(content.encode("utf-8")).decode("ascii")
        cmd = f"echo {b64} | base64 -d > {shlex.quote(self._active_file_path)}"

        self.status_message.emit(f"Saving {self._active_file_path}...")

        def on_saved(out: str, rc: int):
            if rc == 0:
                self.services.notifications.push("ok", "Virtual Host Saved", os.path.basename(self._active_file_path))
                self.status_message.emit("Configuration saved successfully. Remember to test syntax before reloading.")
            else:
                self.services.notifications.push("error", "Save Failed", out.strip())

        self.exec_fn(cmd, on_saved)

    def _toggle_selected_site(self) -> None:
        selected = self.table.selectedItems()
        if not selected:
            QMessageBox.information(self, "Selection", "Select a Virtual Host from the list first.")
            return
        item_data = selected[0].data(Qt.ItemDataRole.UserRole)
        server = item_data.get("server", "")
        name = item_data.get("name", "")
        mode = item_data.get("mode", "")
        path = item_data.get("path", "")
        currently_enabled = item_data.get("enabled", False)

        if mode == "single":
            QMessageBox.information(self, "Info", f"{name} is a global single-file configuration.")
            return

        if mode == "confd":
            # RHEL-style: rename .conf <-> .conf.disabled
            if currently_enabled:
                target_disabled = f"{path}.disabled"
                cmd = f"mv {shlex.quote(path)} {shlex.quote(target_disabled)}"
            else:
                target_enabled = item_data.get("enabled_path") or path.removesuffix(".disabled").removesuffix(".bak")
                cmd = f"mv {shlex.quote(path)} {shlex.quote(target_enabled)}"
        elif server == "nginx":
            if currently_enabled:
                cmd = f"rm -f /etc/nginx/sites-enabled/{shlex.quote(name)}"
            else:
                cmd = f"ln -sf /etc/nginx/sites-available/{shlex.quote(name)} /etc/nginx/sites-enabled/{shlex.quote(name)}"
        elif server in ("apache2", "httpd"):
            if mode == "a2enmod":
                cmd = f"a2dissite {shlex.quote(name)}" if currently_enabled else f"a2ensite {shlex.quote(name)}"
            else:
                if currently_enabled:
                    cmd = f"mv {shlex.quote(path)} {shlex.quote(path)}.disabled"
                else:
                    target_enabled = path.removesuffix(".disabled")
                    cmd = f"mv {shlex.quote(path)} {shlex.quote(target_enabled)}"
        else:
            return

        action_name = "Disable" if currently_enabled else "Enable"

        def on_done(out: str, rc: int):
            self.services.notifications.push("ok" if rc == 0 else "error", f"{action_name} {name}", out.strip() or "Done")
            self.refresh()

        self.exec_fn(cmd, on_done)

    def _test_syntax(self) -> None:
        selected = self.table.selectedItems()
        server = "nginx"
        if selected:
            server = selected[0].data(Qt.ItemDataRole.UserRole).get("server", "nginx")

        if server == "nginx":
            cmd = "nginx -t 2>&1"
        elif server in ("apache2", "httpd"):
            cmd = "apachectl configtest 2>&1 || apache2ctl configtest 2>&1 || httpd -t 2>&1"
        elif server == "caddy":
            cmd = "caddy validate --config /etc/caddy/Caddyfile 2>&1"
        else:
            cmd = "nginx -t 2>&1"

        self.status_message.emit(f"Running syntax check for {server}...")

        def on_done(out: str, rc: int):
            is_ok = rc == 0 and any(kw in out.lower() for kw in ("syntax is ok", "syntax ok", "valid", "successful"))
            if is_ok or rc == 0:
                QMessageBox.information(self, "Syntax Test Passed", f"✅ Configuration syntax is valid:\n\n{out.strip()}")
            else:
                QMessageBox.critical(self, "Syntax Test Failed", f"❌ Configuration syntax error detected:\n\n{out.strip()}")

        self.exec_fn(cmd, on_done)

    def _reload_server(self) -> None:
        selected = self.table.selectedItems()
        server = "nginx"
        if selected:
            server = selected[0].data(Qt.ItemDataRole.UserRole).get("server", "nginx")

        if server == "nginx":
            check_cmd = "nginx -t 2>&1"
        elif server in ("apache2", "httpd"):
            check_cmd = "apachectl configtest 2>&1 || apache2ctl configtest 2>&1 || httpd -t 2>&1"
        elif server == "caddy":
            check_cmd = "caddy validate --config /etc/caddy/Caddyfile 2>&1"
        else:
            check_cmd = "nginx -t 2>&1"

        reload_cmd = f"systemctl reload {server}"

        self.status_message.emit(f"Verifying syntax before reloading {server}...")

        def after_check(check_out: str, check_rc: int):
            if check_rc != 0:
                QMessageBox.critical(
                    self, "Reload Aborted",
                    f"Reload aborted because configuration syntax check failed:\n\n{check_out.strip()}"
                )
                return

            def after_reload(rel_out: str, rel_rc: int):
                if rel_rc == 0:
                    self.services.notifications.push("ok", f"{server} Reloaded", "Zero-downtime reload succeeded.")
                    self.status_message.emit(f"{server} reloaded successfully.")
                else:
                    self.services.notifications.push("error", f"{server} Reload Failed", rel_out.strip())

            self.exec_fn(reload_cmd, after_reload)

        self.exec_fn(check_cmd, after_check)

    def _export_sites_list(self) -> None:
        """Export virtual hosts inventory to CSV or JSON."""
        menu = QMenu(self)
        csv_act = menu.addAction("📊 Export as CSV File")
        json_act = menu.addAction("📄 Export as JSON File")
        pos = self.sender().mapToGlobal(self.sender().rect().bottomLeft()) if self.sender() else self.mapToGlobal(self.pos())
        action = menu.exec(pos)
        if action == csv_act:
            ReportExporter.export_table_csv(self, self.table, "vhosts_inventory.csv", "Export Virtual Hosts to CSV")
        elif action == json_act:
            ReportExporter.export_table_json(self, self.table, "vhosts_inventory.json", "Export Virtual Hosts to JSON")

    def _create_new_vhost(self) -> None:
        dlg = NewVHostDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        res = dlg.get_result()
        domain = res["domain"]
        server = res["server"]
        content = res["config"]

        # Safe fallback: create in sites-available if dir exists, else conf.d
        if server == "nginx":
            target_path = f"/etc/nginx/sites-available/{domain}.conf"
        else:
            target_path = f"/etc/apache2/sites-available/{domain}.conf"

        import base64
        b64 = base64.b64encode(content.encode("utf-8")).decode("ascii")
        # Script checks directory existence and places config appropriately for Debian or RHEL
        cmd = (
            f"if [ -d /etc/nginx/sites-available ] && [ '{server}' = 'nginx' ]; then "
            f"  echo {b64} | base64 -d > /etc/nginx/sites-available/{shlex.quote(domain)}.conf && echo '/etc/nginx/sites-available/{domain}.conf'; "
            f"elif [ -d /etc/nginx/conf.d ] && [ '{server}' = 'nginx' ]; then "
            f"  echo {b64} | base64 -d > /etc/nginx/conf.d/{shlex.quote(domain)}.conf && echo '/etc/nginx/conf.d/{domain}.conf'; "
            f"elif [ -d /etc/apache2/sites-available ]; then "
            f"  echo {b64} | base64 -d > /etc/apache2/sites-available/{shlex.quote(domain)}.conf && echo '/etc/apache2/sites-available/{domain}.conf'; "
            f"elif [ -d /etc/httpd/conf.d ]; then "
            f"  echo {b64} | base64 -d > /etc/httpd/conf.d/{shlex.quote(domain)}.conf && echo '/etc/httpd/conf.d/{domain}.conf'; "
            f"else "
            f"  echo {b64} | base64 -d > {shlex.quote(target_path)} && echo '{target_path}'; "
            f"fi"
        )

        def on_created(out: str, rc: int):
            actual_path = out.strip().splitlines()[-1] if out.strip() else target_path
            if rc == 0:
                self.services.notifications.push("ok", "Virtual Host Created", actual_path)
                if "/sites-available/" in actual_path:
                    if QMessageBox.question(
                        self, "Enable Site",
                        f"Virtual Host created at {actual_path}.\nDo you want to enable it now?"
                    ) == QMessageBox.StandardButton.Yes:
                        if server == "nginx":
                            en_cmd = f"ln -sf {shlex.quote(actual_path)} /etc/nginx/sites-enabled/{shlex.quote(domain)}.conf"
                        else:
                            en_cmd = f"a2ensite {shlex.quote(domain)}.conf"
                        self.exec_fn(en_cmd, lambda *_: self.refresh())
                    else:
                        self.refresh()
                else:
                    self.refresh()
            else:
                QMessageBox.critical(self, "Creation Failed", f"Failed to create Virtual Host:\n{out}")

        self.exec_fn(cmd, on_created)
