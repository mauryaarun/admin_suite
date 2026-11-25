from __future__ import annotations

import shlex
import subprocess
from typing import Any, Callable, Optional

from PyQt6.QtCore import Qt, QSortFilterProxyModel, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QCursor, QStandardItem, QStandardItemModel
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
from admin_suite.core.fonts import mono_font
from admin_suite.core.icons import style_icon_button
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
    SysAdminProcessesWidget,
    SysAdminSecurityWidget,
    SysAdminServicesWidget,
    SysAdminStorageWidget,
    SysAdminUsersWidget,
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
        self._proc: Optional[subprocess.Popen] = None
        self._stopped = False

    def stop(self) -> None:
        self._stopped = True
        if self._proc is not None:
            try:
                self._proc.terminate()
            except Exception:
                pass

    def run(self) -> None:
        try:
            self._proc = subprocess.Popen(
                ["bash", "-c", self.cmd],
                stdin=subprocess.PIPE if self.sudo_password else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            out, err = self._proc.communicate(
                input=(self.sudo_password + "\n") if self.sudo_password else None,
                timeout=self.timeout,
            )
            combined = (out or "") + (("\n" + err) if err else "")
            rc = self._proc.returncode if self._proc.returncode is not None else 0
            if not self._stopped:
                self.finished_cmd.emit(combined, rc)
        except subprocess.TimeoutExpired:
            if self._proc is not None:
                try:
                    self._proc.kill()
                except Exception:
                    pass
            if not self._stopped:
                self.finished_cmd.emit("[timeout]\n", 124)
        except Exception as exc:
            if not self._stopped:
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
        self._worker_meta: dict[int, dict[str, Any]] = {}
        self._stopped = False
        self._loading = False

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

        self.sudo_btn = QPushButton()
        style_icon_button(
            self.sudo_btn,
            "key",
            "Configure Root / Sudo credentials for this profile",
            icon_only=True,
        )
        self.sudo_btn.clicked.connect(self._configure_sudo_credentials)
        sudo_row.addWidget(self.sudo_btn)

        left.addLayout(sudo_row)

        self.refresh_btn = QPushButton()
        style_icon_button(self.refresh_btn, "refresh", "Refresh active section", icon_only=True)
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
            "Users": SysAdminUsersWidget,
            "Services": SysAdminServicesWidget,
            "Processes": SysAdminProcessesWidget,
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
            raw_text.setFont(mono_font(11))
            raw_text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
            ReportExporter.attach_export_context_menu(raw_text)
            self.raw_text_views[section] = raw_text

            p_cls = presenter_classes.get(section)
            if p_cls:
                if section in ("Users", "Services", "Processes", "Journal", "Packages", "Containers"):
                    pres_widget = p_cls(theme=theme, run_cmd_fn=self.execute_custom_cmd, parent=self)
                else:
                    pres_widget = p_cls(theme=theme, parent=self)
                self.presenter_views[section] = pres_widget
                if hasattr(pres_widget, "table"):
                    self.table_views[section] = pres_widget.table
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
        if self._stopped and loading:
            return
        self.nav.setEnabled(not loading)
        self.refresh_btn.setEnabled(not loading)
        self.sudo_chk.setEnabled(not loading)
        if loading and not self._loading:
            self._loading = True
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        elif not loading and self._loading:
            self._loading = False
            QApplication.restoreOverrideCursor()

    def _cleanup_workers(self) -> None:
        self._workers = [w for w in self._workers if w.isRunning()]
        active_ids = {id(w) for w in self._workers}
        for key in list(self._worker_meta):
            if key not in active_ids:
                self._worker_meta.pop(key, None)

    def _create_worker(self, cmd: str, timeout: int, sudo_pw: Optional[str]) -> QThread:
        if self._is_remote():
            worker: QThread = RemoteExecThread(
                self.profile,
                cmd,
                timeout=timeout,
                sudo_password=sudo_pw,
                parent=self,
            )
        else:
            worker = LocalExecThread(
                cmd,
                timeout=timeout,
                sudo_password=sudo_pw,
                parent=self,
            )
        self._workers.append(worker)
        worker.finished.connect(self._on_worker_thread_finished)
        worker.finished_cmd.connect(self._on_worker_finished_cmd)
        if hasattr(worker, "error"):
            worker.error.connect(self._on_worker_error)
        return worker

    def _on_worker_thread_finished(self) -> None:
        self._workers = [w for w in self._workers if w.isRunning()]
        if not self._stopped and not self._workers:
            self._set_loading(False)

    def _on_worker_finished_cmd(self, out: str, rc: int) -> None:
        if self._stopped:
            return
        worker = self.sender()
        meta = self._worker_meta.pop(id(worker), None) if worker is not None else None
        if not meta:
            return

        if meta.get("kind") == "section":
            name = meta["name"]
            if "sudo: a password is required" in out or "incorrect password attempt" in out:
                self.services.clear_sudo_password(self.profile_name)
            self._render(name, out, rc)
            return

        if meta.get("kind") == "custom":
            self._set_loading(False)
            title = meta.get("title", "Command")
            callback = meta.get("callback")
            refresh_after = meta.get("refresh_after", True)
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

    def _on_worker_error(self, msg: str) -> None:
        if self._stopped:
            return
        worker = self.sender()
        meta = self._worker_meta.pop(id(worker), None) if worker is not None else None
        if meta and meta.get("name"):
            self._handle_error(meta["name"], msg)

    def stop_all_workers(self) -> None:
        """Stop all active background workers cleanly before closing."""
        self._stopped = True
        self._set_loading(False)
        for w in list(self._workers):
            if hasattr(w, "stop"):
                try:
                    w.stop()
                except Exception:
                    pass
            try:
                w.quit()
                if not w.wait(500):
                    w.terminate()
                    w.wait(300)
            except Exception:
                pass
        self._workers.clear()
        self._worker_meta.clear()

    def closeEvent(self, event) -> None:
        self.stop_all_workers()
        super().closeEvent(event)

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
        worker = self._create_worker(cmd, timeout=45, sudo_pw=sudo_pw)
        self._worker_meta[id(worker)] = {"kind": "section", "name": name}
        worker.start()

    def _handle_error(self, name: str, msg: str) -> None:
        if self._stopped:
            return
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

    def _render_users(self, out: str) -> None:
        if "Users" in self.presenter_views:
            self.presenter_views["Users"].update_data(out)

    def _render_services(self, out: str) -> None:
        if "Services" in self.presenter_views:
            self.presenter_views["Services"].update_data(out)

    def _render_processes(self, out: str) -> None:
        if "Processes" in self.presenter_views:
            self.presenter_views["Processes"].update_data(out)

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

        worker = self._create_worker(final_cmd, timeout=timeout, sudo_pw=sudo_pw)
        self._worker_meta[id(worker)] = {
            "kind": "custom",
            "title": title,
            "callback": callback,
            "refresh_after": refresh_after,
        }
        worker.start()

    # ------------------------------------------------------------------
    # Service operations
    # ------------------------------------------------------------------
    def service_op(self, op: str) -> None:
        pres = self.presenter_views.get("Services")
        if pres and hasattr(pres, "_service_action"):
            pres._service_action(op)
            return

        table = self.table_views.get("Services")
        if not table:
            return
        row = table.currentRow()
        if row < 0:
            QMessageBox.information(self, "Services", "Select a service row first.")
            return
        it = table.item(row, 0)
        unit = it.text() if it else ""
        if not unit:
            return

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