"""
AI Assistant & Copilot Sidebar for Admin Suite.
Provides a modern chat interface and context-aware assistance for active Terminals, Databases, and Server Administration.
"""

from __future__ import annotations

import logging
import re
import shlex
import subprocess
import threading
import time
import traceback
from typing import Any, Optional

from PyQt6.QtCore import QObject, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
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

from admin_suite.ai.llm_client import UniversalLlmClient
from admin_suite.ai.ollama_client import OllamaClient
from admin_suite.core.export import ReportExporter
from admin_suite.core.fonts import mono_font, ui_font
from admin_suite.core.icons import get_icon, style_icon_button

logger = logging.getLogger(__name__)


# =============================================================================
# Background Thread Signals
# =============================================================================

class _WorkerSignals(QObject):
    """Signals for background model fetching and connection pinging."""
    models_loaded = pyqtSignal(list)
    models_error = pyqtSignal(str)
    ping_success = pyqtSignal()
    ping_failed = pyqtSignal(str)
    server_assistant_detected = pyqtSignal(dict)


class _GenSignals(QObject):
    """Signals for streaming generation."""
    token = pyqtSignal(str)
    done = pyqtSignal(str, float, int)
    error = pyqtSignal(str)


# =============================================================================
# Settings Dialog (Enhanced Ollama Connection & Diagnostics)
# =============================================================================

class AiSettingsDialog(QDialog):
    """Configuration dialog for AI Provider, endpoint URL, API key, and Ollama connection testing."""

    def __init__(self, services, parent=None):
        super().__init__(parent)
        self.services = services
        self.setWindowTitle("AI Copilot & Ollama Connection Settings")
        self.resize(520, 440)

        theme = self.services.theme.current
        accent = theme.get("accent", "#3daee9")
        panel = theme.get("panel", "#222222")
        border = theme.get("border", "#444444")
        ok_color = theme.get("ok", "#0dbc79")

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        form_layout = QFormLayout()
        form_layout.setSpacing(8)

        # 1. Provider
        self.provider_combo = QComboBox()
        self.provider_combo.addItems([
            "Ollama (Local / Self-Hosted)",
            "OpenAI / Compatible",
            "Anthropic Claude",
            "Google Gemini",
        ])

        cur_provider = self.services.config.get("ai_provider", "ollama").lower()
        prov_map = {
            "ollama": 0,
            "openai": 1,
            "openrouter": 1,
            "groq": 1,
            "deepseek": 1,
            "anthropic": 2,
            "gemini": 3,
        }
        self.provider_combo.setCurrentIndex(prov_map.get(cur_provider, 0))
        self.provider_combo.currentIndexChanged.connect(self._on_provider_changed)
        form_layout.addRow("AI Provider:", self.provider_combo)

        # 2. Base URL + Presets
        url_box = QVBoxLayout()
        saved_url = self.services.config.get("ai_base_url", "")
        self.base_url_edit = QLineEdit(saved_url or "http://127.0.0.1:11434")
        self.base_url_edit.setPlaceholderText("http://127.0.0.1:11434 or http://localhost:11434")
        url_box.addWidget(self.base_url_edit)

        # Presets container
        self.presets_widget = QWidget()
        preset_row = QHBoxLayout(self.presets_widget)
        preset_row.setContentsMargins(0, 0, 0, 0)
        preset_row.setSpacing(4)
        lbl_presets = QLabel("Presets:")
        lbl_presets.setStyleSheet("font-size: 10px; color: #888;")
        preset_row.addWidget(lbl_presets)

        for label, url in [
            ("127.0.0.1:11434", "http://127.0.0.1:11434"),
            ("localhost:11434", "http://localhost:11434"),
        ]:
            btn = QPushButton(label)
            btn.setStyleSheet("font-size: 10px; padding: 2px 6px;")
            btn.clicked.connect(lambda _, u=url: self.base_url_edit.setText(u))
            preset_row.addWidget(btn)
        preset_row.addStretch()
        url_box.addWidget(self.presets_widget)
        form_layout.addRow("API Base URL:", url_box)

        # 3. API Key
        self.api_key_edit = QLineEdit(self.services.secrets.get("ai_api_key", ""))
        self.api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_edit.setPlaceholderText("Optional for local Ollama, required for cloud APIs")
        form_layout.addRow("API Key:", self.api_key_edit)

        # 4. Default Model + Fetch Models button
        model_row = QHBoxLayout()
        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)
        cur_model = self.services.config.get("ai_default_model", "")
        if cur_model:
            self.model_combo.addItem(cur_model)
        self.model_combo.setPlaceholderText("Select or enter model (e.g. llama3, qwen2.5:7b)")
        model_row.addWidget(self.model_combo, 1)

        self.btn_fetch_models = QPushButton("Fetch Models")
        self.btn_fetch_models.setStyleSheet("padding: 4px 8px; font-size: 11px;")
        self.btn_fetch_models.clicked.connect(self._fetch_models)
        model_row.addWidget(self.btn_fetch_models)
        form_layout.addRow("Default Model:", model_row)

        layout.addLayout(form_layout)

        # 5. Diagnostic & Test Connection Frame
        self.diag_frame = QFrame()
        self.diag_frame.setStyleSheet(
            f"background: {panel}; border: 1px solid {border}; border-radius: 5px; padding: 6px;"
        )
        diag_layout = QVBoxLayout(self.diag_frame)
        diag_layout.setContentsMargins(6, 6, 6, 6)
        diag_layout.setSpacing(4)

        diag_hdr = QHBoxLayout()
        self.diag_title = QLabel("Connection Health & Diagnostics:")
        self.diag_title.setStyleSheet("font-weight: bold; font-size: 11px;")
        diag_hdr.addWidget(self.diag_title)
        diag_hdr.addStretch()

        self.btn_test = QPushButton("🔌 Test Connection")
        self.btn_test.setStyleSheet(
            f"font-size: 11px; padding: 3px 10px; font-weight: bold; background: {accent}; color: white; border-radius: 3px;"
        )
        self.btn_test.clicked.connect(self._test_connection)
        diag_hdr.addWidget(self.btn_test)
        diag_layout.addLayout(diag_hdr)

        self.diag_text = QLabel("Click 'Test Connection' to test endpoint reachability and detect models.")
        self.diag_text.setWordWrap(True)
        self.diag_text.setStyleSheet("font-size: 11px; color: #888;")
        diag_layout.addWidget(self.diag_text)

        layout.addWidget(self.diag_frame)
        layout.addStretch()

        # Buttons
        btn_box = QHBoxLayout()
        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        btn_save = QPushButton("Save Settings")
        btn_save.setStyleSheet(
            f"background-color: {ok_color}; color: white; font-weight: bold; padding: 6px 14px; border-radius: 4px;"
        )
        btn_save.clicked.connect(self._save_settings)
        btn_box.addWidget(btn_cancel)
        btn_box.addStretch()
        btn_box.addWidget(btn_save)
        layout.addLayout(btn_box)

        self._on_provider_changed()

    def _on_provider_changed(self):
        idx = self.provider_combo.currentIndex()
        is_ollama = (idx == 0)
        self.presets_widget.setVisible(is_ollama)
        if is_ollama:
            self.api_key_edit.setPlaceholderText("Not required for local Ollama")
            if not self.base_url_edit.text().strip():
                self.base_url_edit.setText("http://127.0.0.1:11434")
        else:
            self.api_key_edit.setPlaceholderText("Required for cloud APIs")
            if "11434" in self.base_url_edit.text():
                self.base_url_edit.setText("")

    def _get_current_client(self) -> UniversalLlmClient:
        prov_keys = ["ollama", "openai", "anthropic", "gemini"]
        selected_prov = prov_keys[self.provider_combo.currentIndex()]
        url = self.base_url_edit.text().strip()
        key = self.api_key_edit.text().strip()
        return UniversalLlmClient(provider=selected_prov, base_url=url, api_key=key)

    def _test_connection(self):
        self.diag_text.setText("⏳ Testing connection to AI endpoint...")
        self.btn_test.setEnabled(False)

        client = self._get_current_client()

        def work():
            res = client.test_connection_detailed()
            QTimer.singleShot(0, lambda: self._on_test_done(res))

        threading.Thread(target=work, daemon=True).start()

    def _on_test_done(self, res: dict[str, Any]):
        self.btn_test.setEnabled(True)
        ok = res.get("ok", False)
        msg = res.get("message", "")
        theme = self.services.theme.current
        ok_color = theme.get("ok", "#0dbc79")
        err_color = theme.get("danger", "#ff5555")

        if ok:
            self.diag_text.setStyleSheet(f"font-size: 11px; color: {ok_color}; font-weight: bold;")
            self.diag_text.setText(f"✅ {msg}")
            models = res.get("models", [])
            if models:
                cur = self.model_combo.currentText().strip()
                self.model_combo.clear()
                self.model_combo.addItems(models)
                if cur in models:
                    self.model_combo.setCurrentText(cur)
        else:
            self.diag_text.setStyleSheet(f"font-size: 11px; color: {err_color};")
            self.diag_text.setText(f"❌ {msg}")

    def _fetch_models(self):
        self.btn_fetch_models.setEnabled(False)
        client = self._get_current_client()

        def work():
            try:
                models = client.list_models()
                names = [m.get("name", "") for m in models if m.get("name")]
                QTimer.singleShot(0, lambda: self._on_fetch_models_done(names))
            except Exception as e:
                QTimer.singleShot(0, lambda: self._on_fetch_models_error(str(e)))

        threading.Thread(target=work, daemon=True).start()

    def _on_fetch_models_done(self, names: list[str]):
        self.btn_fetch_models.setEnabled(True)
        if names:
            cur = self.model_combo.currentText().strip()
            self.model_combo.clear()
            self.model_combo.addItems(names)
            if cur in names:
                self.model_combo.setCurrentText(cur)
            self.diag_text.setText(f"✅ Found {len(names)} model(s): {', '.join(names[:5])}")
        else:
            self.diag_text.setText("⚠️ No models returned. Check if any models are pulled in Ollama (`ollama pull <model>`).")

    def _on_fetch_models_error(self, err: str):
        self.btn_fetch_models.setEnabled(True)
        self.diag_text.setText(f"❌ Failed to fetch models: {err}")

    def _save_settings(self):
        prov_keys = ["ollama", "openai", "anthropic", "gemini"]
        selected_prov = prov_keys[self.provider_combo.currentIndex()]
        self.services.config.set("ai_provider", selected_prov)
        self.services.config.set("ai_base_url", self.base_url_edit.text().strip())
        self.services.config.set("ai_default_model", self.model_combo.currentText().strip())
        self.services.config.save()

        key = self.api_key_edit.text().strip()
        self.services.secrets.set("ai_api_key", key)
        self.accept()


