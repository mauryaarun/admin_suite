"""
Main UI dialogs.
"""

from __future__ import annotations

import getpass
import os
import re
import socket
import subprocess

from PyQt6.QtCore import Qt
from PyQt6.QtGui import (QFont, QColor,)
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
    QHeaderView
)

from admin_suite.core.paths import LOG_DIR, SNIPPETS_FILE
from admin_suite.core.utils import read_json, write_json_secure

try:
    import paramiko

except ImportError:
    paramiko = None

from admin_suite.db.backends import PG_AVAILABLE


# ------------------------------------------------------------
# Sudo credentials dialog and helper
# ------------------------------------------------------------

class SudoCredentialsDialog(QDialog):
    """
    Prompt user for root / sudo credentials when non-root profile requires administrative elevation.
    """

    def __init__(
        self,
        parent,
        services,
        profile_name: str,
        user: str = "",
        current_password: str = "",
    ):
        super().__init__(parent)
        self.services = services
        self.profile_name = profile_name
        self.user = user or "current user"
        self._is_passwordless = False

        self.setWindowTitle("🔐 Root / Sudo Authentication")
        self.setMinimumWidth(440)

        theme = self.services.theme.current if hasattr(self.services, "theme") else {}
        accent = theme.get("accent", "#3daee9")

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        header_lbl = QLabel(
            f"<b style='font-size:14px; color:{accent};'>🔐 Privileged Operation Elevation</b>"
        )
        layout.addWidget(header_lbl)

        info_lbl = QLabel(
            f"The profile <b>'{profile_name}'</b> operates as user <b>'{self.user}'</b> (non-root).<br>"
            "Administrative operations require elevated root / sudo privileges.<br>"
            f"Please enter the root user or sudo password for <b>{self.user}</b>:"
        )
        info_lbl.setWordWrap(True)
        layout.addWidget(info_lbl)

        pass_row = QHBoxLayout()
        self.pass_in = QLineEdit()
        self.pass_in.setEchoMode(QLineEdit.EchoMode.Password)
        self.pass_in.setPlaceholderText("Enter sudo / root password")
        if current_password:
            self.pass_in.setText(current_password)
        pass_row.addWidget(self.pass_in, 1)

        toggle_eye = QPushButton("👁️")
        toggle_eye.setToolTip("Show / Hide password")
        toggle_eye.setFixedWidth(36)

        def _toggle_pw():
            if self.pass_in.echoMode() == QLineEdit.EchoMode.Password:
                self.pass_in.setEchoMode(QLineEdit.EchoMode.Normal)
            else:
                self.pass_in.setEchoMode(QLineEdit.EchoMode.Password)

        toggle_eye.clicked.connect(_toggle_pw)
        pass_row.addWidget(toggle_eye)
        layout.addLayout(pass_row)

        self.remember_session_chk = QCheckBox("Remember for this application session")
        self.remember_session_chk.setChecked(True)
        layout.addWidget(self.remember_session_chk)

        self.save_profile_chk = QCheckBox("Save password securely to profile credentials")
        self.save_profile_chk.setChecked(False)
        layout.addWidget(self.save_profile_chk)

        btn_row = QHBoxLayout()
        skip_btn = QPushButton("⏩ Passwordless (NOPASSWD)")
        skip_btn.setToolTip("Proceed without password if sudo NOPASSWD is configured")
        skip_btn.clicked.connect(self._on_skip)
        btn_row.addWidget(skip_btn)

        btn_row.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        ok_btn = QPushButton("✅ Authenticate")
        ok_btn.setDefault(True)
        ok_btn.setStyleSheet(f"font-weight:bold; background:{accent}; color:#fff; padding:6px 14px;")
        ok_btn.clicked.connect(self._on_ok)
        btn_row.addWidget(ok_btn)

        layout.addLayout(btn_row)

    def _on_skip(self):
        self._is_passwordless = True
        self.accept()

    def _on_ok(self):
        self._is_passwordless = False
        self.accept()

    def get_password(self) -> str:
        if self._is_passwordless:
            return ""
        return self.pass_in.text()

    def should_remember(self) -> bool:
        return self.remember_session_chk.isChecked()

    def should_save_to_profile(self) -> bool:
        return self.save_profile_chk.isChecked()


def ensure_sudo_credentials(
    parent,
    services,
    profile_name: str,
    profile_data: dict | None = None,
    allow_prompt: bool = True,
) -> tuple[str | None, bool]:
    """
    Ensure sudo credentials exist for a non-root user or localhost profile.
    Returns (sudo_password, ok_boolean).
    If user is root, returns ("", True).
    If user cancels prompt, returns (None, False).
    """
    p = profile_data or {}
    user = p.get("ssh_user") or p.get("user")
    if not user:
        if p.get("is_local") or profile_name.lower() == "localhost":
            user = getpass.getuser()
        else:
            user = ""

    # If already root, no sudo password needed
    if user == "root":
        return "", True

    # Check cached or stored password
    if hasattr(services, "get_sudo_password"):
        pw = services.get_sudo_password(profile_name)
        if pw is not None:
            return pw, True

    if p.get("sudo_pass"):
        if hasattr(services, "set_sudo_password"):
            services.set_sudo_password(profile_name, p["sudo_pass"])
        return p["sudo_pass"], True

    # For local execution, test if passwordless sudo works
    is_local = (
        not p
        or bool(p.get("is_local"))
        or profile_name.lower() == "localhost"
        or (
            p.get("ssh_host") in ("localhost", "127.0.0.1")
            and bool(p.get("use_local_exec", True))
        )
    )
    if is_local:
        try:
            check_res = subprocess.run(
                ["sudo", "-n", "true"],
                capture_output=True,
                timeout=3,
            )
            if check_res.returncode == 0:
                if hasattr(services, "set_sudo_password"):
                    services.set_sudo_password(profile_name, "")
                return "", True
        except Exception:
            pass

    if not allow_prompt:
        return None, False

    dlg = SudoCredentialsDialog(parent, services, profile_name, user)
    if dlg.exec() == QDialog.DialogCode.Accepted:
        pw = dlg.get_password()
        if hasattr(services, "set_sudo_password"):
            services.set_sudo_password(
                profile_name,
                pw,
                persist=dlg.should_save_to_profile(),
            )
        if dlg.should_save_to_profile() and profile_data is not None:
            profile_data["sudo_pass"] = pw
            if hasattr(parent, "save_profiles"):
                parent.save_profiles()
            elif hasattr(parent, "main_window") and hasattr(parent.main_window, "save_profiles"):
                parent.main_window.save_profiles()
        return pw, True

    return None, False


