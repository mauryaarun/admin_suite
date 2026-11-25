"""
Remote text editor tab with line numbers, search/replace, and syntax
highlighting.
"""
from __future__ import annotations
from typing import Any, Optional

from PyQt6.QtCore import Qt, QRect, QSize, pyqtSignal
from PyQt6.QtGui import (
    QColor,
    QFont,
    QKeySequence,
    QShortcut,          # moved from QtWidgets in PyQt6
    QSyntaxHighlighter,
    QTextCharFormat,
    QTextDocument,
)
from PyQt6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from admin_suite.core.fonts import mono_font
from admin_suite.sftp.models import SftpAction, SftpTask
from admin_suite.sftp.worker import SftpWorker


# --------------------------------------------------------------------------
# Line-number gutter
# --------------------------------------------------------------------------
class _LineNumberArea(QWidget):
    def __init__(self, editor: "CodeEditor"):
        super().__init__(editor)
        self._editor = editor

    def sizeHint(self) -> QSize:
        return QSize(self._editor.line_number_width(), 0)

    def paintEvent(self, event):
        self._editor.paint_line_numbers(event)


class CodeEditor(QPlainTextEdit):
    """QPlainTextEdit with a line-number gutter and active-line highlight."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._ln_area = _LineNumberArea(self)
        self.blockCountChanged.connect(self._update_ln_width)
        self.updateRequest.connect(self._update_ln_area)
        self.cursorPositionChanged.connect(self._highlight_line)
        self._update_ln_width(0)
        self._highlight_line()
        self.setFont(mono_font(11))
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)

    # -- geometry ---------------------------------------------------------
    def line_number_width(self) -> int:
        digits = max(2, len(str(max(1, self.blockCount()))))
        return 14 + digits * self.fontMetrics().horizontalAdvance("9")

    def _update_ln_width(self, _):
        self.setViewportMargins(self.line_number_width(), 0, 0, 0)

    def _update_ln_area(self, rect: QRect, dy: int):
        if dy:
            self._ln_area.scroll(0, dy)
        else:
            self._ln_area.update(
                0, rect.y(), self._ln_area.width(), rect.height()
            )
        if rect.contains(self.viewport().rect()):
            self._update_ln_width(0)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        cr = self.contentsRect()
        self._ln_area.setGeometry(
            QRect(cr.left(), cr.top(), self.line_number_width(), cr.height())
        )

    # -- painting ---------------------------------------------------------
    def paint_line_numbers(self, event):
        painter = None
        from PyQt6.QtGui import QPainter
        painter = QPainter(self._ln_area)
        painter.fillRect(event.rect(), QColor("#1e1e2e"))
        block = self.firstVisibleBlock()
        top = round(
            self.blockBoundingGeometry(block).translated(
                self.contentOffset()
            ).top()
        )
        bottom = top + round(self.blockBoundingRect(block).height())
        painter.setPen(QColor("#6c7086"))
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                painter.drawText(
                    0, top,
                    self._ln_area.width() - 6,
                    self.fontMetrics().height(),
                    Qt.AlignmentFlag.AlignRight,
                    str(block.blockNumber() + 1),
                )
            block = block.next()
            top = bottom
            bottom = top + round(self.blockBoundingRect(block).height())
        painter.end()

    def _highlight_line(self):
        selections = []
        if not self.isReadOnly():
            from PyQt6.QtWidgets import QTextEdit
            sel = QTextEdit.ExtraSelection()
            color = QColor("#2a2b3c")
            sel.format.setBackground(color)
            sel.format.setProperty(
                QTextCharFormat.Property.FullWidthSelection, True
            )
            sel.cursor = self.textCursor()
            sel.cursor.clearSelection()
            selections.append(sel)
        self.setExtraSelections(selections)


# --------------------------------------------------------------------------
# Lightweight syntax highlighting
# --------------------------------------------------------------------------
class BasicHighlighter(QSyntaxHighlighter):
    KEYWORDS = {
        "def", "class", "import", "from", "return", "if", "elif", "else",
        "for", "while", "try", "except", "finally", "with", "as", "lambda",
        "pass", "break", "continue", "global", "nonlocal", "raise", "yield",
        "True", "False", "None", "and", "or", "not", "in", "is",
    }

    def __init__(self, doc: QTextDocument):
        super().__init__(doc)
        self._rules = []

        kw_fmt = QTextCharFormat()
        kw_fmt.setForeground(QColor("#cba6f7"))
        kw_fmt.setFontWeight(QFont.Weight.Bold)
        for kw in self.KEYWORDS:
            self._rules.append((rf"\b{kw}\b", kw_fmt))

        str_fmt = QTextCharFormat()
        str_fmt.setForeground(QColor("#a6e3a1"))
        self._rules.append((r'"[^"\\]*(\\.[^"\\]*)*"', str_fmt))
        self._rules.append((r"'[^'\\]*(\\.[^'\\]*)*'", str_fmt))

        num_fmt = QTextCharFormat()
        num_fmt.setForeground(QColor("#fab387"))
        self._rules.append((r"\b[0-9]+(\.[0-9]+)?\b", num_fmt))

        cmt_fmt = QTextCharFormat()
        cmt_fmt.setForeground(QColor("#6c7086"))
        cmt_fmt.setFontItalic(True)
        self._rules.append((r"#[^\n]*", cmt_fmt))
        self._rules.append((r"//[^\n]*", cmt_fmt))

    def highlightBlock(self, text: str):
        import re
        for pattern, fmt in self._rules:
            for m in re.finditer(pattern, text):
                self.setFormat(m.start(), m.end() - m.start(), fmt)


# --------------------------------------------------------------------------
# Main editor tab
# --------------------------------------------------------------------------
class RemoteEditorTab(QWidget):
    """Edit a remote text file over SFTP with search & info toolbars."""

    def __init__(
        self,
        services,
        host_info: dict[str, Any],
        remote_path: str,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.services = services
        self.host_info = host_info
        self.remote_path = remote_path
        self._loader: Optional[SftpWorker] = None
        self._saver: Optional[SftpWorker] = None
        self._file_info: dict[str, Any] = {}

        theme = self.services.theme.current
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(4)

        # ---- top bar ----
        bar = QHBoxLayout()
        title = QLabel(f"✏️ {remote_path}")
        title.setStyleSheet(f"color:{theme['accent']};font-weight:bold;")
        bar.addWidget(title)
        self.dirty_label = QLabel("")
        self.dirty_label.setStyleSheet(f"color:{theme['warn']};")
        bar.addWidget(self.dirty_label)
        self.info_label = QLabel("")
        self.info_label.setStyleSheet(f"color:{theme['sub']};font-size:11px;")
        bar.addWidget(self.info_label)
        bar.addStretch()
        reload_b = QPushButton("🔄 Reload")
        reload_b.clicked.connect(self.load)
        save = QPushButton("💾 Save (Ctrl+S)")
        save.setShortcut("Ctrl+S")
        save.clicked.connect(self.save)
        bar.addWidget(reload_b)
        bar.addWidget(save)
        lay.addLayout(bar)

        # ---- editor ----
        self.edit = CodeEditor()
        self.edit.textChanged.connect(self._mark_dirty)
        self._highlighter = BasicHighlighter(self.edit.document())
        lay.addWidget(self.edit, 1)

        # ---- search / replace bar (hidden by default) ----
        self.search_bar = QWidget()
        sb = QHBoxLayout(self.search_bar)
        sb.setContentsMargins(0, 0, 0, 0)
        self.search_box = QComboBox()
        self.search_box.setEditable(True)
        self.search_box.setPlaceholderText("Find…")
        self.search_box.setMinimumWidth(220)
        self.search_box.lineEdit().returnPressed.connect(self.find_next)
        sb.addWidget(QLabel("Find:"))
        sb.addWidget(self.search_box, 2)
        self.replace_box = QComboBox()
        self.replace_box.setEditable(True)
        self.replace_box.setPlaceholderText("Replace…")
        self.replace_box.setMinimumWidth(220)
        sb.addWidget(QLabel("Replace:"))
        sb.addWidget(self.replace_box, 2)
        for label, slot in (
            ("Next", self.find_next),
            ("Prev", self.find_prev),
            ("Replace", self.replace_one),
            ("All", self.replace_all),
        ):
            b = QPushButton(label)
            b.clicked.connect(slot)
            sb.addWidget(b)
        close_sb = QPushButton("✕")
        close_sb.setFixedWidth(28)
        close_sb.clicked.connect(lambda: self.search_bar.hide())
        sb.addWidget(close_sb)
        self.search_bar.hide()
        lay.addWidget(self.search_bar)

        # ---- shortcuts ----
        QShortcut(QKeySequence("Ctrl+F"), self, activated=self.show_search)
        QShortcut(QKeySequence("Ctrl+H"), self, activated=self.show_search)
        QShortcut(QKeySequence("Escape"), self.search_bar,
                  activated=self.search_bar.hide)

        self.load()

    # ------------------------------------------------------------------
    def _mark_dirty(self):
        self.dirty_label.setText("● modified")

    def show_search(self):
        self.search_bar.show()
        self.search_box.setFocus()
        self.search_box.lineEdit().selectAll()

    def _make_worker(self) -> SftpWorker:
        h = self.host_info
        return SftpWorker(
            h.get("host", ""), h.get("port", 22), h.get("user", ""),
            h.get("creds"),
            use_agent=h.get("use_agent", False),
            strict_host_keys=h.get("strict_host_keys", False),
        )

    # ------------------------------------------------------------------
    def load(self) -> None:
        self._loader = self._make_worker()
        task = SftpTask(action=SftpAction.READ, remote_path=self.remote_path)
        self._loader.set_task(task)
        self._loader.file_content.connect(self._loaded)
        self._loader.error_occurred.connect(
            lambda e: self.services.notifications.push(
                "error", "Remote Editor", e)
        )
        self._loader.start()

    def _loaded(self, path: str, content: str) -> None:
        self.edit.blockSignals(True)
        self.edit.setPlainText(content)
        self.edit.blockSignals(False)
        self.dirty_label.setText("")
        lines = content.count("\n") + 1
        size = len(content.encode("utf-8", errors="replace"))
        self.info_label.setText(
            f"{lines} lines · {self._fmt(size)} · utf-8"
        )

    @staticmethod
    def _fmt(n: int) -> str:
        for u in ("B", "KB", "MB"):
            if n < 1024:
                return f"{n:.1f}{u}"
            n /= 1024
        return f"{n:.1f}GB"

    def save(self) -> None:
        self._saver = self._make_worker()
        task = SftpTask(
            action=SftpAction.WRITE,
            remote_path=self.remote_path,
            content=self.edit.toPlainText(),
        )
        self._saver.set_task(task)
        self._saver.transfer_complete.connect(
            lambda n, up: (
                self.dirty_label.setText(""),
                self.services.notifications.push(
                    "ok", "Saved", f"{self.remote_path} written"),
            )
        )
        self._saver.error_occurred.connect(
            lambda e: self.services.notifications.push(
                "error", "Save Error", e)
        )
        self._saver.start()

    # ---- search helpers ------------------------------------------------
    def _find(self, backwards=False):
        text = self.search_box.currentText()
        if not text:
            return False
        from PyQt6.QtGui import QTextDocument
        flags = QTextDocument.FindFlag(0)
        if backwards:
            flags |= QTextDocument.FindFlag.FindBackward
        found = self.edit.find(text, flags)
        if not found:
            # wrap around
            cur = self.edit.textCursor()
            cur.movePosition(
                QTextDocument and
                (self.edit.textCursor().MoveOperation.End
                 if not backwards else
                 self.edit.textCursor().MoveOperation.Start)
            )
            self.edit.setTextCursor(cur)
            found = self.edit.find(text, flags)
        return found

    def find_next(self):
        self._remember(self.search_box)
        self._find(False)

    def find_prev(self):
        self._remember(self.search_box)
        self._find(True)

    def replace_one(self):
        self._remember(self.replace_box)
        cur = self.edit.textCursor()
        if cur.hasSelection() and cur.selectedText() == self.search_box.currentText():
            cur.insertText(self.replace_box.currentText())
        self._find(False)

    def replace_all(self):
        self._remember(self.replace_box)
        doc = self.edit.document()
        cur = self.edit.textCursor()
        cur.select(cur.SelectionType.Document)
        self.edit.setTextCursor(cur)
        found = self.edit.find(self.search_box.currentText())
        count = 0
        while found:
            c = self.edit.textCursor()
            c.insertText(self.replace_box.currentText())
            count += 1
            found = self.edit.find(self.search_box.currentText())
        self.services.notifications.push(
            "ok", "Replace All", f"{count} replacement(s) made")

    @staticmethod
    def _remember(box: QComboBox):
        t = box.currentText()
        if t and box.findText(t) == -1:
            box.insertItem(0, t)

    def closeEvent(self, event):
        for attr in ("_loader", "_saver"):
            w = getattr(self, attr, None)
            if w is not None:
                try:
                    if w.isRunning():
                        w.wait(1000)
                except Exception:
                    pass
        event.accept()