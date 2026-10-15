"""
VPN Configuration Dialog & WireGuard Server Setup GUI.
Provides comprehensive VPN Profiles management (WireGuard & Cisco AnyConnect)
and WireGuard VPN Server configuration on connected servers.
"""

from __future__ import annotations

import os
import re
import shutil
from typing import Any, Optional

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from admin_suite.vpn.profiles import (
    VpnProfileManager,
    build_wireguard_conf_content,
    find_cisco_binary,
    find_wireguard_binary,
    generate_wireguard_keys,
    parse_wireguard_conf_content,
)
from admin_suite.vpn.wireguard_server import WireGuardServerManager
from admin_suite.ssh.remote_exec import RemoteExecThread


class _ExecWorker(QThread):
    finished_result = pyqtSignal(int, str, str)

    def __init__(self, cmd: str, is_remote: bool, profile: Optional[dict[str, Any]], sudo_pass: Optional[str] = None, parent=None):
        super().__init__(parent)
        self.cmd = cmd
        self.is_remote = is_remote
        self.profile = profile
        self.sudo_pass = sudo_pass

    def run(self):
        if self.is_remote and self.profile:
            # Remote SSH execution
            from admin_suite.ssh.remote_exec import run_remote_ssh_command
            host = self.profile.get("ssh_host") or self.profile.get("host", "")
            port = int(self.profile.get("ssh_port") or self.profile.get("port", 22))
            user = self.profile.get("ssh_user") or self.profile.get("user", "")
            auth_method = self.profile.get("auth_method", "Password")
            password = self.profile.get("ssh_pass", "")
            if not password and self.parent() and hasattr(self.parent(), "services") and hasattr(self.parent().services, "secrets"):
                p_name = self.profile.get("name", "")
                password = self.parent().services.secrets.get(f"prof_{p_name}", "")
            key_path = self.profile.get("ssh_key_path", "")

            # If command requires sudo and sudo_pass is provided
            final_cmd = self.cmd
            if self.sudo_pass:
                final_cmd = f"echo '{self.sudo_pass}' | sudo -S -p '' bash -c {shlex_quote(self.cmd)}"

            try:
                out, err, rc = run_remote_ssh_command(
                    host=host,
                    port=port,
                    username=user,
                    auth_method=auth_method,
                    password=password,
                    key_path=key_path,
                    cmd=final_cmd,
                    timeout=90,
                )
                self.finished_result.emit(rc, out, err)
            except Exception as ex:
                self.finished_result.emit(1, "", str(ex))
        else:
            # Local execution
            import subprocess
            final_cmd = self.cmd
            input_text = None
            if self.sudo_pass:
                final_cmd = f"sudo -S -p '' bash -c {shlex_quote(self.cmd)}"
                input_text = self.sudo_pass + "\n"

            try:
                proc = subprocess.Popen(
                    ["bash", "-c", final_cmd],
                    stdin=subprocess.PIPE if input_text else subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                out, err = proc.communicate(input=input_text, timeout=90)
                self.finished_result.emit(proc.returncode, out, err)
            except Exception as ex:
                self.finished_result.emit(1, "", str(ex))


def shlex_quote(s: str) -> str:
    import shlex
    return shlex.quote(s)


class ProfileEditDialog(QDialog):
    """Dialog to create or edit a WireGuard or Cisco AnyConnect VPN profile."""

    def __init__(self, services, profile: Optional[dict[str, Any]] = None, parent=None):
        super().__init__(parent)
        self.services = services
        self.profile = profile or {}
        self.setWindowTitle("Edit VPN Profile" if profile else "Add VPN Profile")
        self.setMinimumSize(560, 480)

        theme = self.services.theme.current
        layout = QVBoxLayout(self)

        form = QFormLayout()

        self.name_edit = QLineEdit(self.profile.get("name", "New VPN"))
        form.addRow("Profile Name:", self.name_edit)

        self.type_combo = QComboBox()
        self.type_combo.addItems(["WireGuard", "Cisco AnyConnect"])
        if self.profile.get("type") == "cisco":
            self.type_combo.setCurrentText("Cisco AnyConnect")
        else:
            self.type_combo.setCurrentText("WireGuard")
        self.type_combo.currentTextChanged.connect(self._on_type_changed)
        form.addRow("VPN Type:", self.type_combo)

        layout.addLayout(form)

        # WireGuard Stack Page
        self.wg_frame = QFrame()
        wg_layout = QFormLayout(self.wg_frame)
        wg_layout.setContentsMargins(0, 4, 0, 4)

        self.wg_conf_path = QLineEdit(self.profile.get("config_path", ""))
        btn_browse_conf = QPushButton("Browse .conf...")
        btn_browse_conf.clicked.connect(self._browse_conf_file)
        row_conf = QHBoxLayout()
        row_conf.addWidget(self.wg_conf_path)
        row_conf.addWidget(btn_browse_conf)
        wg_layout.addRow("Existing Config File (.conf):", row_conf)

        self.wg_address = QLineEdit(self.profile.get("address", "10.8.0.2/24"))
        wg_layout.addRow("Interface Address:", self.wg_address)

        self.wg_privkey = QLineEdit(self.profile.get("private_key", ""))
        self.wg_privkey.setEchoMode(QLineEdit.EchoMode.Password)
        btn_gen_keys = QPushButton("🔑 Gen Keys")
        btn_gen_keys.clicked.connect(self._generate_wg_keys)
        row_priv = QHBoxLayout()
        row_priv.addWidget(self.wg_privkey)
        row_priv.addWidget(btn_gen_keys)
        wg_layout.addRow("Client Private Key:", row_priv)

        self.wg_dns = QLineEdit(self.profile.get("dns", "1.1.1.1, 8.8.8.8"))
        wg_layout.addRow("DNS Servers:", self.wg_dns)

        self.wg_pubkey = QLineEdit(self.profile.get("public_key", ""))
        wg_layout.addRow("Server Public Key:", self.wg_pubkey)

        self.wg_endpoint = QLineEdit(self.profile.get("endpoint", ""))
        self.wg_endpoint.setPlaceholderText("vpn.example.com:51820")
        wg_layout.addRow("Server Endpoint (Host:Port):", self.wg_endpoint)

        self.wg_allowed_ips = QLineEdit(self.profile.get("allowed_ips", "0.0.0.0/0, ::/0"))
        wg_layout.addRow("Allowed IPs:", self.wg_allowed_ips)

        self.wg_keepalive = QLineEdit(self.profile.get("persistent_keepalive", "25"))
        wg_layout.addRow("Persistent Keepalive (s):", self.wg_keepalive)

        layout.addWidget(self.wg_frame)

        # Cisco Stack Page
        self.cisco_frame = QFrame()
        cisco_layout = QFormLayout(self.cisco_frame)
        cisco_layout.setContentsMargins(0, 4, 0, 4)

        default_bin = self.profile.get("cisco_bin") or find_cisco_binary()
        self.cisco_bin_edit = QLineEdit(default_bin)
        btn_browse_cisco = QPushButton("Browse...")
        btn_browse_cisco.clicked.connect(self._browse_cisco_bin)
        row_bin = QHBoxLayout()
        row_bin.addWidget(self.cisco_bin_edit)
        row_bin.addWidget(btn_browse_cisco)
        cisco_layout.addRow("Cisco Binary Path:", row_bin)

        self.cisco_host_edit = QLineEdit(self.profile.get("cisco_host", ""))
        self.cisco_host_edit.setPlaceholderText("vpn.company.com")
        cisco_layout.addRow("Gateway / Host:", self.cisco_host_edit)

        self.cisco_group_edit = QLineEdit(self.profile.get("cisco_group", ""))
        cisco_layout.addRow("Group (Optional):", self.cisco_group_edit)

        self.cisco_user_edit = QLineEdit(self.profile.get("cisco_user", ""))
        cisco_layout.addRow("Username:", self.cisco_user_edit)

        prof_id = self.profile.get("id", "")
        existing_pass = self.services.secrets.get(f"vpn_prof_{prof_id}_pass", "") if prof_id else ""
        existing_cert = self.services.secrets.get(f"vpn_prof_{prof_id}_cert_pass", "") if prof_id else ""

        self.cisco_pass_edit = QLineEdit(existing_pass)
        self.cisco_pass_edit.setEchoMode(QLineEdit.EchoMode.Password)
        cisco_layout.addRow("Password:", self.cisco_pass_edit)

        self.cisco_cert_edit = QLineEdit(existing_cert)
        self.cisco_cert_edit.setEchoMode(QLineEdit.EchoMode.Password)
        cisco_layout.addRow("Cert Password:", self.cisco_cert_edit)

        layout.addWidget(self.cisco_frame)

        self._on_type_changed(self.type_combo.currentText())

        # Buttons
        btn_box = QHBoxLayout()
        btn_box.addStretch()
        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        btn_box.addWidget(btn_cancel)

        btn_save = QPushButton("💾 Save Profile")
        btn_save.setStyleSheet(f"background:{theme.get('accent', '#3daee9')}; color:#fff; font-weight:bold;")
        btn_save.clicked.connect(self._save)
        btn_box.addWidget(btn_save)

        layout.addLayout(btn_box)

    def _on_type_changed(self, text: str):
        is_wg = (text == "WireGuard")
        self.wg_frame.setVisible(is_wg)
        self.cisco_frame.setVisible(not is_wg)

    def _browse_conf_file(self):
        fpath, _ = QFileDialog.getOpenFileName(self, "Open WireGuard Config", "", "WireGuard Config (*.conf);;All Files (*)")
        if fpath:
            self.wg_conf_path.setText(fpath)
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    parsed = parse_wireguard_conf_content(f.read())
                    if parsed.get("address"):
                        self.wg_address.setText(parsed["address"])
                    if parsed.get("private_key"):
                        self.wg_privkey.setText(parsed["private_key"])
                    if parsed.get("dns"):
                        self.wg_dns.setText(parsed["dns"])
                    if parsed.get("public_key"):
                        self.wg_pubkey.setText(parsed["public_key"])
                    if parsed.get("endpoint"):
                        self.wg_endpoint.setText(parsed["endpoint"])
                    if parsed.get("allowed_ips"):
                        self.wg_allowed_ips.setText(parsed["allowed_ips"])
                    if parsed.get("persistent_keepalive"):
                        self.wg_keepalive.setText(parsed["persistent_keepalive"])
            except Exception as ex:
                QMessageBox.warning(self, "Import Warning", f"Could not parse file: {ex}")

    def _generate_wg_keys(self):
        priv, pub = generate_wireguard_keys()
        self.wg_privkey.setText(priv)
        QMessageBox.information(self, "Key Generated", f"WireGuard Client Private Key generated successfully.\nPublic Key: {pub}")

    def _browse_cisco_bin(self):
        fpath, _ = QFileDialog.getOpenFileName(self, "Select Cisco AnyConnect Binary", "/opt/cisco", "Binary (*);;All Files (*)")
        if fpath:
            self.cisco_bin_edit.setText(fpath)

    def _save(self):
        name = self.name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "Validation", "Profile name cannot be empty.")
            return

        is_wg = (self.type_combo.currentText() == "WireGuard")
        prof_data = dict(self.profile)
        prof_data["name"] = name
        prof_data["type"] = "wireguard" if is_wg else "cisco"

        secrets = {}

        if is_wg:
            prof_data["config_path"] = self.wg_conf_path.text().strip()
            prof_data["address"] = self.wg_address.text().strip()
            prof_data["private_key"] = self.wg_privkey.text().strip()
            prof_data["dns"] = self.wg_dns.text().strip()
            prof_data["public_key"] = self.wg_pubkey.text().strip()
            prof_data["endpoint"] = self.wg_endpoint.text().strip()
            prof_data["allowed_ips"] = self.wg_allowed_ips.text().strip()
            prof_data["persistent_keepalive"] = self.wg_keepalive.text().strip()
            prof_data["config_type"] = "file" if prof_data["config_path"] else "manual"
        else:
            prof_data["cisco_bin"] = self.cisco_bin_edit.text().strip()
            prof_data["cisco_host"] = self.cisco_host_edit.text().strip()
            prof_data["cisco_group"] = self.cisco_group_edit.text().strip()
            prof_data["cisco_user"] = self.cisco_user_edit.text().strip()
            secrets["pass"] = self.cisco_pass_edit.text()
            secrets["cert_pass"] = self.cisco_cert_edit.text()

        manager = VpnProfileManager(self.services)
        saved = manager.save_or_update_profile(prof_data, secrets=secrets)
        self.saved_profile = saved
        self.accept()


