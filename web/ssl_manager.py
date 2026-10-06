"""
SSL/TLS Certificate Manager with Let's Encrypt / Certbot integration,
expiration countdown tracker, and custom certificate installer.
"""

from __future__ import annotations

import datetime
import shlex
from typing import Any, Callable, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
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


class CertbotRequestDialog(QDialog):
    """Wizard for requesting new Let's Encrypt SSL certificates via Certbot."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Request Let's Encrypt SSL Certificate")
        self.resize(550, 420)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.domains_in = QLineEdit()
        self.domains_in.setPlaceholderText("example.com, www.example.com")

        self.email_in = QLineEdit()
        self.email_in.setPlaceholderText("admin@example.com")

        self.plugin_combo = QComboBox()
        self.plugin_combo.addItems([
            "--nginx (Auto-configure Nginx)",
            "--apache (Auto-configure Apache)",
            "--webroot (Specify web root path)",
            "--standalone (Temporary standalone server)",
        ])
        self.plugin_combo.currentTextChanged.connect(self._on_plugin_changed)

        self.webroot_in = QLineEdit("/var/www/html")
        self.webroot_in.setEnabled(False)

        self.dry_run_chk = QCheckBox("Test run only (--dry-run, does not issue real cert)")
        self.dry_run_chk.setChecked(False)

        self.agree_tos_chk = QCheckBox("Agree to Let's Encrypt Terms of Service (--agree-tos)")
        self.agree_tos_chk.setChecked(True)

        self.redirect_chk = QCheckBox("Automatically redirect HTTP traffic to HTTPS")
        self.redirect_chk.setChecked(True)

        form.addRow("Domains:", self.domains_in)
        form.addRow("Admin Email:", self.email_in)
        form.addRow("Authenticator:", self.plugin_combo)
        form.addRow("Webroot Path:", self.webroot_in)
        form.addRow("", self.dry_run_chk)
        form.addRow("", self.agree_tos_chk)
        form.addRow("", self.redirect_chk)

        layout.addLayout(form)

        btn_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btn_box.accepted.connect(self._validate)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

    def _on_plugin_changed(self, text: str) -> None:
        self.webroot_in.setEnabled("--webroot" in text)

    def _validate(self) -> None:
        if not self.domains_in.text().strip():
            QMessageBox.warning(self, "Validation", "At least one domain name is required.")
            return
        if not self.agree_tos_chk.isChecked():
            QMessageBox.warning(self, "Validation", "You must agree to the Terms of Service.")
            return
        self.accept()

    def get_command(self) -> str:
        domains = [d.strip() for d in self.domains_in.text().replace(" ", ",").split(",") if d.strip()]
        domain_args = " ".join([f"-d {shlex.quote(d)}" for d in domains])

        email = self.email_in.text().strip()
        email_arg = f"-m {shlex.quote(email)}" if email else "--register-unsafely-without-email"

        plugin_choice = self.plugin_combo.currentText()
        if "--nginx" in plugin_choice:
            cmd = f"certbot --nginx {domain_args} {email_arg} --non-interactive --agree-tos"
            if self.redirect_chk.isChecked():
                cmd += " --redirect"
        elif "--apache" in plugin_choice:
            cmd = f"certbot --apache {domain_args} {email_arg} --non-interactive --agree-tos"
            if self.redirect_chk.isChecked():
                cmd += " --redirect"
        elif "--webroot" in plugin_choice:
            wpath = self.webroot_in.text().strip() or "/var/www/html"
            cmd = f"certbot certonly --webroot -w {shlex.quote(wpath)} {domain_args} {email_arg} --non-interactive --agree-tos"
        else:  # standalone
            cmd = f"certbot certonly --standalone {domain_args} {email_arg} --non-interactive --agree-tos"

        if self.dry_run_chk.isChecked():
            cmd += " --dry-run"

        return cmd


class SSLManagerWidget(QWidget):
    """SSL/TLS Certificate inspector and manager."""

    status_message = pyqtSignal(str)

    def __init__(self, services, exec_fn: Callable[[str, Callable[[str, int], None]], None], parent=None):
        super().__init__(parent)
        self.services = services
        self.exec_fn = exec_fn
        self._cert_entries: list[dict[str, Any]] = []

        theme = self.services.theme.current

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # Toolbar
        toolbar = QHBoxLayout()

        title = QLabel("🔒 SSL / TLS Certificates")
        title.setStyleSheet(f"font-weight:bold;font-size:14px;color:{theme.get('accent', '#3daee9')};")
        toolbar.addWidget(title)
        toolbar.addStretch()

        req_btn = QPushButton("➕ Request Let's Encrypt Cert")
        req_btn.clicked.connect(self._request_cert)
        toolbar.addWidget(req_btn)

        dry_run_btn = QPushButton("🧪 Test Auto-Renewal")
        dry_run_btn.setToolTip("Runs certbot renew --dry-run")
        dry_run_btn.clicked.connect(self._test_renewal)
        toolbar.addWidget(dry_run_btn)

        renew_btn = QPushButton("🔄 Force Renew Selected")
        renew_btn.clicked.connect(self._renew_selected)
        toolbar.addWidget(renew_btn)

        refresh_btn = QPushButton("🔄 Refresh")
        refresh_btn.clicked.connect(self.refresh)
        toolbar.addWidget(refresh_btn)

        layout.addLayout(toolbar)

        # Certificate Table
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Certificate / Domain", "Issuer", "Expires On", "Days Left", "Status"])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.table, 1)

        # Output / details log
        self.log_output = QPlainTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setMaximumHeight(140)
        self.log_output.setFont(QFont("JetBrains Mono, Consolas", 9))
        self.log_output.setPlaceholderText("Execution log & renewal output...")
        layout.addWidget(self.log_output)

    def refresh(self) -> None:
        """Scan active SSL certificates."""
        from admin_suite.web.commands import SSL_INSPECTION_CMD
        self.status_message.emit("Scanning SSL/TLS certificates...")

        def on_done(out: str, rc: int):
            self._parse_ssl_inspection(out)
            self.status_message.emit(f"Found {len(self._cert_entries)} certificate(s)")

        self.exec_fn(SSL_INSPECTION_CMD, on_done)

    def _parse_ssl_inspection(self, out: str) -> None:
        entries = []
        raw_dates_active = False

        for line in out.splitlines():
            line = line.strip()
            if line == "=== RAW_CERT_DATES ===":
                raw_dates_active = True
                continue
            if not raw_dates_active:
                continue

            # format: domain|enddate|issuer
            if "|" in line:
                parts = line.split("|", 2)
                if len(parts) >= 2:
                    domain = parts[0]
                    end_str = parts[1]
                    issuer = parts[2] if len(parts) > 2 else "Let's Encrypt"

                    # Calculate days remaining
                    days_left = -1
                    try:
                        # OpenSSL enddate format e.g. "Oct 15 12:00:00 2026 GMT"
                        clean_date = end_str.replace(" GMT", "").strip()
                        exp_dt = datetime.datetime.strptime(clean_date, "%b %d %H:%M:%S %Y")
                        diff = exp_dt - datetime.datetime.utcnow()
                        days_left = diff.days
                    except Exception:
                        pass

                    entries.append({
                        "domain": domain,
                        "issuer": issuer,
                        "expires": end_str,
                        "days_left": days_left,
                    })

        self._cert_entries = entries
        self._render_table(entries)

    def _render_table(self, entries: list[dict[str, Any]]) -> None:
        theme = self.services.theme.current
        self.table.setRowCount(0)

        for row, itm in enumerate(entries):
            self.table.insertRow(row)

            domain_item = QTableWidgetItem(f"🔒 {itm['domain']}")
            issuer_item = QTableWidgetItem(itm["issuer"][:40])
            exp_item = QTableWidgetItem(itm["expires"])

            days = itm["days_left"]
            if days >= 0:
                days_item = QTableWidgetItem(f"{days} days")
                if days > 30:
                    status_item = QTableWidgetItem("● Active & Valid")
                    status_item.setForeground(QColor(theme.get("ok", "#0dbc79")))
                else:
                    status_item = QTableWidgetItem("⚠️ Expiring Soon")
                    status_item.setForeground(QColor(theme.get("warn", "#f39c12")))
            else:
                days_item = QTableWidgetItem("Unknown / Expired")
                status_item = QTableWidgetItem("❌ Expired")
                status_item.setForeground(QColor(theme.get("danger", "#ff5555")))

            for col, w_itm in enumerate([domain_item, issuer_item, exp_item, days_item, status_item]):
                w_itm.setData(Qt.ItemDataRole.UserRole, itm)
                self.table.setItem(row, col, w_itm)

    def _request_cert(self) -> None:
        dlg = CertbotRequestDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        cmd = dlg.get_command()
        self.log_output.appendPlainText(f"$ {cmd}")
        self.status_message.emit("Running Certbot issuance...")

        def on_done(out: str, rc: int):
            self.log_output.appendPlainText(out)
            if rc == 0:
                self.services.notifications.push("ok", "SSL Certificate Issued", "Certbot execution succeeded.")
                self.refresh()
            else:
                self.services.notifications.push("error", "Certbot Failed", out[:150])

        self.exec_fn(cmd, on_done)

    def _test_renewal(self) -> None:
        cmd = "certbot renew --dry-run"
        self.log_output.appendPlainText(f"$ {cmd}")
        self.status_message.emit("Testing certificate renewal...")

        def on_done(out: str, rc: int):
            self.log_output.appendPlainText(out)
            if rc == 0:
                QMessageBox.information(self, "Renewal Dry-Run Passed", "✅ All renewals simulated successfully.")
            else:
                QMessageBox.warning(self, "Renewal Dry-Run Warning", f"⚠️ Renewal check completed with warnings:\n\n{out[-300:]}")

        self.exec_fn(cmd, on_done)

    def _renew_selected(self) -> None:
        selected = self.table.selectedItems()
        if not selected:
            QMessageBox.information(self, "Selection", "Select a certificate to renew first.")
            return

        itm_data = selected[0].data(Qt.ItemDataRole.UserRole)
        domain = itm_data["domain"]

        cmd = f"certbot renew --cert-name {shlex.quote(domain)} --force-renewal"
        self.log_output.appendPlainText(f"$ {cmd}")
        self.status_message.emit(f"Force renewing {domain}...")

        def on_done(out: str, rc: int):
            self.log_output.appendPlainText(out)
            if rc == 0:
                self.services.notifications.push("ok", "SSL Renewed", domain)
                self.refresh()
            else:
                self.services.notifications.push("error", "Renewal Failed", out[:150])

        self.exec_fn(cmd, on_done)
