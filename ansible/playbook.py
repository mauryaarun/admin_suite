"""
Ansible playbook/vault runner.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from typing import Any, Optional

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QTextCursor
from PyQt6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from admin_suite.core.fonts import mono_font
from admin_suite.core.icons import style_icon_button
from admin_suite.ssh.credentials import profile_creds


class AnsiblePlaybookThread(QThread):
    """
    Runs ansible-playbook as a subprocess.
    """

    output_ready = pyqtSignal(str)
    run_finished = pyqtSignal(bool)

    def __init__(
        self,
        args: list[str],
        cwd: Optional[str] = None,
        env: Optional[dict[str, str]] = None,
        cleanup_files: Optional[list[str]] = None,
    ):
        super().__init__()

        self.args = args
        self.cwd = cwd
        self.env = env or os.environ.copy()
        self.cleanup_files = cleanup_files or []

        self.proc: Optional[subprocess.Popen] = None

    def run(self) -> None:
        try:
            safe_cmd = " ".join(self.args)

            self.output_ready.emit(f"$ {safe_cmd}\n")

            preexec = getattr(os, "setsid", None)

            self.proc = subprocess.Popen(
                self.args,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                errors="replace",
                cwd=self.cwd,
                env=self.env,
                preexec_fn=preexec,
            )

            for line in self.proc.stdout:
                self.output_ready.emit(line)

            rc = self.proc.wait()

            self.output_ready.emit(
                f"\n[ansible-playbook exited with code {rc}]\n"
            )

            self.run_finished.emit(rc == 0)

        except Exception as e:
            self.output_ready.emit(f"\n[ERROR] {e}\n")
            self.run_finished.emit(False)

        finally:
            for path in self.cleanup_files:
                try:
                    os.remove(path)
                except Exception:
                    pass

    def stop(self) -> None:
        if not self.proc:
            return

        if self.proc.poll() is not None:
            return

        try:
            import signal

            if os.name != "nt":
                os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
            else:
                self.proc.terminate()

        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass


class AnsiblePlaybookTab(QWidget):
    """
    UI for running ansible-playbook with optional vault password and
    temporary inventory generated from selected SSH profiles.
    """

    def __init__(self, services, main_window=None):
        super().__init__(main_window)

        self.services = services
        self.main_window = main_window

        self._worker: Optional[AnsiblePlaybookThread] = None

        theme = self.services.theme.current

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        header = QLabel("📜 Ansible Playbook / Vault Runner")
        header.setStyleSheet(
            f"color:{theme['accent']};font-weight:bold;font-size:15px;padding:6px;"
        )

        layout.addWidget(header)

        splitter = QSplitter(Qt.Orientation.Vertical)

        top = QWidget()
        top_layout = QVBoxLayout(top)
        top_layout.setContentsMargins(0, 0, 0, 0)

        form = QGridLayout()

        # Playbook.
        form.addWidget(QLabel("Playbook:"), 0, 0)

        self.playbook_path = QLineEdit(
            self.services.config.get("playbook_dir", "")
        )
        self.playbook_path.setPlaceholderText("/path/to/playbook.yml")

        form.addWidget(self.playbook_path, 0, 1)

        pb_browse = QPushButton("...")
        pb_browse.clicked.connect(self._browse_playbook)

        form.addWidget(pb_browse, 0, 2)

        # Inventory.
        form.addWidget(QLabel("Inventory File:"), 1, 0)

        self.inventory_path = QLineEdit()
        self.inventory_path.setPlaceholderText(
            "Optional: /path/to/inventory"
        )

        form.addWidget(self.inventory_path, 1, 1)

        inv_browse = QPushButton("...")
        inv_browse.clicked.connect(self._browse_inventory)

        self.inventory_browse = inv_browse

        form.addWidget(inv_browse, 1, 2)

        # Vault.
        form.addWidget(QLabel("Vault Password:"), 2, 0)

        self.vault_pass = QLineEdit()
        self.vault_pass.setEchoMode(QLineEdit.EchoMode.Password)

        form.addWidget(self.vault_pass, 2, 1)

        form.addWidget(QLabel("Vault ID:"), 3, 0)

        self.vault_id = QLineEdit()
        self.vault_id.setPlaceholderText("Optional, e.g. dev@vault")

        form.addWidget(self.vault_id, 3, 1)

        # Options.
        form.addWidget(QLabel("Forks:"), 4, 0)

        self.forks = QSpinBox()
        self.forks.setRange(1, 100)
        self.forks.setValue(5)

        form.addWidget(self.forks, 4, 1)

        form.addWidget(QLabel("Limit:"), 5, 0)

        self.limit = QLineEdit()
        self.limit.setPlaceholderText("Optional: host or group pattern")

        form.addWidget(self.limit, 5, 1)

        form.addWidget(QLabel("Tags:"), 6, 0)

        self.tags = QLineEdit()

        form.addWidget(self.tags, 6, 1)

        form.addWidget(QLabel("Skip Tags:"), 7, 0)

        self.skip_tags = QLineEdit()

        form.addWidget(self.skip_tags, 7, 1)

        form.addWidget(QLabel("Extra Vars:"), 8, 0)

        self.extra_vars = QLineEdit()
        self.extra_vars.setPlaceholderText(
            'Optional: key=value or {"json":"vars"}'
        )

        form.addWidget(self.extra_vars, 8, 1)

        form.addWidget(QLabel("Verbosity:"), 9, 0)

        self.verbose = QSpinBox()
        self.verbose.setRange(0, 4)
        self.verbose.setValue(0)

        form.addWidget(self.verbose, 9, 1)

        # Checkboxes.
        self.check_mode = QCheckBox("Check mode (--check)")
        self.diff_mode = QCheckBox("Diff (--diff)")

        self.use_profiles_chk = QCheckBox(
            "Generate temporary inventory from selected SSH profiles"
        )

        self.use_profiles_chk.setChecked(True)
        self.use_profiles_chk.stateChanged.connect(self._on_use_profiles)

        form.addWidget(self.check_mode, 10, 0, 1, 2)
        form.addWidget(self.diff_mode, 10, 2)
        form.addWidget(self.use_profiles_chk, 11, 0, 1, 3)

        top_layout.addLayout(form)

        # Profile selection.
        profile_bar = QHBoxLayout()

        profile_bar.addWidget(
            QLabel("SSH Profiles for temporary inventory:")
        )

        profile_bar.addStretch()

        select_all = QPushButton("Select All")
        select_all.clicked.connect(self.profile_list_select_all)

        profile_bar.addWidget(select_all)

        top_layout.addLayout(profile_bar)

        self.profile_list = QListWidget()
        self.profile_list.setSelectionMode(
            QListWidget.SelectionMode.MultiSelection
        )

        self._populate_profiles()

        top_layout.addWidget(self.profile_list)

        splitter.addWidget(top)

        # Output.
        output_widget = QWidget()
        output_layout = QVBoxLayout(output_widget)
        output_layout.setContentsMargins(0, 0, 0, 0)

        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setFont(mono_font(11))

        output_layout.addWidget(self.output)

        splitter.addWidget(output_widget)

        splitter.setSizes([430, 430])

        layout.addWidget(splitter, 1)

        # Buttons.
        buttons = QHBoxLayout()

        self.run_btn = QPushButton()
        style_icon_button(
            self.run_btn,
            "play",
            "Run Ansible Playbook on selected hosts",
            icon_only=True,
            color="#22c55e",
        )
        self.run_btn.clicked.connect(self.run_playbook)

        self.stop_btn = QPushButton()
        style_icon_button(self.stop_btn, "stop", "Stop running playbook", icon_only=True, color="#ef4444")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.stop_playbook)

        clear = QPushButton()
        style_icon_button(clear, "trash", "Clear playbook output", icon_only=True)
        clear.clicked.connect(lambda: self.output.clear())

        buttons.addWidget(self.run_btn)
        buttons.addWidget(self.stop_btn)
        buttons.addWidget(clear)
        buttons.addStretch()

        layout.addLayout(buttons)

        self._on_use_profiles()

    # ------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------

    def _populate_profiles(self) -> None:
        self.profile_list.clear()

        if not self.main_window:
            return

        profiles = getattr(self.main_window, "profiles", {})

        for name, data in sorted(profiles.items()):
            data = dict(data)
            data.setdefault("name", name)

            item = QListWidgetItem(
                f"🖥 {name} ({data.get('ssh_host', '')})"
            )

            item.setData(Qt.ItemDataRole.UserRole, data)

            self.profile_list.addItem(item)

    def profile_list_select_all(self) -> None:
        self.profile_list.selectAll()

    def _on_use_profiles(self, *args) -> None:
        use_profiles = self.use_profiles_chk.isChecked()

        self.inventory_path.setEnabled(not use_profiles)
        self.inventory_browse.setEnabled(not use_profiles)
        self.profile_list.setEnabled(use_profiles)

    def _browse_playbook(self) -> None:
        start_dir = (
            self.services.config.get("playbook_dir", "")
            or os.path.expanduser("~")
        )

        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Playbook",
            start_dir,
            "YAML files (*.yml *.yaml);;All files (*)",
        )

        if path:
            self.playbook_path.setText(path)

    def _browse_inventory(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Inventory",
            os.path.expanduser("~"),
            "Inventory files (*.ini *.json *.yml *.yaml);;All files (*)",
        )

        if path:
            self.inventory_path.setText(path)

    def _append_output(self, text: str) -> None:
        self.output.moveCursor(QTextCursor.MoveOperation.End)
        self.output.insertPlainText(text)
        self.output.moveCursor(QTextCursor.MoveOperation.End)

    @staticmethod
    def _cleanup_files(paths: list[str]) -> None:
        for path in paths:
            try:
                os.remove(path)
            except Exception:
                pass

    # ------------------------------------------------------------
    # Run
    # ------------------------------------------------------------

    def run_playbook(self) -> None:
        if self._worker:
            return

        playbook = os.path.expanduser(self.playbook_path.text().strip())

        if not playbook:
            QMessageBox.warning(
                self,
                "Playbook",
                "Playbook path is required.",
            )
            return

        if not os.path.exists(playbook):
            QMessageBox.warning(
                self,
                "Playbook",
                f"Playbook not found:\n{playbook}",
            )
            return

        args = ["ansible-playbook", playbook]

        cleanup_files: list[str] = []

        env = os.environ.copy()
        env["ANSIBLE_FORCE_COLOR"] = "false"
        env["ANSIBLE_HOST_KEY_CHECKING"] = "False"

        # Verbosity.
        verbosity = int(self.verbose.value())

        if verbosity > 0:
            args.append("-" + ("v" * verbosity))

        # Forks.
        args += ["-f", str(int(self.forks.value()))]

        # Limit.
        limit = self.limit.text().strip()

        if limit:
            args += ["--limit", limit]

        # Tags.
        tags = self.tags.text().strip()

        if tags:
            args += ["--tags", tags]

        skip_tags = self.skip_tags.text().strip()

        if skip_tags:
            args += ["--skip-tags", skip_tags]

        # Extra vars.
        extra_vars = self.extra_vars.text().strip()

        if extra_vars:
            args += ["--extra-vars", extra_vars]

        # Modes.
        if self.check_mode.isChecked():
            args.append("--check")

        if self.diff_mode.isChecked():
            args.append("--diff")

        # Inventory.
        inventory_path = self.inventory_path.text().strip()

        if self.use_profiles_chk.isChecked():
            selected = []

            for item in self.profile_list.selectedItems():
                data = item.data(Qt.ItemDataRole.UserRole)

                if data:
                    selected.append(data)

            if not selected:
                QMessageBox.warning(
                    self,
                    "Profiles",
                    "Select at least one SSH profile or disable profile inventory.",
                )
                return

            inventory = {
                "targets": {
                    "hosts": {},
                    "vars": {
                        "ansible_connection": "ssh",
                    },
                }
            }

            for data in selected:
                name = data.get("name", "host")

                alias = re.sub(r"[^A-Za-z0-9_]", "_", name)

                creds = profile_creds(data)

                host_vars = {
                    "ansible_host": (
                        data.get("ssh_host")
                        or data.get("host")
                        or ""
                    ),
                    "ansible_user": (
                        data.get("ssh_user")
                        or data.get("user")
                        or ""
                    ),
                    "ansible_port": int(
                        data.get("ssh_port")
                        or data.get("port")
                        or 22
                    ),
                }

                if creds.key_path:
                    host_vars["ansible_ssh_private_key_file"] = creds.key_path

                if creds.password:
                    host_vars["ansible_password"] = creds.password

                inventory["targets"]["hosts"][alias] = host_vars

            fd, tmp_inventory = tempfile.mkstemp(
                prefix="admin_suite_ansible_inventory_",
                suffix=".json",
            )

            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(inventory, f, indent=2)

            try:
                os.chmod(tmp_inventory, 0o600)
            except Exception:
                pass

            cleanup_files.append(tmp_inventory)

            inventory_path = tmp_inventory

            args += ["-i", inventory_path]

            self._append_output(
                "[INFO] Generated temporary JSON inventory from selected profiles.\n"
            )

            self._append_output(
                "[WARN] Temporary inventory may contain SSH passwords. "
                "It will be deleted after execution.\n"
            )

        elif inventory_path:
            inventory_path = os.path.expanduser(inventory_path)

            if not os.path.exists(inventory_path):
                QMessageBox.warning(
                    self,
                    "Inventory",
                    f"Inventory not found:\n{inventory_path}",
                )
                return

            args += ["-i", inventory_path]

        else:
            self._append_output(
                "[WARN] No inventory selected. "
                "Using default Ansible inventory.\n"
            )

        # Vault.
        vault_pass = self.vault_pass.text()

        if vault_pass:
            fd, tmp_vault = tempfile.mkstemp(
                prefix="admin_suite_ansible_vault_",
                suffix=".txt",
            )

            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(vault_pass)

            try:
                os.chmod(tmp_vault, 0o600)
            except Exception:
                pass

            cleanup_files.append(tmp_vault)

            vault_id = self.vault_id.text().strip()

            if vault_id:
                args += ["--vault-id", f"{vault_id}@{tmp_vault}"]
            else:
                args += ["--vault-password-file", tmp_vault]

            self._append_output(
                "[INFO] Vault password file created for this run. "
                "It will be deleted after execution.\n"
            )

        cwd = os.path.dirname(playbook) or None

        self.run_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)

        self._append_output("\n" + "=" * 70 + "\n")
        self._append_output("Starting ansible-playbook run...\n")
        self._append_output("=" * 70 + "\n")

        self._worker = AnsiblePlaybookThread(
            args=args,
            cwd=cwd,
            env=env,
            cleanup_files=cleanup_files,
        )

        self._worker.output_ready.connect(self._append_output)
        self._worker.run_finished.connect(self._run_finished)

        self._worker.start()

    def stop_playbook(self) -> None:
        if self._worker:
            self._append_output(
                "\n[WARN] Stop requested. Terminating ansible-playbook...\n"
            )

            self._worker.stop()

    def _run_finished(self, ok: bool) -> None:
        self.run_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)

        if ok:
            self.services.notifications.push(
                "ok",
                "Ansible",
                "Playbook run completed successfully.",
            )
        else:
            self.services.notifications.push(
                "error",
                "Ansible",
                "Playbook run failed or was stopped.",
            )

        self._worker = None