class VpnConfigDialog(QDialog):
    """
    Main VPN Configuration Dialog.
    Contains:
      Tab 1: 🔒 VPN Profiles (WireGuard & Cisco AnyConnect with connect/disconnect & active state)
      Tab 2: 🖥️ WireGuard Server Setup (Configure WireGuard server on currently connected server)
    """

    def __init__(self, services, parent=None, initial_tab: int = 0):
        super().__init__(parent)
        self.services = services
        self.parent_win = parent
        self.manager = VpnProfileManager(services)
        self._workers: list[QThread] = []

        self.setWindowTitle("🔒 VPN Configuration & WireGuard Server Manager")
        self.resize(880, 620)

        theme = self.services.theme.current
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)

        # Tab 1: Profiles
        self.profiles_tab = self._build_profiles_tab(theme)
        self.tabs.addTab(self.profiles_tab, "🔒 VPN Profiles")

        # Tab 2: WireGuard Server Setup
        self.server_tab = self._build_server_setup_tab(theme)
        self.tabs.addTab(self.server_tab, "🖥️ WireGuard Server Setup (On Server)")

        self.tabs.setCurrentIndex(initial_tab)

        # Bottom close bar
        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.accept)
        row_bot = QHBoxLayout()
        row_bot.addStretch()
        row_bot.addWidget(btn_close)
        layout.addLayout(row_bot)

        self._refresh_profiles()

    # ============================================================
    # TAB 1: VPN PROFILES
    # ============================================================

    def _build_profiles_tab(self, theme: dict[str, Any]) -> QWidget:
        widget = QWidget()
        layout = QHBoxLayout(widget)

        # Left list of profiles
        left_box = QVBoxLayout()
        lbl_list = QLabel("Configured VPN Profiles:")
        lbl_list.setStyleSheet(f"font-weight:bold; color:{theme.get('accent', '#3daee9')};")
        left_box.addWidget(lbl_list)

        self.profile_list = QListWidget()
        self.profile_list.setFixedWidth(260)
        self.profile_list.currentRowChanged.connect(self._on_profile_selected)
        left_box.addWidget(self.profile_list, 1)

        btn_row1 = QHBoxLayout()
        btn_add = QPushButton("➕ Add Profile")
        btn_add.clicked.connect(self._add_profile)
        btn_import = QPushButton("📂 Import .conf")
        btn_import.clicked.connect(self._import_conf_file)
        btn_row1.addWidget(btn_add)
        btn_row1.addWidget(btn_import)
        left_box.addLayout(btn_row1)

        btn_row2 = QHBoxLayout()
        btn_edit = QPushButton("✏️ Edit")
        btn_edit.clicked.connect(self._edit_profile)
        btn_delete = QPushButton("🗑 Delete")
        btn_delete.clicked.connect(self._delete_profile)
        btn_row2.addWidget(btn_edit)
        btn_row2.addWidget(btn_delete)
        left_box.addLayout(btn_row2)

        self.btn_set_active = QPushButton("★ Set as Active Profile")
        self.btn_set_active.clicked.connect(self._set_active_profile)
        left_box.addWidget(self.btn_set_active)

        layout.addLayout(left_box)

        # Right profile detail & control
        right_box = QVBoxLayout()

        self.detail_frame = QFrame()
        self.detail_frame.setStyleSheet(f"background:{theme.get('panel2', '#222')}; border-radius:6px; padding:8px;")
        df_layout = QVBoxLayout(self.detail_frame)

        self.prof_title = QLabel("Select a profile")
        self.prof_title.setStyleSheet(f"font-size:16px; font-weight:bold; color:{theme.get('accent', '#3daee9')};")
        df_layout.addWidget(self.prof_title)

        self.prof_type_badge = QLabel("")
        self.prof_type_badge.setStyleSheet("font-size:11px; padding:2px 6px; border-radius:3px;")
        df_layout.addWidget(self.prof_type_badge)

        self.prof_info_text = QPlainTextEdit()
        self.prof_info_text.setReadOnly(True)
        self.prof_info_text.setFont(QFont("Monospace", 10))
        df_layout.addWidget(self.prof_info_text, 1)

        # Connect / Disconnect Buttons
        action_row = QHBoxLayout()
        self.btn_connect = QPushButton("⚡ Connect This VPN")
        self.btn_connect.setStyleSheet("background:#2e7d32; color:#fff; font-weight:bold; padding:6px;")
        self.btn_connect.clicked.connect(self._connect_selected_profile)

        self.btn_disconnect = QPushButton("⏹ Disconnect")
        self.btn_disconnect.setStyleSheet("background:#c62828; color:#fff; font-weight:bold; padding:6px;")
        self.btn_disconnect.clicked.connect(self._disconnect_selected_profile)

        self.btn_export_conf = QPushButton("💾 Export .conf")
        self.btn_export_conf.clicked.connect(self._export_conf_file)

        action_row.addWidget(self.btn_connect)
        action_row.addWidget(self.btn_disconnect)
        action_row.addWidget(self.btn_export_conf)
        df_layout.addLayout(action_row)

        right_box.addWidget(self.detail_frame, 1)

        # Log output
        lbl_log = QLabel("Connection / Command Log:")
        right_box.addWidget(lbl_log)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setFont(QFont("Monospace", 9))
        self.log_view.setMaximumHeight(140)
        right_box.addWidget(self.log_view)

        layout.addLayout(right_box, 1)
        return widget

    def _refresh_profiles(self):
        self.profile_list.clear()
        profs = self.manager.load_profiles()
        active = self.manager.get_active_profile()
        active_id = active.get("id") if active else ""

        for p in profs:
            is_active = (p.get("id") == active_id)
            prefix = "★ " if is_active else "   "
            ptype = "🛡️ WG" if p.get("type") == "wireguard" else "🔒 Cisco"
            item = QListWidgetItem(f"{prefix}{ptype}: {p.get('name', 'Unnamed')}")
            item.setData(Qt.ItemDataRole.UserRole, p)
            self.profile_list.addItem(item)

        if profs:
            self.profile_list.setCurrentRow(0)
        else:
            self.prof_title.setText("No VPN Profiles Configured")
            self.prof_info_text.setPlainText("Click '➕ Add Profile' or '📂 Import .conf' to create a WireGuard or Cisco profile.")

    def _on_profile_selected(self, row: int):
        item = self.profile_list.item(row)
        if not item:
            return
        prof = item.data(Qt.ItemDataRole.UserRole)
        if not prof:
            return

        is_wg = (prof.get("type") == "wireguard")
        self.prof_title.setText(f"{prof.get('name')}")
        self.prof_type_badge.setText(f"Type: {'WireGuard' if is_wg else 'Cisco AnyConnect'}")
        self.btn_export_conf.setVisible(is_wg)

        info_lines = []
        if is_wg:
            info_lines.append(f"Interface: {prof.get('interface', 'wg0')}")
            info_lines.append(f"Client Address: {prof.get('address', '')}")
            info_lines.append(f"Server Endpoint: {prof.get('endpoint', '')}")
            info_lines.append(f"Server Public Key: {prof.get('public_key', '')}")
            info_lines.append(f"Allowed IPs: {prof.get('allowed_ips', '')}")
            info_lines.append(f"DNS: {prof.get('dns', '')}")
            info_lines.append(f"Config File: {prof.get('config_path', '')}")
        else:
            info_lines.append(f"Cisco CLI Binary: {prof.get('cisco_bin', '')}")
            info_lines.append(f"Gateway / Host: {prof.get('cisco_host', '')}")
            info_lines.append(f"Username: {prof.get('cisco_user', '')}")
            info_lines.append(f"Group: {prof.get('cisco_group', '') or '(None)'}")

        self.prof_info_text.setPlainText("\n".join(info_lines))

    def _add_profile(self):
        dlg = ProfileEditDialog(self.services, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._refresh_profiles()

    def _edit_profile(self):
        row = self.profile_list.currentRow()
        if row < 0:
            return
        item = self.profile_list.item(row)
        prof = item.data(Qt.ItemDataRole.UserRole)
        dlg = ProfileEditDialog(self.services, profile=prof, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._refresh_profiles()

    def _delete_profile(self):
        row = self.profile_list.currentRow()
        if row < 0:
            return
        item = self.profile_list.item(row)
        prof = item.data(Qt.ItemDataRole.UserRole)
        ans = QMessageBox.question(self, "Delete Profile", f"Are you sure you want to delete '{prof.get('name')}'?")
        if ans == QMessageBox.StandardButton.Yes:
            self.manager.delete_profile(prof.get("id"))
            self._refresh_profiles()

    def _set_active_profile(self):
        row = self.profile_list.currentRow()
        if row < 0:
            return
        item = self.profile_list.item(row)
        prof = item.data(Qt.ItemDataRole.UserRole)
        self.manager.set_active_profile(prof.get("id"))
        self._refresh_profiles()
        QMessageBox.information(self, "Active Profile", f"'{prof.get('name')}' is now the active VPN profile.")

    def _import_conf_file(self):
        fpath, _ = QFileDialog.getOpenFileName(self, "Import WireGuard .conf", "", "WireGuard Config (*.conf);;All Files (*)")
        if not fpath:
            return
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                content = f.read()
            parsed = parse_wireguard_conf_content(content)
            base_name = os.path.splitext(os.path.basename(fpath))[0]
            prof = {
                "name": base_name,
                "type": "wireguard",
                "config_type": "file",
                "config_path": fpath,
                "raw_content": content,
                **parsed,
            }
            self.manager.save_or_update_profile(prof)
            self._refresh_profiles()
            QMessageBox.information(self, "Imported", f"WireGuard profile '{base_name}' imported successfully.")
        except Exception as ex:
            QMessageBox.warning(self, "Import Error", f"Failed to import config: {ex}")

    def _export_conf_file(self):
        row = self.profile_list.currentRow()
        if row < 0:
            return
        item = self.profile_list.item(row)
        prof = item.data(Qt.ItemDataRole.UserRole)
        if prof.get("type") != "wireguard":
            return
        fpath, _ = QFileDialog.getSaveFileName(self, "Export WireGuard .conf", f"{prof.get('name', 'wg0')}.conf", "WireGuard Config (*.conf)")
        if fpath:
            content = prof.get("raw_content") or build_wireguard_conf_content(prof)
            with open(fpath, "w", encoding="utf-8") as f:
                f.write(content)
            QMessageBox.information(self, "Exported", f"Config exported to {fpath}")

    def _connect_selected_profile(self):
        row = self.profile_list.currentRow()
        if row < 0:
            return
        item = self.profile_list.item(row)
        prof = item.data(Qt.ItemDataRole.UserRole)
        self.log_view.appendPlainText(f">>> Connecting to {prof.get('name')} ({prof.get('type')})...")
        vpn_srv = getattr(self.parent_win, "vpn", None)
        if vpn_srv:
            vpn_srv.connect_vpn(target_prof=prof)
        else:
            self.log_view.appendPlainText("Error: VpnService reference not found.")

    def _disconnect_selected_profile(self):
        row = self.profile_list.currentRow()
        if row < 0:
            return
        item = self.profile_list.item(row)
        prof = item.data(Qt.ItemDataRole.UserRole)
        self.log_view.appendPlainText(f">>> Disconnecting {prof.get('name')}...")
        vpn_srv = getattr(self.parent_win, "vpn", None)
        if vpn_srv:
            vpn_srv.disconnect_vpn(target_prof=prof)

    # ============================================================
    # TAB 2: WIREGUARD SERVER SETUP
    # ============================================================

    def _build_server_setup_tab(self, theme: dict[str, Any]) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # Host Selector & Status Bar
        top_bar = QHBoxLayout()
        top_bar.addWidget(QLabel("Target Server:"))
        self.server_combo = QComboBox()
        self._populate_server_combo()
        top_bar.addWidget(self.server_combo, 1)

        self.btn_check_wg = QPushButton("🔍 Check WireGuard Status")
        self.btn_check_wg.clicked.connect(self._check_server_wg_status)
        top_bar.addWidget(self.btn_check_wg)

        self.btn_install_wg = QPushButton("📦 Install WireGuard on Server")
        self.btn_install_wg.clicked.connect(self._install_server_wg_packages)
        top_bar.addWidget(self.btn_install_wg)

        layout.addLayout(top_bar)

        # Splitter: Config + Peers | Output Log
        split = QSplitter(Qt.Orientation.Vertical)

        top_widget = QWidget()
        tw_layout = QHBoxLayout(top_widget)
        tw_layout.setContentsMargins(0, 0, 0, 0)

        # Server Settings Form
        form_frame = QFrame()
        form_frame.setStyleSheet(f"background:{theme.get('panel2', '#222')}; border-radius:6px; padding:8px;")
        f_layout = QFormLayout(form_frame)

        f_title = QLabel("WireGuard Server Settings:")
        f_title.setStyleSheet(f"font-weight:bold; color:{theme.get('accent', '#3daee9')};")
        f_layout.addRow(f_title)

        self.srv_ip_edit = QLineEdit()
        self.srv_ip_edit.setPlaceholderText("Auto-detect or e.g. 203.0.113.1")
        f_layout.addRow("Public IP / Domain:", self.srv_ip_edit)

        self.srv_port_spin = QSpinBox()
        self.srv_port_spin.setRange(1024, 65535)
        self.srv_port_spin.setValue(51820)
        f_layout.addRow("Listen UDP Port:", self.srv_port_spin)

        self.srv_subnet_edit = QLineEdit("10.8.0.1/24")
        f_layout.addRow("VPN Subnet CIDR:", self.srv_subnet_edit)

        self.srv_iface_edit = QLineEdit("auto")
        self.srv_iface_edit.setPlaceholderText("auto (e.g. eth0, ens3)")
        f_layout.addRow("NAT Outgoing Interface:", self.srv_iface_edit)

        self.srv_dns_edit = QLineEdit("1.1.1.1, 8.8.8.8")
        f_layout.addRow("Client DNS Servers:", self.srv_dns_edit)

        self.srv_fw_chk = QCheckBox("Automatically open port in Firewall (UFW/Firewalld)")
        self.srv_fw_chk.setChecked(True)
        f_layout.addRow("", self.srv_fw_chk)

        btn_deploy = QPushButton("🚀 Deploy / Configure Server")
        btn_deploy.setStyleSheet("background:#1976d2; color:#fff; font-weight:bold; padding:6px;")
        btn_deploy.clicked.connect(self._deploy_server_wg)
        f_layout.addRow("", btn_deploy)

        # Service control buttons
        srv_ctrl_row = QHBoxLayout()
        btn_start = QPushButton("▶ Start")
        btn_start.clicked.connect(lambda: self._run_server_cmd("systemctl start wg-quick@wg0"))
        btn_stop = QPushButton("⏹ Stop")
        btn_stop.clicked.connect(lambda: self._run_server_cmd("systemctl stop wg-quick@wg0"))
        btn_restart = QPushButton("🔄 Restart")
        btn_restart.clicked.connect(lambda: self._run_server_cmd("systemctl restart wg-quick@wg0"))
        srv_ctrl_row.addWidget(btn_start)
        srv_ctrl_row.addWidget(btn_stop)
        srv_ctrl_row.addWidget(btn_restart)
        f_layout.addRow("Service Controls:", srv_ctrl_row)

        tw_layout.addWidget(form_frame, 1)

        # Peer Management Table
        peer_frame = QFrame()
        peer_frame.setStyleSheet(f"background:{theme.get('panel2', '#222')}; border-radius:6px; padding:8px;")
        pf_layout = QVBoxLayout(peer_frame)

        pf_title_row = QHBoxLayout()
        pf_title = QLabel("Client Peers on this Server:")
        pf_title.setStyleSheet(f"font-weight:bold; color:{theme.get('accent', '#3daee9')};")
        pf_title_row.addWidget(pf_title)
        pf_title_row.addStretch()

        btn_add_peer = QPushButton("➕ Add Client Peer")
        btn_add_peer.setStyleSheet("background:#2e7d32; color:#fff; font-weight:bold;")
        btn_add_peer.clicked.connect(self._add_client_peer)
        pf_title_row.addWidget(btn_add_peer)

        btn_del_peer = QPushButton("🗑 Remove Peer")
        btn_del_peer.clicked.connect(self._remove_client_peer)
        pf_title_row.addWidget(btn_del_peer)

        pf_layout.addLayout(pf_title_row)

        self.peer_table = QTableWidget(0, 4)
        self.peer_table.setHorizontalHeaderLabels(["Public Key", "Allowed IPs", "Handshake", "Transfer"])
        self.peer_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        pf_layout.addWidget(self.peer_table, 1)

        tw_layout.addWidget(peer_frame, 1)
        split.addWidget(top_widget)

        # Bottom Server Execution Console
        bot_widget = QWidget()
        bw_layout = QVBoxLayout(bot_widget)
        bw_layout.setContentsMargins(0, 0, 0, 0)
        bw_layout.addWidget(QLabel("Server Execution / Status Output:"))
        self.server_log_view = QPlainTextEdit()
        self.server_log_view.setReadOnly(True)
        self.server_log_view.setFont(QFont("Monospace", 9))
        bw_layout.addWidget(self.server_log_view)

        split.addWidget(bot_widget)
        split.setSizes([380, 160])
        layout.addWidget(split, 1)

        return widget

    def _populate_server_combo(self):
        self.server_combo.clear()
        self.server_combo.addItem("🖥️ Localhost (Local Machine)", {"is_local": True, "name": "Local"})
        
        # Load SSH profiles
        from admin_suite.core.paths import PROFILES_FILE
        from admin_suite.core.utils import read_json

        raw_profiles = None
        if self.parent_win and hasattr(self.parent_win, "profiles") and isinstance(self.parent_win.profiles, dict):
            raw_profiles = self.parent_win.profiles
        else:
            raw_profiles = read_json(PROFILES_FILE, {}) or {}

        # Support both dict-of-profiles (standard in Admin Suite) and list-of-profiles
        profile_list: list[dict[str, Any]] = []
        if isinstance(raw_profiles, dict):
            for key, val in raw_profiles.items():
                if isinstance(val, dict):
                    prof = dict(val)
                    if not prof.get("name"):
                        prof["name"] = key
                    profile_list.append(prof)
                elif isinstance(val, str):
                    profile_list.append({"name": key})
        elif isinstance(raw_profiles, list):
            for item in raw_profiles:
                if isinstance(item, dict):
                    profile_list.append(dict(item))
                elif isinstance(item, str):
                    profile_list.append({"name": item})

        for p in profile_list:
            p_name = p.get("name", "Unknown")
            # Don't duplicate localhost entry
            if p.get("is_local") or p_name.lower() in ("localhost", "local"):
                continue

            # Populate ssh_pass from secrets if missing
            if not p.get("ssh_pass") and hasattr(self, "services") and hasattr(self.services, "secrets"):
                ssh_pass = self.services.secrets.get(f"prof_{p_name}", "")
                if ssh_pass:
                    p = dict(p)
                    p["ssh_pass"] = ssh_pass

            p_host = p.get("ssh_host") or p.get("host", "")
            display_text = f"🌐 {p_name} ({p_host})" if p_host else f"🌐 {p_name}"
            self.server_combo.addItem(display_text, p)

    def _get_current_server_target(self) -> tuple[bool, Optional[dict[str, Any]]]:
        data = self.server_combo.currentData()
        if not data or data.get("is_local"):
            return False, None
        return True, data

    def _execute_server_script(self, script: str, on_done_cb):
        is_remote, profile = self._get_current_server_target()
        sudo_pass = None
        if profile:
            p_name = profile.get("name", "")
            sudo_pass = (
                self.services.get_sudo_password(p_name)
                or profile.get("sudo_pass")
            )
            if not sudo_pass and hasattr(self.services, "secrets"):
                sudo_pass = self.services.secrets.get(f"prof_sudo_{p_name}", "")
        else:
            sudo_pass = (
                self.services.get_sudo_password("Local")
                or self.services.get_sudo_password("localhost")
            )
            if not sudo_pass and hasattr(self.services, "secrets"):
                sudo_pass = (
                    self.services.secrets.get("prof_sudo_localhost", "")
                    or self.services.secrets.get("prof_sudo_Local", "")
                )

        self.server_log_view.appendPlainText(f">>> Executing command on {self.server_combo.currentText()}...")
        worker = _ExecWorker(script, is_remote=is_remote, profile=profile, sudo_pass=sudo_pass, parent=self)
        worker.finished_result.connect(on_done_cb)
        self._workers.append(worker)
        worker.start()

    def _run_server_cmd(self, cmd: str):
        self._execute_server_script(cmd, self._on_generic_cmd_finished)

    def _on_generic_cmd_finished(self, rc: int, out: str, err: str):
        if out:
            self.server_log_view.appendPlainText(out.strip())
        if err:
            self.server_log_view.appendPlainText(f"[stderr] {err.strip()}")
        self.server_log_view.appendPlainText(f">>> Finished with exit code: {rc}\n")

    def _check_server_wg_status(self):
        script = WireGuardServerManager.get_prerequisites_check_script() + "\nwg show wg0 2>/dev/null || true"
        def on_done(rc, out, err):
            self.server_log_view.appendPlainText(out.strip())
            # Parse external IP & default iface
            for line in out.splitlines():
                if line.startswith("EXT_IP:"):
                    ip = line.partition(":")[2].strip()
                    if ip and not self.srv_ip_edit.text():
                        self.srv_ip_edit.setText(ip)
                elif line.startswith("DEF_IFACE:"):
                    iface = line.partition(":")[2].strip()
                    if iface and (not self.srv_iface_edit.text() or self.srv_iface_edit.text() == "auto"):
                        self.srv_iface_edit.setText(iface)

            # Parse peers
            parsed = WireGuardServerManager.parse_server_status(out)
            self._update_peers_table(parsed.get("peers", []))
            
            is_active = parsed.get("active")
            status_text = "Active & Running" if is_active else "Inactive / Not Deployed"
            self.server_log_view.appendPlainText(f"\n[Status Check] WireGuard Status: {status_text}\n")
        self._execute_server_script(script, on_done)

    def _install_server_wg_packages(self):
        ans = QMessageBox.question(self, "Install WireGuard", "Install WireGuard utilities and iptables from system repositories on this server?")
        if ans != QMessageBox.StandardButton.Yes:
            return
        script = WireGuardServerManager.get_install_packages_script()
        self._execute_server_script(script, self._on_generic_cmd_finished)

    def _deploy_server_wg(self):
        ext_ip = self.srv_ip_edit.text().strip()
        port = self.srv_port_spin.value()
        subnet = self.srv_subnet_edit.text().strip()
        dns = self.srv_dns_edit.text().strip()
        iface = self.srv_iface_edit.text().strip()

        script = WireGuardServerManager.get_deploy_server_script(
            server_ext_ip=ext_ip,
            port=port,
            subnet_cidr=subnet,
            dns=dns,
            net_iface=iface,
            enable_firewall=self.srv_fw_chk.isChecked(),
        )

        def on_done(rc, out, err):
            self._on_generic_cmd_finished(rc, out, err)
            if rc == 0:
                QMessageBox.information(self, "WireGuard Server", "WireGuard server deployed and started successfully!")
                self._check_server_wg_status()
            else:
                QMessageBox.warning(self, "Deploy Failed", f"Failed to deploy WireGuard server. Check logs.\n{err}")

        self._execute_server_script(script, on_done)

    def _update_peers_table(self, peers: list[dict[str, Any]]):
        self.peer_table.setRowCount(len(peers))
        for row, p in enumerate(peers):
            self.peer_table.setItem(row, 0, QTableWidgetItem(p.get("public_key", "")))
            self.peer_table.setItem(row, 1, QTableWidgetItem(p.get("allowed_ips", "")))
            self.peer_table.setItem(row, 2, QTableWidgetItem(p.get("latest_handshake", "")))
            transfer = f"↓ {p.get('transfer_rx', '0 B')} / ↑ {p.get('transfer_tx', '0 B')}"
            self.peer_table.setItem(row, 3, QTableWidgetItem(transfer))

    def _add_client_peer(self):
        # Dialog to add client peer
        from PyQt6.QtWidgets import QInputDialog
        peer_name, ok = QInputDialog.getText(self, "Add Client Peer", "Client Device Name (e.g. laptop-arun):")
        if not ok or not peer_name.strip():
            return
        peer_name = peer_name.strip()

        # Generate client keys
        client_priv, client_pub = generate_wireguard_keys()

        # Suggest client IP: count current peers + 2 (e.g. 10.8.0.2)
        next_ip_num = self.peer_table.rowCount() + 2
        subnet_base = self.srv_subnet_edit.text().partition("/")[0].rsplit(".", 1)[0]
        suggested_ip = f"{subnet_base}.{next_ip_num}"

        client_ip, ok2 = QInputDialog.getText(self, "Client IP", "Assign Client VPN IP:", text=suggested_ip)
        if not ok2 or not client_ip.strip():
            return
        client_ip = client_ip.strip()

        # Run script to add peer on server
        script = WireGuardServerManager.get_add_peer_script(
            peer_name=peer_name,
            client_ip=client_ip,
            client_pubkey=client_pub,
        )

        def on_done(rc, out, err):
            self._on_generic_cmd_finished(rc, out, err)
            if rc == 0:
                # Build client .conf
                server_endpoint = self.srv_ip_edit.text().strip() or "YOUR_SERVER_IP"
                server_port = self.srv_port_spin.value()
                
                # Fetch server public key from server
                def on_key_fetched(k_rc, k_out, _):
                    srv_pubkey = k_out.strip() if k_rc == 0 else "SERVER_PUBLIC_KEY"
                    client_conf_text = f"""[Interface]
PrivateKey = {client_priv}
Address = {client_ip}/24
DNS = {self.srv_dns_edit.text().strip()}

[Peer]
PublicKey = {srv_pubkey}
Endpoint = {server_endpoint}:{server_port}
AllowedIPs = 0.0.0.0/0, ::/0
PersistentKeepalive = 25
"""
                    self._show_generated_client_conf(peer_name, client_conf_text, client_ip, client_priv, srv_pubkey, f"{server_endpoint}:{server_port}")
                    self._check_server_wg_status()

                self._execute_server_script("cat /etc/wireguard/server_public.key 2>/dev/null || wg show wg0 public-key", on_key_fetched)
            else:
                QMessageBox.warning(self, "Add Peer Failed", f"Could not add peer: {err}")

        self._execute_server_script(script, on_done)

    def _show_generated_client_conf(self, peer_name: str, conf_text: str, client_ip: str, client_priv: str, server_pub: str, endpoint: str):
        dlg = QDialog(self)
        dlg.setWindowTitle(f"WireGuard Client Configuration — {peer_name}")
        dlg.resize(600, 480)
        d_layout = QVBoxLayout(dlg)

        d_layout.addWidget(QLabel(f"<b>Generated Client Configuration for:</b> {peer_name}"))
        txt = QPlainTextEdit(conf_text)
        txt.setReadOnly(True)
        txt.setFont(QFont("Monospace", 10))
        d_layout.addWidget(txt, 1)

        b_row = QHBoxLayout()

        btn_add_to_profiles = QPushButton("⚡ Add to Admin Suite VPN Profiles")
        btn_add_to_profiles.setStyleSheet("background:#2e7d32; color:#fff; font-weight:bold; padding:6px;")
        def add_local():
            prof = {
                "name": f"WG: {peer_name}",
                "type": "wireguard",
                "config_type": "manual",
                "raw_content": conf_text,
                "address": f"{client_ip}/24",
                "private_key": client_priv,
                "public_key": server_pub,
                "endpoint": endpoint,
                "dns": self.srv_dns_edit.text().strip(),
                "allowed_ips": "0.0.0.0/0, ::/0",
                "persistent_keepalive": "25",
            }
            self.manager.save_or_update_profile(prof)
            self._refresh_profiles()
            QMessageBox.information(dlg, "Profile Added", f"Profile '{prof['name']}' has been added to Admin Suite VPN Profiles!")
        btn_add_to_profiles.clicked.connect(add_local)

        btn_save_file = QPushButton("💾 Save .conf File")
        def save_file():
            fpath, _ = QFileDialog.getSaveFileName(dlg, "Save Client Config", f"{peer_name}.conf", "WireGuard Config (*.conf)")
            if fpath:
                with open(fpath, "w", encoding="utf-8") as f:
                    f.write(conf_text)
                QMessageBox.information(dlg, "Saved", f"Configuration saved to {fpath}")
        btn_save_file.clicked.connect(save_file)

        btn_copy = QPushButton("📋 Copy Config")
        def copy_clip():
            QApplication.clipboard().setText(conf_text)
            QMessageBox.information(dlg, "Copied", "Configuration copied to clipboard!")
        btn_copy.clicked.connect(copy_clip)

        b_row.addWidget(btn_add_to_profiles)
        b_row.addWidget(btn_save_file)
        b_row.addWidget(btn_copy)
        d_layout.addLayout(b_row)

        dlg.exec()

    def _remove_client_peer(self):
        row = self.peer_table.currentRow()
        if row < 0:
            QMessageBox.information(self, "Select Peer", "Please select a peer from the table to remove.")
            return
        pubkey = self.peer_table.item(row, 0).text()
        ans = QMessageBox.question(self, "Remove Peer", f"Are you sure you want to remove peer:\n{pubkey}?")
        if ans != QMessageBox.StandardButton.Yes:
            return

        script = WireGuardServerManager.get_remove_peer_script(pubkey)
        def on_done(rc, out, err):
            self._on_generic_cmd_finished(rc, out, err)
            self._check_server_wg_status()
        self._execute_server_script(script, on_done)
