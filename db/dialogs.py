"""
Database CRUD dialogs.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt

from PyQt6.QtGui import QColor

from PyQt6.QtWidgets import (
    QDialog,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QHeaderView,
)

from admin_suite.core.fonts import mono_font
from admin_suite.db.quoting import qident


class RecordDialog(QDialog):
    """
    Insert/update record dialog.
    """

    def __init__(
        self,
        parent,
        columns: list[dict],
        mode: str = "insert",
        current_values=None,
    ):
        super().__init__(parent)

        self.columns = columns
        self.mode = mode

        self.setWindowTitle(
            "Insert Record" if mode == "insert" else "Update Record"
        )

        self.setMinimumWidth(460)

        layout = QFormLayout(self)

        self.inputs = []

        for index, col in enumerate(columns):
            inp = QLineEdit()

            nullable = col.get("Null", "") == "YES"

            inp.setPlaceholderText(
                f"{col.get('Type', '')} "
                f"{'(NULL allowed)' if nullable else '(required)'}"
            )

            if current_values and index < len(current_values):
                value = current_values[index]

                if value is None:
                    inp.setText("")
                else:
                    inp.setText(str(value))

            self.inputs.append(inp)

            layout.addRow(f"{col.get('Field', '')}:", inp)

        save = QPushButton("Save")
        save.clicked.connect(self.accept)

        layout.addRow(save)

    def get_values(self):
        """
        Empty fields become NULL.
        """
        return [
            inp.text() if inp.text() != "" else None
            for inp in self.inputs
        ]


class CreateTableDialog(QDialog):
    """
    CREATE TABLE dialog.
    """

    def __init__(self, parent, db_name: str, backend: str):
        super().__init__(parent)

        self.db_name = db_name
        self.backend = backend

        self.setWindowTitle(f"Create Table in '{db_name}'")
        self.setMinimumSize(520, 420)

        layout = QVBoxLayout(self)

        form = QFormLayout()

        self.table_name = QLineEdit()

        form.addRow("Table Name:", self.table_name)

        layout.addLayout(form)

        layout.addWidget(
            QLabel("Columns (name type [NOT NULL] [DEFAULT x], one per line):")
        )

        self.cols_edit = QPlainTextEdit()

        self.cols_edit.setPlaceholderText(
            "id INT AUTO_INCREMENT PRIMARY KEY\n"
            "name VARCHAR(255) NOT NULL\n"
            "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
        )

        self.cols_edit.setFont(mono_font(11))

        layout.addWidget(self.cols_edit, 1)

        button = QPushButton("Generate SQL")
        button.clicked.connect(self.accept)

        layout.addWidget(button)

    def get_sql(self):
        table_name = self.table_name.text().strip()

        if not table_name:
            QMessageBox.warning(self, "Validation", "Table name is required.")
            return None

        cols = self.cols_edit.toPlainText().strip()

        if not cols:
            QMessageBox.warning(
                self,
                "Validation",
                "At least one column is required.",
            )
            return None

        cols_sql = ",\n".join(
            line.strip()
            for line in cols.splitlines()
            if line.strip()
        )

        quoted_db = qident(self.db_name, self.backend)
        quoted_table = qident(table_name, self.backend)

        return f"CREATE TABLE {quoted_db}.{quoted_table} (\n{cols_sql}\n);"