# ------------------------------------------------------------
# SSH profile dialog
# ------------------------------------------------------------

class ProfileDialog(QDialog):
    """
    Add/edit SSH profile with jump host profile selection and root/sudo credentials.
    """

    def __init__(self, parent, services, edit_data=None):
        super().__init__(parent)

        self.services = services
        self.main_parent = parent
        self.edit_data = edit_data or {}

        e = self.edit_data

        self.setWindowTitle("Edit Profile" if edit_data else "Add SSH Profile")
        self.setMinimumWidth(520)

        layout = QFormLayout(self)

        self.name_in = QLineEdit(e.get("name", ""))
        self.group_in = QLineEdit(e.get("group", "Default"))
        self.tags_in = QLineEdit(e.get("tags", ""))
        self.tags_in.setPlaceholderText("comma separated, e.g. prod, web")

        self.fav_chk = QCheckBox("⭐ Favorite")
        self.fav_chk.setChecked(e.get("favorite", False))

        self.host_in = QLineEdit(e.get("ssh_host", ""))
        self.user_in = QLineEdit(e.get("ssh_user", ""))
        self.port_in = QLineEdit(str(e.get("ssh_port", "22")))

        self.auth_method = QComboBox()
        self.auth_method.addItems(["Password", "SSH Key"])
        self.auth_method.setCurrentText(e.get("auth_method", "Password"))
        self.auth_method.currentTextChanged.connect(self._on_auth)

        self.pass_in = QLineEdit()
        self.pass_in.setEchoMode(QLineEdit.EchoMode.Password)

        if e.get("ssh_pass"):
            self.pass_in.setText(e["ssh_pass"])

        self.key_path_in = QLineEdit(e.get("ssh_key_path", ""))

        browse = QPushButton("...")
        browse.clicked.connect(self._browse_key)

        key_row = QHBoxLayout()
        key_row.addWidget(self.key_path_in, 1)
        key_row.addWidget(browse)

        self.agent_chk = QCheckBox("Use SSH agent")
        self.agent_chk.setChecked(e.get("use_agent", False))

        # Root / Sudo credentials for administration
        self.sudo_pass_in = QLineEdit()
        self.sudo_pass_in.setEchoMode(QLineEdit.EchoMode.Password)
        self.sudo_pass_in.setPlaceholderText("Optional: root/sudo password for administration")
        if e.get("sudo_pass"):
            self.sudo_pass_in.setText(e["sudo_pass"])

        self.initial_cmd_in = QLineEdit(e.get("initial_cmd", ""))
        self.initial_cmd_in.setPlaceholderText("e.g. sudo su -")

        # Jump Host settings
        self.use_jump = QCheckBox("Route through jump host")
        self.use_jump.setChecked(e.get("use_jump", False))
        self.use_jump.stateChanged.connect(self._on_jump)

        # Populate current SSH profiles as options for route through jump host
        self.jump_profile_combo = QComboBox()
        self.jump_profile_combo.addItem("(Manual Jump Settings)")

        known_profiles = {}
        if hasattr(parent, "profiles") and isinstance(parent.profiles, dict):
            known_profiles = parent.profiles
        elif hasattr(parent, "main_window") and hasattr(parent.main_window, "profiles"):
            known_profiles = parent.main_window.profiles
        else:
            try:
                from admin_suite.core.paths import PROFILES_FILE
                from admin_suite.core.utils import read_json
                known_profiles = read_json(PROFILES_FILE, {}) or {}
            except Exception:
                known_profiles = {}

        self._known_jump_profiles = known_profiles
        cur_name = e.get("name", "")
        for p_name in sorted(known_profiles.keys()):
            if p_name != cur_name:
                self.jump_profile_combo.addItem(p_name)

        current_jump_prof = e.get("jump_profile", "")
        if current_jump_prof and self.jump_profile_combo.findText(current_jump_prof) >= 0:
            self.jump_profile_combo.setCurrentText(current_jump_prof)
        self.jump_profile_combo.currentTextChanged.connect(self._on_jump_profile_selected)

        self.jump_host = QLineEdit(e.get("jump_host", ""))
        self.jump_port = QLineEdit(str(e.get("jump_port", "22")))
        self.jump_user = QLineEdit(e.get("jump_user", ""))

        self.jump_pass = QLineEdit()
        self.jump_pass.setEchoMode(QLineEdit.EchoMode.Password)
        if e.get("jump_pass"):
            self.jump_pass.setText(e["jump_pass"])

        self.jump_key_path = QLineEdit(e.get("jump_key_path", ""))
        browse_jump_key = QPushButton("...")
        browse_jump_key.clicked.connect(self._browse_jump_key)

        jump_key_row = QHBoxLayout()
        jump_key_row.addWidget(self.jump_key_path, 1)
        jump_key_row.addWidget(browse_jump_key)

        theme = self.services.theme.current

        section = lambda text: QLabel(
            f"<b style='color:{theme['accent']};'>{text}</b>"
        )

        layout.addRow(section("PROFILE"), QLabel(""))
        layout.addRow("Name:", self.name_in)
        layout.addRow("Group / Folder:", self.group_in)
        layout.addRow("Tags:", self.tags_in)
        layout.addRow("", self.fav_chk)

        layout.addRow(section("CONNECTION"), QLabel(""))
        layout.addRow("SSH Host:", self.host_in)
        layout.addRow("Username:", self.user_in)
        layout.addRow("Port:", self.port_in)

        layout.addRow(section("AUTHENTICATION"), QLabel(""))
        layout.addRow("Method:", self.auth_method)
        layout.addRow("Password:", self.pass_in)
        layout.addRow("SSH Key Path:", key_row)
        layout.addRow("", self.agent_chk)

        layout.addRow(section("ADMINISTRATION / ELEVATION"), QLabel(""))
        layout.addRow("Root / Sudo Password:", self.sudo_pass_in)

        layout.addRow(section("OPTIONS"), QLabel(""))
        layout.addRow("Initial Command:", self.initial_cmd_in)

        layout.addRow(section("JUMP HOST"), QLabel(""))
        layout.addRow("", self.use_jump)
        layout.addRow("Jump SSH Profile:", self.jump_profile_combo)
        layout.addRow("Jump Host:", self.jump_host)
        layout.addRow("Jump Port:", self.jump_port)
        layout.addRow("Jump User:", self.jump_user)
        layout.addRow("Jump Password/Passphrase:", self.jump_pass)
        layout.addRow("Jump Key Path:", jump_key_row)

        save = QPushButton("💾 Save Profile")
        save.clicked.connect(self._validate)

        layout.addRow(save)

        self._on_auth(self.auth_method.currentText())
        self._on_jump()

    def _on_jump(self, *args) -> None:
        on = self.use_jump.isChecked()

        for widget in (
            self.jump_profile_combo,
            self.jump_host,
            self.jump_port,
            self.jump_user,
            self.jump_pass,
            self.jump_key_path,
        ):
            widget.setEnabled(on)

    def _on_jump_profile_selected(self, text: str) -> None:
        if text == "(Manual Jump Settings)":
            return
        p = self._known_jump_profiles.get(text)
        if p:
            self.jump_host.setText(p.get("ssh_host", ""))
            self.jump_port.setText(str(p.get("ssh_port", "22")))
            self.jump_user.setText(p.get("ssh_user", ""))
            j_pass = p.get("ssh_pass", "")
            if not j_pass and hasattr(self.services, "secrets"):
                j_pass = self.services.secrets.get(f"prof_{text}", "")
            if j_pass:
                self.jump_pass.setText(j_pass)
            if p.get("ssh_key_path"):
                self.jump_key_path.setText(p.get("ssh_key_path", ""))

    def _browse_jump_key(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Jump SSH Key",
            os.path.expanduser("~/.ssh"),
        )
        if path:
            self.jump_key_path.setText(path)

    def _on_auth(self, method: str) -> None:
        is_key = method == "SSH Key"

        self.pass_in.setPlaceholderText(
            "Key passphrase (optional)" if is_key else "Password"
        )

        self.key_path_in.setEnabled(is_key)

    def _browse_key(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select SSH Key",
            os.path.expanduser("~/.ssh"),
        )

        if path:
            self.key_path_in.setText(path)

    def _validate(self) -> None:
        if not self.name_in.text().strip():
            QMessageBox.warning(self, "Validation", "Name is required.")
            return

        if not self.host_in.text().strip():
            QMessageBox.warning(self, "Validation", "Host is required.")
            return

        if not self.user_in.text().strip():
            QMessageBox.warning(self, "Validation", "Username is required.")
            return

        self.accept()

    def get_data(self) -> dict:
        jump_prof = self.jump_profile_combo.currentText()
        res = {
            "name": self.name_in.text().strip(),
            "group": self.group_in.text().strip() or "Default",
            "tags": self.tags_in.text().strip(),
            "favorite": self.fav_chk.isChecked(),
            "ssh_host": self.host_in.text().strip(),
            "ssh_user": self.user_in.text().strip(),
            "ssh_port": self.port_in.text().strip(),
            "auth_method": self.auth_method.currentText(),
            "ssh_pass": self.pass_in.text(),
            "ssh_key_path": self.key_path_in.text().strip(),
            "sudo_pass": self.sudo_pass_in.text(),
            "use_agent": self.agent_chk.isChecked(),
            "initial_cmd": self.initial_cmd_in.text().strip(),
            "use_jump": self.use_jump.isChecked(),
            "jump_profile": "" if jump_prof == "(Manual Jump Settings)" else jump_prof,
            "jump_host": self.jump_host.text().strip(),
            "jump_port": self.jump_port.text().strip(),
            "jump_user": self.jump_user.text().strip(),
            "jump_pass": self.jump_pass.text(),
            "jump_key_path": self.jump_key_path.text().strip(),
        }
        if "is_local" in self.edit_data:
            res["is_local"] = self.edit_data["is_local"]
        if "use_local_exec" in self.edit_data:
            res["use_local_exec"] = self.edit_data["use_local_exec"]
        return res


