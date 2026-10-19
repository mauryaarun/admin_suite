"""
Database result models.
"""

from __future__ import annotations

import copy
from typing import Any, Optional

from PyQt6.QtCore import QAbstractTableModel, Qt, pyqtSignal
from PyQt6.QtGui import QColor


class SqlResultModel(QAbstractTableModel):
    """
    Lightweight, high-performance table model for SQL results with:
    - Zero-copy data binding for large result sets
    - Inline in-place cell editing with dirty-state tracking
    - Visual dirty highlight (amber tint on modified cells)
    - Batch review and UPDATE SQL generation
    """

    dirty_changed = pyqtSignal(int)  # emits number of currently dirty cells

    def __init__(
        self,
        headers: Optional[list[str]] = None,
        rows: Optional[list[list[Any]]] = None,
        parent=None,
    ):
        super().__init__(parent)

        self._headers = headers or []
        self._rows = [list(r) for r in (rows or [])]
        self._base_rows = [list(r) for r in self._rows]
        self._dirty_cells: dict[tuple[int, int], tuple[Any, Any]] = {}
        self._editable = False

    @property
    def editable(self) -> bool:
        return self._editable

    @editable.setter
    def editable(self, val: bool) -> None:
        self.set_editable(val)

    def set_editable(self, enabled: bool) -> None:
        if self._editable != enabled:
            self._editable = enabled
            if self.rowCount() > 0 and self.columnCount() > 0:
                top_left = self.index(0, 0)
                bottom_right = self.index(self.rowCount() - 1, self.columnCount() - 1)
                self.dataChanged.emit(top_left, bottom_right)

    def rowCount(self, parent=None) -> int:
        return len(self._rows)

    def columnCount(self, parent=None) -> int:
        return len(self._headers)

    def flags(self, index):
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags

        base_flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if self._editable:
            base_flags |= Qt.ItemFlag.ItemIsEditable
        return base_flags

    def data(self, index, role):
        if not index.isValid():
            return None

        row = index.row()
        col = index.column()

        if role == Qt.ItemDataRole.EditRole:
            try:
                val = self._rows[row][col]
                return "" if val is None else str(val)
            except IndexError:
                return ""

        if role == Qt.ItemDataRole.DisplayRole:
            try:
                value = self._rows[row][col]
            except IndexError:
                return None

            if value is None:
                return "NULL"
            return str(value)

        if role == Qt.ItemDataRole.BackgroundRole:
            if (row, col) in self._dirty_cells:
                # Subtle amber tint for dirty cells
                return QColor(245, 158, 11, 65)
            return None

        if role == Qt.ItemDataRole.ToolTipRole:
            if (row, col) in self._dirty_cells:
                orig, current = self._dirty_cells[(row, col)]
                return f"Modified: '{orig}' → '{current}' (Unsaved)"
            return None

        return None

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole) -> bool:
        if not index.isValid() or not self._editable:
            return False

        if role not in (Qt.ItemDataRole.EditRole, Qt.ItemDataRole.DisplayRole):
            return False

        row = index.row()
        col = index.column()

        try:
            current_val = self._rows[row][col]
            base_val = self._base_rows[row][col]
        except IndexError:
            return False

        # Parse special representations
        new_val: Any = value
        if isinstance(value, str):
            if value.strip().upper() == "NULL":
                new_val = None
            elif current_val is not None and isinstance(current_val, int):
                try:
                    new_val = int(value)
                except ValueError:
                    pass
            elif current_val is not None and isinstance(current_val, float):
                try:
                    new_val = float(value)
                except ValueError:
                    pass

        if new_val == current_val:
            return True

        self._rows[row][col] = new_val

        # Compare with base original value
        if new_val == base_val:
            self._dirty_cells.pop((row, col), None)
        else:
            self._dirty_cells[(row, col)] = (base_val, new_val)

        self.dataChanged.emit(
            index,
            index,
            [
                Qt.ItemDataRole.DisplayRole,
                Qt.ItemDataRole.EditRole,
                Qt.ItemDataRole.BackgroundRole,
                Qt.ItemDataRole.ToolTipRole,
            ],
        )
        self.dirty_changed.emit(len(self._dirty_cells))
        return True

    def headerData(self, section, orientation, role):
        if role != Qt.ItemDataRole.DisplayRole:
            return None

        if orientation == Qt.Orientation.Horizontal:
            try:
                return str(self._headers[section])
            except IndexError:
                return None

        return str(section + 1)

    def set_result(
        self,
        headers: list[str],
        rows: list[list[Any]],
    ) -> None:
        self.beginResetModel()
        self._headers = list(headers or [])
        self._rows = [list(r) for r in (rows or [])]
        self._base_rows = [list(r) for r in self._rows]
        self._dirty_cells.clear()
        self.endResetModel()
        self.dirty_changed.emit(0)

    def clear(self) -> None:
        self.set_result([], [])

    def value(self, row: int, column: int) -> Any:
        try:
            return self._rows[row][column]
        except IndexError:
            return None

    def row_values(self, row: int) -> list[Any]:
        try:
            return list(self._rows[row])
        except IndexError:
            return []

    def is_dirty(self) -> bool:
        return len(self._dirty_cells) > 0

    def dirty_count(self) -> int:
        return len(self._dirty_cells)

    def get_dirty_changes(self) -> list[dict[str, Any]]:
        """
        Returns list of dirty cell changes:
        [{
            'row': r,
            'col': c,
            'column_name': name,
            'old_value': orig,
            'new_value': current,
            'row_data': [...]
        }]
        """
        changes = []
        for (r, c), (orig, current) in self._dirty_cells.items():
            col_name = self._headers[c] if c < len(self._headers) else f"Col_{c}"
            changes.append({
                "row": r,
                "col": c,
                "column_name": col_name,
                "old_value": orig,
                "new_value": current,
                "row_data": list(self._rows[r]) if r < len(self._rows) else [],
            })
        return changes

    def revert_changes(self) -> None:
        """Discard all pending cell edits and restore original values."""
        if not self._dirty_cells:
            return

        self.beginResetModel()
        self._rows = [list(r) for r in self._base_rows]
        self._dirty_cells.clear()
        self.endResetModel()
        self.dirty_changed.emit(0)

    def commit_changes_to_base(self) -> None:
        """Mark current edits as clean base state."""
        self._base_rows = [list(r) for r in self._rows]
        self._dirty_cells.clear()
        if self.rowCount() > 0 and self.columnCount() > 0:
            top_left = self.index(0, 0)
            bottom_right = self.index(self.rowCount() - 1, self.columnCount() - 1)
            self.dataChanged.emit(top_left, bottom_right, [Qt.ItemDataRole.BackgroundRole])
        self.dirty_changed.emit(0)

    def generate_update_sql(
        self,
        table_name: str,
        pk_column: Optional[str] = None,
        quote_char: str = '"',
    ) -> list[str]:
        """
        Generate atomic SQL UPDATE statements for all modified rows.
        Groups multiple dirty cells in the same row into a single UPDATE statement.
        """
        if not self._dirty_cells or not table_name:
            return []

        # Find PK column index
        pk_idx = -1
        if pk_column:
            for idx, h in enumerate(self._headers):
                if str(h).lower() == str(pk_column).lower():
                    pk_idx = idx
                    break

        # If no PK specified, default to first column if named 'id' or first column
        if pk_idx == -1:
            for idx, h in enumerate(self._headers):
                if str(h).lower() in ("id", f"{table_name}_id", "pk"):
                    pk_idx = idx
                    break
            if pk_idx == -1 and self._headers:
                pk_idx = 0

        # Group by row
        rows_map: dict[int, dict[int, Any]] = {}
        for (r, c), (_orig, new_val) in self._dirty_cells.items():
            if r not in rows_map:
                rows_map[r] = {}
            rows_map[r][c] = new_val

        statements = []
        for r, col_updates in rows_map.items():
            set_clauses = []
            for c, val in col_updates.items():
                col_name = self._headers[c]
                quoted_col = f"{quote_char}{col_name}{quote_char}"
                if val is None:
                    val_str = "NULL"
                elif isinstance(val, (int, float)):
                    val_str = str(val)
                else:
                    escaped = str(val).replace("'", "''")
                    val_str = f"'{escaped}'"
                set_clauses.append(f"{quoted_col} = {val_str}")

            # WHERE clause based on PK or all original base row columns
            if pk_idx >= 0 and pk_idx < len(self._headers):
                pk_name = self._headers[pk_idx]
                quoted_pk = f"{quote_char}{pk_name}{quote_char}"
                pk_val = self._base_rows[r][pk_idx]
                if pk_val is None:
                    where_clause = f"{quoted_pk} IS NULL"
                elif isinstance(pk_val, (int, float)):
                    where_clause = f"{quoted_pk} = {pk_val}"
                else:
                    escaped_pk = str(pk_val).replace("'", "''")
                    where_clause = f"{quoted_pk} = '{escaped_pk}'"
            else:
                where_parts = []
                for c_idx, h_name in enumerate(self._headers):
                    orig_val = self._base_rows[r][c_idx]
                    q_col = f"{quote_char}{h_name}{quote_char}"
                    if orig_val is None:
                        where_parts.append(f"{q_col} IS NULL")
                    elif isinstance(orig_val, (int, float)):
                        where_parts.append(f"{q_col} = {orig_val}")
                    else:
                        esc = str(orig_val).replace("'", "''")
                        where_parts.append(f"{q_col} = '{esc}'")
                where_clause = " AND ".join(where_parts)

            quoted_table = f"{quote_char}{table_name}{quote_char}"
            stmt = f"UPDATE {quoted_table} SET {', '.join(set_clauses)} WHERE {where_clause};"
            statements.append(stmt)

        return statements