# =============================================================================
# Server Assistant Notification Banner (Red Hat / Debian / Ubuntu)
# =============================================================================

class _ServerAssistantBanner(QFrame):
    """
    Notification banner that displays when connected to a Red Hat (or Ubuntu/Debian)
    server and the native command-line assistant is not installed, allowing 1-click installation.
    """

    def __init__(self, parent_tab):
        super().__init__(parent_tab)
        self.parent_tab = parent_tab
        self.os_info: dict[str, Any] = {}
        self.install_cmd = ""

        theme = parent_tab.services.theme.current
        panel = theme.get("panel", "#222222")
        border = theme.get("border", "#444444")
        ok_color = theme.get("ok", "#0dbc79")

        self.setStyleSheet(
            f"QFrame {{ background: {panel}; border: 1px solid {border}; border-radius: 6px; padding: 4px; }}"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        top_row = QHBoxLayout()
        self.icon_lbl = QLabel("🔴")
        self.icon_lbl.setStyleSheet("font-size: 13px;")
        top_row.addWidget(self.icon_lbl)

        self.title_lbl = QLabel("Red Hat System Detected")
        self.title_lbl.setStyleSheet("font-weight: bold; font-size: 11px;")
        top_row.addWidget(self.title_lbl)
        top_row.addStretch()

        self.close_btn = QToolButton()
        self.close_btn.setText("✕")
        self.close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_btn.setStyleSheet("border:none; color:#888; font-size:11px;")
        self.close_btn.clicked.connect(self.hide)
        top_row.addWidget(self.close_btn)
        layout.addLayout(top_row)

        self.desc_lbl = QLabel(
            "Package 'command-line-assistant' is not installed. Install it to enable Red Hat Lightspeed assistant."
        )
        self.desc_lbl.setWordWrap(True)
        self.desc_lbl.setStyleSheet("font-size: 11px; color: #aaa;")
        layout.addWidget(self.desc_lbl)

        self.btn_row = QHBoxLayout()
        self.btn_row.setSpacing(6)

        self.install_btn = QPushButton("Install on Server")
        self.install_btn.setStyleSheet(
            f"background: {ok_color}; color: white; font-weight: bold; padding: 3px 10px; border-radius: 3px; font-size: 10px;"
        )
        self.install_btn.clicked.connect(self._on_install_clicked)
        self.btn_row.addWidget(self.install_btn)

        self.term_btn = QPushButton("Send to Terminal")
        self.term_btn.setStyleSheet("padding: 3px 8px; font-size: 10px;")
        self.term_btn.clicked.connect(self._on_send_term_clicked)
        self.btn_row.addWidget(self.term_btn)
        self.btn_row.addStretch()

        layout.addLayout(self.btn_row)
        self.hide()

    def update_info(self, os_info: dict[str, Any]):
        self.os_info = os_info
        family = os_info.get("family", "generic")
        pretty = os_info.get("pretty_name") or os_info.get("name", "Linux")
        if family == "rhel":
            self.icon_lbl.setText("🔴")
            self.title_lbl.setText(f"{pretty} Detected")
            self.desc_lbl.setText(
                "Red Hat package 'command-line-assistant' is not installed on this server. "
                "Install it to use Red Hat Lightspeed CLI assistant as your default Copilot."
            )
            self.install_btn.setText("Install command-line-assistant")
            self.install_cmd = "sudo dnf install -y command-line-assistant && sudo systemctl enable --now clad.service"
            self.show()
        elif family == "debian":
            self.icon_lbl.setText("🟣")
            self.title_lbl.setText(f"{pretty} Detected")
            self.desc_lbl.setText(
                "No terminal command-line assistant was found on this server. "
                "Install 'tgpt' (fast, zero API key) or 'shell-gpt' to use server AI."
            )
            self.install_btn.setText("Install tgpt Assistant")
            self.install_cmd = "curl -sSL https://raw.githubusercontent.com/aandrew-me/tgpt/main/install | sudo bash -s /usr/local/bin"
            self.show()
        else:
            self.hide()

    def _on_install_clicked(self):
        cmd = getattr(self, "install_cmd", "")
        if cmd:
            self.parent_tab._install_server_assistant(cmd)

    def _on_send_term_clicked(self):
        cmd = getattr(self, "install_cmd", "")
        if cmd:
            self.parent_tab.execute_in_terminal(cmd, run=True)


# =============================================================================
# Chat Input Editor
# =============================================================================

class _ChatInputEdit(QPlainTextEdit):
    """
    Modern auto-wrap chat input that handles Enter to submit and Shift+Enter for newlines.
    """
    send_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(68)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if event.modifiers() == Qt.KeyboardModifier.ShiftModifier:
                super().keyPressEvent(event)
            else:
                self.send_requested.emit()
                event.accept()
                return
        else:
            super().keyPressEvent(event)


# =============================================================================
# Code Block Card Widget
# =============================================================================

class _CodeBlockWidget(QFrame):
    """
    Syntax-styled code block card with copy, send, and direct execute buttons
    (Execute/Send in Terminal or Execute/Send in Database SQL Editor).
    """

    def __init__(
        self,
        code: str,
        lang: str = "bash",
        parent_tab: Optional["AIAssistantTab"] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.code = code.strip()
        self.lang = (lang or "code").lower()
        self.parent_tab = parent_tab

        theme = parent_tab.services.theme.current if parent_tab else {}
        accent = theme.get("accent", "#3daee9")
        border = theme.get("border", "#3d444d")
        ok_color = theme.get("ok", "#0dbc79")

        self.setStyleSheet(
            f"_CodeBlockWidget {{"
            f"  background: #14171f;"
            f"  border: 1px solid {border};"
            f"  border-radius: 6px;"
            f"  margin: 3px 0;"
            f"}}"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 6)
        layout.setSpacing(4)

        # Header bar
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(6)

        lang_display = self.lang.upper() if self.lang != "code" else "CODE"
        is_sql = "sql" in self.lang or any(
            self.code.upper().startswith(kw)
            for kw in ("SELECT", "INSERT", "UPDATE", "DELETE", "CREATE", "ALTER", "DROP", "SHOW", "EXPLAIN")
        )
        is_bash = ("bash" in self.lang or "sh" in self.lang) or not is_sql

        badge_color = "#0078d4" if is_sql else ok_color
        lang_lbl = QLabel(f" {lang_display} ")
        lang_lbl.setStyleSheet(
            f"background: {badge_color}33; color: {badge_color};"
            f"border: 1px solid {badge_color}66; border-radius: 3px; font-weight: bold; font-size: 10px; padding: 1px 4px;"
        )
        header.addWidget(lang_lbl)

        line_count = len(self.code.splitlines())
        line_lbl = QLabel(f"{line_count} line{'s' if line_count != 1 else ''}")
        line_lbl.setStyleSheet(f"color: {theme.get('sub', '#888')}; font-size: 10px;")
        header.addWidget(line_lbl)

        header.addStretch()

        # Copy button
        self.copy_btn = QPushButton("Copy")
        style_icon_button(self.copy_btn, "copy", "Copy code block to clipboard", size=14)
        self.copy_btn.setStyleSheet("font-size: 10px; padding: 2px 6px;")
        self.copy_btn.clicked.connect(self._copy_code)
        header.addWidget(self.copy_btn)

        layout.addLayout(header)

        # Code text display
        self.editor = QPlainTextEdit()
        self.editor.setReadOnly(True)
        self.editor.setPlainText(self.code)
        self.editor.setFont(mono_font(10))
        self.editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.editor.setStyleSheet(
            "QPlainTextEdit { background: transparent; border: none; padding: 2px; color: #e2e8f0; }"
        )

        lines = len(self.code.splitlines())
        calc_height = max(42, min(240, lines * 18 + 16))
        self.editor.setFixedHeight(calc_height)
        ReportExporter.attach_export_context_menu(self.editor)
        layout.addWidget(self.editor)

        # Context-aware action buttons
        action_layout = QHBoxLayout()
        action_layout.setContentsMargins(0, 2, 0, 0)
        action_layout.setSpacing(6)

        # Context detection
        active_ctx = parent_tab._current_context_info.get("type", "") if parent_tab else ""

        # Terminal Actions
        if is_bash or active_ctx == "terminal":
            btn_run_term = QPushButton("▶ Run in Terminal")
            btn_run_term.setStyleSheet(
                f"background: {ok_color}; color: white; font-weight: bold; font-size: 10px; padding: 3px 8px; border-radius: 4px;"
            )
            btn_run_term.setToolTip("Execute command directly in active terminal (presses Enter)")
            btn_run_term.clicked.connect(lambda: self._execute_term(run=True))
            action_layout.addWidget(btn_run_term)

            btn_insert_term = QPushButton("📥 Send to Terminal")
            btn_insert_term.setStyleSheet(
                f"background: {accent}22; color: {accent}; border: 1px solid {accent}66; font-size: 10px; padding: 3px 8px; border-radius: 4px;"
            )
            btn_insert_term.setToolTip("Insert command into terminal without pressing Enter")
            btn_insert_term.clicked.connect(lambda: self._execute_term(run=False))
            action_layout.addWidget(btn_insert_term)

        # Database Actions
        if is_sql or active_ctx == "database":
            btn_run_db = QPushButton("▶ Execute SQL")
            btn_run_db.setStyleSheet(
                "background: #0078d4; color: white; font-weight: bold; font-size: 10px; padding: 3px 8px; border-radius: 4px;"
            )
            btn_run_db.setToolTip("Send SQL to Database Manager and execute query (F5)")
            btn_run_db.clicked.connect(lambda: self._execute_db(run=True))
            action_layout.addWidget(btn_run_db)

            btn_insert_db = QPushButton("📝 Send to SQL Editor")
            btn_insert_db.setStyleSheet(
                f"background: {accent}22; color: {accent}; border: 1px solid {accent}66; font-size: 10px; padding: 3px 8px; border-radius: 4px;"
            )
            btn_insert_db.setToolTip("Paste SQL into Database Manager query editor")
            btn_insert_db.clicked.connect(lambda: self._execute_db(run=False))
            action_layout.addWidget(btn_insert_db)

        action_layout.addStretch()
        layout.addLayout(action_layout)

    def _copy_code(self):
        QApplication.clipboard().setText(self.code)
        self.copy_btn.setText("Copied!")
        QTimer.singleShot(1500, lambda: self.copy_btn.setText("Copy"))
        if self.parent_tab and hasattr(self.parent_tab, "status_label"):
            self.parent_tab.status_label.setText("📋 Copied code to clipboard.")

    def _execute_term(self, run: bool):
        if self.parent_tab:
            self.parent_tab.execute_in_terminal(self.code, run=run)

    def _execute_db(self, run: bool):
        if self.parent_tab:
            self.parent_tab.execute_in_db(self.code, run=run)


# =============================================================================
# Chat Message Bubble Widget
# =============================================================================

class _ChatMessageWidget(QFrame):
    """
    Message bubble in the conversation feed.
    Renders user prompt bubble or assistant response card with parsed code blocks and action buttons.
    """

    def __init__(
        self,
        role: str,
        content: str,
        timestamp: str = "",
        model: str = "",
        stats: str = "",
        parent_tab: Optional["AIAssistantTab"] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.role = role
        self.content = content
        self.timestamp = timestamp or time.strftime("%H:%M")
        self.model = model
        self.stats = stats
        self.parent_tab = parent_tab

        theme = parent_tab.services.theme.current if parent_tab else {}
        accent = theme.get("accent", "#3daee9")
        panel = theme.get("panel", "#232629")
        panel2 = theme.get("panel2", "#2a2e32")
        border = theme.get("border", "#3d444d")
        text_color = theme.get("text", "#eff0f1")
        sub_color = theme.get("sub", "#95a5a6")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)

        if self.role == "user":
            self.setStyleSheet(
                f"_ChatMessageWidget {{"
                f"  background: {panel2};"
                f"  border: 1px solid {border};"
                f"  border-radius: 8px;"
                f"  margin: 2px 0 2px 18px;"
                f"}}"
            )

            # Header
            hdr = QHBoxLayout()
            hdr.setSpacing(6)
            user_tag = QLabel("👤 You")
            user_tag.setStyleSheet(f"font-weight: bold; color: {accent}; font-size: 11px;")
            hdr.addWidget(user_tag)
            hdr.addStretch()
            time_lbl = QLabel(self.timestamp)
            time_lbl.setStyleSheet(f"color: {sub_color}; font-size: 10px;")
            hdr.addWidget(time_lbl)
            layout.addLayout(hdr)

            # Body
            body_lbl = QLabel(self.content)
            body_lbl.setWordWrap(True)
            body_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            body_lbl.setStyleSheet(f"color: {text_color}; font-size: 12px; line-height: 1.4;")
            layout.addWidget(body_lbl)

        else:
            # Assistant card
            self.setStyleSheet(
                f"_ChatMessageWidget {{"
                f"  background: {panel};"
                f"  border: 1px solid {border};"
                f"  border-left: 3px solid {accent};"
                f"  border-radius: 8px;"
                f"  margin: 2px 18px 2px 0;"
                f"}}"
            )

            # Header
            hdr = QHBoxLayout()
            hdr.setSpacing(6)
            bot_tag = QLabel("🤖 Copilot")
            bot_tag.setStyleSheet(f"font-weight: bold; color: {accent}; font-size: 12px;")
            hdr.addWidget(bot_tag)

            if self.model:
                model_lbl = QLabel(f" {self.model} ")
                model_lbl.setStyleSheet(
                    f"background: {accent}22; color: {accent}; border-radius: 3px; font-size: 9px; padding: 1px 4px;"
                )
                hdr.addWidget(model_lbl)

            hdr.addStretch()

            if self.stats:
                stats_lbl = QLabel(self.stats)
                stats_lbl.setStyleSheet(f"color: {sub_color}; font-size: 10px;")
                hdr.addWidget(stats_lbl)

            time_lbl = QLabel(self.timestamp)
            time_lbl.setStyleSheet(f"color: {sub_color}; font-size: 10px;")
            hdr.addWidget(time_lbl)
            layout.addLayout(hdr)

            # Parse content into segments
            segments = AIAssistantTab._parse_message_segments(self.content)
            has_code_block = False

            for seg_type, lang, seg_text in segments:
                if seg_type == "text" and seg_text:
                    txt_lbl = QLabel(seg_text)
                    txt_lbl.setWordWrap(True)
                    txt_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                    txt_lbl.setStyleSheet(f"color: {text_color}; font-size: 12px; line-height: 1.4;")
                    layout.addWidget(txt_lbl)
                elif seg_type == "code" and seg_text:
                    has_code_block = True
                    code_box = _CodeBlockWidget(seg_text, lang=lang, parent_tab=self.parent_tab)
                    layout.addWidget(code_box)

            # Footer
            footer = QHBoxLayout()
            footer.setContentsMargins(0, 4, 0, 0)
            footer.addStretch()

            copy_resp_btn = QPushButton("Copy Message")
            style_icon_button(copy_resp_btn, "copy", "Copy entire response to clipboard", size=14)
            copy_resp_btn.setStyleSheet("font-size: 10px; padding: 2px 6px;")
            copy_resp_btn.clicked.connect(self._copy_full_response)
            footer.addWidget(copy_resp_btn)
            layout.addLayout(footer)

    def _copy_full_response(self):
        QApplication.clipboard().setText(self.content)
        if self.parent_tab and hasattr(self.parent_tab, "status_label"):
            self.parent_tab.status_label.setText("📋 Copied full response to clipboard.")


# =============================================================================
# Live Streaming Assistant Card
# =============================================================================

class _StreamingMessageWidget(QFrame):
    """Live streaming assistant message card displaying tokens as they are received."""

    def __init__(self, model: str = "", parent_tab: Optional["AIAssistantTab"] = None, parent=None):
        super().__init__(parent)
        self.model = model
        self.parent_tab = parent_tab
        self.buffer = ""

        theme = parent_tab.services.theme.current if parent_tab else {}
        accent = theme.get("accent", "#3daee9")
        panel = theme.get("panel", "#232629")
        border = theme.get("border", "#3d444d")
        text_color = theme.get("text", "#eff0f1")
        sub_color = theme.get("sub", "#95a5a6")

        self.setStyleSheet(
            f"_StreamingMessageWidget {{"
            f"  background: {panel};"
            f"  border: 1px solid {border};"
            f"  border-left: 3px solid {accent};"
            f"  border-radius: 8px;"
            f"  margin: 2px 18px 2px 0;"
            f"}}"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)

        hdr = QHBoxLayout()
        hdr.setSpacing(6)
        bot_tag = QLabel("🤖 Copilot")
        bot_tag.setStyleSheet(f"font-weight: bold; color: {accent}; font-size: 12px;")
        hdr.addWidget(bot_tag)

        if self.model:
            model_lbl = QLabel(f" {self.model} ")
            model_lbl.setStyleSheet(
                f"background: {accent}22; color: {accent}; border-radius: 3px; font-size: 9px; padding: 1px 4px;"
            )
            hdr.addWidget(model_lbl)

        hdr.addStretch()

        self.stats_lbl = QLabel("⏳ Generating...")
        self.stats_lbl.setStyleSheet(f"color: {sub_color}; font-size: 10px;")
        hdr.addWidget(self.stats_lbl)
        layout.addLayout(hdr)

        self.text_display = QLabel("...")
        self.text_display.setWordWrap(True)
        self.text_display.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.text_display.setStyleSheet(f"color: {text_color}; font-size: 12px; line-height: 1.4;")
        layout.addWidget(self.text_display)

    def append_token(self, delta: str, elapsed: float = 0.0, tokens: int = 0):
        self.buffer += delta
        self.text_display.setText(self.buffer + " ▋")
        if elapsed > 0:
            speed = (tokens / elapsed) if elapsed > 0 else 0
            self.stats_lbl.setText(f"⏱ {elapsed:.1f}s · {speed:.1f} t/s")


# =============================================================================
# Welcome / Empty State Widget
# =============================================================================

class _WelcomeWidget(QWidget):
    """Empty state welcome card shown when chat history is empty."""

    def __init__(self, parent_tab: "AIAssistantTab", parent=None):
        super().__init__(parent)
        self.parent_tab = parent_tab

        theme = parent_tab.services.theme.current if parent_tab else {}
        accent = theme.get("accent", "#3daee9")
        sub_color = theme.get("sub", "#95a5a6")
        panel2 = theme.get("panel2", "#2a2e32")
        border = theme.get("border", "#3d444d")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 16, 12, 16)
        layout.setSpacing(10)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        icon_lbl = QLabel()
        icon_lbl.setPixmap(get_icon("sparkles", color=accent, size=32).pixmap(32, 32))
        icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(icon_lbl)

        title = QLabel("AI Copilot Assistant")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet(f"font-size: 13px; font-weight: bold; color: {accent};")
        layout.addWidget(title)

        subtitle = QLabel("Context-aware coding & operations assistant.")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setWordWrap(True)
        subtitle.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        subtitle.setStyleSheet(f"font-size: 10px; color: {sub_color};")
        layout.addWidget(subtitle)

        # Quick guide box
        guide = QFrame()
        guide.setStyleSheet(
            f"background: {panel2}; border: 1px solid {border}; border-radius: 6px; padding: 4px;"
        )
        guide_layout = QVBoxLayout(guide)
        guide_layout.setSpacing(4)

        t1 = QLabel("• <b>Terminal Tabs:</b> 1-click Run & Send commands")
        t1.setWordWrap(True)
        t1.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        t1.setStyleSheet("font-size: 10px;")

        t2 = QLabel("• <b>Database Tabs:</b> 1-click SQL Execute & Send")
        t2.setWordWrap(True)
        t2.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        t2.setStyleSheet("font-size: 10px;")

        t3 = QLabel("• <b>Shortcuts:</b> Enter to send, Shift+Enter newline, Ctrl+L clear")
        t3.setWordWrap(True)
        t3.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        t3.setStyleSheet("font-size: 10px;")

        guide_layout.addWidget(t1)
        guide_layout.addWidget(t2)
        guide_layout.addWidget(t3)
        layout.addWidget(guide)


# =============================================================================
# Main AIAssistantTab Widget
# =============================================================================

class AIAssistantTab(QWidget):
    """
    Intelligent AI Copilot right-sidebar widget.
    Redesigned as a modern chat interface with multi-turn history, syntax code cards,
    context-aware execution in active terminal or database tabs, and tailored system prompts.
    """

    MAX_HISTORY = 30

    def __init__(self, services, main_window=None, parent=None):
        super().__init__(parent or main_window)
        self.services = services
        self.main_window = main_window

        provider = self.services.config.get("ai_provider", "ollama")
        base_url = self.services.config.get("ai_base_url", None)
        api_key = self.services.secrets.get("ai_api_key", "")
        self.client = UniversalLlmClient(provider=provider, base_url=base_url, api_key=api_key)

        self._cancel_flag = False
        self._worker_thread: Optional[threading.Thread] = None
        self._chat_history: list[dict[str, Any]] = []
        self._streaming_widget: Optional[_StreamingMessageWidget] = None
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
        self._signals.server_assistant_detected.connect(self._on_server_assistant_detected)

        self._server_assistant_cache: dict[str, dict[str, Any]] = {}
        self._server_os_info: dict[str, Any] = {}
        self._server_asst_label: Optional[str] = None
        self._probing_targets: set[str] = set()

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
        title.setStyleSheet(f"font-weight:bold;font-size:13px;color:{self._accent};")
        header_bar.addWidget(title)

        header_bar.addStretch()

        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)
        self.model_combo.setMinimumWidth(90)
        self.model_combo.setMaximumWidth(150)
        self.model_combo.setToolTip("Select or enter AI model")
        header_bar.addWidget(self.model_combo, 1)

        self.new_chat_btn = QToolButton()
        style_icon_button(self.new_chat_btn, "plus", "New chat / Clear conversation (Ctrl+L)", size=18, icon_only=True)
        self.new_chat_btn.clicked.connect(self._clear_all)
        header_bar.addWidget(self.new_chat_btn)

        self.refresh_models_btn = QToolButton()
        style_icon_button(self.refresh_models_btn, "refresh", "Refresh available models", size=18, icon_only=True)
        self.refresh_models_btn.clicked.connect(self._refresh_models)
        header_bar.addWidget(self.refresh_models_btn)

        self.ping_btn = QToolButton()
        style_icon_button(self.ping_btn, "plug", "Test connection to configured AI endpoint", size=18, icon_only=True)
        self.ping_btn.clicked.connect(self.test_connection)
        header_bar.addWidget(self.ping_btn)

        self.settings_btn = QToolButton()
        style_icon_button(self.settings_btn, "settings", "Configure AI Provider, API Endpoint & Key", size=18, icon_only=True)
        self.settings_btn.clicked.connect(self._open_ai_settings_dialog)
        header_bar.addWidget(self.settings_btn)

        self.close_btn = QToolButton()
        style_icon_button(self.close_btn, "close", "Close Copilot sidebar (Ctrl+Shift+A)", size=18, icon_only=True)
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

        self.context_icon = QLabel()
        self.context_icon.setPixmap(get_icon("terminal", size=16).pixmap(16, 16))
        ctx_layout.addWidget(self.context_icon)

        self.context_label = QLabel("Detecting context...")
        self.context_label.setStyleSheet("font-size:11px;font-weight:bold;")
        self.context_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        ctx_layout.addWidget(self.context_label, 1)

        self.include_context_chk = QCheckBox("Context")
        self.include_context_chk.setChecked(True)
        self.include_context_chk.setStyleSheet("font-size:10px;")
        self.include_context_chk.setToolTip("Include active tab details and context-specific system prompt")
        ctx_layout.addWidget(self.include_context_chk)

        self.sync_ctx_btn = QToolButton()
        style_icon_button(self.sync_ctx_btn, "refresh", "Sync current tab context", size=16, icon_only=True)
        self.sync_ctx_btn.clicked.connect(self.update_context)
        ctx_layout.addWidget(self.sync_ctx_btn)

        layout.addWidget(self.context_frame)

        # 2b. Server Assistant Notification Banner (Red Hat / Debian / Ubuntu)
        self.server_banner = _ServerAssistantBanner(self)
        layout.addWidget(self.server_banner)

        # 3. Dynamic Quick Action Chips
        self.chips_scroll = QScrollArea()
        self.chips_scroll.setFixedHeight(34)
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

        # 4. Scrollable Chat Message Feed
        self.chat_scroll = QScrollArea()
        self.chat_scroll.setWidgetResizable(True)
        self.chat_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.chat_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self.chat_container = QWidget()
        self.chat_layout = QVBoxLayout(self.chat_container)
        self.chat_layout.setContentsMargins(2, 4, 2, 4)
        self.chat_layout.setSpacing(8)

        self.welcome_widget = _WelcomeWidget(self)
        self.chat_layout.addWidget(self.welcome_widget)
        self.chat_layout.addStretch(1)

        self.chat_scroll.setWidget(self.chat_container)
        layout.addWidget(self.chat_scroll, 1)

        # 5. Composer / Input Area
        input_box = QFrame()
        input_box.setStyleSheet(
            f"background:{self._panel2};border:1px solid {self._border};border-radius:6px;padding:4px;"
        )
        input_box_layout = QVBoxLayout(input_box)
        input_box_layout.setContentsMargins(4, 4, 4, 4)
        input_box_layout.setSpacing(4)

        self.task_input = _ChatInputEdit()
        self.task_input.setPlaceholderText("Ask Copilot... (Enter to send, Shift+Enter for newline)")
        self.task_input.setStyleSheet("border:none;background:transparent;")
        self.task_input.send_requested.connect(self.generate)
        input_box_layout.addWidget(self.task_input)

        # Composer Bottom Row
        ctrl_row = QHBoxLayout()
        ctrl_row.setContentsMargins(2, 0, 2, 0)
        ctrl_row.setSpacing(6)

        self.context_badge = QLabel("Terminal")
        self.context_badge.setStyleSheet(
            f"background:{self._accent}22;color:{self._accent};border-radius:3px;font-size:9px;padding:2px 6px;font-weight:bold;"
        )
        ctrl_row.addWidget(self.context_badge)

        ctrl_row.addStretch()

        self.cancel_btn = QPushButton("⏹ Stop")
        self.cancel_btn.setStyleSheet(f"background:{self._err};color:white;padding:3px 10px;font-size:11px;border-radius:3px;")
        self.cancel_btn.clicked.connect(self._cancel_generation)
        self.cancel_btn.hide()
        ctrl_row.addWidget(self.cancel_btn)

        self.gen_btn = QPushButton("✨ Send")
        self.gen_btn.setStyleSheet(f"background:{self._accent};color:white;font-weight:bold;padding:4px 14px;border-radius:3px;")
        self.gen_btn.clicked.connect(self.generate)
        ctrl_row.addWidget(self.gen_btn)

        input_box_layout.addLayout(ctrl_row)
        layout.addWidget(input_box)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(2)
        self.progress.hide()
        layout.addWidget(self.progress)

        # 6. Persistent Action Toolbar (Direct Terminal & SQL Editor buttons for latest command)
        self.action_bar = QVBoxLayout()
        self.action_bar.setSpacing(3)
        self.action_bar.setContentsMargins(0, 0, 0, 0)

        # Terminal Actions Widget
        self.term_action_widget = QWidget()
        term_layout = QHBoxLayout(self.term_action_widget)
        term_layout.setContentsMargins(0, 0, 0, 0)
        term_layout.setSpacing(4)

        self.btn_run_term = QPushButton("▶ Run in Terminal")
        self.btn_run_term.setStyleSheet(f"background:{self._ok};color:white;font-weight:bold;border-radius:4px;padding:3px 8px;font-size:10px;")
        self.btn_run_term.setToolTip("Execute latest generated command in active terminal")
        self.btn_run_term.clicked.connect(lambda: self.execute_in_terminal(run=True))
        term_layout.addWidget(self.btn_run_term)

        self.btn_insert_term = QPushButton("📥 Send to Terminal")
        self.btn_insert_term.setStyleSheet(f"background:{self._accent}22;color:{self._accent};border:1px solid {self._accent}66;border-radius:4px;padding:3px 8px;font-size:10px;")
        self.btn_insert_term.setToolTip("Insert latest command into terminal buffer without pressing Enter")
        self.btn_insert_term.clicked.connect(lambda: self.execute_in_terminal(run=False))
        term_layout.addWidget(self.btn_insert_term)
        self.action_bar.addWidget(self.term_action_widget)

        # Database Actions Widget
        self.db_action_widget = QWidget()
        db_layout = QHBoxLayout(self.db_action_widget)
        db_layout.setContentsMargins(0, 0, 0, 0)
        db_layout.setSpacing(4)

        self.btn_run_db = QPushButton("▶ Execute SQL")
        self.btn_run_db.setStyleSheet("background:#0078d4;color:white;font-weight:bold;border-radius:4px;padding:3px 8px;font-size:10px;")
        self.btn_run_db.setToolTip("Execute latest generated SQL query in Database Manager (F5)")
        self.btn_run_db.clicked.connect(lambda: self.execute_in_db(run=True))
        db_layout.addWidget(self.btn_run_db)

        self.btn_insert_db = QPushButton("📝 Send to SQL Editor")
        self.btn_insert_db.setStyleSheet(f"background:{self._accent}22;color:{self._accent};border:1px solid {self._accent}66;border-radius:4px;padding:3px 8px;font-size:10px;")
        self.btn_insert_db.setToolTip("Paste latest SQL into Database Manager query editor")
        self.btn_insert_db.clicked.connect(lambda: self.execute_in_db(run=False))
        db_layout.addWidget(self.btn_insert_db)
        self.action_bar.addWidget(self.db_action_widget)

        action_row2 = QHBoxLayout()
        action_row2.setSpacing(4)

        self.copy_btn = QPushButton("Copy")
        style_icon_button(self.copy_btn, "copy", "Copy latest command or output", size=14)
        self.copy_btn.setStyleSheet("font-size:10px;padding:2px 6px;")
        self.copy_btn.clicked.connect(self.copy_output)
        action_row2.addWidget(self.copy_btn)

        self.clear_btn = QPushButton("Clear")
        style_icon_button(self.clear_btn, "trash", "Clear chat (Ctrl+L)", size=14)
        self.clear_btn.setStyleSheet("font-size:10px;padding:2px 6px;")
        self.clear_btn.clicked.connect(self._clear_all)
        action_row2.addWidget(self.clear_btn)
        action_row2.addStretch()

        self.action_bar.addLayout(action_row2)
        layout.addLayout(self.action_bar)

        self.status_label = QLabel("Ready.")
        self.status_label.setStyleSheet(f"color:{self._sub};font-size:10px;")
        layout.addWidget(self.status_label)

        # Compatibility hidden widget for any code expecting output_edit
        self.output_edit = QPlainTextEdit()
        self.output_edit.hide()

        # Keyboard shortcuts
        QShortcut(QKeySequence("Ctrl+Return"), self.task_input).activated.connect(self.generate)
        QShortcut(QKeySequence("Ctrl+Enter"), self.task_input).activated.connect(self.generate)
        QShortcut(QKeySequence("Ctrl+L"), self).activated.connect(self._clear_all)

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
        """Inspects the currently active tab in the main window and updates state."""
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

        # Update Banner & Badge
        icon_map = {
            "terminal": "terminal",
            "database": "database",
            "web": "globe",
            "sysadmin": "server",
            "security": "shield_check",
            "generic": "terminal_square",
        }
        icon_name = icon_map.get(ctx_type, "terminal_square")
        self.context_icon.setPixmap(get_icon(icon_name, size=16).pixmap(16, 16))
        self.context_label.setText(f"Active Context: {details}")

        badge_titles = {
            "terminal": "Terminal",
            "database": "SQL DB",
            "web": "Web",
            "sysadmin": "SysAdmin",
            "security": "Security",
            "generic": "General",
        }
        self.context_badge.setText(badge_titles.get(ctx_type, "General"))

        # Update placeholder
        if ctx_type == "terminal":
            self.task_input.setPlaceholderText("Ask Copilot for Linux commands or scripts... (Enter to send)")
        elif ctx_type == "database":
            self.task_input.setPlaceholderText("Ask Copilot for SQL queries or statements... (Enter to send)")
        else:
            self.task_input.setPlaceholderText("Ask Copilot anything... (Enter to send, Shift+Enter for newline)")

        # Update action buttons visibility
        self.term_action_widget.setVisible(ctx_type == "terminal")
        self.db_action_widget.setVisible(ctx_type == "database")
        self.btn_run_term.setVisible(ctx_type == "terminal")
        self.btn_insert_term.setVisible(ctx_type == "terminal")
        self.btn_run_db.setVisible(ctx_type == "database")
        self.btn_insert_db.setVisible(ctx_type == "database")

        self._rebuild_chips(ctx_type)

        if ctx_type == "terminal":
            self._probe_server_assistant()
        else:
            if hasattr(self, "server_banner"):
                self.server_banner.hide()

    def _rebuild_chips(self, ctx_type: str):
        while self.chips_layout.count():
            item = self.chips_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        chips = []
        if ctx_type == "terminal":
            chips = [
                ("💡 Explain Error", "Explain the last command output or error and suggest the fix."),
                ("⚡ Top CPU & Memory", "Show top processes sorted by CPU and RAM usage"),
                ("🔍 Find Modified Files", "Find files in /var or /etc modified in the last 24 hours"),
                ("🛡️ Listening Ports", "Check active listening ports and established connections"),
                ("💽 Disk Free Space", "Check disk space usage, mounts, and inode usage"),
            ]
        elif ctx_type == "database":
            chips = [
                ("🔍 SELECT Sample", "Write a SELECT query to fetch the top 50 records ordered by id DESC"),
                ("⚡ Optimize Query", "Optimize this SQL query for performance and recommend indexes: "),
                ("🛠️ Fix SQL Syntax", "Fix this SQL syntax or query execution error: "),
                ("📊 GROUP BY Count", "Count records grouped by status with percentages and totals"),
                ("📑 Explain Query Plan", "Show EXPLAIN plan query and analyze bottlenecks"),
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
                ("💡 Server Help", "How do I administer this Linux server safely?"),
                ("⚡ Network Info", "Generate command to inspect network interfaces, routing, and IP addresses"),
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
    # Server Command-Line Assistant Detection & Execution
    # =========================================================
    def _probe_server_assistant(self):
        """
        Probes the connected server/terminal OS to detect if it is Red Hat (RHEL/CentOS/Rocky/Alma)
        or Debian/Ubuntu, and checks if a native command-line assistant is installed:
        - Red Hat: 'command-line-assistant' (binary 'c' or 'cla')
        - Debian/Ubuntu: 'tgpt', 'sgpt', 'aichat', or 'mods'
        """
        term = self._get_active_terminal()
        is_remote = False
        target_key = "local"
        profile_info: dict[str, Any] = {}

        if term and hasattr(term, "host") and getattr(term, "host", ""):
            is_remote = True
            host = getattr(term, "host", "")
            port = getattr(term, "port", 22)
            user = getattr(term, "user", "")
            target_key = f"{user}@{host}:{port}"
            profile_info = dict(getattr(term, "profile_data", {}) or {})
            profile_info.update({
                "host": host,
                "port": port,
                "user": user,
                "creds": getattr(term, "creds", None),
                "use_jump": getattr(term, "use_jump", False),
                "jump_host": getattr(term, "jump_host", None),
                "jump_port": getattr(term, "jump_port", 22),
                "jump_user": getattr(term, "jump_user", None),
                "jump_creds": getattr(term, "jump_creds", None),
                "use_agent": getattr(term, "use_agent", False),
                "strict_host_keys": getattr(term, "strict_host_keys", False),
            })

        if target_key in self._server_assistant_cache:
            self._signals.server_assistant_detected.emit(self._server_assistant_cache[target_key])
            return

        if target_key in self._probing_targets:
            return

        self._probing_targets.add(target_key)

        def work():
            probe_cmd = (
                "cat /etc/os-release 2>/dev/null; "
                "echo '---TOOLS---'; "
                "which c 2>/dev/null; "
                "which cla 2>/dev/null; "
                "which tgpt 2>/dev/null; "
                "which sgpt 2>/dev/null; "
                "which aichat 2>/dev/null; "
                "which mods 2>/dev/null"
            )
            raw_out = ""
            try:
                if is_remote:
                    if hasattr(self.services, "ssh_pool") and self.services.ssh_pool:
                        out, err, rc = self.services.ssh_pool.execute(
                            profile_info, probe_cmd, timeout=4.0
                        )
                        raw_out = out
                else:
                    proc = subprocess.run(
                        probe_cmd, shell=True, capture_output=True, text=True, timeout=3.0
                    )
                    raw_out = proc.stdout
            except Exception as e:
                logger.debug("Failed to probe server OS / assistant: %s", e)

            os_info = self._parse_os_and_tools(raw_out, is_remote, profile_info, target_key)
            self._server_assistant_cache[target_key] = os_info
            self._probing_targets.discard(target_key)
            self._signals.server_assistant_detected.emit(os_info)

        threading.Thread(target=work, daemon=True).start()

    @staticmethod
    def _parse_os_and_tools(raw_out: str, is_remote: bool, profile_info: dict[str, Any], target_key: str) -> dict[str, Any]:
        os_part = raw_out
        tools_part = ""
        if "---TOOLS---" in raw_out:
            os_part, tools_part = raw_out.split("---TOOLS---", 1)

        os_id = ""
        id_like = ""
        pretty_name = ""
        name = ""

        for line in os_part.splitlines():
            line = line.strip()
            if not line or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k == "ID":
                os_id = v.lower()
            elif k == "ID_LIKE":
                id_like = v.lower()
            elif k == "PRETTY_NAME":
                pretty_name = v
            elif k == "NAME":
                name = v

        rhel_identifiers = {"rhel", "redhat", "centos", "rocky", "almalinux", "fedora"}
        debian_identifiers = {"ubuntu", "debian", "pop", "mint", "kali", "raspbian"}

        is_rhel = os_id in rhel_identifiers or any(k in id_like for k in rhel_identifiers)
        is_debian = os_id in debian_identifiers or any(k in id_like for k in debian_identifiers)

        if is_rhel:
            family = "rhel"
        elif is_debian:
            family = "debian"
        else:
            family = "generic"

        found_tools: list[str] = []
        for line in tools_part.splitlines():
            line = line.strip()
            if not line or line.startswith("which:"):
                continue
            bin_name = line.split("/")[-1].strip()
            if bin_name and bin_name not in found_tools:
                found_tools.append(bin_name)

        installed_tool = ""
        if family == "rhel":
            if "c" in found_tools:
                installed_tool = "c"
            elif "cla" in found_tools:
                installed_tool = "cla"
            elif found_tools:
                installed_tool = found_tools[0]
        elif family == "debian":
            if "tgpt" in found_tools:
                installed_tool = "tgpt"
            elif "sgpt" in found_tools:
                installed_tool = "sgpt"
            elif "c" in found_tools:
                installed_tool = "c"
            elif found_tools:
                installed_tool = found_tools[0]
        elif found_tools:
            installed_tool = found_tools[0]

        return {
            "target_key": target_key,
            "family": family,
            "os_id": os_id,
            "name": name,
            "pretty_name": pretty_name or name or "Linux",
            "installed_tool": installed_tool,
            "tools": found_tools,
            "remote": is_remote,
            "profile_info": profile_info,
        }

    def _on_server_assistant_detected(self, os_info: dict[str, Any]):
        self._server_os_info = os_info
        family = os_info.get("family", "generic")
        tool = os_info.get("installed_tool", "")

        # Remove old server assistant label if present
        if self._server_asst_label:
            idx = self.model_combo.findText(self._server_asst_label)
            if idx >= 0:
                self.model_combo.removeItem(idx)
            self._server_asst_label = None

        if family == "rhel":
            if tool:
                self.server_banner.hide()
                label = f"🔴 RHEL Assistant ({tool})"
                self._server_asst_label = label
                self.model_combo.insertItem(0, label)
                self.model_combo.setCurrentIndex(0)
                self.status_label.setText(f"🔴 Default assistant set to RHEL command-line-assistant ({tool}).")
            else:
                self.server_banner.update_info(os_info)
                self.status_label.setText("💡 Red Hat detected: command-line-assistant can be installed.")

        elif family == "debian":
            if tool:
                self.server_banner.hide()
                label = f"🟣 Server Assistant ({tool})"
                self._server_asst_label = label
                self.model_combo.insertItem(0, label)
                self.model_combo.setCurrentIndex(0)
                self.status_label.setText(f"🟣 Server assistant set to {tool}.")
            else:
                self.server_banner.update_info(os_info)
                self.status_label.setText("💡 Debian/Ubuntu detected: server assistant can be installed.")

        elif tool:
            self.server_banner.hide()
            label = f"🟢 Server Assistant ({tool})"
            self._server_asst_label = label
            self.model_combo.insertItem(0, label)
            self.model_combo.setCurrentIndex(0)
            self.status_label.setText(f"🟢 Server assistant set to {tool}.")
        else:
            self.server_banner.hide()

    def _install_server_assistant(self, cmd: str):
        if not cmd:
            return
        self.status_label.setText("🚀 Launching installation on server...")
        self.execute_in_terminal(cmd, run=True)
        cur_target = self._server_os_info.get("target_key", "")
        if cur_target and cur_target in self._server_assistant_cache:
            self._server_assistant_cache.pop(cur_target, None)

    @staticmethod
    def _format_assistant_exec_cmd(tool: str, prompt: str) -> str:
        escaped = prompt.replace("'", "'\"'\"'")
        if tool == "c":
            return f"c '{escaped}'"
        elif tool == "cla":
            return f"cla chat '{escaped}'"
        elif tool == "tgpt":
            return f"tgpt '{escaped}'"
        elif tool == "sgpt":
            return f"sgpt '{escaped}'"
        elif tool == "mods":
            return f"mods '{escaped}'"
        elif tool == "aichat":
            return f"aichat '{escaped}'"
        return f"{tool} '{escaped}'"

    def _generate_with_server_assistant(self, task: str, model_label: str):
        if hasattr(self, "welcome_widget") and self.welcome_widget.isVisible():
            self.welcome_widget.hide()

        user_msg = {
            "role": "user",
            "content": task,
            "timestamp": time.strftime("%H:%M"),
        }
        self._chat_history.append(user_msg)
        self._add_message_bubble(user_msg)

        self.task_input.clear()
        self._set_busy(True)
        self.status_label.setText(f"⏳ Running on server ({model_label})...")
        self._start_time = time.time()
        self._token_count = 0
        self._cancel_flag = False

        self.services.emit_log("AI", f"Server assistant query dispatched ({model_label}): {task[:100]}", "INFO")

        self._streaming_widget = _StreamingMessageWidget(model=model_label, parent_tab=self)
        self._insert_chat_widget(self._streaming_widget)
        self._scroll_to_bottom()

        signals = _GenSignals()
        signals.token.connect(self._on_token)
        signals.done.connect(self._on_done)
        signals.error.connect(self._on_error)

        tool = self._server_os_info.get("installed_tool") or "c"
        is_remote = self._server_os_info.get("remote", False)
        profile_info = self._server_os_info.get("profile_info", {})

        def work():
            try:
                signals.token.emit(f"Connecting to server assistant ({tool})...\n")
                exec_cmd = self._format_assistant_exec_cmd(tool, task)
                out = ""
                err = ""
                rc = 0

                if is_remote and hasattr(self.services, "ssh_pool") and self.services.ssh_pool:
                    out, err, rc = self.services.ssh_pool.execute(
                        profile_info, exec_cmd, timeout=45.0
                    )
                else:
                    proc = subprocess.run(
                        exec_cmd, shell=True, capture_output=True, text=True, timeout=45.0
                    )
                    out, err, rc = proc.stdout, proc.stderr, proc.returncode

                ansi_re = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
                out_clean = ansi_re.sub("", out or "").strip()
                err_clean = ansi_re.sub("", err or "").strip()

                if self._cancel_flag:
                    return

                res_text = out_clean or err_clean or "Assistant completed with no output."
                elapsed = time.time() - self._start_time
                approx_tokens = len(res_text.split())

                signals.done.emit(res_text, elapsed, approx_tokens)
            except Exception as e:
                if not self._cancel_flag:
                    signals.error.emit(str(e))

        self._worker_thread = threading.Thread(target=work, daemon=True)
        self._worker_thread.start()

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
        current = self.model_combo.currentText().strip()
        self.model_combo.clear()

        # Preserve detected server assistant at index 0
        if self._server_asst_label:
            self.model_combo.addItem(self._server_asst_label)

        if names:
            self.model_combo.addItems(names)

        # Selection logic:
        if self._server_asst_label and (not current or current == self._server_asst_label):
            self.model_combo.setCurrentIndex(0)
        elif current and self.model_combo.findText(current) >= 0:
            self.model_combo.setCurrentIndex(self.model_combo.findText(current))
        elif names:
            # Select the first available model if current wasn't found
            target_idx = 1 if self._server_asst_label else 0
            self.model_combo.setCurrentIndex(target_idx)
        elif not self._server_asst_label:
            self.model_combo.addItem("qwen2.5:7b")
            self.model_combo.setCurrentIndex(0)

        self.status_label.setText(f"✅ {len(names)} model(s) ready.")

    def _open_ai_settings_dialog(self) -> None:
        dlg = AiSettingsDialog(self.services, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            provider = self.services.config.get("ai_provider", "ollama")
            base_url = self.services.config.get("ai_base_url", None)
            api_key = self.services.secrets.get("ai_api_key", "")
            self.client.configure(provider=provider, base_url=base_url, api_key=api_key)
            self._refresh_models()

    def _on_models_error(self, err: str):
        self.refresh_models_btn.setEnabled(True)
        self.status_dot.setText("○")
        self.status_dot.setStyleSheet(f"color:{self._err};font-size:14px;")
        self.status_label.setText("⚠️ Ollama offline (default: 127.0.0.1:11434)")

    def test_connection(self):
        self.status_label.setText("🔌 Testing AI connection...")
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
        self.status_label.setText("✅ Connected to AI endpoint.")
        self._refresh_models()

    def _on_ping_failed(self, err: str):
        self.ping_btn.setEnabled(True)
        self.status_dot.setStyleSheet(f"color:{self._err};font-size:14px;")
        self.status_label.setText(f"❌ Connection failed: {err}")

    # =========================================================
    # System Prompts Tailored to Selected Tab
    # =========================================================
    def _build_terminal_system_prompt(self, details: str, active_widget: Any) -> str:
        host_info = ""
        if active_widget:
            host = getattr(active_widget, "host", "")
            user = getattr(active_widget, "user", "")
            port = getattr(active_widget, "port", 22)
            if host:
                host_info = f"Remote Host: {user}@{host}:{port}."

        return (
            "You are an expert Linux shell copilot and systems engineer assisting an administrator.\n"
            f"Active Context: The administrator is working in a live terminal tab ({details}). {host_info}\n\n"
            "Instructions:\n"
            "1. Provide exact, production-ready, safe, and modern bash/shell commands or scripts.\n"
            "2. Always enclose executable commands in ```bash code blocks.\n"
            "3. Provide a concise explanation first, followed immediately by the command block.\n"
            "4. Use modern, reliable Linux tools (e.g. systemctl, journalctl, ss, ip, find, curl, tar, jq, rsync).\n"
            "5. If a command could cause data loss or service disruption (e.g. rm -rf, dd, mkfs, iptables -F, systemctl stop/restart), clearly include a safety warning.\n"
            "6. Avoid interactive prompts or unexpanded placeholders where a safe default or user context is available."
        )

    def _build_database_system_prompt(self, details: str, active_widget: Any) -> str:
        db_mgr = active_widget if hasattr(active_widget, "query_edit") else getattr(self.main_window, "db_manager_widget", None)
        dialect = "SQL (MySQL / PostgreSQL / SQLite / MariaDB)"
        schema = ""
        table = ""
        if db_mgr:
            backend_func = getattr(db_mgr, "_backend_name", None)
            if callable(backend_func):
                dialect = backend_func()
            elif hasattr(db_mgr, "active_profile") and isinstance(db_mgr.active_profile, dict):
                dialect = db_mgr.active_profile.get("backend", "mysql").upper()
            schema = getattr(db_mgr, "current_schema", "") or ""
            table = getattr(db_mgr, "current_table", "") or ""

        ctx_info = f"Database Dialect: {dialect}."
        if schema:
            ctx_info += f" Current Schema/Database: '{schema}'."
        if table:
            ctx_info += f" Current Table: '{table}'."

        return (
            "You are an expert Database Administrator (DBA) and SQL developer.\n"
            f"Active Context: The user is working in the SQL query editor of the Database tab ({details}).\n"
            f"{ctx_info}\n\n"
            "Instructions:\n"
            "1. Provide accurate, syntactically correct, and optimized SQL queries adhering strictly to the active database dialect.\n"
            "2. Always enclose executable SQL statements in ```sql code blocks.\n"
            "3. Format standard SQL keywords in UPPERCASE (e.g., SELECT, FROM, WHERE, JOIN, GROUP BY, ORDER BY, LIMIT).\n"
            "4. If recommending performance optimizations or schema modifications, explain indexes and query plan implications concisely.\n"
            "5. For destructive queries (UPDATE, DELETE, DROP, TRUNCATE, ALTER), always require or include safe WHERE filters and transaction recommendations.\n"
            "6. Terminate queries with a semicolon (;)."
        )

    def _build_web_system_prompt(self, details: str) -> str:
        return (
            "You are an expert Linux web hosting and reverse proxy administrator.\n"
            f"Active Context: Working in {details}.\n"
            "Provide production-grade configurations and commands for Nginx, Apache, PHP-FPM, Let's Encrypt SSL, and DNS.\n"
            "Wrap configuration snippets or shell commands in ```bash or ```nginx / ```apache code blocks."
        )

    def _build_sysadmin_system_prompt(self, details: str) -> str:
        return (
            "You are an expert Linux system administrator and site reliability engineer (SRE).\n"
            f"Active Context: Working in {details}.\n"
            "Provide precise diagnostics, troubleshooting steps, and shell commands to inspect CPU, RAM, disk I/O, network traffic, and systemd services.\n"
            "Always wrap shell commands in ```bash code blocks."
        )

    def _build_security_system_prompt(self, details: str) -> str:
        return (
            "You are an expert Linux security auditor and hardening specialist.\n"
            f"Active Context: Working in {details}.\n"
            "Provide security inspection commands, firewall configurations (UFW/firewalld/iptables), CVE mitigation steps, and audit commands.\n"
            "Wrap commands in ```bash code blocks."
        )

    def _build_generic_system_prompt(self, details: str) -> str:
        return (
            "You are an intelligent AI system administration and operations copilot for Linux environments.\n"
            f"Active Context: {details}.\n"
            "Help the administrator solve technical challenges concisely with exact, safe commands and clean configurations.\n"
            "Enclose executable commands in markdown code blocks."
        )

    def _build_system_prompt(self) -> str:
        if not self.include_context_chk.isChecked():
            return "You are a helpful AI coding and Linux systems administration copilot. Enclose commands in code blocks."

        ctx_type = self._current_context_info.get("type", "generic")
        details = self._current_context_info.get("details", "")
        widget = self._current_context_info.get("widget", None)

        if ctx_type == "terminal":
            return self._build_terminal_system_prompt(details, widget)
        elif ctx_type == "database":
            return self._build_database_system_prompt(details, widget)
        elif ctx_type == "web":
            return self._build_web_system_prompt(details)
        elif ctx_type == "sysadmin":
            return self._build_sysadmin_system_prompt(details)
        elif ctx_type == "security":
            return self._build_security_system_prompt(details)
        else:
            return self._build_generic_system_prompt(details)

    # =========================================================
    # Code Extraction & Parsing Helpers
    # =========================================================
    @staticmethod
    def _clean_command(content: str) -> str:
        if not content:
            return ""
        text = content.strip()
        blocks = re.findall(r"```[a-zA-Z0-9_+-]*\s*\n?(.*?)```", text, re.DOTALL)
        if blocks:
            return blocks[-1].strip()
        inline = re.findall(r"`([^`\n]+)`", text)
        if inline and len(inline[-1]) > 2:
            return inline[-1].strip()
        return text

    @staticmethod
    def _parse_message_segments(content: str) -> list[tuple[str, str, str]]:
        """
        Parses content into list of (type, lang, text).
        type is either 'text' or 'code'.
        """
        if not content:
            return [("text", "", "")]

        pattern = re.compile(r"```([a-zA-Z0-9_+-]*)\s*\n?(.*?)```", re.DOTALL)
        segments: list[tuple[str, str, str]] = []
        last_end = 0

        for match in pattern.finditer(content):
            start, end = match.span()
            if start > last_end:
                text_part = content[last_end:start].strip()
                if text_part:
                    segments.append(("text", "", text_part))
            lang = match.group(1).strip().lower() or "code"
            code = match.group(2).strip()
            segments.append(("code", lang, code))
            last_end = end

        if last_end < len(content):
            remaining = content[last_end:].strip()
            if remaining:
                segments.append(("text", "", remaining))

        if not segments:
            segments.append(("text", "", content.strip()))

        return segments

    def _extract_latest_command(self) -> str:
        for msg in reversed(self._chat_history):
            if msg.get("role") == "assistant":
                content = msg.get("content", "")
                cmd = self._clean_command(content)
                if cmd:
                    return cmd
        return ""

    # =========================================================
    # Generation (Streaming)
    # =========================================================
    def generate(self):
        task = self.task_input.toPlainText().strip()
        if not task:
            QMessageBox.warning(self, "Validation Error", "Please enter a prompt or question.")
            return

        model = self.model_combo.currentText().strip() or "qwen2.5:7b"

        # Check if server assistant is selected
        if model.startswith(("🔴", "🟣", "🟢")) or (self._server_asst_label and model == self._server_asst_label):
            self._generate_with_server_assistant(task, model)
            return

        # Hide welcome widget on first message
        if hasattr(self, "welcome_widget") and self.welcome_widget.isVisible():
            self.welcome_widget.hide()

        # Add user message to conversation
        user_msg = {
            "role": "user",
            "content": task,
            "timestamp": time.strftime("%H:%M"),
        }
        self._chat_history.append(user_msg)
        self._add_message_bubble(user_msg)

        self.task_input.clear()
        self._set_busy(True)
        self.status_label.setText(f"⏳ Generating with '{model}'...")
        self._start_time = time.time()
        self._token_count = 0
        self._cancel_flag = False

        # Build multi-turn context
        system_prompt = self._build_system_prompt()
        messages = [{"role": "system", "content": system_prompt}]
        for msg in self._chat_history[-10:]:
            messages.append({"role": msg["role"], "content": msg["content"]})

        self.services.emit_log("AI", f"Copilot prompt dispatched ({model}): {task[:100]}", "INFO")

        # Create live streaming card
        self._streaming_widget = _StreamingMessageWidget(model=model, parent_tab=self)
        self._insert_chat_widget(self._streaming_widget)
        self._scroll_to_bottom()

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
        elapsed = time.time() - self._start_time
        if self._streaming_widget:
            self._streaming_widget.append_token(token, elapsed=elapsed, tokens=self._token_count)
            self._scroll_to_bottom()

    def _on_done(self, full_text: str, elapsed: float, tokens: int):
        self._set_busy(False)
        self.status_label.setText("✅ Response complete.")
        speed = (tokens / elapsed) if elapsed > 0 else 0
        stats_str = f"⏱ {elapsed:.1f}s · {tokens} tokens · {speed:.1f} t/s"

        # Remove streaming widget
        if self._streaming_widget:
            self._streaming_widget.deleteLater()
            self._streaming_widget = None

        # Add assistant message to history and feed
        model = self.model_combo.currentText().strip() or "qwen2.5:7b"
        asst_msg = {
            "role": "assistant",
            "content": full_text,
            "timestamp": time.strftime("%H:%M"),
            "model": model,
            "stats": stats_str,
        }
        self._chat_history.append(asst_msg)
        self._add_message_bubble(asst_msg)

        # Update backward-compatibility output edit
        self.output_edit.setPlainText(full_text)
        self._update_action_buttons(bool(full_text.strip()))
        self._scroll_to_bottom()

        self.services.emit_log("AI", f"Copilot response finished in {elapsed:.2f}s ({tokens} tokens)", "SUCCESS")

    def _on_error(self, err: str):
        self._set_busy(False)
        self.status_label.setText("❌ Generation failed. Check if AI endpoint is active.")

        if self._streaming_widget:
            self._streaming_widget.deleteLater()
            self._streaming_widget = None

        err_msg = {
            "role": "assistant",
            "content": f"⚠️ **Could not generate response:**\n{err}\n\nPlease check your AI endpoint settings in ⚙️.",
            "timestamp": time.strftime("%H:%M"),
            "model": "Error",
            "stats": "",
        }
        self._chat_history.append(err_msg)
        self._add_message_bubble(err_msg)
        self._update_action_buttons(False)
        self._scroll_to_bottom()

        self.services.emit_log("AI", f"Copilot generation failed: {err}", "ERROR")

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

    def _insert_chat_widget(self, widget: QWidget):
        count = self.chat_layout.count()
        # Insert before stretch item at the bottom
        insert_idx = max(0, count - 1)
        self.chat_layout.insertWidget(insert_idx, widget)

    def _add_message_bubble(self, msg: dict[str, Any]):
        bubble = _ChatMessageWidget(
            role=msg.get("role", "user"),
            content=msg.get("content", ""),
            timestamp=msg.get("timestamp", ""),
            model=msg.get("model", ""),
            stats=msg.get("stats", ""),
            parent_tab=self,
        )
        self._insert_chat_widget(bubble)

    def _scroll_to_bottom(self):
        QTimer.singleShot(50, lambda: self.chat_scroll.verticalScrollBar().setValue(
            self.chat_scroll.verticalScrollBar().maximum()
        ))

    def _clear_all(self):
        """Clears chat history and restores the clean welcome screen."""
        self._chat_history.clear()
        self.task_input.clear()
        self.output_edit.clear()

        # Remove all message bubbles
        while self.chat_layout.count() > 1:
            item = self.chat_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # Restore welcome widget
        self.welcome_widget = _WelcomeWidget(self)
        self.chat_layout.insertWidget(0, self.welcome_widget)
        self.welcome_widget.show()

        self.status_label.setText("Conversation cleared.")
        self._update_action_buttons(False)

    def copy_output(self):
        cmd = self._extract_latest_command()
        if not cmd and self._chat_history:
            cmd = self._chat_history[-1].get("content", "")
        if cmd:
            QApplication.clipboard().setText(cmd)
            self.status_label.setText("📋 Copied to clipboard.")

    # =========================================================
    # Terminal & Database Direct Execution & Sending
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

    def execute_in_terminal(self, cmd: Optional[str] = None, run: bool = True):
        """
        Executes or inserts command directly into the active or opened terminal tab.
        If no terminal is open, automatically opens a Local Shell tab and sends the command.
        """
        target_cmd = cmd or self._extract_latest_command()
        clean_cmd = self._clean_command(target_cmd) if target_cmd else ""
        if not clean_cmd:
            QMessageBox.warning(self, "Terminal", "No command found to send to terminal.")
            return

        term = self._get_active_terminal()

        # If current tab is not a terminal, search for any open terminal tab
        if not term and hasattr(self.main_window, "tabs"):
            for i in range(self.main_window.tabs.count()):
                w = self.main_window.tabs.widget(i)
                if hasattr(w, "send_text"):
                    term = w
                    self.main_window.tabs.setCurrentIndex(i)
                    break
                elif hasattr(w, "get_all_terminals"):
                    terms = w.get_all_terminals()
                    if terms:
                        term = terms[0]
                        self.main_window.tabs.setCurrentIndex(i)
                        break

        # If no terminal open at all, open a local shell tab
        if not term:
            if self.main_window and hasattr(self.main_window, "add_local_command_tab"):
                term = self.main_window.add_local_command_tab("bash", "Local Shell")
                QTimer.singleShot(400, lambda: term.send_text(clean_cmd + ("\n" if run else "")))
                action_text = "executed" if run else "inserted"
                self.status_label.setText(f"▶ Opened Local Shell and {action_text} command.")
                if hasattr(self.main_window, "_notify"):
                    self.main_window._notify(f"Opened Local Shell and {action_text} command")
                return
            else:
                QMessageBox.information(
                    self, "Terminal Not Found",
                    "No open terminal tab found. Please open a terminal session first."
                )
                return

        # Focus terminal tab
        if hasattr(self.main_window, "tabs"):
            idx = self.main_window.tabs.indexOf(term)
            if idx >= 0:
                self.main_window.tabs.setCurrentIndex(idx)

        if run:
            term.send_text(clean_cmd + "\n")
            self.status_label.setText(f"▶ Executed in terminal: {clean_cmd[:50]}")
            if hasattr(self.main_window, "_notify"):
                self.main_window._notify("Command executed in terminal")
        else:
            term.send_text(clean_cmd)
            self.status_label.setText(f"📥 Inserted into terminal buffer: {clean_cmd[:50]}")
            if hasattr(self.main_window, "_notify"):
                self.main_window._notify("Command inserted into terminal buffer")

    def execute_in_db(self, sql: Optional[str] = None, run: bool = True):
        """
        Executes or pastes SQL query directly into the SQL editor of the Database tab.
        """
        target_sql = sql or self._extract_latest_command()
        clean_sql = self._clean_command(target_sql) if target_sql else ""
        if not clean_sql:
            QMessageBox.warning(self, "Database", "No SQL query found to execute.")
            return

        if not self.main_window:
            return

        db_mgr = getattr(self.main_window, "db_manager_widget", None)
        if not db_mgr and hasattr(self.main_window, "open_db_manager"):
            db_mgr = self.main_window.open_db_manager()

        if not db_mgr:
            QMessageBox.warning(self, "Database", "Database Manager tab not available.")
            return

        # Switch main tabs to Database Manager
        if hasattr(self.main_window, "tabs"):
            idx = self.main_window.tabs.indexOf(db_mgr)
            if idx >= 0:
                self.main_window.tabs.setCurrentIndex(idx)
            elif hasattr(self.main_window, "open_db_manager"):
                self.main_window.open_db_manager()

        if hasattr(db_mgr, "query_edit") and db_mgr.query_edit is not None:
            db_mgr.query_edit.setPlainText(clean_sql)
            if hasattr(db_mgr.query_edit, "setFocus"):
                db_mgr.query_edit.setFocus()

        if run:
            if hasattr(db_mgr, "execute_query"):
                db_mgr.execute_query()
            self.status_label.setText("▶ Executed SQL query in Database Manager.")
            if hasattr(self.main_window, "_notify"):
                self.main_window._notify("SQL query executed in Database Manager")
        else:
            self.status_label.setText("📝 SQL pasted into query editor.")
            if hasattr(self.main_window, "_notify"):
                self.main_window._notify("SQL pasted into Database Manager editor")