# ------------------------------------------------------------
# DB profile dialog
# ------------------------------------------------------------

class DbProfileDialog(QDialog):
    """
    Add/edit DB profile with SSH key and profile linking support.
    """

    def __init__(self, parent, services, edit_data=None):
        super().__init__(parent)

        self.services = services
        self.main_parent = parent

        e = edit_data or {}

        self.setWindowTitle("Edit DB Profile" if edit_data else "Add DB Profile")
        self.setMinimumWidth(520)

        layout = QFormLayout(self)

        self.name_in = QLineEdit(e.get("name", ""))

        self.backend_in = QComboBox()
        self.backend_in.addItems(["mysql", "postgresql", "sqlite"])
        cur_backend = str(e.get("backend", "mysql")).lower()
        if cur_backend in ("postgres", "postgresql"):
            self.backend_in.setCurrentText("postgresql")
        elif cur_backend in ("mysql", "sqlite"):
            self.backend_in.setCurrentText(cur_backend)
        else:
            self.backend_in.setCurrentText("mysql")
        self.backend_in.currentTextChanged.connect(self._on_backend_change)

        self.db_host = QLineEdit(e.get("db_host", "127.0.0.1"))
        self.db_port = QLineEdit(str(e.get("db_port", "3306")))
        self.db_user = QLineEdit(e.get("db_user", ""))

        self.db_pass = QLineEdit(e.get("db_pass", ""))
        self.db_pass.setEchoMode(QLineEdit.EchoMode.Password)

        self.db_name = QLineEdit(e.get("db_name", ""))

        self.sqlite_path = QLineEdit(e.get("sqlite_path", ""))

        browse_sqlite_btn = QPushButton("...")
        browse_sqlite_btn.clicked.connect(self._browse_sqlite)

        self.sqlite_row = QWidget()
        sqlite_h = QHBoxLayout(self.sqlite_row)
        sqlite_h.setContentsMargins(0, 0, 0, 0)
        sqlite_h.addWidget(self.sqlite_path, 1)
        sqlite_h.addWidget(browse_sqlite_btn)

        # SSH Tunnel settings
        self.use_tunnel = QCheckBox("Route through SSH tunnel")
        self.use_tunnel.setChecked(e.get("use_tunnel", False))
        self.use_tunnel.stateChanged.connect(self._on_tunnel_toggle)

        # SSH Profile selector
        self.ssh_profile_combo = QComboBox()
        self.ssh_profile_combo.addItem("(Custom SSH Settings)")
        known_profiles = []
        if hasattr(parent, "profiles") and isinstance(parent.profiles, dict):
            known_profiles = list(parent.profiles.keys())
        elif hasattr(parent, "main_window") and hasattr(parent.main_window, "profiles"):
            known_profiles = list(parent.main_window.profiles.keys())
        else:
            try:
                from admin_suite.core.paths import PROFILES_FILE
                from admin_suite.core.utils import read_json
                known_profiles = list((read_json(PROFILES_FILE, {}) or {}).keys())
            except Exception:
                known_profiles = []

        for p_name in sorted(known_profiles):
            self.ssh_profile_combo.addItem(p_name)

        current_prof = e.get("ssh_profile", "")
        if current_prof and self.ssh_profile_combo.findText(current_prof) >= 0:
            self.ssh_profile_combo.setCurrentText(current_prof)
        self.ssh_profile_combo.currentTextChanged.connect(self._on_ssh_profile_selected)

        self.ssh_host = QLineEdit(e.get("ssh_host", ""))
        self.ssh_user = QLineEdit(e.get("ssh_user", ""))
        self.ssh_port = QLineEdit(str(e.get("ssh_port", "22")))

        self.ssh_auth_method = QComboBox()
        self.ssh_auth_method.addItems(["Password", "SSH Key"])
        self.ssh_auth_method.setCurrentText(e.get("ssh_auth_method", "SSH Key" if e.get("ssh_key_path") else "Password"))
        self.ssh_auth_method.currentTextChanged.connect(self._on_ssh_auth_toggle)

        self.ssh_pass = QLineEdit(e.get("ssh_pass", ""))
        self.ssh_pass.setEchoMode(QLineEdit.EchoMode.Password)

        self.ssh_key_path = QLineEdit(e.get("ssh_key_path", ""))
        browse_key_btn = QPushButton("...")
        browse_key_btn.clicked.connect(self._browse_ssh_key)

        self.ssh_key_row = QWidget()
        key_h = QHBoxLayout(self.ssh_key_row)
        key_h.setContentsMargins(0, 0, 0, 0)
        key_h.addWidget(self.ssh_key_path, 1)
        key_h.addWidget(browse_key_btn)

        layout.addRow("Name:", self.name_in)
        layout.addRow("Backend:", self.backend_in)
        layout.addRow("SQLite File:", self.sqlite_row)
        layout.addRow("DB Host:", self.db_host)
        layout.addRow("DB Port:", self.db_port)
        layout.addRow("DB User:", self.db_user)
        layout.addRow("DB Password:", self.db_pass)
        layout.addRow("Default Schema:", self.db_name)
        layout.addRow("", self.use_tunnel)
        layout.addRow("Use SSH Profile:", self.ssh_profile_combo)
        layout.addRow("SSH Host:", self.ssh_host)
        layout.addRow("SSH Port:", self.ssh_port)
        layout.addRow("SSH User:", self.ssh_user)
        layout.addRow("SSH Auth:", self.ssh_auth_method)
        layout.addRow("SSH Password/Passphrase:", self.ssh_pass)
        layout.addRow("SSH Key Path:", self.ssh_key_row)

        save = QPushButton("💾 Save DB Profile")
        save.clicked.connect(self._validate)
        layout.addRow(save)

        self._on_backend_change(self.backend_in.currentText())
        self._on_tunnel_toggle()
        self._on_ssh_auth_toggle(self.ssh_auth_method.currentText())

    def _browse_sqlite(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "SQLite database",
            "",
            "SQLite (*.db *.sqlite *.sqlite3);;All (*)",
        )
        if path:
            self.sqlite_path.setText(path)

    def _browse_ssh_key(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select SSH Private Key",
            os.path.expanduser("~/.ssh"),
        )
        if path:
            self.ssh_key_path.setText(path)

    def _on_backend_change(self, backend: str) -> None:
        is_sqlite = backend == "sqlite"
        self.sqlite_row.setVisible(is_sqlite)
        for w in (self.db_host, self.db_port, self.db_user, self.db_pass, self.use_tunnel):
            w.setVisible(not is_sqlite)
        if is_sqlite:
            self.use_tunnel.setChecked(False)
        elif backend in ("postgresql", "postgres"):
            if self.db_port.text().strip() in ("", "3306"):
                self.db_port.setText("5432")
        elif backend == "mysql":
            if self.db_port.text().strip() in ("", "5432"):
                self.db_port.setText("3306")

    def _on_tunnel_toggle(self, *args) -> None:
        on = self.use_tunnel.isChecked() and self.backend_in.currentText() != "sqlite"
        for w in (
            self.ssh_profile_combo, self.ssh_host, self.ssh_port,
            self.ssh_user, self.ssh_auth_method, self.ssh_pass, self.ssh_key_row
        ):
            w.setEnabled(on)

    def _on_ssh_auth_toggle(self, method: str) -> None:
        is_key = method == "SSH Key"
        self.ssh_key_row.setEnabled(is_key and self.use_tunnel.isChecked())
        if is_key:
            self.ssh_pass.setPlaceholderText("Private key passphrase (optional)")
        else:
            self.ssh_pass.setPlaceholderText("SSH password")

    def _on_ssh_profile_selected(self, text: str) -> None:
        if text == "(Custom SSH Settings)":
            return
        profiles = getattr(self.main_parent, "profiles", {})
        if not profiles and hasattr(self.main_parent, "main_window"):
            profiles = getattr(self.main_parent.main_window, "profiles", {})
        p = profiles.get(text)
        if p:
            self.ssh_host.setText(p.get("ssh_host", ""))
            self.ssh_port.setText(str(p.get("ssh_port", "22")))
            self.ssh_user.setText(p.get("ssh_user", ""))
            auth = p.get("auth_method", "Password")
            self.ssh_auth_method.setCurrentText(auth)
            self.ssh_key_path.setText(p.get("ssh_key_path", ""))
            if p.get("ssh_pass"):
                self.ssh_pass.setText(p["ssh_pass"])

    def _validate(self) -> None:
        if not self.name_in.text().strip():
            QMessageBox.warning(self, "Validation", "Name is required.")
            return

        self.accept()

    def get_data(self) -> dict:
        prof_choice = self.ssh_profile_combo.currentText()
        return {
            "name": self.name_in.text().strip(),
            "backend": self.backend_in.currentText(),
            "db_host": self.db_host.text().strip(),
            "db_port": self.db_port.text().strip(),
            "db_user": self.db_user.text().strip(),
            "db_pass": self.db_pass.text(),
            "db_name": self.db_name.text().strip(),
            "sqlite_path": self.sqlite_path.text().strip(),
            "use_tunnel": self.use_tunnel.isChecked(),
            "ssh_profile": "" if prof_choice == "(Custom SSH Settings)" else prof_choice,
            "ssh_host": self.ssh_host.text().strip(),
            "ssh_user": self.ssh_user.text().strip(),
            "ssh_port": self.ssh_port.text().strip(),
            "ssh_auth_method": self.ssh_auth_method.currentText(),
            "ssh_pass": self.ssh_pass.text(),
            "ssh_key_path": self.ssh_key_path.text().strip(),
        }


