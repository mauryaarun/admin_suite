"""
SSH Hardening, Host Security, and Lynis Compliance Auditor for Admin Suite.
Audits sshd_config, kernel network parameters, sensitive file permissions,
and provides deep compliance scores.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from admin_suite.security.commands import SecurityCommands, SecurityParsers


class HardeningSnippetDialog(QDialog):
    """Dialog displaying recommended SSH hardening configuration snippet."""

    def __init__(self, snippet: str, theme: dict[str, str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Recommended SSH Hardening Configuration")
        self.resize(550, 420)

        layout = QVBoxLayout(self)

        info_lbl = QLabel(
            "Copy and save this configuration to:\n"
            "/etc/ssh/sshd_config.d/99-security-hardening.conf\n\n"
            "⚠️ IMPORTANT: Always test your connection in a separate terminal before closing your active session!"
        )
        info_lbl.setWordWrap(True)
        info_lbl.setStyleSheet("color: #ff9800; font-weight: bold; margin-bottom: 6px;")
        layout.addWidget(info_lbl)

        self.editor = QTextEdit()
        self.editor.setFont(QFont("Monospace", 10))
        self.editor.setPlainText(snippet)
        layout.addWidget(self.editor)

        btn_box = QHBoxLayout()
        btn_copy = QPushButton("📋 Copy to Clipboard")
        btn_copy.clicked.connect(lambda: QApplication.clipboard().setText(self.editor.toPlainText()))
        btn_box.addWidget(btn_copy)

        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.accept)
        btn_box.addWidget(btn_close)

        layout.addLayout(btn_box)


class HardeningAuditorWidget(QWidget):
    """Interactive GUI for SSH hardening and Linux host security audits."""

    def __init__(
        self,
        run_cmd: Callable[[str, Callable[[str, str, int], None]], None],
        theme: dict[str, str],
        parent=None,
    ):
        super().__init__(parent)
        self.run_cmd = run_cmd
        self.theme = theme

        self._ssh_checks: list[dict[str, Any]] = []
        self._ssh_score: int = 0
        self._probe_data: dict[str, Any] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(8)

        # Header Score Bar
        header_frame = QFrame()
        header_frame.setStyleSheet(
            f"background: {theme.get('panel2', '#222')}; border-radius: 4px; padding: 8px;"
        )
        header_layout = QHBoxLayout(header_frame)
        header_layout.setSpacing(16)

        v_score = QVBoxLayout()
        lbl_score_title = QLabel("SSH Security Posture Score")
        lbl_score_title.setStyleSheet(f"color: {theme.get('text_dim', '#888')}; font-size: 11px;")
        self.score_label = QLabel("Score: -- / 100")
        self.score_label.setStyleSheet("font-size: 18px; font-weight: bold; color: #4fc3f7;")
        v_score.addWidget(lbl_score_title)
        v_score.addWidget(self.score_label)
        header_layout.addLayout(v_score)

        self.score_bar = QProgressBar()
        self.score_bar.setRange(0, 100)
        self.score_bar.setValue(0)
        self.score_bar.setFixedHeight(18)
        self.score_bar.setStyleSheet(
            "QProgressBar { border: 1px solid #444; border-radius: 4px; text-align: center; } "
            "QProgressBar::chunk { background-color: #3daee9; }"
        )
        header_layout.addWidget(self.score_bar, stretch=1)

        self.btn_gen_snippet = QPushButton("📜 Hardening Snippet")
        self.btn_gen_snippet.clicked.connect(self._show_hardening_snippet)
        header_layout.addWidget(self.btn_gen_snippet)

        self.btn_run_lynis = QPushButton("🔍 Lynis Audit")
        self.btn_run_lynis.clicked.connect(self._on_run_lynis)
        header_layout.addWidget(self.btn_run_lynis)

        self.btn_refresh = QPushButton("🔄 Run Audit")
        self.btn_refresh.clicked.connect(self.refresh)
        header_layout.addWidget(self.btn_refresh)

        layout.addWidget(header_frame)

        # Host Kernel & System Checks Cards
        probe_frame = QFrame()
        probe_frame.setStyleSheet(
            f"background: {theme.get('panel2', '#222')}; border-radius: 4px; padding: 6px;"
        )
        probe_layout = QHBoxLayout(probe_frame)
        probe_layout.setSpacing(12)

        self.badge_syncookies = QLabel("SYN Cookies: ?")
        self.badge_ipforward = QLabel("IP Forward: ?")
        self.badge_shadow = QLabel("Shadow Perms: ?")
        self.badge_passwd = QLabel("Passwd Perms: ?")
        self.badge_sudoers = QLabel("Sudoers: ?")

        for b in (self.badge_syncookies, self.badge_ipforward, self.badge_shadow, self.badge_passwd, self.badge_sudoers):
            b.setStyleSheet(f"border: 1px solid {theme.get('border', '#444')}; padding: 3px 8px; border-radius: 3px; font-size: 11px;")
            probe_layout.addWidget(b)

        probe_layout.addStretch()
        layout.addWidget(probe_frame)

        # SSH Hardening Checks Table
        lbl_table = QLabel("🔐 SSH Configuration Checks & Recommendations:")
        lbl_table.setStyleSheet("font-weight: bold; margin-top: 4px;")
        layout.addWidget(lbl_table)

        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels([
            "Status",
            "Directive / Setting",
            "Current",
            "Recommended",
            "Assessment & Impact",
        ])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)

        layout.addWidget(self.table)

    def refresh(self):
        """Run SSH config audit and host probe."""
        self.run_cmd(SecurityCommands.read_sshd_config(), self._on_sshd_result)
        self.run_cmd(SecurityCommands.system_audit_probe(), self._on_probe_result)

    def _on_sshd_result(self, stdout: str, stderr: str, code: int):
        data = SecurityParsers.parse_sshd_config(stdout)
        self._ssh_checks = data["checks"]
        self._ssh_score = data["score"]

        self.score_label.setText(f"Score: {self._ssh_score} / 100")
        self.score_bar.setValue(self._ssh_score)

        if self._ssh_score >= 80:
            self.score_bar.setStyleSheet(
                "QProgressBar { border: 1px solid #444; border-radius: 4px; text-align: center; } "
                "QProgressBar::chunk { background-color: #2e7d32; }"
            )
        elif self._ssh_score >= 50:
            self.score_bar.setStyleSheet(
                "QProgressBar { border: 1px solid #444; border-radius: 4px; text-align: center; } "
                "QProgressBar::chunk { background-color: #f57f17; }"
            )
        else:
            self.score_bar.setStyleSheet(
                "QProgressBar { border: 1px solid #444; border-radius: 4px; text-align: center; } "
                "QProgressBar::chunk { background-color: #c62828; }"
            )

        self._populate_table(self._ssh_checks)

    def _populate_table(self, checks: list[dict[str, Any]]):
        self.table.setRowCount(len(checks))

        for row, c in enumerate(checks):
            # 0. Status
            st = c["status"]
            if st == "PASS":
                item_st = QTableWidgetItem("✅ PASS")
                item_st.setForeground(QColor("#44eb74"))
            elif st == "WARN":
                item_st = QTableWidgetItem("⚠️ WARN")
                item_st.setForeground(QColor("#ffb300"))
            else:
                item_st = QTableWidgetItem("❌ FAIL")
                item_st.setForeground(QColor("#ff3d00"))

            item_st.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 0, item_st)

            # 1. Directive
            item_dir = QTableWidgetItem(c["rule"])
            item_dir.setFont(QFont("Monospace", weight=QFont.Weight.Bold))
            self.table.setItem(row, 1, item_dir)

            # 2. Current
            item_curr = QTableWidgetItem(c["current"])
            item_curr.setFont(QFont("Monospace"))
            self.table.setItem(row, 2, item_curr)

            # 3. Recommended
            item_rec = QTableWidgetItem(c["recommended"])
            item_rec.setFont(QFont("Monospace"))
            item_rec.setForeground(QColor("#4fc3f7"))
            self.table.setItem(row, 3, item_rec)

            # 4. Description
            self.table.setItem(row, 4, QTableWidgetItem(c["description"]))

    def _on_probe_result(self, stdout: str, stderr: str, code: int):
        data = SecurityParsers.parse_system_audit_probe(stdout)
        self._probe_data = data

        # SYN Cookies
        if data.get("kernel_syncookies"):
            self.badge_syncookies.setText("SYN Cookies: ON ✅")
            self.badge_syncookies.setStyleSheet("background: #1b4d24; color: #44eb74; padding: 3px 8px; border-radius: 3px; font-size: 11px;")
        else:
            self.badge_syncookies.setText("SYN Cookies: OFF ⚠️")
            self.badge_syncookies.setStyleSheet("background: #4d3a1b; color: #ffb300; padding: 3px 8px; border-radius: 3px; font-size: 11px;")

        # IP Forward
        if not data.get("kernel_ipforward"):
            self.badge_ipforward.setText("IP Forward: OFF (Secure)")
            self.badge_ipforward.setStyleSheet("background: #1b4d24; color: #44eb74; padding: 3px 8px; border-radius: 3px; font-size: 11px;")
        else:
            self.badge_ipforward.setText("IP Forward: ON (Routing)")
            self.badge_ipforward.setStyleSheet("background: #4d3a1b; color: #ffb300; padding: 3px 8px; border-radius: 3px; font-size: 11px;")

        # Shadow perms
        sh_perms = data.get("shadow_perms", "unknown")
        if sh_perms in ("640", "600", "000"):
            self.badge_shadow.setText(f"/etc/shadow: {sh_perms} ✅")
            self.badge_shadow.setStyleSheet("background: #1b4d24; color: #44eb74; padding: 3px 8px; border-radius: 3px; font-size: 11px;")
        else:
            self.badge_shadow.setText(f"/etc/shadow: {sh_perms} ❌")
            self.badge_shadow.setStyleSheet("background: #4d1b1b; color: #eb4444; padding: 3px 8px; border-radius: 3px; font-size: 11px;")

        # Passwd perms
        pw_perms = data.get("passwd_perms", "unknown")
        self.badge_passwd.setText(f"/etc/passwd: {pw_perms}")

        # Sudoers
        sudoers = data.get("sudo_users", [])
        if sudoers:
            self.badge_sudoers.setText(f"Sudo Users: {', '.join(sudoers[:3])}")
        else:
            self.badge_sudoers.setText("Sudo Users: None detected")

    def _show_hardening_snippet(self):
        snippet = (
            "# ============================================================\n"
            "# Production SSH Security Hardening Profile\n"
            "# Drop-in file: /etc/ssh/sshd_config.d/99-security-hardening.conf\n"
            "# ============================================================\n\n"
            "# 1. Authentication\n"
            "PermitRootLogin no\n"
            "PasswordAuthentication no\n"
            "PermitEmptyPasswords no\n"
            "PubkeyAuthentication yes\n"
            "MaxAuthTries 3\n"
            "LoginGraceTime 30\n\n"
            "# 2. Cryptography & Key Exchange\n"
            "KexAlgorithms curve25519-sha256,curve25519-sha256@libssh.org,diffie-hellman-group16-sha512,diffie-hellman-group18-sha512\n"
            "Ciphers chacha20-poly1305@openssh.com,aes256-gcm@openssh.com,aes128-gcm@openssh.com\n"
            "MACs hmac-sha2-512-etm@openssh.com,hmac-sha2-256-etm@openssh.com\n\n"
            "# 3. Access Controls & Forwarding\n"
            "X11Forwarding no\n"
            "AllowAgentForwarding no\n"
            "AllowTcpForwarding yes\n"
            "MaxSessions 4\n\n"
            "# 4. Logging & Verification\n"
            "LogLevel VERBOSE\n"
        )
        dlg = HardeningSnippetDialog(snippet, self.theme, self)
        dlg.exec()

    def _on_run_lynis(self):
        if not self._probe_data.get("lynis_avail"):
            QMessageBox.information(
                self,
                "Lynis Not Found",
                "Lynis is not currently installed on this target host.\n\n"
                "To install Lynis:\n"
                "  • Ubuntu / Debian: sudo apt-get install lynis\n"
                "  • RHEL / Alma / Rocky: sudo dnf install lynis\n"
                "  • Arch Linux: sudo pacman -S lynis",
            )
            return

        cmd = "lynis audit system --quick"
        self.run_cmd(cmd, self._on_lynis_result)

    def _on_lynis_result(self, stdout: str, stderr: str, code: int):
        # Show Lynis output dialog
        dlg = QDialog(self)
        dlg.setWindowTitle("Lynis Audit Results")
        dlg.resize(750, 500)
        vbox = QVBoxLayout(dlg)

        edit = QTextEdit()
        edit.setFont(QFont("Monospace", 9))
        edit.setReadOnly(True)
        edit.setPlainText(stdout or stderr or "No output returned.")
        vbox.addWidget(edit)

        btn = QPushButton("Close")
        btn.clicked.connect(dlg.accept)
        vbox.addWidget(btn)

        dlg.exec()
