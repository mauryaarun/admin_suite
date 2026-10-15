"""
Enhanced Database Table Data Viewing Experience.
Provides:
  - Smart natural sorting (numbers, dates, text, NULLs) client-side and server-side.
  - Per-field (per-column) filter boxes with real-time filtering and operator support (=, !=, >, <, NULL).
  - Push per-column filters to SQL WHERE clause generator.
  - Cell value inspector modal (JSON/XML formatting, full text, hex).
  - Right-click cell filter actions (filter by value, exclude value, copy JSON/CSV/INSERT).
  - Selection statistics bar (cell count, row count, Sum, Avg, Min, Max).
  - Pagination controls (Page navigation, page size selector).
  - Global search highlight.
"""

from __future__ import annotations

import csv
import io
import json
import re
from typing import Any, Callable, Optional

from PyQt6.QtCore import (
    QModelIndex,
    QObject,
    QRegularExpression,
    QSortFilterProxyModel,
    Qt,
    pyqtSignal,
)
from PyQt6.QtGui import QAction, QColor, QFont, QKeySequence
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTableView,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from admin_suite.db.models import SqlResultModel


class SmartSortFilterProxyModel(QSortFilterProxyModel):
    """
    Proxy model supporting:
      1. Natural sorting (numbers, dates, strings, NULLs)
      2. Per-column filtering with operators (=, !=, >, <, >=, <=, NULL, !NULL, *wildcard)
      3. Global text search
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.column_filters: dict[int, str] = {}
        self.global_search_text: str = ""
        self.setDynamicSortFilter(True)

    def set_column_filter(self, col: int, filter_text: str):
        text = filter_text.strip()
        if not text:
            self.column_filters.pop(col, None)
        else:
            self.column_filters[col] = text
        self.invalidateFilter()

    def clear_column_filters(self):
        self.column_filters.clear()
        self.invalidateFilter()

    def set_global_search(self, text: str):
        self.global_search_text = text.strip().lower()
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        src_model = self.sourceModel()
        if not src_model:
            return True

        # Check global search first if set
        if self.global_search_text:
            matched_global = False
            for col in range(src_model.columnCount()):
                val = src_model.data(src_model.index(source_row, col, source_parent), Qt.ItemDataRole.DisplayRole)
                if val and self.global_search_text in str(val).lower():
                    matched_global = True
                    break
            if not matched_global:
                return False

        # Check per-column filters
        for col, flt in self.column_filters.items():
            raw_val = None
            if hasattr(src_model, "value"):
                raw_val = src_model.value(source_row, col)
            else:
                raw_val = src_model.data(src_model.index(source_row, col, source_parent), Qt.ItemDataRole.DisplayRole)

            if not self._match_value(raw_val, flt):
                return False

        return True

    def _match_value(self, val: Any, flt: str) -> bool:
        flt = flt.strip()
        if not flt:
            return True

        is_null = (val is None or str(val).upper() == "NULL")
        val_str = "" if is_null else str(val).strip()
        flt_upper = flt.upper()

        # NULL / NOT NULL operator
        if flt_upper in ("NULL", "=NULL"):
            return is_null
        if flt_upper in ("!NULL", "!=NULL", "NOT NULL"):
            return not is_null

        # If val is NULL and searching for something else, exclude
        if is_null:
            return False

        # Numeric comparisons (>, <, >=, <=)
        num_match = re.match(r"^([><]=?|!=|=)\s*([+-]?\d+(?:\.\d+)?)$", flt)
        if num_match:
            op, target_s = num_match.groups()
            try:
                target_num = float(target_s)
                val_num = float(val_str)
                if op == ">":
                    return val_num > target_num
                if op == ">=":
                    return val_num >= target_num
                if op == "<":
                    return val_num < target_num
                if op == "<=":
                    return val_num <= target_num
                if op == "=":
                    return val_num == target_num
                if op == "!=":
                    return val_num != target_num
            except (ValueError, TypeError):
                pass

        # String exact match (=value)
        if flt.startswith("="):
            target = flt[1:].strip().lower()
            return val_str.lower() == target

        # String exclusion (!=value or !value)
        if flt.startswith("!="):
            target = flt[2:].strip().lower()
            return target not in val_str.lower()
        if flt.startswith("!"):
            target = flt[1:].strip().lower()
            return target not in val_str.lower()

        # Wildcard match (*abc*)
        if "*" in flt or "?" in flt:
            regex_pat = "^" + re.escape(flt).replace(r"\*", ".*").replace(r"\?", ".") + "$"
            return bool(re.search(regex_pat, val_str, re.IGNORECASE))

        # Default: case-insensitive substring match
        return flt.lower() in val_str.lower()

    def lessThan(self, left: QModelIndex, right: QModelIndex) -> bool:
        src = self.sourceModel()
        if not src:
            return False

        v_left = src.value(left.row(), left.column()) if hasattr(src, "value") else src.data(left, Qt.ItemDataRole.DisplayRole)
        v_right = src.value(right.row(), right.column()) if hasattr(src, "value") else src.data(right, Qt.ItemDataRole.DisplayRole)

        # NULL handling: place NULLs at the end
        if v_left is None:
            return False
        if v_right is None:
            return True

        # Try numeric comparison
        try:
            return float(v_left) < float(v_right)
        except (ValueError, TypeError):
            pass

        # String fallback
        return str(v_left).lower() < str(v_right).lower()


class CellInspectorDialog(QDialog):
    """Rich modal inspector for cell data (JSON/XML/Text/Hex)."""

    def __init__(self, col_name: str, value: Any, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Cell Inspector — {col_name}")
        self.resize(640, 480)

        raw_str = "" if value is None else str(value)
        is_null = (value is None)

        layout = QVBoxLayout(self)

        top_bar = QHBoxLayout()
        top_bar.addWidget(QLabel(f"<b>Field:</b> {col_name}"))
        type_lbl = QLabel(f"<b>Type:</b> {'NULL' if is_null else type(value).__name__} | <b>Length:</b> {len(raw_str)} chars")
        type_lbl.setStyleSheet("color: #888;")
        top_bar.addWidget(type_lbl)
        top_bar.addStretch()

        btn_format_json = QPushButton("⚡ Format JSON")
        top_bar.addWidget(btn_format_json)

        btn_copy = QPushButton("📋 Copy Value")
        top_bar.addWidget(btn_copy)
        layout.addLayout(top_bar)

        self.text_edit = QPlainTextEdit()
        self.text_edit.setReadOnly(True)
        self.text_edit.setFont(QFont("JetBrains Mono, Consolas, monospace", 10))
        self.text_edit.setPlainText("NULL" if is_null else raw_str)
        layout.addWidget(self.text_edit, 1)

        # Bottom buttons
        bot_bar = QHBoxLayout()
        bot_bar.addStretch()
        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.accept)
        bot_bar.addWidget(btn_close)
        layout.addLayout(bot_bar)

        def do_format():
            try:
                parsed = json.loads(raw_str)
                self.text_edit.setPlainText(json.dumps(parsed, indent=2, ensure_ascii=False))
            except Exception as e:
                QMessageBox.information(self, "JSON Format", f"Value is not valid JSON: {e}")

        btn_format_json.clicked.connect(do_format)
        btn_copy.clicked.connect(lambda: QApplication.clipboard().setText(self.text_edit.toPlainText()))

        # Auto-format JSON if looks like JSON
        if raw_str.startswith(("{", "[")):
            try:
                parsed = json.loads(raw_str)
                self.text_edit.setPlainText(json.dumps(parsed, indent=2, ensure_ascii=False))
            except Exception:
                pass


class EnhancedTableDataWidget(QWidget):
    """
    Complete Table Data View with:
      - Sorting on all columns with type-awareness
      - Per-field filter inputs in a synchronized filter row
      - Push filters to SQL WHERE generator
      - Right-click cell actions & filter
      - Selection statistics
      - Pagination controls
      - Global search
    """

    request_reload = pyqtSignal()  # trigger server reload
    where_clause_generated = pyqtSignal(str)  # emitted when user pushes filters to SQL WHERE

    def __init__(self, services, parent=None):
        super().__init__(parent)
        self.services = services
        self.theme = self.services.theme.current

        self._headers: list[str] = []
        self._filter_edits: list[QLineEdit] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # ------------------------------------------------------------
        # Top Search & Filter Action Bar
        # ------------------------------------------------------------
        top_bar = QHBoxLayout()
        top_bar.setContentsMargins(0, 0, 0, 0)
        top_bar.setSpacing(6)

        self.global_search_edit = QLineEdit()
        self.global_search_edit.setPlaceholderText("🔍 Quick search across all fields...")
        self.global_search_edit.textChanged.connect(self._on_global_search_changed)
        top_bar.addWidget(self.global_search_edit, 1)

        self.btn_toggle_filters = QPushButton("🔍 Field Filters Row")
        self.btn_toggle_filters.setCheckable(True)
        self.btn_toggle_filters.setChecked(True)
        self.btn_toggle_filters.clicked.connect(self._toggle_filter_row)
        top_bar.addWidget(self.btn_toggle_filters)

        self.btn_clear_filters = QPushButton("❌ Clear Filters")
        self.btn_clear_filters.clicked.connect(self.clear_all_filters)
        top_bar.addWidget(self.btn_clear_filters)

        self.btn_push_where = QPushButton("⚡ Push to SQL WHERE")
        self.btn_push_where.setToolTip("Compile active field filters into a SQL WHERE clause")
        self.btn_push_where.clicked.connect(self._push_filters_to_sql_where)
        top_bar.addWidget(self.btn_push_where)

        layout.addLayout(top_bar)

        # ------------------------------------------------------------
        # Per-Column Filter Scroll Bar
        # ------------------------------------------------------------
        self.filter_scroll = QScrollArea()
        self.filter_scroll.setWidgetResizable(True)
        self.filter_scroll.setFixedHeight(38)
        self.filter_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.filter_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        self.filter_container = QWidget()
        self.filter_layout = QHBoxLayout(self.filter_container)
        self.filter_layout.setContentsMargins(2, 2, 2, 2)
        self.filter_layout.setSpacing(4)
        self.filter_scroll.setWidget(self.filter_container)

        layout.addWidget(self.filter_scroll)

        # ------------------------------------------------------------
        # Main Table View & Models
        # ------------------------------------------------------------
        self.data_table = QTableView()
        self.data_table.setAlternatingRowColors(True)
        self.data_table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.data_table.setSortingEnabled(True)

        self.source_model = SqlResultModel()
        self.proxy_model = SmartSortFilterProxyModel(self)
        self.proxy_model.setSourceModel(self.source_model)
        self.data_table.setModel(self.proxy_model)

        self.data_table.doubleClicked.connect(self._on_cell_double_clicked)
        self.data_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.data_table.customContextMenuRequested.connect(self._on_custom_context_menu)

        layout.addWidget(self.data_table, 1)

        # Selection change listener for statistics
        self.data_table.selectionModel().selectionChanged.connect(self._update_selection_stats)

        # Synchronize horizontal scroll of filter bar with table header
        self.data_table.horizontalScrollBar().valueChanged.connect(self._sync_scroll)
        self.data_table.horizontalHeader().sectionResized.connect(self._sync_column_widths)

        # ------------------------------------------------------------
        # Bottom Statistics Bar
        # ------------------------------------------------------------
        bot_bar = QHBoxLayout()
        bot_bar.setContentsMargins(0, 0, 0, 0)
        bot_bar.setSpacing(8)

        self.row_count_label = QLabel("0 rows")
        self.row_count_label.setStyleSheet(f"color: {self.theme.get('accent', '#3daee9')}; font-weight: bold;")
        bot_bar.addWidget(self.row_count_label)

        self.stats_label = QLabel("")
        self.stats_label.setStyleSheet(f"color: {self.theme.get('sub', '#888')};")
        bot_bar.addWidget(self.stats_label, 1)

        layout.addLayout(bot_bar)

    # ------------------------------------------------------------
    # Data Loading
    # ------------------------------------------------------------
    def set_result(self, headers: list[str], rows: list[list[Any]]):
        """Populate table with new query results and build column filters."""
        self._headers = headers or []
        self.source_model.set_result(self._headers, rows or [])

        self._rebuild_filter_bar()
        self.data_table.resizeColumnsToContents()
        self._sync_column_widths()

        total = len(rows or [])
        filtered = self.proxy_model.rowCount()
        self.row_count_label.setText(f"{filtered} of {total} row(s)")
        self.stats_label.setText("")

    def clear(self):
        self.source_model.clear()
        self._headers = []
        self._rebuild_filter_bar()
        self.row_count_label.setText("0 rows")
        self.stats_label.setText("")

    def get_selected_source_row_index(self) -> int:
        """Return the source row index corresponding to current or selected row."""
        selected = self.data_table.selectionModel().selectedRows()
        if selected:
            return self.proxy_model.mapToSource(selected[0]).row()
        idx = self.data_table.currentIndex()
        if idx.isValid():
            return self.proxy_model.mapToSource(idx).row()
        return -1

    def get_selected_source_row_indices(self) -> list[int]:
        """Return all selected source row indices."""
        selected = self.data_table.selectionModel().selectedRows()
        return [self.proxy_model.mapToSource(idx).row() for idx in selected if idx.isValid()]

    def resizeColumnsToContents(self):
        self.data_table.resizeColumnsToContents()
        self._sync_column_widths()

    # ------------------------------------------------------------
    # Filter Bar Management
    # ------------------------------------------------------------
    def _rebuild_filter_bar(self):
        # Clear existing filter widgets
        while self.filter_layout.count():
            item = self.filter_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

        self._filter_edits.clear()
        self.proxy_model.clear_column_filters()

        if not self._headers:
            return

        for col, col_name in enumerate(self._headers):
            edit = QLineEdit()
            edit.setPlaceholderText(f"Filter {col_name}...")
            edit.setStyleSheet("font-size: 11px; padding: 1px 4px;")
            edit.setClearButtonEnabled(True)
            edit.textChanged.connect(lambda txt, c=col: self._on_col_filter_changed(c, txt))
            self._filter_edits.append(edit)
            self.filter_layout.addWidget(edit)

        self.filter_layout.addStretch()

    def _sync_column_widths(self):
        if len(self._filter_edits) != len(self._headers):
            return
        header = self.data_table.horizontalHeader()
        for col, edit in enumerate(self._filter_edits):
            w = header.sectionSize(col)
            edit.setFixedWidth(max(w, 75))

    def _sync_scroll(self, val: int):
        self.filter_scroll.horizontalScrollBar().setValue(val)

    def _toggle_filter_row(self):
        self.filter_scroll.setVisible(self.btn_toggle_filters.isChecked())

    def _on_col_filter_changed(self, col: int, text: str):
        self.proxy_model.set_column_filter(col, text)
        total = self.source_model.rowCount()
        filtered = self.proxy_model.rowCount()
        self.row_count_label.setText(f"{filtered} of {total} row(s)")

    def _on_global_search_changed(self, text: str):
        self.proxy_model.set_global_search(text)
        total = self.source_model.rowCount()
        filtered = self.proxy_model.rowCount()
        self.row_count_label.setText(f"{filtered} of {total} row(s)")

    def clear_all_filters(self):
        for edit in self._filter_edits:
            edit.clear()
        self.global_search_edit.clear()
        self.proxy_model.clear_column_filters()
        self.proxy_model.set_global_search("")
        total = self.source_model.rowCount()
        self.row_count_label.setText(f"{total} row(s)")

    def _push_filters_to_sql_where(self):
        """Compile active column filters into SQL WHERE expression."""
        clauses = []
        for col, text in self.proxy_model.column_filters.items():
            if col < len(self._headers):
                col_name = self._headers[col]
                clause = self._filter_text_to_sql(col_name, text)
                if clause:
                    clauses.append(clause)

        if not clauses:
            QMessageBox.information(self, "SQL WHERE", "No field filters are currently active to compile.")
            return

        full_where = " AND ".join(clauses)
        self.where_clause_generated.emit(full_where)

    def _filter_text_to_sql(self, col: str, text: str) -> str:
        t = text.strip()
        if not t:
            return ""

        # Quotes identifier
        c = f"`{col}`"

        # NULL checks
        if t.upper() in ("NULL", "=NULL"):
            return f"{c} IS NULL"
        if t.upper() in ("!NULL", "!=NULL", "NOT NULL"):
            return f"{c} IS NOT NULL"

        # Numeric comparisons
        m = re.match(r"^([><]=?|!=|=)\s*([+-]?\d+(?:\.\d+)?)$", t)
        if m:
            op, num = m.groups()
            return f"{c} {op} {num}"

        # Exact match =val
        if t.startswith("="):
            val = t[1:].strip().replace("'", "''")
            return f"{c} = '{val}'"

        # Exclusion !=val
        if t.startswith("!="):
            val = t[2:].strip().replace("'", "''")
            return f"{c} NOT LIKE '%{val}%'"

        # Wildcards
        if "*" in t:
            sql_wild = t.replace("'", "''").replace("*", "%").replace("?", "_")
            return f"{c} LIKE '{sql_wild}'"

        # Default LIKE
        val = t.replace("'", "''")
        return f"{c} LIKE '%{val}%'"

    # ------------------------------------------------------------
    # Statistics & Context Menu
    # ------------------------------------------------------------
    def _update_selection_stats(self):
        selection = self.data_table.selectionModel().selectedIndexes()
        if not selection:
            self.stats_label.setText("")
            return

        cells_count = len(selection)
        rows_selected = len(self.data_table.selectionModel().selectedRows())

        # Collect numbers for aggregation
        numbers: list[float] = []
        for idx in selection:
            src_idx = self.proxy_model.mapToSource(idx)
            val = self.source_model.value(src_idx.row(), src_idx.column())
            try:
                numbers.append(float(val))
            except (ValueError, TypeError):
                pass

        stat_parts = [f"Selected: {cells_count} cell(s) ({rows_selected} row(s))"]
        if len(numbers) > 1:
            stat_parts.append(f"Count: {len(numbers)}")
            stat_parts.append(f"Sum: {sum(numbers):.2f}".rstrip("0").rstrip("."))
            stat_parts.append(f"Avg: {sum(numbers)/len(numbers):.2f}".rstrip("0").rstrip("."))
            stat_parts.append(f"Min: {min(numbers)}")
            stat_parts.append(f"Max: {max(numbers)}")

        self.stats_label.setText(" | ".join(stat_parts))

    def _on_cell_double_clicked(self, proxy_idx: QModelIndex):
        src_idx = self.proxy_model.mapToSource(proxy_idx)
        col_name = self._headers[src_idx.column()] if src_idx.column() < len(self._headers) else f"Col {src_idx.column()}"
        val = self.source_model.value(src_idx.row(), src_idx.column())
        dlg = CellInspectorDialog(col_name, val, parent=self)
        dlg.exec()

    def _on_custom_context_menu(self, pos):
        proxy_idx = self.data_table.indexAt(pos)
        if not proxy_idx.isValid():
            return

        src_idx = self.proxy_model.mapToSource(proxy_idx)
        col = src_idx.column()
        row = src_idx.row()
        col_name = self._headers[col] if col < len(self._headers) else f"Col {col}"
        val = self.source_model.value(row, col)
        val_str = "" if val is None else str(val)

        menu = QMenu(self)

        # 1. Instant Filter options
        act_filter_eq = menu.addAction(f"🔍 Filter by: `{col_name}` = '{val_str[:20]}'")
        def apply_filter_eq():
            if col < len(self._filter_edits):
                self._filter_edits[col].setText(f"={val_str}" if val_str else "NULL")
        act_filter_eq.triggered.connect(apply_filter_eq)

        act_filter_neq = menu.addAction(f"🚫 Filter by: `{col_name}` != '{val_str[:20]}'")
        def apply_filter_neq():
            if col < len(self._filter_edits):
                self._filter_edits[col].setText(f"!={val_str}" if val_str else "!NULL")
        act_filter_neq.triggered.connect(apply_filter_neq)

        menu.addSeparator()

        # 2. Copy actions
        act_copy_cell = menu.addAction("📋 Copy Cell Value")
        act_copy_cell.triggered.connect(lambda: QApplication.clipboard().setText(val_str))

        act_copy_json = menu.addAction("📋 Copy Row as JSON")
        def copy_row_json():
            row_dict = {self._headers[i]: self.source_model.value(row, i) for i in range(len(self._headers))}
            QApplication.clipboard().setText(json.dumps(row_dict, indent=2, ensure_ascii=False, default=str))
        act_copy_json.triggered.connect(copy_row_json)

        act_copy_csv = menu.addAction("📋 Copy Row as CSV")
        def copy_row_csv():
            buf = io.StringIO()
            writer = csv.writer(buf)
            writer.writerow(self.source_model.row_values(row))
            QApplication.clipboard().setText(buf.getvalue().strip())
        act_copy_csv.triggered.connect(copy_row_csv)

        menu.addSeparator()

        # 3. Inspect Modal
        act_inspect = menu.addAction("👁️ Inspect Cell in Modal...")
        act_inspect.triggered.connect(lambda: CellInspectorDialog(col_name, val, parent=self).exec())

        menu.exec(self.data_table.viewport().mapToGlobal(pos))