# ------------------------------------------------------------
# Connection manager
# ------------------------------------------------------------

class ConnectionManagerDialog(QDialog):
    """
    Global connection/settings manager.
    """

    def __init__(self, parent, services):
        super().__init__(parent)

        self.services = services

        self.setWindowTitle("Connection Manager — Global Settings")
        self.setMinimumWidth(580)

        layout = QVBoxLayout(self)

        self.tabs = QTabWidget()

        # Database tab.
        db_tab = QWidget()
        db_layout = QFormLayout(db_tab)

        self.backend = QComboBox()
        self.backend.addItems(["mysql", "postgresql", "sqlite"])
        cur_backend = str(self.services.config.get("db_backend", "mysql")).lower()
        if cur_backend in ("postgres", "postgresql"):
            self.backend.setCurrentText("postgresql")
        else:
            self.backend.setCurrentText(cur_backend)

        self.ssh_host = QLineEdit(self.services.config.get("ssh_host", ""))
        self.ssh_user = QLineEdit(self.services.config.get("ssh_user", ""))
        self.ssh_port = QLineEdit(self.services.config.get("ssh_port", "22"))

        self.ssh_pass = QLineEdit(self.services.secrets.get("ssh_pass", ""))
        self.ssh_pass.setEchoMode(QLineEdit.EchoMode.Password)

        self.use_tunnel = QCheckBox("Route DB through SSH tunnel")
        self.use_tunnel.setChecked(
            self.services.config.get("db_use_tunnel", True)
        )

        self.db_host = QLineEdit(self.services.config.get("db_host", "127.0.0.1"))
        self.db_port = QLineEdit(self.services.config.get("db_port", "3306"))
        self.db_user = QLineEdit(self.services.config.get("db_user", ""))

        self.db_pass = QLineEdit(self.services.secrets.get("db_pass", ""))
        self.db_pass.setEchoMode(QLineEdit.EchoMode.Password)

        self.db_name = QLineEdit(self.services.config.get("db_name", ""))

        self.sqlite_path = QLineEdit(self.services.config.get("sqlite_path", ""))

        browse = QPushButton("...")
        browse.clicked.connect(self._browse_sqlite)

        sqlite_row = QHBoxLayout()
        sqlite_row.addWidget(self.sqlite_path, 1)
        sqlite_row.addWidget(browse)

        theme = self.services.theme.current

        section = lambda text: QLabel(
            f"<b style='color:{theme['accent']};'>{text}</b>"
        )

        db_layout.addRow("Backend:", self.backend)
        db_layout.addRow(section("SSH TUNNEL"), QLabel(""))
        db_layout.addRow("SSH Host:", self.ssh_host)
        db_layout.addRow("SSH User:", self.ssh_user)
        db_layout.addRow("SSH Port:", self.ssh_port)
        db_layout.addRow("SSH Password:", self.ssh_pass)
        db_layout.addRow("", self.use_tunnel)
        db_layout.addRow(section("DATABASE"), QLabel(""))
        db_layout.addRow("DB Host:", self.db_host)
        db_layout.addRow("DB Port:", self.db_port)
        db_layout.addRow("DB User:", self.db_user)
        db_layout.addRow("DB Password:", self.db_pass)
        db_layout.addRow("Default Schema:", self.db_name)
        db_layout.addRow("SQLite File:", sqlite_row)

        self.tabs.addTab(db_tab, "🗄 Default Database")

        # VPN tab.
        vpn_tab = QWidget()
        vpn_layout = QFormLayout(vpn_tab)

        self.vpn_cli = QLineEdit(self.services.config.get("vpn_cli", ""))
        self.vpn_host = QLineEdit(self.services.config.get("vpn_host", ""))

        self.vpn_cert = QLineEdit(self.services.secrets.get("vpn_cert_pass", ""))
        self.vpn_cert.setEchoMode(QLineEdit.EchoMode.Password)

        self.vpn_pass = QLineEdit(self.services.secrets.get("vpn_pass", ""))
        self.vpn_pass.setEchoMode(QLineEdit.EchoMode.Password)

        vpn_layout.addRow("VPN CLI Path:", self.vpn_cli)
        vpn_layout.addRow("VPN Host:", self.vpn_host)
        vpn_layout.addRow("Cert Password:", self.vpn_cert)
        vpn_layout.addRow("Password:", self.vpn_pass)

        self.tabs.addTab(vpn_tab, "🔒 VPN")

        # Terminal tab.
        term_tab = QWidget()
        term_layout = QFormLayout(term_tab)

        self.font_size = QSpinBox()
        self.font_size.setRange(8, 32)

        self.font_size.setValue(
            int(self.services.config.get("terminal_font_size", 13))
        )

        self.term_theme = QComboBox()
        self.term_theme.addItems(
            list(self.services.theme.terminal_themes.keys())
        )
        self.term_theme.setCurrentText(
            self.services.config.get("terminal_theme", "dark")
        )

        self.auto_reconnect = QCheckBox("Auto-reconnect dropped terminals")
        self.auto_reconnect.setChecked(
            self.services.config.get("auto_reconnect", True)
        )

        self.session_log = QCheckBox(
            "Record terminal sessions to ~/.admin_suite_sessions/"
        )
        self.session_log.setChecked(
            self.services.config.get("session_logging", True)
        )

        term_layout.addRow("Font Size:", self.font_size)
        term_layout.addRow("Terminal Theme:", self.term_theme)
        term_layout.addRow("", self.auto_reconnect)
        term_layout.addRow("", self.session_log)

        self.tabs.addTab(term_tab, "🖥 Terminal")

        layout.addWidget(self.tabs)

        save = QPushButton("💾 Save All Settings")
        save.clicked.connect(self.save)

        layout.addWidget(save)

    def _browse_sqlite(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "SQLite database",
            "",
            "SQLite (*.db *.sqlite *.sqlite3);;All (*)",
        )

        if path:
            self.sqlite_path.setText(path)

    def save(self) -> None:
        config = self.services.config
        secrets = self.services.secrets

        config.set("db_backend", self.backend.currentText())
        config.set("ssh_host", self.ssh_host.text().strip())
        config.set("ssh_user", self.ssh_user.text().strip())
        config.set("ssh_port", self.ssh_port.text().strip())
        config.set("db_host", self.db_host.text().strip())
        config.set("db_port", self.db_port.text().strip())
        config.set("db_user", self.db_user.text().strip())
        config.set("db_name", self.db_name.text().strip())
        config.set("db_use_tunnel", self.use_tunnel.isChecked())
        config.set("sqlite_path", self.sqlite_path.text().strip())

        config.set("terminal_font_size", self.font_size.value())
        config.set("terminal_theme", self.term_theme.currentText())
        config.set("auto_reconnect", self.auto_reconnect.isChecked())
        config.set("session_logging", self.session_log.isChecked())

        config.set("vpn_cli", self.vpn_cli.text().strip())
        config.set("vpn_host", self.vpn_host.text().strip())

        config.save()

        secrets.set("ssh_pass", self.ssh_pass.text())
        secrets.set("db_pass", self.db_pass.text())
        secrets.set("vpn_cert_pass", self.vpn_cert.text())
        secrets.set("vpn_pass", self.vpn_pass.text())

        self.services.notifications.push(
            "ok",
            "Settings",
            "Global settings saved",
        )

        self.accept()


