from __future__ import annotations

import shlex
import subprocess
from typing import Any, Callable, Optional

from PyQt6.QtCore import Qt, QSortFilterProxyModel, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QCursor, QFont, QStandardItem, QStandardItemModel
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QStackedWidget,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from admin_suite.core.export import ReportExporter
from admin_suite.ssh.remote_exec import RemoteExecThread
from admin_suite.ui.dialogs import SudoCredentialsDialog, ensure_sudo_credentials
from admin_suite.sysadmin.commands import SYSADMIN_CMDS, SYSADMIN_SECTIONS
from admin_suite.sysadmin.presenter import (
    SysAdminContainersWidget,
    SysAdminCronWidget,
    SysAdminJournalWidget,
    SysAdminNetworkWidget,
    SysAdminOverviewWidget,
    SysAdminPackagesWidget,
    SysAdminPerformanceWidget,
    SysAdminSecurityWidget,
    SysAdminStorageWidget,
)


# ------------------------------------------------------------------
# Local execution fallback (when no SSH profile is configured)
# ------------------------------------------------------------------
class LocalExecThread(QThread):
    """Run a shell command locally with optional sudo password piping and emit the output."""

    finished_cmd = pyqtSignal(str, int)  # output, return_code

    def __init__(
        self,
        cmd: str,
        timeout: int = 45,
        sudo_password: Optional[str] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.cmd = cmd
        self.timeout = timeout
        self.sudo_password = sudo_password

    def run(self) -> None:
        try:
            p = subprocess.Popen(
                ["bash", "-c", self.cmd],
                stdin=subprocess.PIPE if self.sudo_password is not None else None,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            out, err = p.communicate(
                input=(self.sudo_password + "\n") if self.sudo_password else None,
                timeout=self.timeout,
            )
            combined = out + (("\n" + err) if err else "")
            self.finished_cmd.emit(combined, p.returncode)
        except subprocess.TimeoutExpired:
            self.finished_cmd.emit("[timeout]\n", 124)
        except Exception as exc:
            self.finished_cmd.emit(f"[error] {exc}\n", 1)


# ------------------------------------------------------------------
# Main dashboard widget
# ------------------------------------------------------------------
class SysAdminTab(QWidget):
    def __init__(
        self,
        services,
        profile_name: str,
        profile: Optional[dict[str, Any]],
        parent=None,
    ):
        super().__init__(parent)
        self.services = services
        self.profile_name = profile_name or "Local"
        self.profile = profile  # None → local execution

        # Keep references to workers to prevent garbage collection
        self._workers: list[QThread] = []

        theme = self.services.theme.current
        self.theme = theme

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        # --- Left Panel (Navigation) ---
        left = QVBoxLayout()
        is_local_display = " (Localhost)" if not self._is_remote() else ""
        header = QLabel(f"🖥 {self.profile_name}{is_local_display}")
        header.setStyleSheet(
            f"color:{theme['accent']};font-weight:bold;font-size:14px;padding:4px;"
        )
        left.addWidget(header)

        self.nav = QListWidget()
        self.nav.setFixedWidth(160)
        for section in SYSADMIN_SECTIONS:
            self.nav.addItem(section)
        self.nav.currentTextChanged.connect(self.load_section)
        left.addWidget(self.nav)

        sudo_row = QHBoxLayout()
        self.sudo_chk = QCheckBox("Use sudo")
        self.sudo_chk.setToolTip("Elevate commands using sudo")
        self.sudo_chk.setChecked(True)
        sudo_row.addWidget(self.sudo_chk, 1)

        self.sudo_btn = QPushButton("🔑")
        self.sudo_btn.setToolTip("Configure Root / Sudo credentials for this profile")
        self.sudo_btn.setFixedWidth(28)
        self.sudo_btn.clicked.connect(self._configure_sudo_credentials)
        sudo_row.addWidget(self.sudo_btn)

        left.addLayout(sudo_row)

        self.refresh_btn = QPushButton("🔄 Refresh")
        self.refresh_btn.clicked.connect(self._refresh_current)
        left.addWidget(self.refresh_btn)

        left.addStretch()
        layout.addLayout(left)

        # --- Right Panel (Content) ---
        right = QVBoxLayout()
        self.action_bar = QHBoxLayout()
        right.addLayout(self.action_bar)

        self.stack = QStackedWidget()
        self.view_mode = "formatted"  # "formatted" or "raw"
        self.btn_view_mode: Optional[QPushButton] = None
        self.presenter_views: dict[str, Any] = {}
        self.section_stacks: dict[str, QStackedWidget] = {}
        self.raw_text_views: dict[str, QPlainTextEdit] = {}
        self.text_views: dict[str, QPlainTextEdit] = self.raw_text_views  # alias for backward compatibility
        self.table_views: dict[str, QTableView] = {}

        presenter_classes = {
            "Overview": SysAdminOverviewWidget,
            "Storage": SysAdminStorageWidget,
            "Network": SysAdminNetworkWidget,
            "Journal": SysAdminJournalWidget,
            "Cron": SysAdminCronWidget,
            "Security": SysAdminSecurityWidget,
            "Packages": SysAdminPackagesWidget,
            "Containers": SysAdminContainersWidget,
            "Performance": SysAdminPerformanceWidget,
        }

        for section in SYSADMIN_SECTIONS:
            sec_stack = QStackedWidget()
            self.section_stacks[section] = sec_stack

            # Create raw CLI text view for every section
            raw_text = QPlainTextEdit()
            raw_text.setReadOnly(True)
            raw_text.setFont(QFont("JetBrains Mono, Consolas", 11))
            raw_text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
            ReportExporter.attach_export_context_menu(raw_text)
            self.raw_text_views[section] = raw_text

            if section in ("Users", "Services", "Processes"):
                table = QTableView()
                table.setAlternatingRowColors(True)
                table.setSortingEnabled(True)

                model = QStandardItemModel()
                proxy = QSortFilterProxyModel()
                proxy.setSourceModel(model)
                table.setModel(proxy)

                table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
                table.customContextMenuRequested.connect(
                    lambda pos, t=table, s=section: self._show_table_menu(t, s, pos)
                )

                self.table_views[section] = table
                sec_stack.addWidget(table)     # Page 0: formatted table
                sec_stack.addWidget(raw_text)  # Page 1: raw output
            else:
                p_cls = presenter_classes.get(section)
                if p_cls:
                    if section in ("Journal", "Packages", "Containers"):
                        pres_widget = p_cls(theme=theme, run_cmd_fn=self.execute_custom_cmd, parent=self)
                    else:
                        pres_widget = p_cls(theme=theme, parent=self)
                    self.presenter_views[section] = pres_widget
                    sec_stack.addWidget(pres_widget)  # Page 0: visual cards/tables
                    sec_stack.addWidget(raw_text)     # Page 1: raw output
                else:
                    sec_stack.addWidget(raw_text)

            self.stack.addWidget(sec_stack)

        right.addWidget(self.stack, 1)

        self.status = QLabel("Select a section")
        self.status.setStyleSheet(f"color:{theme['sub']};")
        right.addWidget(self.status)

        layout.addLayout(right, 1)
        self.nav.setCurrentRow(0)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _is_remote(self) -> bool:
        """Return True when a profile is a remote SSH host, False for localhost/local execution."""
        if not self.profile:
            return False
        if bool(self.profile.get("is_local")):
            return False
        if self.profile_name.lower() == "localhost":
            return False
        host = self.profile.get("ssh_host") or self.profile.get("host")
        if host in ("localhost", "127.0.0.1", "::1") and bool(self.profile.get("use_local_exec", True)):
            return False
        return True

    def _configure_sudo_credentials(self) -> None:
        import getpass
        user = ""
        if self.profile:
            user = self.profile.get("ssh_user") or self.profile.get("user") or ""
        if not user:
            user = getpass.getuser() if not self._is_remote() else ""
        cur_pw = self.services.get_sudo_password(self.profile_name) or ""
        dlg = SudoCredentialsDialog(
            self,
            self.services,
            self.profile_name,
            user,
            current_password=cur_pw,
        )
        if dlg.exec() == QDialog.DialogCode.Accepted:
            pw = dlg.get_password()
            self.services.set_sudo_password(
                self.profile_name,
                pw,
                persist=dlg.should_save_to_profile(),
            )
            if dlg.should_save_to_profile() and self.profile is not None:
                self.profile["sudo_pass"] = pw
            self.services.notifications.push(
                "ok",
                "Root/Sudo Credentials",
                f"Updated elevation credentials for {self.profile_name}",
            )

    def _show_table_menu(self, table: QTableView, section: str, pos) -> None:
        menu = QMenu(self)
        copy_act = menu.addAction("📋 Copy Selected Cell")
        menu.addSeparator()
        csv_act = menu.addAction("📊 Export Table to CSV...")
        json_act = menu.addAction("📄 Export Table to JSON...")
        action = menu.exec(table.mapToGlobal(pos))
        if action == copy_act:
            idx = table.currentIndex()
            if idx.isValid():
                QApplication.clipboard().setText(str(table.model().data(idx)))
        elif action == csv_act:
            ReportExporter.export_table_csv(self, table, f"{section.lower()}_report.csv", f"Export {section} to CSV")
        elif action == json_act:
            ReportExporter.export_table_json(self, table, f"{section.lower()}_report.json", f"Export {section} to JSON")

    def _toggle_view_mode(self) -> None:
        if self.view_mode == "formatted":
            self.view_mode = "raw"
        else:
            self.view_mode = "formatted"
        self._update_view_mode_button()
        self._apply_view_mode()

    def _update_view_mode_button(self) -> None:
        if not self.btn_view_mode:
            return
        if self.view_mode == "formatted":
            self.btn_view_mode.setText("📊 View: Formatted")
            self.btn_view_mode.setToolTip("Currently viewing formatted visual cards & tables. Click to switch to raw CLI output.")
            self.btn_view_mode.setStyleSheet(f"background: {self.theme.get('accent', '#3daee9')}; color: #ffffff; font-weight: bold; padding: 3px 8px; border-radius: 3px;")
        else:
            self.btn_view_mode.setText("📜 View: Raw Output")
            self.btn_view_mode.setToolTip("Currently viewing raw CLI output. Click to switch to formatted visual cards & tables.")
            self.btn_view_mode.setStyleSheet("padding: 3px 8px; border-radius: 3px;")

    def _apply_view_mode(self) -> None:
        target_idx = 0 if self.view_mode == "formatted" else 1
        for stack in self.section_stacks.values():
            if stack.count() > 1:
                stack.setCurrentIndex(target_idx)

    def _open_linux_settings(self) -> None:
        from admin_suite.ui.linux_settings_dialog import LinuxSettingsDialog
        dlg = LinuxSettingsDialog(self.services, self)
        dlg.settings_applied.connect(self._refresh_current)
        dlg.exec()

    def _export_current_section(self, name: str) -> None:
        menu = QMenu(self)
        if name in ("Users", "Services", "Processes") and self.view_mode == "formatted":
            csv_act = menu.addAction("📊 Export as CSV File")
            json_act = menu.addAction("📄 Export as JSON File")
            txt_act = menu.addAction("📜 Export Raw Output (.txt)")
            action = menu.exec(QCursor.pos())
            table = self.table_views.get(name)
            if not table:
                return
            if action == csv_act:
                ReportExporter.export_table_csv(self, table, f"{name.lower()}_report.csv", f"Export {name} to CSV")
            elif action == json_act:
                ReportExporter.export_table_json(self, table, f"{name.lower()}_report.json", f"Export {name} to JSON")
            elif action == txt_act:
                raw_edit = self.raw_text_views.get(name)
                if raw_edit:
                    ReportExporter.export_text_file(self, raw_edit.toPlainText(), f"{name.lower()}_report.txt", f"Export {name} Report")
        else:
            txt_act = menu.addAction("📄 Export as Text File (.txt)")
            md_act = menu.addAction("📝 Export as Markdown (.md)")
            sel_act = menu.addAction("💾 Export Selected Excerpt")
            action = menu.exec(QCursor.pos())
            text_edit = self.raw_text_views.get(name)
            if not text_edit:
                return
            if action == txt_act:
                ReportExporter.export_text_file(self, text_edit.toPlainText(), f"{name.lower()}_report.txt", f"Export {name} Report")
            elif action == md_act:
                content = f"# {name} Report — {self.profile_name}\n\n```\n{text_edit.toPlainText()}\n```\n"
                ReportExporter.export_text_file(self, content, f"{name.lower()}_report.md", f"Export {name} Markdown", "Markdown (*.md);;All Files (*)")
            elif action == sel_act:
                selected = text_edit.textCursor().selectedText().replace("\u2029", "\n")
                ReportExporter.export_excerpt_dialog(self, selected, f"{name.lower()}_excerpt.txt")

    def _set_loading(self, loading: bool) -> None:
        self.nav.setEnabled(not loading)
        self.refresh_btn.setEnabled(not loading)
        self.sudo_chk.setEnabled(not loading)
        if loading:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        else:
            QApplication.restoreOverrideCursor()

    def _cleanup_workers(self) -> None:
        self._workers = [w for w in self._workers if w.isRunning()]

    def _refresh_current(self) -> None:
        item = self.nav.currentItem()
        if item:
            self.load_section(item.text())

    def _clear_actions(self) -> None:
        while self.action_bar.count():
            item = self.action_bar.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    # ------------------------------------------------------------------
    # Section loading
    # ------------------------------------------------------------------
    def load_section(self, name: str) -> None:
        if not name:
            return

        self._cleanup_workers()

        index = SYSADMIN_SECTIONS.index(name)
        self.stack.setCurrentIndex(index)
        self._clear_actions()

        if name == "Services":
            for op in ("start", "stop", "restart", "enable", "disable"):
                button = QPushButton(op.capitalize())
                button.clicked.connect(
                    lambda checked, o=op: self.service_op(o)
                )
                self.action_bar.addWidget(button)

        self.action_bar.addStretch()

        # View mode toggle button
        self.btn_view_mode = QPushButton()
        self.btn_view_mode.clicked.connect(self._toggle_view_mode)
        self._update_view_mode_button()
        self.action_bar.addWidget(self.btn_view_mode)

        # Distro Settings button
        btn_distro = QPushButton("⚙️ Distro Settings")
        btn_distro.setToolTip("Configure Linux distro preference (Debian/RedHat), firewall backend, and log paths")
        btn_distro.clicked.connect(self._open_linux_settings)
        self.action_bar.addWidget(btn_distro)

        # Export button
        export_btn = QPushButton("📤 Export Section")
        export_btn.setToolTip("Export current report/table to CSV, JSON, or Text file")
        export_btn.clicked.connect(lambda _, n=name: self._export_current_section(n))
        self.action_bar.addWidget(export_btn)

        self._apply_view_mode()

        cmd = SYSADMIN_CMDS[name]
        sudo_pw = None

        if self.sudo_chk.isChecked():
            sudo_pw, ok = ensure_sudo_credentials(
                self, self.services, self.profile_name, self.profile
            )
            if not ok:
                self.status.setText(f"{name} — Cancelled (Root/Sudo elevation required)")
                self._set_loading(False)
                return

            escaped_cmd = cmd.replace("'", "'\\''")
            if sudo_pw:
                cmd = f"sudo -S -p '' bash -c '{escaped_cmd}'"
            else:
                cmd = f"sudo bash -c '{escaped_cmd}'"

        self.status.setText(f"Loading {name}...")
        self._set_loading(True)

        # ---- Choose execution backend ----
        if self._is_remote():
            # SSH: pass profile dict as first positional arg
            worker = RemoteExecThread(
                self.profile,
                cmd,
                timeout=45,
                sudo_password=sudo_pw,
            )
        else:
            # Local: run via subprocess
            worker = LocalExecThread(cmd, timeout=45, sudo_password=sudo_pw)

        self._workers.append(worker)

        def _on_section_done(out: str, rc: int, n=name):
            if "sudo: a password is required" in out or "incorrect password attempt" in out:
                self.services.clear_sudo_password(self.profile_name)
            self._render(n, out, rc)

        worker.finished_cmd.connect(_on_section_done)
        worker.finished.connect(lambda: self._set_loading(False))

        if hasattr(worker, "error"):
            worker.error.connect(
                lambda msg, n=name: self._handle_error(n, msg)
            )

        worker.start()

    def _handle_error(self, name: str, msg: str) -> None:
        self.status.setText(f"{name} — Error")
        if name in self.raw_text_views:
            self.raw_text_views[name].setPlainText(
                f"Error executing command:\n{msg}"
            )
        if name in self.presenter_views:
            self.presenter_views[name].update_data(f"Error executing command:\n{msg}")

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------
    def _render(self, name: str, out: str, rc: int) -> None:
        status_text = f"{name} — exit {rc}"
        if rc != 0 and not out.strip():
            status_text += " (No output)"
        self.status.setText(status_text)

        # Store raw command text
        if name in self.raw_text_views:
            self.raw_text_views[name].setPlainText(out)

        # Update formatted presenter widget if present
        if name in self.presenter_views:
            self.presenter_views[name].update_data(out)

        # Update tabular sections
        if name == "Users":
            self._render_users(out)
        elif name == "Services":
            self._render_services(out)
        elif name == "Processes":
            self._render_processes(out)

    def _get_source_model(self, section: str) -> QStandardItemModel:
        proxy = self.table_views[section].model()
        if isinstance(proxy, QSortFilterProxyModel):
            return proxy.sourceModel()
        return proxy

    def _render_users(self, out: str) -> None:
        model = self._get_source_model("Users")
        model.clear()
        model.setHorizontalHeaderLabels(
            ["User", "UID", "Type", "Privileged", "Status", "Home", "Shell"]
        )
        theme = self.theme

        current_section = "passwd"
        passwd_lines = []
        logged_in_users = set()
        privileged_users = set()

        for line in out.splitlines():
            line_s = line.strip()
            if line_s == "=== PASSWD ===":
                current_section = "passwd"
                continue
            elif line_s == "=== LOGGED_IN ===":
                current_section = "logged_in"
                continue
            elif line_s == "=== PRIVILEGED ===":
                current_section = "privileged"
                continue

            if not line_s or line_s.startswith("#") or line_s.startswith("=="):
                continue

            if current_section == "logged_in":
                parts = line_s.split()
                if parts:
                    logged_in_users.add(parts[0])
            elif current_section == "privileged":
                if ":" in line_s:
                    parts = line_s.split(":")
                    if len(parts) >= 4 and parts[3]:
                        for u in parts[3].split(","):
                            if u.strip():
                                privileged_users.add(u.strip())
            else:
                passwd_lines.append(line_s)

        for line in passwd_lines:
            parts = line.split(":")
            if len(parts) >= 7:
                username = parts[0]
                uid_str = parts[2]
                try:
                    uid = int(uid_str)
                except ValueError:
                    uid = 9999

                if uid == 0:
                    acc_type = "Root"
                elif 1000 <= uid < 65534:
                    acc_type = "Human"
                else:
                    acc_type = "System"

                is_priv = "Yes (sudo)" if (username in privileged_users or uid == 0) else "No"
                is_logged = "● Online" if username in logged_in_users else "Offline"
                home = parts[5]
                shell = parts[6]

                item_user = QStandardItem(username)
                item_uid = QStandardItem()
                item_uid.setData(uid, Qt.ItemDataRole.DisplayRole)
                item_type = QStandardItem(acc_type)
                item_priv = QStandardItem(is_priv)
                item_status = QStandardItem(is_logged)
                item_home = QStandardItem(home)
                item_shell = QStandardItem(shell)

                if is_logged == "● Online":
                    item_status.setForeground(QColor(theme.get("ok", "#0dbc79")))
                else:
                    item_status.setForeground(QColor(theme.get("sub", "#888888")))

                if is_priv.startswith("Yes"):
                    item_priv.setForeground(QColor(theme.get("accent", "#3daee9")))

                if acc_type == "Human":
                    item_type.setForeground(QColor(theme.get("ok", "#0dbc79")))
                elif acc_type == "Root":
                    item_type.setForeground(QColor(theme.get("danger", "#ff5555")))

                model.appendRow([
                    item_user, item_uid, item_type, item_priv, item_status, item_home, item_shell
                ])

        self.table_views["Users"].resizeColumnsToContents()

    def _render_services(self, out: str) -> None:
        model = self._get_source_model("Services")
        model.clear()
        model.setHorizontalHeaderLabels(
            ["Unit", "Load", "Active", "Sub", "Description"]
        )
        theme = self.theme
        seen = set()
        for line in out.splitlines():
            line_s = line.strip()
            if not line_s or line_s.startswith("==") or line_s.startswith("--"):
                continue
            parts = line_s.split(None, 4)
            if len(parts) >= 4:
                unit = parts[0]
                if not (unit.endswith(".service") or unit.endswith(".target") or unit.endswith(".socket")):
                    continue
                if unit in seen:
                    continue
                seen.add(unit)

                desc = parts[4] if len(parts) > 4 else ""
                row = [
                    QStandardItem(x)
                    for x in (parts[0], parts[1], parts[2], parts[3], desc)
                ]
                if parts[2] == "active":
                    row[2].setForeground(QColor(theme.get("ok", "#0dbc79")))
                elif parts[2] == "failed":
                    row[2].setForeground(QColor(theme.get("danger", "#ff5555")))
                else:
                    row[2].setForeground(QColor(theme.get("sub", "#888888")))
                model.appendRow(row)
        self.table_views["Services"].resizeColumnsToContents()

    def _render_processes(self, out: str) -> None:
        model = self._get_source_model("Processes")
        model.clear()
        lines = [line for line in out.splitlines() if line.strip()]
        if not lines:
            return

        header_idx = -1
        for idx, line in enumerate(lines):
            if "USER" in line and "PID" in line:
                header_idx = idx
                break

        if header_idx == -1:
            header_idx = 0

        headers = lines[header_idx].split(None, 10)
        model.setHorizontalHeaderLabels(headers)

        for line in lines[header_idx + 1:]:
            if line.startswith("==") or line.startswith("--"):
                continue
            parts = line.split(None, 10)
            if len(parts) >= 11:
                row_items = []
                for col_idx, x in enumerate(parts):
                    item = QStandardItem()
                    if col_idx == 1:  # PID
                        try:
                            item.setData(int(x), Qt.ItemDataRole.DisplayRole)
                        except ValueError:
                            item.setText(x)
                    elif col_idx in (2, 3):  # %CPU, %MEM
                        try:
                            item.setData(float(x), Qt.ItemDataRole.DisplayRole)
                        except ValueError:
                            item.setText(x)
                    else:
                        item.setText(x)
                    row_items.append(item)
                model.appendRow(row_items)

        self.table_views["Processes"].resizeColumnsToContents()

    # ------------------------------------------------------------------
    # Custom Command Execution
    # ------------------------------------------------------------------
    def execute_custom_cmd(
        self,
        cmd: str,
        title: str = "Command",
        timeout: int = 180,
        callback: Optional[Callable[[str, int], None]] = None,
        refresh_after: bool = True,
    ) -> None:
        """Executes a custom shell command (locally or via SSH) with optional sudo elevation."""
        final_cmd = cmd
        sudo_pw = None
        if self.sudo_chk.isChecked() and not cmd.strip().startswith("sudo"):
            sudo_pw, ok = ensure_sudo_credentials(
                self, self.services, self.profile_name, self.profile
            )
            if not ok:
                self.services.notifications.push(
                    "warn",
                    title,
                    "Execution cancelled: Root/sudo elevation required",
                )
                return

            escaped_cmd = cmd.replace("'", "'\\''")
            if sudo_pw:
                final_cmd = f"sudo -S -p '' bash -c '{escaped_cmd}'"
            else:
                final_cmd = f"sudo bash -c '{escaped_cmd}'"

        self.status.setText(f"Running {title}...")
        self._set_loading(True)

        if self._is_remote():
            worker = RemoteExecThread(
                self.profile, final_cmd, timeout=timeout, sudo_password=sudo_pw
            )
        else:
            worker = LocalExecThread(
                final_cmd, timeout=timeout, sudo_password=sudo_pw
            )

        self._workers.append(worker)

        def on_finished(out: str, rc: int) -> None:
            self._set_loading(False)
            target = self.profile_name if self._is_remote() else "Localhost"
            if "sudo: a password is required" in out or "incorrect password attempt" in out:
                self.services.clear_sudo_password(self.profile_name)
                self.services.notifications.push(
                    "error",
                    f"{title} (Sudo Auth Failed)",
                    "Root/sudo authentication failed. Invalid password.",
                )
            elif rc == 0:
                self.services.notifications.push(
                    "ok",
                    title,
                    f"Completed successfully on {target}",
                )
                self.services.audit("SYSADMIN", f"{title} succeeded on {target}")
            else:
                self.services.notifications.push(
                    "error",
                    f"{title} (exit {rc})",
                    out.strip()[:200] or "Execution returned non-zero exit code",
                )
                self.services.emit_log("SYSADMIN", f"{title} failed (rc={rc}) on {target}: {out.strip()}", "ERROR")

            if callback:
                callback(out, rc)

            if refresh_after:
                self._refresh_current()

        worker.finished_cmd.connect(on_finished)
        worker.start()

    # ------------------------------------------------------------------
    # Service operations
    # ------------------------------------------------------------------
    def service_op(self, op: str) -> None:
        table = self.table_views["Services"]
        proxy = table.model()
        index = table.currentIndex()

        if not index.isValid():
            QMessageBox.information(
                self, "Services", "Select a service row first."
            )
            return

        # Map through proxy to get the real source row
        if isinstance(proxy, QSortFilterProxyModel):
            source_index = proxy.mapToSource(index)
            unit = proxy.sourceModel().item(source_index.row(), 0).text()
        else:
            unit = proxy.item(index.row(), 0).text()

        cmd = (
            f"systemctl {op} {shlex.quote(unit)} "
            f"&& echo '[{op} OK] {shlex.quote(unit)}'"
        )

        if (
            QMessageBox.question(self, "Confirm", f"Run: systemctl {op} {unit}")
            != QMessageBox.StandardButton.Yes
        ):
            return

        self.execute_custom_cmd(cmd, f"systemctl {op} {unit}", timeout=60, refresh_after=True)