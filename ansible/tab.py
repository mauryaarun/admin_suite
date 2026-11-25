"""
Ansible and multi-host command runner tab.
"""

from __future__ import annotations

from typing import Any, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from admin_suite.core.fonts import mono_font
from admin_suite.core.icons import style_icon_button
from admin_suite.ansible.command_sets import CommandSetStore
from admin_suite.ansible.multihost import MultiHostExecThread


class MultiLineInputDialog(QDialog):
    """
    Simple multi-line input dialog.
    """

    def __init__(
        self,
        parent=None,
        title: str = "Input",
        label: str = "Commands:",
        text: str = "",
    ):
        super().__init__(parent)

        self.setWindowTitle(title)
        self.resize(560, 420)

        layout = QVBoxLayout(self)

        layout.addWidget(QLabel(label))

        self.edit = QPlainTextEdit()
        self.edit.setFont(mono_font(11))
        self.edit.setPlainText(text)

        layout.addWidget(self.edit, 1)

        button_row = QHBoxLayout()

        ok = QPushButton("OK")
        ok.clicked.connect(self.accept)

        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)

        button_row.addStretch()
        button_row.addWidget(ok)
        button_row.addWidget(cancel)

        layout.addLayout(button_row)

    def text(self) -> str:
        return self.edit.toPlainText()

    @staticmethod
    def get_text(
        parent=None,
        title: str = "Input",
        label: str = "Commands:",
        text: str = "",
    ) -> Optional[str]:
        dlg = MultiLineInputDialog(parent, title, label, text)

        if dlg.exec() == QDialog.DialogCode.Accepted:
            return dlg.text()

        return None