# ------------------------------------------------------------
# Theme dialog
# ------------------------------------------------------------

class ThemeDialog(QDialog):
    """
    UI theme chooser.
    """

    def __init__(self, parent, services):
        super().__init__(parent)

        self.services = services

        self.setWindowTitle("Theme Manager")
        self.setMinimumWidth(380)

        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("Interface theme:"))

        self.list = QListWidget()

        current_theme = self.services.config.get("ui_theme", "Breeze Dark")

        for name, theme in self.services.theme.themes.items():
            item = QListWidgetItem(name)

            item.setBackground(QColor(theme["panel"]))
            item.setForeground(QColor(theme["text"]))

            if name == current_theme:
                item.setSelected(True)

            self.list.addItem(item)

        self.list.itemDoubleClicked.connect(lambda: self.accept())

        layout.addWidget(self.list)

        ok = QPushButton("Apply")
        ok.clicked.connect(self.accept)

        layout.addWidget(ok)

    def chosen(self):
        item = self.list.currentItem()
        return item.text() if item else None


# ------------------------------------------------------------
# SSH key manager
# ------------------------------------------------------------

class KeyManagerDialog(QDialog):
    """
    SSH key manager.
    """

    def __init__(self, parent, services):
        super().__init__(parent)

        self.services = services

        self.setWindowTitle("SSH Key Manager")
        self.resize(640, 480)

        self.ssh_dir = os.path.expanduser("~/.ssh")

        os.makedirs(self.ssh_dir, exist_ok=True)

        layout = QVBoxLayout(self)

        toolbar = QHBoxLayout()

        generate = QPushButton("🔑 Generate RSA Key...")
        generate.clicked.connect(self.generate)

        refresh = QPushButton("🔄 Refresh")
        refresh.clicked.connect(self.populate)

        toolbar.addWidget(generate)
        toolbar.addWidget(refresh)
        toolbar.addStretch()

        layout.addLayout(toolbar)

        self.list = QListWidget()
        self.list.itemSelectionChanged.connect(self.show_pub)

        layout.addWidget(self.list, 1)

        self.pub_view = QPlainTextEdit()
        self.pub_view.setReadOnly(True)
        self.pub_view.setMaximumHeight(120)
        self.pub_view.setPlaceholderText("Select a key to view its public part")

        layout.addWidget(self.pub_view)

        self.populate()

    def populate(self) -> None:
        self.list.clear()

        skip = {"known_hosts", "config", "authorized_keys"}

        for f in sorted(os.listdir(self.ssh_dir)):
            if f.endswith(".pub") and f[:-4] not in skip:
                self.list.addItem(f[:-4])

    def show_pub(self) -> None:
        item = self.list.currentItem()

        if item:
            try:
                with open(
                    os.path.join(self.ssh_dir, item.text() + ".pub"),
                    encoding="utf-8",
                ) as f:
                    self.pub_view.setPlainText(f.read())

            except Exception as e:
                self.pub_view.setPlainText(str(e))

    def generate(self) -> None:
        if paramiko is None:
            QMessageBox.warning(
                self,
                "SSH Keys",
                "paramiko is not installed.",
            )
            return

        name, ok = QInputDialog.getText(
            self,
            "Generate Key",
            "Key name:",
            text="id_admin_suite",
        )

        if not ok or not name:
            return

        bits, ok2 = QInputDialog.getItem(
            self,
            "Key Size",
            "Bits:",
            ["4096", "2048"],
            0,
            False,
        )

        if not ok2:
            return

        path = os.path.join(self.ssh_dir, name)

        if os.path.exists(path):
            QMessageBox.warning(self, "Generate", "File already exists.")
            return

        try:
            key = paramiko.RSAKey.generate(int(bits))
            key.write_private_key_file(path)

            os.chmod(path, 0o600)

            pub = f"ssh-rsa {key.get_base64()} admin-suite@{socket.gethostname()}"

            with open(path + ".pub", "w", encoding="utf-8") as f:
                f.write(pub + "\n")

            self.services.notifications.push(
                "ok",
                "Key generated",
                path,
            )

            self.populate()

        except Exception as e:
            QMessageBox.critical(self, "Generate", str(e))


