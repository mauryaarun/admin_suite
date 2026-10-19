"""
MySQL backend.
"""

from __future__ import annotations

from typing import Any, Optional

import pymysql


class MySQLBackend:
    """
    MySQL backend using PyMySQL.
    """

    name = "mysql"

    @staticmethod
    def connect_direct(
        cfg: dict[str, Any],
        host: Optional[str],
        port: Optional[int],
    ):
        return pymysql.connect(
            host=host or cfg.get("db_host", "127.0.0.1"),
            port=int(port or cfg.get("db_port", 3306) or 3306),
            user=cfg.get("db_user", ""),
            password=cfg.get("db_pass", ""),
            database=cfg.get("db_name") or None,
            cursorclass=pymysql.cursors.DictCursor,
            connect_timeout=15,
            read_timeout=600,
            write_timeout=600,
            charset="utf8mb4",
        )

    @staticmethod
    def schemas(cur):
        cur.execute("SHOW DATABASES")
        return [list(row.values())[0] for row in cur.fetchall()]

    @staticmethod
    def tables(cur, db_name: str):
        safe_db = str(db_name).replace("`", "``")
        cur.execute(f"SHOW TABLES FROM `{safe_db}`")
        return [list(row.values())[0] for row in cur.fetchall()]

    @staticmethod
    def columns(cur, db_name: str, table_name: str):
        safe_db = str(db_name).replace("`", "``")
        safe_table = str(table_name).replace("`", "``")

        cur.execute(f"SHOW COLUMNS FROM `{safe_db}`.`{safe_table}`")

        return cur.fetchall()

    @staticmethod
    def run(cur, sql: str, params=None):
        if params:
            cur.execute(sql, params)
        else:
            cur.execute(sql)

        if cur.description:
            rows = cur.fetchall()
            headers = [d[0] for d in cur.description]

            return headers, [list(row.values()) if isinstance(row, dict) else list(row) for row in rows], True

        cur.connection.commit()

        return [], [], False

    @staticmethod
    def q(ident: str) -> str:
        return "`" + str(ident).replace("`", "``") + "`"
