"""
Database worker thread.
"""

from __future__ import annotations

from typing import Any, Optional

from PyQt6.QtCore import QThread, pyqtSignal

from admin_suite.db.backends import BACKENDS
from admin_suite.db.session import DbSessionManager
import os
import time


def _is_transient_db_error(message: str) -> bool:
    msg = str(message).lower()

    return any(
        x in msg
        for x in (
            "2013",
            "2006",
            "lost connection",
            "gone away",
            "timed out",
            "connection reset",
            "broken pipe",
            "can't connect",
        )
    )


def _is_read_only_query(sql: str) -> bool:
    s = str(sql or "").strip().upper()

    return s.startswith(
        (
            "SELECT",
            "SHOW",
            "DESC",
            "DESCRIBE",
            "EXPLAIN",
        )
    )

class DbWorker(QThread):
    """
    Executes database metadata/query operations in a background thread.
    """

    schemas_loaded = pyqtSignal(list)
    tables_loaded = pyqtSignal(list)
    columns_loaded = pyqtSignal(list)
    data_loaded = pyqtSignal(list, list)
    db_info_loaded = pyqtSignal(dict)
    table_info_loaded = pyqtSignal(dict)
    error_occurred = pyqtSignal(str)

    def __init__(
        self,
        session_manager: DbSessionManager,
        cfg: dict[str, Any],
        mode: str = "fetch_schemas",
        db_name: str = "",
        table_name: str = "",
        query: str = "",
        params: Optional[list[Any]] = None,
    ):
        super().__init__()

        self.session_manager = session_manager
        self.cfg = cfg

        self.mode = mode
        self.db_name = db_name
        self.table_name = table_name
        self.query = query
        self.params = params or []
        
    def run(self) -> None:
        backend = BACKENDS.get(self.cfg.get("backend", "mysql"))

        if not backend:
            self.error_occurred.emit(
                f"Backend '{self.cfg.get('backend')}' not available"
            )
            return

        read_only = (
            self.mode != "run_query"
            or _is_read_only_query(self.query)
        )

        max_attempts = 2 if read_only else 1

        last_error = None

        for attempt in range(1, max_attempts + 1):
            conn = None

            try:
                conn = self.session_manager.connect(self.cfg)

                with conn.cursor() as cur:
                    # Apply schema/database context where appropriate.
                    if self.db_name and backend.name == "mysql":
                        try:
                            safe_db = str(self.db_name).replace("`", "``")
                            cur.execute(f"USE `{safe_db}`")
                        except Exception:
                            pass

                    if self.db_name and backend.name == "postgresql":
                        try:
                            safe_schema = str(self.db_name).replace('"', '""')
                            cur.execute(f'SET search_path TO "{safe_schema}"')
                        except Exception:
                            pass

                    if self.mode == "fetch_schemas":
                        self.schemas_loaded.emit(backend.schemas(cur))
                        return

                    elif self.mode == "fetch_tables":
                        self.tables_loaded.emit(
                            backend.tables(cur, self.db_name)
                        )
                        return

                    elif self.mode == "fetch_columns":
                        self.columns_loaded.emit(
                            backend.columns(cur, self.db_name, self.table_name)
                        )
                        return

                    elif self.mode == "fetch_db_info":
                        info: dict[str, Any] = {}
                        if backend.name == "mysql":
                            cur.execute(
                                "SELECT default_character_set_name, default_collation_name "
                                "FROM information_schema.SCHEMATA WHERE schema_name = %s",
                                (self.db_name,),
                            )
                            row = cur.fetchone()
                            charset = row.get("default_character_set_name", "utf8mb4") if row else "Unknown"
                            collation = row.get("default_collation_name", "") if row else ""

                            cur.execute(
                                "SELECT table_name, engine, table_rows, data_length, index_length, table_collation "
                                "FROM information_schema.TABLES WHERE table_schema = %s ORDER BY table_name",
                                (self.db_name,),
                            )
                            t_rows = cur.fetchall() or []
                            tables_list = []
                            total_data = 0
                            total_index = 0
                            for tr in t_rows:
                                d_len = tr.get("data_length") or 0
                                i_len = tr.get("index_length") or 0
                                total_data += d_len
                                total_index += i_len
                                tables_list.append({
                                    "name": tr.get("table_name", ""),
                                    "engine": tr.get("engine", "InnoDB") or "InnoDB",
                                    "rows": tr.get("table_rows", 0) or 0,
                                    "data_length": d_len,
                                    "index_length": i_len,
                                    "collation": tr.get("table_collation", "") or "",
                                })
                            info = {
                                "type": "database",
                                "backend": "MySQL / MariaDB",
                                "schema_name": self.db_name,
                                "charset": charset,
                                "collation": collation,
                                "table_count": len(tables_list),
                                "total_data_bytes": total_data,
                                "total_index_bytes": total_index,
                                "tables": tables_list,
                            }
                        elif backend.name == "sqlite":
                            cur.execute("SELECT count(*) FROM sqlite_master WHERE type IN ('table', 'view') AND name NOT LIKE 'sqlite_%'")
                            cnt_row = cur.fetchone()
                            cnt = cnt_row[0] if cnt_row else 0
                            cur.execute("SELECT name, type FROM sqlite_master WHERE type IN ('table', 'view') AND name NOT LIKE 'sqlite_%' ORDER BY name")
                            t_rows = cur.fetchall() or []
                            tables_list = [{"name": r[0], "engine": r[1], "rows": 0, "data_length": 0, "index_length": 0, "collation": "BINARY"} for r in t_rows]
                            path = self.cfg.get("sqlite_path", "")
                            size = os.path.getsize(path) if path and os.path.exists(path) else 0
                            info = {
                                "type": "database",
                                "backend": "SQLite",
                                "schema_name": self.db_name or "main",
                                "charset": "UTF-8",
                                "collation": "BINARY",
                                "table_count": cnt,
                                "total_data_bytes": size,
                                "total_index_bytes": 0,
                                "tables": tables_list,
                            }
                        else:
                            cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = %s ORDER BY table_name", (self.db_name,))
                            t_rows = cur.fetchall() or []
                            tables_list = [{"name": r["table_name"] if isinstance(r, dict) else r[0], "engine": "PostgreSQL", "rows": 0, "data_length": 0, "index_length": 0, "collation": "default"} for r in t_rows]
                            info = {
                                "type": "database",
                                "backend": "PostgreSQL",
                                "schema_name": self.db_name,
                                "charset": "UTF-8",
                                "collation": "default",
                                "table_count": len(tables_list),
                                "total_data_bytes": 0,
                                "total_index_bytes": 0,
                                "tables": tables_list,
                            }
                        self.db_info_loaded.emit(info)
                        return

                    elif self.mode == "fetch_table_info":
                        t_info: dict[str, Any] = {}
                        if backend.name == "mysql":
                            safe_db = str(self.db_name).replace("`", "``")
                            safe_table = str(self.table_name).replace("`", "``")
                            cur.execute(f"SHOW TABLE STATUS FROM `{safe_db}` LIKE %s", (self.table_name,))
                            status = cur.fetchone() or {}

                            ddl = ""
                            try:
                                cur.execute(f"SHOW CREATE TABLE `{safe_db}`.`{safe_table}`")
                                ddl_row = cur.fetchone() or {}
                                ddl = ddl_row.get("Create Table") or ddl_row.get("Create View") or (list(ddl_row.values())[-1] if ddl_row else "")
                            except Exception as ddl_err:
                                ddl = f"-- Could not retrieve DDL: {ddl_err}"

                            t_info = {
                                "type": "table",
                                "backend": "MySQL / MariaDB",
                                "schema_name": self.db_name,
                                "table_name": self.table_name,
                                "engine": status.get("Engine", "InnoDB") or "InnoDB",
                                "row_format": status.get("Row_format", "Dynamic") or "Dynamic",
                                "rows": status.get("Rows", 0) or 0,
                                "data_length": status.get("Data_length", 0) or 0,
                                "index_length": status.get("Index_length", 0) or 0,
                                "auto_increment": status.get("Auto_increment"),
                                "collation": status.get("Collation", "") or "",
                                "create_time": str(status.get("Create_time") or ""),
                                "update_time": str(status.get("Update_time") or ""),
                                "comment": status.get("Comment", "") or "",
                                "ddl": ddl,
                            }
                        elif backend.name == "sqlite":
                            cur.execute("SELECT sql FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?", (self.table_name,))
                            row = cur.fetchone()
                            ddl = row[0] if row else ""
                            cnt = 0
                            try:
                                safe_tbl = str(self.table_name).replace('"', '""')
                                cur.execute(f'SELECT count(*) FROM "{safe_tbl}"')
                                cnt_res = cur.fetchone()
                                cnt = cnt_res[0] if cnt_res else 0
                            except Exception:
                                pass
                            t_info = {
                                "type": "table",
                                "backend": "SQLite",
                                "schema_name": self.db_name,
                                "table_name": self.table_name,
                                "engine": "SQLite",
                                "row_format": "SQLite3 B-Tree",
                                "rows": cnt,
                                "data_length": 0,
                                "index_length": 0,
                                "auto_increment": None,
                                "collation": "BINARY",
                                "create_time": "",
                                "update_time": "",
                                "comment": "",
                                "ddl": ddl,
                            }
                        else:
                            t_info = {
                                "type": "table",
                                "backend": "PostgreSQL",
                                "schema_name": self.db_name,
                                "table_name": self.table_name,
                                "engine": "PostgreSQL Heap",
                                "row_format": "Tuple",
                                "rows": 0,
                                "data_length": 0,
                                "index_length": 0,
                                "auto_increment": None,
                                "collation": "default",
                                "create_time": "",
                                "update_time": "",
                                "comment": "",
                                "ddl": f"-- PostgreSQL Table: {self.db_name}.{self.table_name}",
                            }
                        self.table_info_loaded.emit(t_info)
                        return

                    elif self.mode == "run_query":
                        headers, rows, _ = backend.run(
                            cur,
                            self.query,
                            self.params,
                        )

                        self.data_loaded.emit(headers, rows)
                        return

            except Exception as e:
                last_error = e

                if (
                    attempt < max_attempts
                    and read_only
                    and _is_transient_db_error(e)
                ):
                    time.sleep(1.0)
                    continue

                self.error_occurred.emit(str(e))
                return

            finally:
                try:
                    if conn:
                        conn.close()
                except Exception:
                    pass

        if last_error is not None:
            self.error_occurred.emit(str(last_error))