# ------------------------------------------------------------
# Session log viewer
# ------------------------------------------------------------

class SessionLogViewerDialog(QDialog):
    """
    Viewer for recorded terminal sessions.
    """

    def __init__(self, parent, services):
        super().__init__(parent)

        self.services = services

        self.setWindowTitle("Recorded Sessions")
        self.resize(820, 540)

        layout = QHBoxLayout(self)

        self.list = QListWidget()
        self.list.setFixedWidth(280)
        self.list.itemSelectionChanged.connect(self.show_log)

        layout.addWidget(self.list)

        right = QVBoxLayout()

        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setFont(QFont("JetBrains Mono, Consolas", 10))

        right.addWidget(self.view)

        row = QHBoxLayout()

        open_folder = QPushButton("📂 Open Folder")
        open_folder.clicked.connect(self.open_folder)

        row.addStretch()
        row.addWidget(open_folder)

        right.addLayout(row)

        layout.addLayout(right, 1)

        self.populate()

    def populate(self) -> None:
        self.list.clear()

        LOG_DIR.mkdir(parents=True, exist_ok=True)

        for f in sorted(os.listdir(LOG_DIR), reverse=True):
            if f.endswith(".log"):
                self.list.addItem(f)

    def show_log(self) -> None:
        item = self.list.currentItem()

        if item:
            try:
                with open(
                    os.path.join(LOG_DIR, item.text()),
                    encoding="utf-8",
                    errors="replace",
                ) as f:
                    self.view.setPlainText(f.read())

            except Exception as e:
                self.view.setPlainText(str(e))

    def open_folder(self) -> None:
        try:
            import sys

            if sys.platform == "win32":
                os.startfile(LOG_DIR)

            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(LOG_DIR)])

            else:
                subprocess.Popen(["xdg-open", str(LOG_DIR)])

        except Exception as e:
            self.services.emit_log("system", f"Cannot open folder: {e}")


