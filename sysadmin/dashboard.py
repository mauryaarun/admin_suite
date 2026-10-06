from __future__ import annotations

import shlex
import subprocess
from typing import Any, Optional

from PyQt6.QtCore import Qt, QSortFilterProxyModel, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QStandardItem, QStandardItemModel
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
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

from admin_suite.ssh.remote_exec import RemoteExecThread
from admin_suite.sysadmin.commands import SYSADMIN_CMDS, SYSADMIN_SECTIONS


# ------------------------------------------------------------------
# Local execution fallback (when no SSH profile is configured)
# ------------------------------------------------------------------
class LocalExecThread(QThread):
    """Run a shell command locally and emit the output."""

    finished_cmd = pyqtSignal(str, int)  # output, return_code

    def __init__(self, cmd: str, timeout: int = 45, parent=None):
        super().__init__(parent)
        self.cmd = cmd
        self.timeout = timeout

    def run(self) -> None:
        try:
            result = subprocess.run(
                self.cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
            output = result.stdout
            if result.stderr:
                output += "\n" + result.stderr
            self.finished_cmd.emit(output, result.returncode)
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
        header = QLabel(f"🖥 {self.profile_name}")
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

        self.sudo_chk = QCheckBox("Use sudo")
        self.sudo_chk.setToolTip("Elevate commands using sudo")
        left.addWidget(self.sudo_chk)

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
        self.text_views: dict[str, QPlainTextEdit] = {}
        self.table_views: dict[str, QTableView] = {}

        for section in SYSADMIN_SECTIONS:
            if section in ("Users", "Services", "Processes"):
                table = QTableView()
                table.setAlternatingRowColors(True)
                table.setSortingEnabled(True)

                model = QStandardItemModel()
                proxy = QSortFilterProxyModel()
                proxy.setSourceModel(model)
                table.setModel(proxy)

                self.table_views[section] = table
                self.stack.addWidget(table)
            else:
                text = QPlainTextEdit()
                text.setReadOnly(True)
                text.setFont(QFont("JetBrains Mono, Consolas", 11))
                text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)

                text.setContextMenuPolicy(
                    Qt.ContextMenuPolicy.CustomContextMenu
                )
                text.customContextMenuRequested.connect(
                    lambda pos, t=text: self._show_text_menu(t, pos)
                )

                self.text_views[section] = text
                self.stack.addWidget(text)

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
        """Return True when a profile is available (not None)."""
        return self.profile is not None

    def _show_text_menu(self, text_edit: QPlainTextEdit, pos) -> None:
        menu = QMenu(self)
        copy_action = menu.addAction("Copy Selection")
        copy_all_action = menu.addAction("Copy All")
        action = menu.exec(text_edit.mapToGlobal(pos))
        if action == copy_action:
            text_edit.copy()
        elif action == copy_all_action:
            QApplication.clipboard().setText(text_edit.toPlainText())

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

        cmd = SYSADMIN_CMDS[name]

        if self.sudo_chk.isChecked():
            escaped_cmd = cmd.replace("'", "'\\''")
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
            )
        else:
            # Local: run via subprocess
            worker = LocalExecThread(cmd, timeout=45)

        self._workers.append(worker)

        worker.finished_cmd.connect(
            lambda out, rc, n=name: self._render(n, out, rc)
        )
        worker.finished.connect(lambda: self._set_loading(False))

        if hasattr(worker, "error"):
            worker.error.connect(
                lambda msg, n=name: self._handle_error(n, msg)
            )

        worker.start()

    def _handle_error(self, name: str, msg: str) -> None:
        self.status.setText(f"{name} — Error")
        if name in self.text_views:
            self.text_views[name].setPlainText(
                f"Error executing command:\n{msg}"
            )

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------
    def _render(self, name: str, out: str, rc: int) -> None:
        status_text = f"{name} — exit {rc}"
        if rc != 0 and not out.strip():
            status_text += " (No output)"
        self.status.setText(status_text)

        if name == "Users":
            self._render_users(out)
        elif name == "Services":
            self._render_services(out)
        elif name == "Processes":
            self._render_processes(out)
        else:
            self.text_views[name].setPlainText(out)

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

        sudo = "sudo " if self.sudo_chk.isChecked() else ""
        cmd = (
            f"{sudo}systemctl {op} {shlex.quote(unit)} "
            f"&& echo '[{op} OK] {shlex.quote(unit)}'"
        )

        if (
            QMessageBox.question(self, "Confirm", f"Run: {cmd}")
            != QMessageBox.StandardButton.Yes
        ):
            return

        self.status.setText(f"Running {op} {unit}...")
        self._set_loading(True)

        if self._is_remote():
            worker = RemoteExecThread(self.profile, cmd, timeout=60)
        else:
            worker = LocalExecThread(cmd, timeout=60)

        self._workers.append(worker)

        def after(out: str, rc: int) -> None:
            self.services.notifications.push(
                "ok" if rc == 0 else "error",
                f"systemctl {op}",
                out.strip()[:200] or unit,
            )
            self._refresh_current()

        worker.finished_cmd.connect(after)
        worker.finished.connect(lambda: self._set_loading(False))
        worker.start()