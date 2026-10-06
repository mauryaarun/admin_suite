"""
Database export helpers.
"""

from __future__ import annotations

import csv
import json
import os
import sqlite3
import subprocess
try:
    from sshtunnel import SSHTunnelForwarder
except ImportError:
    SSHTunnelForwarder = None

from admin_suite.db.quoting import sql_literal


def export_result_csv(
    path: str,
    headers: list[str],
    rows: list[list[Any]],
) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)

        writer.writerow(headers)

        for row in rows:
            writer.writerow(
                ["" if value is None else value for value in row]
            )


def export_result_json(
    path: str,
    headers: list[str],
    rows: list[list[Any]],
) -> None:
    data = []

    for row in rows:
        item = {}

        for index, header in enumerate(headers):
            value = row[index] if index < len(row) else None

            if isinstance(value, (bytes, bytearray)):
                value = value.hex()

            item[str(header)] = value

        data.append(item)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)


def export_table_sql(
    path: str,
    fqn: str,
    headers: list[str],
    rows: list[list[Any]],
    backend: str,
    quote_ident,
) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            values = ", ".join(
                sql_literal(value, backend) for value in row
            )

            cols = ", ".join(quote_ident(header) for header in headers)

            f.write(f"INSERT INTO {fqn} ({cols}) VALUES ({values});\n")


def export_database(
    cfg: dict[str, Any],
    db_name: str,
    path: str,
) -> None:
    """
    Export a database using native tools where possible.

    MySQL:
        mysqldump

    PostgreSQL:
        pg_dump

    SQLite:
        sqlite3 iterdump
    """
    backend = cfg.get("backend", "mysql")

    tunnel: Optional[SSHTunnelForwarder] = None

    try:
        host = cfg.get("db_host", "127.0.0.1")

        if backend == "mysql":
            default_port = 3306
        elif backend == "postgresql":
            default_port = 5432
        else:
            default_port = 0

        try:
            port = int(cfg.get("db_port", default_port) or default_port)
        except Exception:
            port = default_port

        if backend in ("mysql", "postgresql") and cfg.get("use_tunnel") and cfg.get("ssh_host"):
            try:
                ssh_port = int(cfg.get("ssh_port", 22) or 22)
            except Exception:
                ssh_port = 22

            tunnel = SSHTunnelForwarder(
                (cfg.get("ssh_host", ""), ssh_port),
                ssh_username=cfg.get("ssh_user", ""),
                ssh_password=cfg.get("ssh_pass") or None,
                ssh_pkey=cfg.get("ssh_key_path") or None,
                remote_bind_address=(host, port),
            )

            tunnel.start()

            host = "127.0.0.1"
            port = tunnel.local_bind_port

        env = os.environ.copy()

        if backend == "mysql":
            if cfg.get("db_pass"):
                env["MYSQL_PWD"] = cfg.get("db_pass")

            cmd = [
                "mysqldump",
                f"--host={host}",
                f"--port={port}",
                f"--user={cfg.get('db_user', '')}",
                "--single-transaction",
                "--routines",
                "--triggers",
                db_name,
            ]

            with open(path, "w", encoding="utf-8") as out:
                subprocess.run(
                    cmd,
                    stdout=out,
                    stderr=subprocess.PIPE,
                    env=env,
                    check=True,
                    timeout=600,
                )

        elif backend == "postgresql":
            if cfg.get("db_pass"):
                env["PGPASSWORD"] = cfg.get("db_pass")

            cmd = [
                "pg_dump",
                f"--host={host}",
                f"--port={port}",
                f"--username={cfg.get('db_user', '')}",
                "--dbname",
                db_name,
            ]

            with open(path, "w", encoding="utf-8") as out:
                subprocess.run(
                    cmd,
                    stdout=out,
                    stderr=subprocess.PIPE,
                    env=env,
                    check=True,
                    timeout=600,
                )

        elif backend == "sqlite":
            sqlite_path = cfg.get("sqlite_path", "")

            if not sqlite_path or not os.path.exists(sqlite_path):
                raise Exception(f"SQLite file not found: {sqlite_path}")

            conn = sqlite3.connect(sqlite_path, timeout=15)

            try:
                with open(path, "w", encoding="utf-8") as f:
                    for line in conn.iterdump():
                        f.write(line + "\n")

            finally:
                conn.close()

        else:
            raise Exception(f"Unsupported backend for export: {backend}")

    finally:
        if tunnel:
            try:
                tunnel.stop()
            except Exception:
                pass