# ------------------------------------------------------------
# Snippets
# ------------------------------------------------------------

class SnippetDialog(QDialog):
    """
    Add/edit snippet.
    """

    def __init__(self, parent=None, snippet=None):
        super().__init__(parent)

        self.setWindowTitle("Edit Snippet" if snippet else "Add Snippet")
        self.setMinimumWidth(440)

        layout = QFormLayout(self)

        snippet = snippet or {}

        self.name_input = QLineEdit(snippet.get("name", ""))
        self.cat_input = QLineEdit(snippet.get("category", "General"))
        self.desc_input = QLineEdit(snippet.get("description", ""))

        self.cmd_input = QTextEdit(snippet.get("command", ""))
        self.cmd_input.setFont(QFont("JetBrains Mono, Consolas", 11))
        self.cmd_input.setMaximumHeight(130)

        layout.addRow("Name:", self.name_input)
        layout.addRow("Category:", self.cat_input)
        layout.addRow("Description:", self.desc_input)
        layout.addRow("Command:", self.cmd_input)

        save = QPushButton("Save")
        save.clicked.connect(self.accept)

        layout.addRow(save)

    def get_data(self) -> dict:
        return {
            "name": self.name_input.text().strip(),
            "command": self.cmd_input.toPlainText(),
            "description": self.desc_input.text().strip(),
            "category": self.cat_input.text().strip() or "General",
        }


