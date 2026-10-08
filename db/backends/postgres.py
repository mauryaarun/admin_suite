"""
PostgreSQL backend.
Supports both psycopg2 and psycopg (v3).
"""

from __future__ import annotations

from typing import Any, Optional

PG_AVAILABLE = False
PG_DRIVER = None

try:
    import psycopg2
    import psycopg2.extras

    PG_AVAILABLE = True
    PG_DRIVER = "psycopg2"
except ImportError:
    try:
        import psycopg
        import psycopg.rows

        PG_AVAILABLE = True
        PG_DRIVER = "psycopg"
    except ImportError:
        psycopg2 = None
        psycopg = None
        PG_AVAILABLE = False
        PG_DRIVER = None


class PGBackend:
    """
    PostgreSQL backend.
    """

    name = "postgresql"

    @staticmethod
    def connect_direct(
        cfg: dict[str, Any],
        host: Optional[str],
        port: Optional[int],
    ):
        if not PG_AVAILABLE:
            raise RuntimeError(
                "PostgreSQL driver is not installed. "
                "Install it with: pip install psycopg2-binary"
            )

        target_host = host or cfg.get("db_host", "127.0.0.1")
        target_port = int(port or cfg.get("db_port", 5432) or 5432)
        target_user = cfg.get("db_user", "")
        target_pass = cfg.get("db_pass", "")
        target_db = cfg.get("db_name") or "postgres"

        if PG_DRIVER == "psycopg":
            import psycopg.rows
            return psycopg.connect(
                host=target_host,
                port=target_port,
                user=target_user,
                password=target_pass,
                dbname=target_db,
                connect_timeout=10,
                row_factory=psycopg.rows.dict_row,
            )

        return psycopg2.connect(
            host=target_host,
            port=target_port,
            user=target_user,
            password=target_pass,
            dbname=target_db,
            connect_timeout=10,
            cursor_factory=psycopg2.extras.RealDictCursor,
        )

    @staticmethod
    def schemas(cur):
        cur.execute(
            """
            SELECT schema_name
            FROM information_schema.schemata
            WHERE schema_name NOT LIKE 'pg_%'
              AND schema_name <> 'information_schema'
            """
        )

        return [row["schema_name"] for row in cur.fetchall()]

    @staticmethod
    def tables(cur, db_name: str):
        cur.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = %s
            ORDER BY table_name
            """,
            (db_name,),
        )

        return [row["table_name"] for row in cur.fetchall()]

    @staticmethod
    def columns(cur, db_name: str, table_name: str):
        cur.execute(
            """
            SELECT kcu.column_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name
             AND tc.table_schema = kcu.table_schema
            WHERE tc.constraint_type = 'PRIMARY KEY'
              AND tc.table_schema = %s
              AND tc.table_name = %s
            """,
            (db_name, table_name),
        )

        pk_cols = {row["column_name"] for row in cur.fetchall()}

        cur.execute(
            """
            SELECT column_name, data_type, is_nullable, column_default
            FROM information_schema.columns
            WHERE table_schema = %s
              AND table_name = %s
            ORDER BY ordinal_position
            """,
            (db_name, table_name),
        )

        out = []

        for row in cur.fetchall():
            out.append(
                {
                    "Field": row["column_name"],
                    "Type": row["data_type"],
                    "Null": row["is_nullable"],
                    "Key": "PRI" if row["column_name"] in pk_cols else "",
                    "Default": row["column_default"],
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

            return headers, [list(row.values()) for row in cur.fetchall()], True

        if hasattr(cur, "connection") and cur.connection:
            cur.connection.commit()

        return [], [], False

    @staticmethod
    def q(ident: str) -> str:
        return '"' + str(ident).replace('"', '""') + '"'
