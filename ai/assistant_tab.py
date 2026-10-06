"""
AI Assistant & Copilot Sidebar for Admin Suite.
Provides context-aware assistance for active Terminals, Databases, and Server Administration.
"""

from __future__ import annotations

import logging
import re
import threading
import time
import traceback
from typing import Any, Optional

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from admin_suite.ai.ollama_client import OllamaClient
from admin_suite.core.export import ReportExporter

logger = logging.getLogger(__name__)


class _WorkerSignals(QObject):
    """Signals for background model fetching and connection pinging."""
    models_loaded = pyqtSignal(list)
    models_error = pyqtSignal(str)
    ping_success = pyqtSignal()
    ping_failed = pyqtSignal(str)


class _GenSignals(QObject):
    """Signals for streaming generation."""
    token = pyqtSignal(str)
    done = pyqtSignal(str, float, int)
    error = pyqtSignal(str)


class AIAssistantTab(QWidget):
    """
    Intelligent AI Copilot right-sidebar widget.
    Context-aware of currently active Terminals, Databases, and System administration tabs.
    """

    MAX_HISTORY = 20

    def __init__(self, services, main_window=None, parent=None):
        super().__init__(parent or main_window)
        self.services = services
        self.main_window = main_window
        self.client = OllamaClient()

        self._cancel_flag = False
        self._worker_thread: Optional[threading.Thread] = None
        self._history: list[str] = []
        self._start_time = 0.0
        self._token_count = 0
        self._current_context_info: dict[str, Any] = {}

        theme = self.services.theme.current
        self._accent = theme.get("accent", "#3daee9")
        self._sub = theme.get("sub", "#888888")
        self._ok = theme.get("ok", "#0dbc79")
        self._warn = theme.get("warn", "#f39c12")
        self._err = theme.get("danger", "#ff5555")
        self._panel2 = theme.get("panel2", "#222222")
        self._border = theme.get("border", "#444444")

        # Setup thread-safe signals
        self._signals = _WorkerSignals()
        self._signals.models_loaded.connect(self._populate_models)
        self._signals.models_error.connect(self._on_models_error)
        self._signals.ping_success.connect(self._on_ping_success)
        self._signals.ping_failed.connect(self._on_ping_failed)

        self._build_ui()
        self._refresh_models()
        self.update_context()

    # =========================================================
    # UI Setup
    # =========================================================
    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        # 1. Header Toolbar
        header_bar = QHBoxLayout()
        header_bar.setSpacing(4)

        self.status_dot = QLabel("●")
        self.status_dot.setStyleSheet(f"color:{self._sub};font-size:14px;")
        header_bar.addWidget(self.status_dot)

        title = QLabel("🤖 Copilot")
        title.setStyleSheet(f"font-weight:bold;font-size:14px;color:{self._accent};")
        header_bar.addWidget(title)

        header_bar.addStretch()

        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)
        self.model_combo.setFixedWidth(130)
        self.model_combo.setToolTip("Select or enter local Ollama model")
        header_bar.addWidget(self.model_combo)

        self.refresh_models_btn = QToolButton()
        self.refresh_models_btn.setText("🔄")
        self.refresh_models_btn.setToolTip("Refresh Ollama models")
        self.refresh_models_btn.clicked.connect(self._refresh_models)
        header_bar.addWidget(self.refresh_models_btn)

        self.ping_btn = QToolButton()
        self.ping_btn.setText("🔌")
        self.ping_btn.setToolTip("Test connection to Ollama (127.0.0.1:11434)")
        self.ping_btn.clicked.connect(self.test_connection)
        header_bar.addWidget(self.ping_btn)

        self.close_btn = QToolButton()
        self.close_btn.setText("✖")
        self.close_btn.setToolTip("Close Copilot sidebar (Ctrl+Shift+A)")
        self.close_btn.clicked.connect(self._on_close_clicked)
        header_bar.addWidget(self.close_btn)

        layout.addLayout(header_bar)

        # 2. Context Indicator Banner
        self.context_frame = QFrame()
        self.context_frame.setStyleSheet(
            f"background:{self._panel2};border:1px solid {self._border};border-radius:4px;padding:3px;"
        )
        ctx_layout = QHBoxLayout(self.context_frame)
        ctx_layout.setContentsMargins(6, 2, 6, 2)
        ctx_layout.setSpacing(4)

        self.context_icon = QLabel("🐚")
        ctx_layout.addWidget(self.context_icon)

        self.context_label = QLabel("Detecting context...")
        self.context_label.setStyleSheet("font-size:11px;font-weight:bold;")
        ctx_layout.addWidget(self.context_label, 1)

        self.sync_ctx_btn = QToolButton()
        self.sync_ctx_btn.setText("🔄")
        self.sync_ctx_btn.setToolTip("Sync current tab context")
        self.sync_ctx_btn.clicked.connect(self.update_context)
        ctx_layout.addWidget(self.sync_ctx_btn)

        layout.addWidget(self.context_frame)

        # 3. Dynamic Quick Action Chips
        self.chips_scroll = QScrollArea()
        self.chips_scroll.setFixedHeight(36)
        self.chips_scroll.setWidgetResizable(True)
        self.chips_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.chips_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.chips_scroll.setFrameShape(QFrame.Shape.NoFrame)

        self.chips_container = QWidget()
        self.chips_layout = QHBoxLayout(self.chips_container)
        self.chips_layout.setContentsMargins(0, 0, 0, 0)
        self.chips_layout.setSpacing(4)
        self.chips_scroll.setWidget(self.chips_container)

        layout.addWidget(self.chips_scroll)

        # 4. Prompt Input Box
        self.task_input = QPlainTextEdit()
        self.task_input.setFixedHeight(75)
        self.task_input.setPlaceholderText("Ask Copilot for command or SQL... (Ctrl+Enter to send)")
        self.task_input.setStyleSheet("padding:4px;border-radius:4px;")
        layout.addWidget(self.task_input)

        QShortcut(QKeySequence("Ctrl+Return"), self.task_input).activated.connect(self.generate)
        QShortcut(QKeySequence("Ctrl+Enter"), self.task_input).activated.connect(self.generate)
        QShortcut(QKeySequence("Ctrl+L"), self).activated.connect(self._clear_all)

        # Controls under Input
        input_controls = QHBoxLayout()
        self.include_context_chk = QCheckBox("Include active context")
        self.include_context_chk.setChecked(True)
        self.include_context_chk.setStyleSheet("font-size:11px;")
        input_controls.addWidget(self.include_context_chk)

        input_controls.addStretch()

        self.cancel_btn = QPushButton("⏹ Stop")
        self.cancel_btn.setStyleSheet(f"background:{self._err};color:white;padding:3px 10px;font-size:11px;border-radius:3px;")
        self.cancel_btn.clicked.connect(self._cancel_generation)
        self.cancel_btn.hide()
        input_controls.addWidget(self.cancel_btn)

        self.gen_btn = QPushButton("✨ Generate")
        self.gen_btn.setStyleSheet(f"background:{self._accent};color:white;font-weight:bold;padding:4px 14px;border-radius:3px;")
        self.gen_btn.clicked.connect(self.generate)
        input_controls.addWidget(self.gen_btn)

        layout.addLayout(input_controls)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(3)
        self.progress.hide()
        layout.addWidget(self.progress)

        # 5. Output / Code Editor
        splitter = QSplitter(Qt.Orientation.Vertical)

        out_box = QWidget()
        out_layout = QVBoxLayout(out_box)
        out_layout.setContentsMargins(0, 0, 0, 0)
        out_layout.setSpacing(2)

        out_header = QHBoxLayout()
        self.out_title = QLabel("Copilot Response:")
        self.out_title.setStyleSheet("font-size:11px;font-weight:bold;")
        out_header.addWidget(self.out_title)
        out_header.addStretch()

        self.stats_label = QLabel("")
        self.stats_label.setStyleSheet(f"color:{self._sub};font-size:10px;")
        out_header.addWidget(self.stats_label)
        out_layout.addLayout(out_header)

        self.output_edit = QPlainTextEdit()
        self.output_edit.setReadOnly(True)
        self.output_edit.setFont(QFont("JetBrains Mono, Consolas, monospace", 10))
        self.output_edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.output_edit.setPlaceholderText("Generated commands, SQL, or explanations will appear here...")
        ReportExporter.attach_export_context_menu(self.output_edit)
        out_layout.addWidget(self.output_edit, 1)

        splitter.addWidget(out_box)
        layout.addWidget(splitter, 1)

        # 6. Action Bar
        self.action_bar = QHBoxLayout()
        self.action_bar.setSpacing(4)

        self.btn_run_term = QPushButton("▶ Run in Terminal")
        self.btn_run_term.setStyleSheet(f"background:{self._ok};color:white;font-weight:bold;padding:4px 8px;font-size:11px;")
        self.btn_run_term.clicked.connect(lambda: self.execute_in_terminal(run=True))
        self.action_bar.addWidget(self.btn_run_term)

        self.btn_insert_term = QPushButton("➕ Insert in Term")
        self.btn_insert_term.setStyleSheet(f"padding:4px 6px;font-size:11px;")
        self.btn_insert_term.clicked.connect(lambda: self.execute_in_terminal(run=False))
        self.action_bar.addWidget(self.btn_insert_term)

        self.btn_run_db = QPushButton("▶ Run in DB (F5)")
        self.btn_run_db.setStyleSheet("background:#0078d4;color:white;font-weight:bold;padding:4px 8px;font-size:11px;")
        self.btn_run_db.clicked.connect(lambda: self.execute_in_db(run=True))
        self.action_bar.addWidget(self.btn_run_db)

        self.btn_insert_db = QPushButton("📝 Paste to SQL")
        self.btn_insert_db.setStyleSheet("padding:4px 6px;font-size:11px;")
        self.btn_insert_db.clicked.connect(lambda: self.execute_in_db(run=False))
        self.action_bar.addWidget(self.btn_insert_db)

        self.copy_btn = QPushButton("📋 Copy")
        self.copy_btn.setStyleSheet("padding:4px 8px;font-size:11px;")
        self.copy_btn.clicked.connect(self.copy_output)
        self.action_bar.addWidget(self.copy_btn)

        self.clear_btn = QPushButton("🧹 Clear")
        self.clear_btn.setStyleSheet("padding:4px 6px;font-size:11px;")
        self.clear_btn.clicked.connect(self._clear_all)
        self.action_bar.addWidget(self.clear_btn)

        layout.addLayout(self.action_bar)

        self.status_label = QLabel("Ready.")
        self.status_label.setStyleSheet(f"color:{self._sub};font-size:10px;")
        layout.addWidget(self.status_label)

        self._update_action_buttons(False)

    def _on_close_clicked(self):
        if self.main_window and hasattr(self.main_window, "toggle_copilot"):
            self.main_window.toggle_copilot()
        else:
            self.hide()

    # =========================================================
    # Context Awareness
    # =========================================================
    def update_context(self):
        """Inspects the currently active tab in the main window and reconfigures chips."""
        if not self.main_window or not hasattr(self.main_window, "tabs"):
            self.context_label.setText("No active workspace")
            return

        idx = self.main_window.tabs.currentIndex()
        tab_text = self.main_window.tabs.tabText(idx) if idx >= 0 else ""
        widget = self.main_window.tabs.currentWidget() if idx >= 0 else None

        ctx_type = "generic"
        details = ""

        # 1. Database Manager
        if widget is not None and (
            "Database" in tab_text
            or hasattr(widget, "query_edit")
            or "TableDetailTab" in widget.__class__.__name__
        ):
            ctx_type = "database"
            db_mgr = widget if hasattr(widget, "query_edit") else getattr(self.main_window, "db_manager_widget", None)
            curr_db = getattr(db_mgr, "current_schema", None) if db_mgr else None
            curr_tbl = getattr(db_mgr, "current_table", None) if db_mgr else None
            if curr_db and curr_tbl:
                details = f"{curr_db}.{curr_tbl}"
            elif curr_db:
                details = f"Database: {curr_db}"
            else:
                details = "Database Manager"

        # 2. Terminal
        elif widget is not None and (
            hasattr(widget, "send_text")
            or hasattr(widget, "get_all_terminals")
            or "Terminal" in tab_text
            or "Shell" in tab_text
            or "SSH" in tab_text
        ):
            ctx_type = "terminal"
            details = tab_text or "Active Terminal"

        # 3. Web Hosting Manager
        elif widget is not None and ("Web" in tab_text or "Hosting" in tab_text):
            ctx_type = "web"
            details = "Web Hosting Manager"

        # 4. SysAdmin Dashboard
        elif widget is not None and ("SysAdmin" in tab_text or "System" in tab_text):
            ctx_type = "sysadmin"
            details = "SysAdmin Dashboard"

        # 5. Security Hub
        elif widget is not None and ("Security" in tab_text or "Firewall" in tab_text):
            ctx_type = "security"
            details = "Security Hub"

        else:
            ctx_type = "generic"
            details = tab_text or "General Workspace"

        self._current_context_info = {
            "type": ctx_type,
            "details": details,
            "widget": widget,
        }

        # Update Banner
        icon_map = {
            "terminal": "🐚",
            "database": "🗄",
            "web": "🌐",
            "sysadmin": "🖥",
            "security": "🛡️",
            "generic": "💻",
        }
        self.context_icon.setText(icon_map.get(ctx_type, "💻"))
        self.context_label.setText(f"Active Context: {details}")

        # Update action buttons visibility
        self.btn_run_term.setVisible(ctx_type == "terminal")
        self.btn_insert_term.setVisible(ctx_type == "terminal")
        self.btn_run_db.setVisible(ctx_type == "database")
        self.btn_insert_db.setVisible(ctx_type == "database")

        self._rebuild_chips(ctx_type)

    def _rebuild_chips(self, ctx_type: str):
        # Clear existing chips
        while self.chips_layout.count():
            item = self.chips_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        chips = []
        if ctx_type == "terminal":
            chips = [
                ("💡 Explain Error", "Explain the last command output or error and suggest the fix."),
                ("⚡ Suggest Command", "What shell command should I use to "),
                ("🔍 Find Files", "Find files in /var modified in the last 24 hours"),
                ("🛡️ Security Audit", "Check listening ports and recent failed SSH login attempts"),
            ]
        elif ctx_type == "database":
            chips = [
                ("🔍 Generate SELECT", "SELECT query to fetch the top 100 records ordered by id DESC"),
                ("⚡ Optimize Query", "Optimize this SQL query for performance and recommend indexes: "),
                ("🛠️ Fix SQL Error", "Fix this SQL syntax or query error: "),
                ("📊 Count by Group", "Count records grouped by status with percentages"),
            ]
        elif ctx_type == "web":
            chips = [
                ("🩺 Syntax Check", "Check syntax and troubleshoot Nginx / Apache virtual hosts"),
                ("🔒 SSL Expiry", "Inspect Let's Encrypt certificates and test auto-renewal"),
                ("🐘 PHP Tuning", "Tune PHP-FPM pm.max_children and process manager settings"),
            ]
        elif ctx_type == "sysadmin":
            chips = [
                ("📈 Top Processes", "Check top CPU and RAM consuming processes and inspect zombies"),
                ("💽 Disk Space", "Inspect filesystem disk usage and inode exhaustion"),
                ("📜 Journal Errors", "Check journalctl for system warnings and unit failures"),
            ]
        else:
            chips = [
                ("💡 Help", "How do I administer this Linux server?"),
                ("⚡ Quick Command", "Generate command to inspect network interfaces"),
            ]

        for label, prompt in chips:
            btn = QPushButton(label)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(
                f"padding:2px 8px;border-radius:9px;font-size:10px;"
                f"background:{self._accent}22;color:{self._accent};border:1px solid {self._accent}55;"
            )
            btn.clicked.connect(lambda _, p=prompt: self._set_prompt_chip(p))
            self.chips_layout.addWidget(btn)

        self.chips_layout.addStretch()

    def _set_prompt_chip(self, prompt: str):
        self.task_input.setPlainText(prompt)
        self.task_input.setFocus()
        cursor = self.task_input.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.task_input.setTextCursor(cursor)

    # =========================================================
    # Model List / Connection
    # =========================================================
    def _refresh_models(self):
        self.status_label.setText("🔄 Refreshing models...")
        self.refresh_models_btn.setEnabled(False)

        def work():
            try:
                models = self.client.list_models()
                names = [m.get("name", "") for m in models if m.get("name")]
                self._signals.models_loaded.emit(names)
            except Exception as e:
                self._signals.models_error.emit(str(e))

        threading.Thread(target=work, daemon=True).start()

    def _populate_models(self, names: list[str]):
        self.refresh_models_btn.setEnabled(True)
        self.status_dot.setText("●")
        self.status_dot.setStyleSheet(f"color:{self._ok};font-size:14px;")
        current = self.model_combo.currentText().strip() or "qwen2.5:7b"
        self.model_combo.clear()
        if names:
            self.model_combo.addItems(names)
        if current and current not in names:
            self.model_combo.insertItem(0, current)
        idx = self.model_combo.findText(current)
        if idx >= 0:
            self.model_combo.setCurrentIndex(idx)
        self.status_label.setText(f"✅ {len(names)} model(s) ready.")

    def _on_models_error(self, err: str):
        self.refresh_models_btn.setEnabled(True)
        self.status_dot.setText("○")
        self.status_dot.setStyleSheet(f"color:{self._err};font-size:14px;")
        self.status_label.setText("⚠️ Ollama offline (default: 127.0.0.1:11434)")

    def test_connection(self):
        self.status_label.setText("🔌 Testing Ollama connection...")
        self.ping_btn.setEnabled(False)

        def work():
            try:
                if self.client.ping():
                    self._signals.ping_success.emit()
                else:
                    self._signals.ping_failed.emit("Unexpected response")
            except Exception as e:
                self._signals.ping_failed.emit(str(e))

        threading.Thread(target=work, daemon=True).start()

    def _on_ping_success(self):
        self.ping_btn.setEnabled(True)
        self.status_dot.setStyleSheet(f"color:{self._ok};font-size:14px;")
        self.status_label.setText("✅ Connected to Ollama.")
        self._refresh_models()

    def _on_ping_failed(self, err: str):
        self.ping_btn.setEnabled(True)
        self.status_dot.setStyleSheet(f"color:{self._err};font-size:14px;")
        self.status_label.setText(f"❌ Connection failed: {err}")

    # =========================================================
    # Prompt Building
    # =========================================================
    def _build_system_prompt(self) -> str:
        ctx_type = self._current_context_info.get("type", "generic")
        details = self._current_context_info.get("details", "")

        if ctx_type == "terminal":
            prompt = (
                "You are an expert Linux shell copilot assisting a systems administrator. "
                "Provide the exact, production-ready bash/shell commands or scripts to accomplish the task. "
                "If explaining or answering a question, provide a brief concise explanation followed by the exact command in a ```bash block. "
                "Ensure commands are safe and modern for Linux (Debian, Ubuntu, RHEL, CentOS)."
            )
            if self.include_context_chk.isChecked():
                prompt += f"\nActive context: Terminal session '{details}'."
            return prompt

        elif ctx_type == "database":
            prompt = (
                "You are an expert database administrator and SQL developer. "
                "Provide accurate, optimized SQL queries formatted cleanly. "
                "If explaining or answering a question, provide a brief concise explanation followed by the SQL in a ```sql block."
            )
            if self.include_context_chk.isChecked():
                db_mgr = getattr(self.main_window, "db_manager_widget", None)
                if db_mgr:
                    backend = getattr(db_mgr, "_backend_name", lambda: "MySQL")()
                    curr_schema = getattr(db_mgr, "current_schema", None)
                    curr_table = getattr(db_mgr, "current_table", None)
                    prompt += f"\nActive database dialect: {backend}."
                    if curr_schema:
                        prompt += f" Current schema/database: '{curr_schema}'."
                    if curr_table:
                        prompt += f" Current table: '{curr_table}'."
            return prompt

        return (
            "You are an AI system administration and hosting copilot for Linux environments. "
            "Help the administrator solve technical challenges concisely with exact commands or configurations."
        )

    @staticmethod
    def _extract_command(content: str) -> str:
        if not content:
            return ""
        blocks = re.findall(r"```[a-zA-Z0-9_+-]*\s*\n?(.*?)```", content, re.DOTALL)
        if blocks:
            return blocks[-1].strip()
        inline = re.findall(r"`([^`\n]+)`", content)
        if inline and len(inline[-1]) > 3:
            return inline[-1].strip()
        return content.strip()

    # =========================================================
    # Generation (Streaming)
    # =========================================================
    def generate(self):
        task = self.task_input.toPlainText().strip()
        if not task:
            QMessageBox.warning(self, "Validation Error", "Please enter a prompt or task description.")
            return

        model = self.model_combo.currentText().strip() or "qwen2.5:7b"
        messages = [
            {"role": "system", "content": self._build_system_prompt()},
            {"role": "user", "content": task},
        ]

        self._set_busy(True)
        self.output_edit.clear()
        self.stats_label.setText("")
        self.status_label.setText(f"⏳ Generating with '{model}'...")
        self._start_time = time.time()
        self._token_count = 0
        self._cancel_flag = False
        self._push_history(task)

        signals = _GenSignals()
        signals.token.connect(self._on_token)
        signals.done.connect(self._on_done)
        signals.error.connect(self._on_error)

        def work():
            try:
                stream = self.client.chat(model, messages, stream=True)
                buf = ""
                for chunk in stream:
                    if self._cancel_flag:
                        break
                    delta = chunk.get("message", {}).get("content", "") or chunk.get("response", "")
                    if delta:
                        buf += delta
                        signals.token.emit(delta)

                elapsed = time.time() - self._start_time
                signals.done.emit(buf, elapsed, self._token_count)
            except Exception as e:
                signals.error.emit(str(e))

        self._worker_thread = threading.Thread(target=work, daemon=True)
        self._worker_thread.start()

    def _on_token(self, token: str):
        self._token_count += 1
        cursor = self.output_edit.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.insertText(token)
        self.output_edit.setTextCursor(cursor)
        self.output_edit.ensureCursorVisible()
        elapsed = time.time() - self._start_time
        speed = (self._token_count / elapsed) if elapsed > 0 else 0
        self.stats_label.setText(f"⏱ {elapsed:.1f}s · {speed:.1f} t/s")

    def _on_done(self, full_text: str, elapsed: float, tokens: int):
        self._set_busy(False)
        self.status_label.setText("✅ Response complete.")
        speed = (tokens / elapsed) if elapsed > 0 else 0
        self.stats_label.setText(f"⏱ {elapsed:.1f}s · {tokens} tokens · {speed:.1f} t/s")
        self._update_action_buttons(bool(full_text.strip()))

    def _on_error(self, err: str):
        self._set_busy(False)
        self.output_edit.setPlainText(f"[ERROR] Could not connect to Ollama:\n{err}")
        self.status_label.setText("❌ Generation failed. Check if Ollama is running.")
        self._update_action_buttons(False)

    def _cancel_generation(self):
        self._cancel_flag = True
        self.status_label.setText("⏹ Cancelling...")

    def _set_busy(self, busy: bool):
        self.gen_btn.setVisible(not busy)
        self.cancel_btn.setVisible(busy)
        self.progress.setVisible(busy)
        self.task_input.setReadOnly(busy)

    def _update_action_buttons(self, has_output: bool):
        self.copy_btn.setEnabled(has_output)
        self.btn_run_term.setEnabled(has_output)
        self.btn_insert_term.setEnabled(has_output)
        self.btn_run_db.setEnabled(has_output)
        self.btn_insert_db.setEnabled(has_output)

    def _push_history(self, task: str):
        if task in self._history:
            self._history.remove(task)
        self._history.insert(0, task)
        self._history = self._history[: self.MAX_HISTORY]

    def _clear_all(self):
        self.task_input.clear()
        self.output_edit.clear()
        self.stats_label.setText("")
        self.status_label.setText("Cleared.")
        self._update_action_buttons(False)

    def copy_output(self):
        text = self.output_edit.toPlainText().strip()
        cmd = self._extract_command(text) or text
        if cmd:
            QApplication.clipboard().setText(cmd)
            self.status_label.setText("📋 Copied command to clipboard.")

    # =========================================================
    # Terminal & Database Execution
    # =========================================================
    def _get_active_terminal(self):
        if not self.main_window or not hasattr(self.main_window, "tabs"):
            return None
        widget = self.main_window.tabs.currentWidget()
        if not widget:
            return None
        if hasattr(widget, "send_text"):
            return widget
        if hasattr(widget, "get_all_terminals"):
            terms = widget.get_all_terminals()
            if terms:
                return terms[0]
        return None

    def execute_in_terminal(self, run: bool = True):
        raw_text = self.output_edit.toPlainText().strip()
        cmd = self._extract_command(raw_text) or raw_text
        if not cmd:
            QMessageBox.warning(self, "Execute", "No command found in output.")
            return

        term = self._get_active_terminal()
        if not term:
            # Fallback: scan for any terminal tab
            if hasattr(self.main_window, "tabs"):
                for i in range(self.main_window.tabs.count()):
                    w = self.main_window.tabs.widget(i)
                    if hasattr(w, "send_text"):
                        term = w
                        self.main_window.tabs.setCurrentIndex(i)
                        break

        if not term:
            QMessageBox.information(
                self, "Terminal Not Found",
                "No open terminal tab found. Please open a terminal session first."
            )
            return

        if run:
            term.send_text(cmd + "\n")
            self.status_label.setText("▶ Executed in terminal.")
        else:
            term.send_text(cmd)
            self.status_label.setText("➕ Inserted into terminal buffer.")

    def execute_in_db(self, run: bool = True):
        raw_text = self.output_edit.toPlainText().strip()
        sql = self._extract_command(raw_text) or raw_text
        if not sql:
            QMessageBox.warning(self, "Database", "No SQL query found in output.")
            return

        if not self.main_window or not hasattr(self.main_window, "db_manager_widget"):
            QMessageBox.warning(self, "Database", "Database Manager not found.")
            return

        db_mgr = self.main_window.db_manager_widget
        db_mgr.query_edit.setPlainText(sql)
        # Switch main tabs to DB manager
        self.main_window.tabs.setCurrentWidget(db_mgr)

        if run:
            db_mgr.execute_query()
            self.status_label.setText("▶ Executed SQL in Database Manager.")
        else:
            self.status_label.setText("📝 SQL pasted into query editor.")