class SnippetVariablesDialog(QDialog):
    """Prompts the user to fill in values for template snippet variables ({{var}} or {{var:default}})."""

    def __init__(self, raw_cmd: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Snippet Parameters (Termius Style)")
        self.setMinimumWidth(460)
        self.raw_cmd = raw_cmd
        self._pattern = re.compile(r"\{\{([a-zA-Z0-9_-]+)(?::([^}]*))?\}\}")

        matches = self._pattern.findall(raw_cmd)
        self.var_inputs: dict[str, QLineEdit] = {}

        layout = QVBoxLayout(self)

        info_lbl = QLabel("Provide values for snippet variables:")
        info_lbl.setStyleSheet("font-weight: bold; margin-bottom: 4px;")
        layout.addWidget(info_lbl)

        form = QFormLayout()
        seen = set()
        for var_name, default_val in matches:
            if var_name in seen:
                continue
            seen.add(var_name)
            edit = QLineEdit(default_val)
            edit.textChanged.connect(self._update_preview)
            form.addRow(f"{var_name}:", edit)
            self.var_inputs[var_name] = edit

        layout.addLayout(form)

        layout.addWidget(QLabel("Rendered Command Preview:"))
        self.preview_lbl = QLabel()
        self.preview_lbl.setStyleSheet(
            "font-family: monospace; color: #3daee9; padding: 6px; background: #222; border: 1px solid #444; border-radius: 4px;"
        )
        self.preview_lbl.setWordWrap(True)
        layout.addWidget(self.preview_lbl)

        btn_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btn_box.accepted.connect(self.accept)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

        self._update_preview()

    def _update_preview(self):
        self.preview_lbl.setText(self.get_rendered_command())

    def get_rendered_command(self) -> str:
        def _replace_var(match):
            name = match.group(1)
            if name in self.var_inputs:
                return self.var_inputs[name].text().strip()
            return match.group(2) or ""

        return self._pattern.sub(_replace_var, self.raw_cmd)


class SnippetManagerDialog(QDialog):
    """
    Snippet library.
    """

    def __init__(self, parent, services):
        super().__init__(parent)

        self.services = services

        self.setWindowTitle("Command Snippets Library")
        self.resize(780, 540)

        self.snippets = []

        self.load()

        layout = QVBoxLayout(self)

        toolbar = QHBoxLayout()

        add = QPushButton("➕ Add")
        add.clicked.connect(self.add)

        edit = QPushButton("✏️ Edit")
        edit.clicked.connect(self.edit)

        delete = QPushButton("🗑 Delete")
        delete.clicked.connect(self.delete)

        toolbar.addWidget(add)
        toolbar.addWidget(edit)
        toolbar.addWidget(delete)
        toolbar.addStretch()

        run = QPushButton("▶ Run in active terminal")
        run.clicked.connect(self.run_selected)

        toolbar.addWidget(run)

        layout.addLayout(toolbar)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search snippets...")
        self.search.textChanged.connect(lambda t: self.populate(t))

        layout.addWidget(self.search)

        self.list = QTreeWidget()
        self.list.setColumnCount(3)
        self.list.setHeaderLabels(["Name", "Category", "Command"])

        self.list.header().setSectionResizeMode(
            2,
            QHeaderView.ResizeMode.Stretch,
        )

        self.list.itemDoubleClicked.connect(lambda: self.run_selected())

        layout.addWidget(self.list, 1)

        self.populate()

    def load(self) -> None:
        self.snippets = read_json(SNIPPETS_FILE, [])

        if not self.snippets:
            self.snippets = [
                {
                    "name": "System Info",
                    "command": "uname -a && cat /etc/os-release",
                    "description": "OS info",
                    "category": "System",
                },
                {
                    "name": "Disk Usage",
                    "command": "df -h",
                    "description": "Disk usage",
                    "category": "System",
                },
                {
                    "name": "Memory",
                    "command": "free -h",
                    "description": "Memory",
                    "category": "System",
                },
                {
                    "name": "Top CPU",
                    "command": "ps aux --sort=-%cpu | head -20",
                    "description": "Top CPU",
                    "category": "Process",
                },
                {
                    "name": "Listening Ports",
                    "command": "ss -tunlp",
                    "description": "Network",
                    "category": "Network",
                },
                {
                    "name": "Docker PS",
                    "command": "docker ps -a",
                    "description": "Containers",
                    "category": "Docker",
                },
                {
                    "name": "Docker Tail Logs",
                    "command": "docker logs -f --tail={{lines:100}} {{container_name}}",
                    "description": "Live tail container logs",
                    "category": "Docker",
                },
                {
                    "name": "Restart Service",
                    "command": "sudo systemctl restart {{service_name:nginx}}",
                    "description": "Restart systemd service",
                    "category": "System",
                },
                {
                    "name": "Search Log for Pattern",
                    "command": "grep -rn \"{{pattern:error}}\" {{log_path:/var/log/syslog}}",
                    "description": "Search log file with pattern",
                    "category": "Log",
                },
                {
                    "name": "MySQL Dump Database",
                    "command": "mysqldump -u {{user:root}} -p {{database}} > {{output_file:backup.sql}}",
                    "description": "Export database dump",
                    "category": "Database",
                },
            ]

            self.save()

    def save(self) -> None:
        write_json_secure(SNIPPETS_FILE, self.snippets)

    def populate(self, filt: str = "") -> None:
        self.list.clear()

        for snippet in self.snippets:
            haystack = (
                snippet.get("name", "") + snippet.get("command", "")
            ).lower()

            if filt and filt.lower() not in haystack:
                continue

            item = QTreeWidgetItem(
                [
                    snippet.get("name", ""),
                    snippet.get("category", ""),
                    snippet.get("command", "")[:90],
                ]
            )

            item.setToolTip(2, snippet.get("command", ""))
            item.setData(0, Qt.ItemDataRole.UserRole, snippet)

            self.list.addTopLevelItem(item)

    def add(self) -> None:
        dialog = SnippetDialog(self)

        if dialog.exec() == QDialog.DialogCode.Accepted:
            data = dialog.get_data()

            if data["name"] and data["command"]:
                self.snippets.append(data)
                self.save()
                self.populate()

    def edit(self) -> None:
        item = self.list.currentItem()

        if not item:
            return

        snippet = item.data(0, Qt.ItemDataRole.UserRole)

        dialog = SnippetDialog(self, snippet)

        if dialog.exec() == QDialog.DialogCode.Accepted:
            snippet.update(dialog.get_data())

            self.save()
            self.populate()

    def delete(self) -> None:
        item = self.list.currentItem()

        if item and QMessageBox.question(
            self,
            "Delete",
            "Delete snippet?",
        ) == QMessageBox.StandardButton.Yes:
            snippet = item.data(0, Qt.ItemDataRole.UserRole)

            if snippet in self.snippets:
                self.snippets.remove(snippet)

            self.save()
            self.populate()

    def run_selected(self) -> None:
        item = self.list.currentItem()

        if item and self.parent() and hasattr(
            self.parent(),
            "run_snippet_in_terminal",
        ):
            snippet = item.data(0, Qt.ItemDataRole.UserRole)
            cmd = snippet.get("command", "")

            if "{{" in cmd and "}}" in cmd:
                dlg = SnippetVariablesDialog(cmd, self)
                if dlg.exec() != QDialog.DialogCode.Accepted:
                    return
                cmd = dlg.get_rendered_command()

            self.parent().run_snippet_in_terminal(cmd)

            self.accept()
