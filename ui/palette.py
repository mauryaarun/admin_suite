"""
Smart Fuzzy Command Palette for Admin Suite.

Features:
- Fast fuzzy matching with score ranking and word boundary bonuses
- Categorized action badges (Profiles, DBs, Terminal, System, Tools)
- Keyboard shortcut indicators
- MRU (Most Recently Used) command ranking
- Match highlighting with theme accent
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from PyQt6.QtCore import QRectF, QSize, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QTextDocument
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QStyle,
    QStyledItemDelegate,
    QVBoxLayout,
)

# Persistent in-memory MRU tracking
_RECENT_COMMAND_NAMES: list[str] = []


def fuzzy_score(query: str, target: str) -> tuple[bool, int, list[int]]:
    """
    Subsequence fuzzy match with positional scoring and word-boundary bonuses.
    Returns (matched, score, list_of_matched_indices).
    """
    q = query.strip().lower()
    t = target.lower()

    if not q:
        return True, 0, []

    q_idx = 0
    indices: list[int] = []
    score = 0
    prev_idx = -2
    consecutive = 0

    for i, char in enumerate(t):
        if q_idx < len(q) and char == q[q_idx]:
            indices.append(i)
            # Bonus for word boundary
            is_word_boundary = (i == 0 or target[i - 1] in " ._/-:(@" or target[i].isupper())
            if is_word_boundary:
                score += 40

            # Bonus for consecutive matches
            if i == prev_idx + 1:
                consecutive += 1
                score += 25 * consecutive
            else:
                consecutive = 0

            prev_idx = i
            q_idx += 1

    if q_idx != len(q):
        return False, 0, []

    # Exact or prefix match bonuses
    if t == q:
        score += 350
    elif t.startswith(q):
        score += 180

    # Shorter target penalty (avoids bloated matches)
    score += max(0, 60 - len(target))

    return True, score, indices


def highlight_matches(text: str, indices: list[int], accent_color: str) -> str:
    """Highlight matched characters in HTML with accent color."""
    if not indices:
        return text

    idx_set = set(indices)
    chars = []
    for i, ch in enumerate(text):
        if i in idx_set:
            chars.append(f"<b style='color:{accent_color};'>{ch}</b>")
        else:
            chars.append(ch)
    return "".join(chars)


def infer_category(command: dict[str, Any]) -> str:
    """Infer action category for clear visual grouping."""
    explicit = command.get("category")
    if explicit:
        return explicit

    name = command.get("name", "")
    hint = command.get("hint", "").lower()

    if name.startswith("Connect:"):
        return "Profiles"
    if any(k in name.lower() for k in ("db", "database", "mysql", "postgres", "sqlite")):
        return "Database"
    if any(k in name.lower() for k in ("vpn", "wireguard", "cisco")):
        return "VPN & Mesh"
    if "ansible" in name.lower():
        return "Automation"
    if "terminal" in name.lower() or "shell" in name.lower() or "broadcast" in name.lower():
        return "Terminal"
    if any(k in name.lower() for k in ("vapt", "security", "sysadmin", "web hosting", "forwarding")):
        return "Admin & Security"
    if any(k in name.lower() for k in ("theme", "settings", "key", "config", "snippet", "recording")):
        return "Settings"

    return "General"


class PaletteItemDelegate(QStyledItemDelegate):
    """Custom delegate that renders formatted HTML entries with category badges and shortcuts."""

    def __init__(self, theme: dict[str, str], parent=None):
        super().__init__(parent)
        self.theme = theme

    def sizeHint(self, option, index) -> QSize:
        return QSize(option.rect.width(), 36)

    def paint(self, painter, option, index):
        html = index.data(Qt.ItemDataRole.UserRole + 1)
        if not html:
            super().paint(painter, option, index)
            return

        painter.save()
        rect = option.rect

        # Selection or hover background
        if option.state & QStyle.StateFlag.State_Selected:
            bg_color = QColor(self.theme.get("hover", "#2d3238"))
            painter.fillRect(rect, bg_color)
            # Left accent highlight bar
            accent_bar = QColor(self.theme.get("accent", "#3daee9"))
            painter.fillRect(rect.left(), rect.top(), 3, rect.height(), accent_bar)

        doc = QTextDocument()
        doc.setDefaultFont(option.font)
        doc.setHtml(html)

        # Center vertically
        content_height = doc.size().height()
        y_offset = (rect.height() - content_height) / 2
        painter.translate(rect.left() + 8, rect.top() + max(0, y_offset))
        doc.drawContents(painter, QRectF(0, 0, rect.width() - 16, rect.height()))

        painter.restore()


class CommandPaletteDialog(QDialog):
    """
    Smart fuzzy-style command palette and action launcher.
    """

    def __init__(self, parent, commands: list[dict[str, Any]]):
        super().__init__(parent)

        self.setWindowTitle("Command Palette")
        self.setModal(True)
        self.resize(640, 460)

        self.commands = commands
        self.parent_window = parent

        services = getattr(parent, "services", None)
        self.services = services
        self.theme = services.theme.current if (services and hasattr(services, "theme")) else {}

        bg = self.theme.get("panel", "#232629")
        win_bg = self.theme.get("win", "#1b1e23")
        text_color = self.theme.get("text", "#eff0f1")
        border_color = self.theme.get("border", "#31363b")
        accent_color = self.theme.get("accent", "#3daee9")
        sub_color = self.theme.get("sub", "#95a5a6")

        self.setStyleSheet(
            f"QDialog {{ background: {win_bg}; border: 1px solid {border_color}; border-radius: 6px; }}"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)

        # Search input
        self.input = QLineEdit()
        self.input.setPlaceholderText("Type to search commands, profiles, tools, or shortcuts... (Esc to close)")
        self.input.setStyleSheet(
            f"QLineEdit {{ background: {bg}; color: {text_color}; border: 1px solid {accent_color}; "
            f"border-radius: 5px; font-size: 14px; padding: 8px 12px; }}"
        )
        self.input.textChanged.connect(self.filter)
        layout.addWidget(self.input)

        # Command list
        self.list = QListWidget()
        self.list.setStyleSheet(
            f"QListWidget {{ background: {bg}; border: 1px solid {border_color}; "
            f"border-radius: 4px; outline: none; }} "
            f"QListWidget::item {{ padding: 2px 4px; border-bottom: 1px solid {border_color}; }}"
        )
        self.list.setItemDelegate(PaletteItemDelegate(self.theme, self.list))
        self.list.itemDoubleClicked.connect(lambda: self.run_current())
        layout.addWidget(self.list, 1)

        # Footer bar
        footer = QHBoxLayout()
        self.status_lbl = QLabel(f"{len(self.commands)} commands available")
        self.status_lbl.setStyleSheet(f"color: {sub_color}; font-size: 11px;")

        hint_lbl = QLabel("↑↓ to navigate · Enter to run · Esc to cancel")
        hint_lbl.setStyleSheet(f"color: {sub_color}; font-size: 11px;")

        footer.addWidget(self.status_lbl)
        footer.addStretch()
        footer.addWidget(hint_lbl)
        layout.addLayout(footer)

        self.filter("")
        self.input.setFocus()

    def filter(self, text: str) -> None:
        """Filter and rank commands using fuzzy scoring."""
        self.list.clear()
        query = text.strip()

        scored_items: list[tuple[int, dict[str, Any], list[int]]] = []
        accent = self.theme.get("accent", "#3daee9")
        sub = self.theme.get("sub", "#95a5a6")

        for command in self.commands:
            name = command.get("name", "")
            hint = command.get("hint", "")
            cat = infer_category(command)
            haystack = f"{name} {hint} {cat}"

            matched, score, indices = fuzzy_score(query, name)
            if not matched:
                # Also try matching against the full haystack if name didn't match
                matched_full, score_full, _ = fuzzy_score(query, haystack)
                if matched_full:
                    matched = True
                    score = score_full - 50
                    indices = []

            if not matched:
                continue

            # MRU bonus
            if name in _RECENT_COMMAND_NAMES:
                mru_rank = _RECENT_COMMAND_NAMES.index(name)
                score += max(0, 100 - (mru_rank * 10))

            scored_items.append((score, command, indices))

        # Sort descending by score
        scored_items.sort(key=lambda x: x[0], reverse=True)

        for score, command, indices in scored_items:
            name = command.get("name", "")
            hint = command.get("hint", "")
            category = infer_category(command)

            # Build HTML representation
            highlighted_name = highlight_matches(name, indices, accent) if query else name
            badge_html = (
                f"<span style='background:rgba(61,174,233,0.15); color:{accent}; "
                f"border-radius:3px; padding:2px 6px; font-size:10px; font-weight:bold;'>{category.upper()}</span>"
            )

            shortcut_html = ""
            if hint:
                shortcut_html = (
                    f"<span style='color:{sub}; font-size:11px; "
                    f"background:rgba(255,255,255,0.05); padding:1px 5px; border-radius:3px;'>{hint}</span>"
                )

            html = (
                f"<table width='100%' cellpadding='0' cellspacing='0'>"
                f"<tr>"
                f"<td style='width:90px;'>{badge_html}</td>"
                f"<td style='font-size:13px; font-weight:500;'>{highlighted_name}</td>"
                f"<td align='right'>{shortcut_html}</td>"
                f"</tr>"
                f"</table>"
            )

            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, command)
            item.setData(Qt.ItemDataRole.UserRole + 1, html)
            item.setToolTip(f"{name} — {hint}" if hint else name)
            self.list.addItem(item)

        count = self.list.count()
        self.status_lbl.setText(f"{count} match{'es' if count != 1 else ''}")

        if count > 0:
            self.list.setCurrentRow(0)

    def keyPressEvent(self, event):
        key = event.key()
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.run_current()
        elif key == Qt.Key.Key_Down:
            if self.list.count():
                self.list.setCurrentRow(min(self.list.currentRow() + 1, self.list.count() - 1))
        elif key == Qt.Key.Key_Up:
            if self.list.count():
                self.list.setCurrentRow(max(self.list.currentRow() - 1, 0))
        elif key == Qt.Key.Key_PageDown:
            if self.list.count():
                self.list.setCurrentRow(min(self.list.currentRow() + 8, self.list.count() - 1))
        elif key == Qt.Key.Key_PageUp:
            if self.list.count():
                self.list.setCurrentRow(max(self.list.currentRow() - 8, 0))
        elif key == Qt.Key.Key_Escape:
            self.reject()
        else:
            super().keyPressEvent(event)

    def run_current(self) -> None:
        item = self.list.currentItem()
        if item:
            command = item.data(Qt.ItemDataRole.UserRole)
            if command:
                name = command.get("name", "")
                if name:
                    # Update MRU
                    if name in _RECENT_COMMAND_NAMES:
                        _RECENT_COMMAND_NAMES.remove(name)
                    _RECENT_COMMAND_NAMES.insert(0, name)
                    if len(_RECENT_COMMAND_NAMES) > 20:
                        _RECENT_COMMAND_NAMES.pop()

                self.accept()
                cb = command.get("cb")
                if callable(cb):
                    QTimer.singleShot(50, cb)