class AnsibleTab(QWidget):
    """
    Multi-host command runner with predefined command sets.
    """

    def __init__(self, services, main_window=None):
        super().__init__(main_window)

        self.services = services
        self.main_window = main_window

        self.store = CommandSetStore()

        self._cmd_queue: list[tuple[list[dict[str, Any]], str]] = []
        self._worker: Optional[MultiHostExecThread] = None
        self._running = False

        theme = self.services.theme.current

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        header = QLabel("🚀 Ansible & Multi-Host Command Runner")
        header.setStyleSheet(
            f"color:{theme['accent']};font-weight:bold;font-size:15px;padding:6px;"
        )

        layout.addWidget(header)

        top_split = QSplitter(Qt.Orientation.Horizontal)

        # Command sets panel.
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)

        left_layout.addWidget(QLabel("📋 Predefined Command Sets:"))

        self.cmdset_list = QListWidget()
        self.cmdset_list.itemDoubleClicked.connect(self.run_selected_cmdset)

        self._populate_cmdsets()

        left_layout.addWidget(self.cmdset_list)

        button_row = QHBoxLayout()

        add_cs = QPushButton("➕ Add Set")
        add_cs.clicked.connect(self.add_cmdset)

        edit_cs = QPushButton("✏️ Edit Set")
        edit_cs.clicked.connect(self.edit_cmdset)

        del_cs = QPushButton("🗑 Delete Set")
        del_cs.clicked.connect(self.del_cmdset)

        button_row.addWidget(add_cs)
        button_row.addWidget(edit_cs)
        button_row.addWidget(del_cs)

        left_layout.addLayout(button_row)

        top_split.addWidget(left_panel)

        # Host selection panel.
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)

        right_layout.addWidget(QLabel("🖥 Target Hosts:"))

        self.host_list = QListWidget()
        self.host_list.setSelectionMode(
            QListWidget.SelectionMode.MultiSelection
        )

        self._populate_hosts()

        right_layout.addWidget(self.host_list)

        select_all = QPushButton("Select All")
        select_all.clicked.connect(self.host_list.selectAll)

        right_layout.addWidget(select_all)

        top_split.addWidget(right_panel)
        top_split.setSizes([420, 320])

        layout.addWidget(top_split, 1)

        # Custom command input.
        cmd_row = QHBoxLayout()

        cmd_row.addWidget(QLabel("Custom Command:"))

        self.custom_cmd = QLineEdit()
        self.custom_cmd.setPlaceholderText(
            "Enter shell command to run on selected hosts..."
        )
        self.custom_cmd.returnPressed.connect(self.run_on_selected)

        cmd_row.addWidget(self.custom_cmd, 1)

        run_btn = QPushButton()
        style_icon_button(
            run_btn,
            "play",
            "Run command on selected hosts",
            icon_only=True,
            color="#22c55e",
        )
        run_btn.clicked.connect(self.run_on_selected)

        cmd_row.addWidget(run_btn)

        layout.addLayout(cmd_row)

        # Output.
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setFont(mono_font(11))

        layout.addWidget(self.output, 1)

    # ------------------------------------------------------------
    # Populate helpers
    # ------------------------------------------------------------

    def _populate_cmdsets(self) -> None:
        self.cmdset_list.clear()

        for cs in self.store.all():
            self.cmdset_list.addItem(
                f"📦 {cs.get('name', '')} "
                f"({len(cs.get('commands', []))} cmds)"
            )

    def _populate_hosts(self) -> None:
        self.host_list.clear()

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

            self.host_list.addItem(item)

    def _get_selected_hosts(self) -> list[dict[str, Any]]:
        hosts = []

        for item in self.host_list.selectedItems():
            data = item.data(Qt.ItemDataRole.UserRole)

            if data:
                hosts.append(data)

        return hosts

    # ------------------------------------------------------------
    # Command set management
    # ------------------------------------------------------------

    def add_cmdset(self) -> None:
        name, ok = QInputDialog.getText(
            self,
            "New Command Set",
            "Name:",
        )

        if not ok or not name.strip():
            return

        commands = MultiLineInputDialog.get_text(
            self,
            "New Command Set",
            "Commands (one per line):",
        )

        if commands is None:
            return

        self.store.add(name, commands)
        self._populate_cmdsets()

    def edit_cmdset(self) -> None:
        row = self.cmdset_list.currentRow()

        cs = self.store.get(row)

        if not cs:
            QMessageBox.information(
                self,
                "Edit Command Set",
                "Select a command set first.",
            )
            return

        commands = MultiLineInputDialog.get_text(
            self,
            f"Edit '{cs.get('name', '')}'",
            "Commands (one per line):",
            "\n".join(cs.get("commands", [])),
        )

        if commands is None:
            return

        self.store.update_commands(row, commands)
        self._populate_cmdsets()

    def del_cmdset(self) -> None:
        row = self.cmdset_list.currentRow()

        cs = self.store.get(row)

        if not cs:
            QMessageBox.information(
                self,
                "Delete Command Set",
                "Select a command set first.",
            )
            return

        if QMessageBox.question(
            self,
            "Delete",
            f"Delete '{cs.get('name', '')}'?",
        ) == QMessageBox.StandardButton.Yes:
            self.store.delete(row)
            self._populate_cmdsets()

    # ------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------

    def run_on_selected(self) -> None:
        cmd = self.custom_cmd.text().strip()

        hosts = self._get_selected_hosts()

        if not hosts:
            QMessageBox.information(
                self,
                "No Hosts",
                "Select at least one host.",
            )
            return

        if cmd:
            self.output.appendPlainText(
                f"\n{'=' * 60}\n"
                f"▶ Running custom command: {cmd}\n"
                f"{'=' * 60}\n"
            )

            self._enqueue(hosts, [cmd])

        else:
            self.run_selected_cmdset()

    def run_selected_cmdset(self, item=None) -> None:
        row = self.cmdset_list.currentRow()

        cs = self.store.get(row)

        if not cs:
            QMessageBox.information(
                self,
                "No Command Set",
                "Select a command set or enter a custom command.",
            )
            return

        hosts = self._get_selected_hosts()

        if not hosts:
            QMessageBox.information(
                self,
                "No Hosts",
                "Select at least one host.",
            )
            return

        commands = cs.get("commands", [])

        if not commands:
            QMessageBox.information(
                self,
                "Empty Set",
                "This command set has no commands.",
            )
            return

        self.output.appendPlainText(
            f"\n{'=' * 60}\n"
            f"📦 Running Command Set: {cs.get('name', '')}\n"
            f"{'=' * 60}\n"
        )

        self._enqueue(hosts, commands)

    def _enqueue(
        self,
        hosts: list[dict[str, Any]],
        commands: list[str],
    ) -> None:
        for command in commands:
            self._cmd_queue.append((hosts, command))

        if not self._running:
            self._start_next_command()

    def _start_next_command(self) -> None:
        if not self._cmd_queue:
            self._running = False
            self._worker = None

            self.output.appendPlainText("\n--- All commands complete ---\n")

            return

        hosts, command = self._cmd_queue.pop(0)

        self._running = True

        self.output.appendPlainText(f"\n--- Command: {command} ---")

        self._worker = MultiHostExecThread(hosts, command)

        self._worker.result_ready.connect(self._on_result)
        self._worker.all_done.connect(self._start_next_command)
        self._worker.finished.connect(self._worker.deleteLater)

        self._worker.start()

    def _on_result(self, host: str, output: str, success: bool) -> None:
        icon = "✅" if success else "❌"

        self.output.appendPlainText(f"\n{icon} [{host}]\n{output}")
