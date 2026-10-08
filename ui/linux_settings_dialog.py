"""
Linux Customization & Environment Settings Dialog.
Allows users to configure OS distribution preferences (Debian, RedHat, etc.),
firewall backend selection (UFW vs Firewalld vs iptables), and custom log file paths.
"""

from __future__ import annotations

from typing import Any, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)


class LinuxSettingsDialog(QDialog):
    """Configuration dialog for Linux distro adaptation, firewall choice, and log paths."""

    settings_applied = pyqtSignal()

    def __init__(self, services, parent=None):
        super().__init__(parent)
        self.services = services
        self.theme = services.theme.current

        self.setWindowTitle("⚙️ Linux Environment & Customization Settings")
        self.resize(580, 480)

        layout = QVBoxLayout(self)

        tabs = QTabWidget()

        # ---------------- Tab 1: OS & Distro ----------------
        os_tab = QWidget()
        os_layout = QVBoxLayout(os_tab)

        os_box = QGroupBox("Linux Distribution Family")
        os_form = QFormLayout(os_box)

        self.os_combo = QComboBox()
        self.os_combo.addItem("Auto-Detect from Target Host (/etc/os-release)", "auto")
        self.os_combo.addItem("Debian / Ubuntu / Linux Mint", "debian")
        self.os_combo.addItem("RedHat / CentOS / AlmaLinux / Rocky / Fedora", "rhel")
        self.os_combo.addItem("Arch Linux / Manjaro", "arch")
        self.os_combo.addItem("openSUSE / SLES", "suse")
        self.os_combo.addItem("Generic / Other Linux", "generic")

        saved_os = self.services.config.get("os_family", "auto")
        idx = self.os_combo.findData(saved_os)
        if idx >= 0:
            self.os_combo.setCurrentIndex(idx)

        os_form.addRow("Target Distro:", self.os_combo)
        os_layout.addWidget(os_box)

        # Distro notes
        desc_lbl = QLabel(
            "Selecting a distribution family controls the default paths and package tools:\n"
            "• Debian: apache2, /etc/apache2/sites-available, a2ensite, UFW, apt\n"
            "• RedHat: httpd, /etc/httpd/conf.d, firewalld, SELinux, dnf/yum\n"
            "• Auto: Discovers installed services and configurations dynamically on host."
        )
        desc_lbl.setStyleSheet(f"color: {self.theme.get('sub', '#888')}; padding: 6px; font-size: 11px;")
        desc_lbl.setWordWrap(True)
        os_layout.addWidget(desc_lbl)
        os_layout.addStretch()

        tabs.addTab(os_tab, "🐧 Distro Adaptation")

        # ---------------- Tab 2: Firewall ----------------
        fw_tab = QWidget()
        fw_layout = QVBoxLayout(fw_tab)

        fw_box = QGroupBox("Firewall Subsystem Preference")
        fw_form = QFormLayout(fw_box)

        self.fw_combo = QComboBox()
        self.fw_combo.addItem("Auto-Detect Active (Firewalld or UFW or iptables)", "auto")
        self.fw_combo.addItem("UFW — Uncomplicated Firewall (Ubuntu / Debian default)", "ufw")
        self.fw_combo.addItem("Firewalld — firewall-cmd (RHEL / CentOS / Rocky default)", "firewalld")
        self.fw_combo.addItem("iptables / nftables legacy CLI", "iptables")

        saved_fw = self.services.config.get("firewall_backend", "auto")
        idx_fw = self.fw_combo.findData(saved_fw)
        if idx_fw >= 0:
            self.fw_combo.setCurrentIndex(idx_fw)

        fw_form.addRow("Firewall Backend:", self.fw_combo)

        self.zone_edit = QLineEdit(self.services.config.get("firewall_zone", "public"))
        self.zone_edit.setPlaceholderText("e.g. public or external")
        fw_form.addRow("Default Firewalld Zone:", self.zone_edit)

        fw_layout.addWidget(fw_box)

        fw_note = QLabel(
            "Firewall manager will interact directly with your selected backend.\n"
            "When set to Auto-Detect, Admin Suite probes if firewalld or ufw is active on the host."
        )
        fw_note.setStyleSheet(f"color: {self.theme.get('sub', '#888')}; font-size: 11px; padding: 4px;")
        fw_note.setWordWrap(True)
        fw_layout.addWidget(fw_note)
        fw_layout.addStretch()

        tabs.addTab(fw_tab, "🛡️ Firewall (UFW / Firewalld)")

        # ---------------- Tab 3: Custom Log Paths ----------------
        log_tab = QWidget()
        log_layout = QVBoxLayout(log_tab)

        log_box = QGroupBox("User-Defined Web Access & Error Log Paths")
        box_layout = QVBoxLayout(log_box)

        path_row = QHBoxLayout()
        self.new_path_in = QLineEdit()
        self.new_path_in.setPlaceholderText("Enter custom log path, e.g. /var/log/httpd/access_log or /var/www/site/logs/access.log")
        path_row.addWidget(self.new_path_in, 1)

        add_btn = QPushButton("➕ Add Path")
        add_btn.clicked.connect(self._add_log_path)
        path_row.addWidget(add_btn)
        box_layout.addLayout(path_row)

        self.log_list = QListWidget()
        self.log_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)

        # Load existing custom paths
        saved_paths = self.services.config.get("custom_access_log_paths", [])
        if not saved_paths:
            # Common defaults
            saved_paths = [
                "/var/log/nginx/access.log",
                "/var/log/apache2/access.log",
                "/var/log/httpd/access_log",
                "/var/log/caddy/access.log",
            ]
        for p in saved_paths:
            self.log_list.addItem(p)

        box_layout.addWidget(self.log_list)

        btn_row = QHBoxLayout()
        rem_btn = QPushButton("🗑️ Remove Selected")
        rem_btn.clicked.connect(self._remove_log_path)
        btn_row.addWidget(rem_btn)

        preset_btn = QPushButton("📋 Load Common Presets")
        preset_btn.clicked.connect(self._load_presets)
        btn_row.addWidget(preset_btn)
        btn_row.addStretch()
        box_layout.addLayout(btn_row)

        log_layout.addWidget(log_box)

        tabs.addTab(log_tab, "📜 Custom Log Paths")

        layout.addWidget(tabs, 1)

        # Dialog buttons
        btn_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btn_box.accepted.connect(self._save_and_accept)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

    def _add_log_path(self):
        text = self.new_path_in.text().strip()
        if text:
            # Prevent duplicates
            for i in range(self.log_list.count()):
                if self.log_list.item(i).text() == text:
                    self.new_path_in.clear()
                    return
            self.log_list.addItem(text)
            self.new_path_in.clear()

    def _remove_log_path(self):
        row = self.log_list.currentRow()
        if row >= 0:
            self.log_list.takeItem(row)

    def _load_presets(self):
        presets = [
            "/var/log/nginx/access.log",
            "/var/log/nginx/error.log",
            "/var/log/apache2/access.log",
            "/var/log/apache2/error.log",
            "/var/log/httpd/access_log",
            "/var/log/httpd/error_log",
            "/var/log/caddy/access.log",
            "/var/log/messages",
            "/var/log/syslog",
        ]
        current = [self.log_list.item(i).text() for i in range(self.log_list.count())]
        for p in presets:
            if p not in current:
                self.log_list.addItem(p)

    def _save_and_accept(self):
        os_val = self.os_combo.currentData()
        fw_val = self.fw_combo.currentData()
        zone_val = self.zone_edit.text().strip() or "public"

        log_paths = [self.log_list.item(i).text() for i in range(self.log_list.count())]

        self.services.config.set("os_family", os_val)
        self.services.config.set("firewall_backend", fw_val)
        self.services.config.set("firewall_zone", zone_val)
        self.services.config.set("custom_access_log_paths", log_paths)
        self.services.config.save()

        self.services.notifications.push(
            "ok", "Settings Saved", f"Distro: {os_val} | Firewall: {fw_val}"
        )
        self.settings_applied.emit()
        self.accept()
