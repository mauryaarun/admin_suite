"""
SQLite backend.
"""

from __future__ import annotations

import os
import sqlite3
from typing import Any, Optional


class SQLiteBackend:
    """
    SQLite backend.
    """

    name = "sqlite"

    @staticmethod
    def connect_direct(
        cfg: dict[str, Any],
        host: Optional[str],
        port: Optional[int],
    ):
        path = cfg.get("sqlite_path", "")

        if not path or not os.path.exists(path):
            raise Exception(
                f"SQLite file not found: {path}\n"
                "Set it in Connection Manager → Database."
            )

        conn = sqlite3.connect(path, timeout=15, check_same_thread=False)
        conn.row_factory = sqlite3.Row

        return conn

    @staticmethod
    def schemas(cur):
        return ["main"]

    @staticmethod
    def tables(cur, db_name: str):
        cur.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type IN ('table', 'view')
              AND name NOT LIKE 'sqlite_%'
            ORDER BY name
            """
        )

        return [row[0] for row in cur.fetchall()]

    @staticmethod
    def columns(cur, db_name: str, table_name: str):
        safe_table = str(table_name).replace('"', '""')

        cur.execute(f'PRAGMA table_info("{safe_table}")')

        out = []

        for row in cur.fetchall():
            out.append(
                {
                    "Field": row[1],
                    "Type": row[2],
                    "Null": "NO" if row[3] else "YES",
                    "Key": "PRI" if row[5] else "",
                    "Default": row[4],
                    "Extra": "",
                }
            )

        return out

    @staticmethod
    def run(cur, sql: str, params=None):
        if params:
            cur.execute(sql, params)
        else:
            cur.execute(sql)

        if cur.description:
            headers = [d[0] for d in cur.description]

            return headers, [list(row) for row in cur.fetchall()], True

        cur.connection.commit()

        return [], [], False

    @staticmethod
    def q(ident: str) -> str:
        return '"' + str(ident).replace('"', '""') + '"'
