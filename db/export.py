"""
Database export helpers and asynchronous export worker.

Supports table/query exports in CSV, TSV, Excel (XLSX), JSON, JSONL (NDJSON),
SQL INSERT, SQLite DB, Markdown, HTML, and XML, plus full database backups
in SQL, gzipped SQL (.sql.gz), zipped CSVs, zipped JSONs, multi-sheet Excel,
and portable SQLite snapshots — executed asynchronously without blocking the UI.
"""

from __future__ import annotations

import csv
import gzip
import html
import io
import json
import os
import re
import sqlite3
import subprocess
import threading
import time
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any, Callable, Optional

from PyQt6.QtCore import QThread, pyqtSignal

try:
    from sshtunnel import SSHTunnelForwarder
except ImportError:
    SSHTunnelForwarder = None

from admin_suite.db.quoting import qident as _default_qident, sql_literal

try:
    import openpyxl
    from openpyxl.styles import Font as XlFont, PatternFill
    from openpyxl.utils import get_column_letter

    OPENPYXL_AVAILABLE = True
except ImportError:
    openpyxl = None
    OPENPYXL_AVAILABLE = False


TABLE_EXPORT_FORMATS: list[tuple[str, str, str]] = [
    ("csv", "CSV (Comma-Separated Values)", "CSV (*.csv)"),
    ("tsv", "TSV (Tab-Separated Values)", "TSV (*.tsv)"),
    ("xlsx", "Excel Workbook (*.xlsx)", "Excel Workbook (*.xlsx)"),
    ("json", "JSON Array (*.json)", "JSON (*.json)"),
    ("jsonl", "JSON Lines / NDJSON (*.jsonl)", "JSON Lines (*.jsonl *.ndjson)"),
    ("sql", "SQL INSERT Statements (*.sql)", "SQL Files (*.sql)"),
    ("sqlite", "SQLite Database (*.db)", "SQLite Database (*.db *.sqlite)"),
    ("markdown", "Markdown Table (*.md)", "Markdown (*.md)"),
    ("html", "HTML Table Report (*.html)", "HTML Files (*.html)"),
    ("xml", "XML Document (*.xml)", "XML Files (*.xml)"),
]

DATABASE_EXPORT_FORMATS: list[tuple[str, str, str, str]] = [
    ("sql", "SQL Dump (*.sql)", "_dump.sql", "SQL Files (*.sql)"),
    ("sql_gz", "Compressed SQL Dump (*.sql.gz)", "_dump.sql.gz", "Gzipped SQL (*.sql.gz)"),
    ("csv_zip", "ZIP Archive of All Tables (CSV)", "_csv.zip", "ZIP Archive (*.zip)"),
    ("json_zip", "ZIP Archive of All Tables (JSON)", "_json.zip", "ZIP Archive (*.zip)"),
    ("xlsx", "Multi-Sheet Excel Workbook (*.xlsx)", ".xlsx", "Excel Workbook (*.xlsx)"),
    ("sqlite", "Portable SQLite Snapshot (*.db)", ".db", "SQLite Database (*.db)"),
]


def _serialize_cell(value: Any) -> Any:
    if isinstance(value, (bytes, bytearray)):
        return value.hex()
    return value


def _str_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (bytes, bytearray)):
        return value.hex()
    return str(value)


def _safe_filename(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", str(name or "table")).strip("_")
    return cleaned or "table"


def _safe_sheet_title(name: str, used: set[str]) -> str:
    base = re.sub(r"[\\/*?:\[\]]+", "_", str(name or "Sheet")).strip() or "Sheet"
    cand = base[:31]
    idx = 1
    while cand.lower() in used:
        suffix = f"_{idx}"
        cand = base[: 31 - len(suffix)] + suffix
        idx += 1
    used.add(cand.lower())
    return cand


def _safe_sql_col(name: str) -> str:
    return '"' + str(name or "col").replace('"', '""') + '"'


# ---------------------------------------------------------------------------
# Dataset / Table Format Writers
# ---------------------------------------------------------------------------


def export_result_csv(
    path: str,
    headers: list[str],
    rows: list[list[Any]],
    *,
    delimiter: str = ",",
) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, delimiter=delimiter)
        writer.writerow([str(h) for h in headers])
        for row in rows:
            writer.writerow(["" if v is None else _serialize_cell(v) for v in row])


def export_result_tsv(
    path: str,
    headers: list[str],
    rows: list[list[Any]],
) -> None:
    export_result_csv(path, headers, rows, delimiter="\t")


def _rows_to_records(headers: list[str], rows: list[list[Any]]) -> list[dict[str, Any]]:
    data = []
    for row in rows:
        item = {}
        for index, header in enumerate(headers):
            value = row[index] if index < len(row) else None
            item[str(header)] = _serialize_cell(value)
        data.append(item)
    return data


def export_result_json(
    path: str,
    headers: list[str],
    rows: list[list[Any]],
) -> None:
    data = _rows_to_records(headers, rows)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)


def export_result_jsonl(
    path: str,
    headers: list[str],
    rows: list[list[Any]],
) -> None:
    str_headers = [str(h) for h in headers]
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            item = {
                h: _serialize_cell(row[idx] if idx < len(row) else None)
                for idx, h in enumerate(str_headers)
            }
            f.write(json.dumps(item, default=str) + "\n")


def _write_xlsx_sheet(ws, headers: list[str], rows: list[list[Any]]) -> None:
    header_fill = PatternFill(start_color="1E293B", end_color="1E293B", fill_type="solid")
    header_font = XlFont(bold=True, color="F8FAFC")

    widths = [max(len(str(h)), 8) for h in headers] if headers else [10]
    if headers:
        ws.append([str(h) for h in headers])
        for col_idx in range(1, len(headers) + 1):
            cell = ws.cell(row=1, column=col_idx)
            cell.font = header_font
            cell.fill = header_fill
        ws.freeze_panes = "A2"

    for row in rows:
        out_row = []
        for idx, val in enumerate(row):
            if val is None:
                cval: Any = None
            elif isinstance(val, (int, float, bool)):
                cval = val
            elif isinstance(val, (bytes, bytearray)):
                cval = val.hex()
            else:
                cval = str(val)
            out_row.append(cval)
            if idx < len(widths) and cval is not None:
                widths[idx] = min(60, max(widths[idx], len(str(cval)) + 2))
        ws.append(out_row)

    for idx, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(idx)].width = min(60, width + 2)


def export_result_xlsx(
    path: str,
    headers: list[str],
    rows: list[list[Any]],
    *,
    sheet_name: str = "Data",
) -> None:
    if not OPENPYXL_AVAILABLE:
        raise RuntimeError("openpyxl is required for Excel (.xlsx) export.")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = _safe_sheet_title(sheet_name, set())
    _write_xlsx_sheet(ws, headers, rows)
    wb.save(path)


def export_result_markdown(
    path: str,
    headers: list[str],
    rows: list[list[Any]],
) -> None:
    with open(path, "w", encoding="utf-8") as f:
        if not headers:
            return
        f.write("| " + " | ".join(str(h).replace("|", "\\|") for h in headers) + " |\n")
        f.write("| " + " | ".join(["---"] * len(headers)) + " |\n")
        for row in rows:
            cells = [_str_cell(v).replace("\n", " ").replace("|", "\\|") for v in row]
            f.write("| " + " | ".join(cells) + " |\n")


def export_result_html(
    path: str,
    headers: list[str],
    rows: list[list[Any]],
    *,
    title: str = "Exported Table",
) -> None:
    esc_title = html.escape(title)
    with open(path, "w", encoding="utf-8") as f:
        f.write(
            "<!DOCTYPE html>\n<html><head><meta charset='utf-8'>\n"
            f"<title>{esc_title}</title>\n"
            "<style>"
            "body{font-family:system-ui,-apple-system,sans-serif;margin:24px;color:#0f172a;background:#fff;}"
            "h1{font-size:18px;margin-bottom:12px;}"
            "table{border-collapse:collapse;width:100%;font-size:13px;}"
            "th,td{border:1px solid #cbd5e1;padding:6px 10px;text-align:left;vertical-align:top;}"
            "th{background:#1e293b;color:#f8fafc;position:sticky;top:0;}"
            "tr:nth-child(even){background:#f8fafc;}"
            "</style></head><body>\n"
            f"<h1>{esc_title} ({len(rows)} rows)</h1>\n<table><thead><tr>"
        )
        for h in headers:
            f.write(f"<th>{html.escape(str(h))}</th>")
        f.write("</tr></thead><tbody>\n")
        for row in rows:
            f.write("<tr>")
            for idx in range(len(headers)):
                val = row[idx] if idx < len(row) else None
                f.write(f"<td>{html.escape(_str_cell(val))}</td>")
            f.write("</tr>\n")
        f.write("</tbody></table></body></html>\n")


def export_result_xml(
    path: str,
    headers: list[str],
    rows: list[list[Any]],
    *,
    table_name: str = "data",
) -> None:
    root = ET.Element("dataset", attrib={"table": str(table_name), "rows": str(len(rows))})
    for row in rows:
        row_el = ET.SubElement(root, "row")
        for idx, h in enumerate(headers):
            col_el = ET.SubElement(row_el, "column", attrib={"name": str(h)})
            val = row[idx] if idx < len(row) else None
            if val is None:
                col_el.set("null", "true")
            else:
                col_el.text = _str_cell(val)
    tree = ET.ElementTree(root)
    try:
        ET.indent(tree, space="  ")
    except Exception:
        pass
    tree.write(path, encoding="utf-8", xml_declaration=True)


def export_table_sql(
    path: str,
    fqn: str,
    headers: list[str],
    rows: list[list[Any]],
    backend: str,
    quote_ident,
) -> None:
    cols = ", ".join(quote_ident(header) for header in headers)
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            values = ", ".join(sql_literal(value, backend) for value in row)
            f.write(f"INSERT INTO {fqn} ({cols}) VALUES ({values});\n")


def _write_sqlite_table(
    conn: sqlite3.Connection,
    table_name: str,
    headers: list[str],
    rows: list[list[Any]],
) -> None:
    if not headers:
        return
    tbl_q = _safe_sql_col(table_name)
    col_defs = ", ".join(f"{_safe_sql_col(h)} TEXT" for h in headers)
    conn.execute(f"DROP TABLE IF EXISTS {tbl_q};")
    conn.execute(f"CREATE TABLE {tbl_q} ({col_defs});")
    placeholders = ", ".join(["?"] * len(headers))
    col_names = ", ".join(_safe_sql_col(h) for h in headers)
    sql = f"INSERT INTO {tbl_q} ({col_names}) VALUES ({placeholders});"
    batch = []
    for r in rows:
        converted = []
        for idx in range(len(headers)):
            v = r[idx] if idx < len(r) else None
            if v is None or isinstance(v, (str, int, float, bytes)):
                converted.append(v)
            elif isinstance(v, bool):
                converted.append(int(v))
            else:
                converted.append(str(v))
        batch.append(converted)
    if batch:
        conn.executemany(sql, batch)


def export_result_sqlite(
    path: str,
    table_name: str,
    headers: list[str],
    rows: list[list[Any]],
) -> None:
    if os.path.exists(path):
        os.unlink(path)
    conn = sqlite3.connect(path)
    try:
        with conn:
            _write_sqlite_table(conn, table_name or "exported_table", headers, rows)
    finally:
        conn.close()


def export_dataset(
    path: str,
    fmt: str,
    headers: list[str],
    rows: list[list[Any]],
    *,
    table_name: str = "data",
    fqn: Optional[str] = None,
    backend: str = "mysql",
    quote_ident: Optional[Callable[[str], str]] = None,
) -> None:
    """Unified dispatcher for exporting tabular headers/rows into any supported format."""
    norm = str(fmt or "csv").lower().strip()
    qfn = quote_ident or (lambda ident: _default_qident(backend, ident))
    tbl_fqn = fqn or qfn(table_name or "data")

    if norm == "csv":
        export_result_csv(path, headers, rows)
    elif norm == "tsv":
        export_result_tsv(path, headers, rows)
    elif norm == "xlsx":
        export_result_xlsx(path, headers, rows, sheet_name=table_name or "Data")
    elif norm == "json":
        export_result_json(path, headers, rows)
    elif norm in ("jsonl", "ndjson"):
        export_result_jsonl(path, headers, rows)
    elif norm in ("md", "markdown"):
        export_result_markdown(path, headers, rows)
    elif norm == "html":
        export_result_html(path, headers, rows, title=table_name or "Exported Table")
    elif norm == "xml":
        export_result_xml(path, headers, rows, table_name=table_name or "data")
    elif norm == "sql":
        export_table_sql(path, tbl_fqn, headers, rows, backend, qfn)
    elif norm in ("sqlite", "db"):
        export_result_sqlite(path, table_name or "data", headers, rows)
    else:
        raise ValueError(f"Unsupported export format: {fmt}")


# ---------------------------------------------------------------------------
# Database-Level Export (SQL, Gzipped SQL, Multi-Table Archives, XLSX, SQLite)
# ---------------------------------------------------------------------------


def _run_cancellable_dump(
    cmd: list[str],
    env: dict[str, str],
    out_file,
    *,
    cancel_check: Optional[Callable[[], bool]] = None,
    proc_cb: Optional[Callable[[subprocess.Popen], None]] = None,
    timeout: int = 1800,
) -> None:
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    if proc_cb is not None:
        proc_cb(proc)

    stderr_chunks: list[bytes] = []

    def _drain_stderr() -> None:
        if proc.stderr is not None:
            for chunk in iter(lambda: proc.stderr.read(4096), b""):
                stderr_chunks.append(chunk)

    err_thread = threading.Thread(target=_drain_stderr, daemon=True)
    err_thread.start()

    start = time.monotonic()
    try:
        while True:
            if cancel_check is not None and cancel_check():
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                raise RuntimeError("Export cancelled by user.")

            if proc.stdout is not None:
                chunk = proc.stdout.read(65536)
                if chunk:
                    out_file.write(chunk)
                    continue

            if proc.poll() is not None:
                break

            if time.monotonic() - start > timeout:
                proc.kill()
                raise RuntimeError(f"Database dump timed out after {timeout}s.")
            time.sleep(0.05)

        rc = proc.wait()
        err_thread.join(timeout=2)
        if cancel_check is not None and cancel_check():
            raise RuntimeError("Export cancelled by user.")
        if rc != 0:
            err_msg = b"".join(stderr_chunks).decode("utf-8", errors="replace").strip()
            raise RuntimeError(err_msg or f"{cmd[0]} exited with code {rc}")
    finally:
        if proc.stdout is not None:
            try:
                proc.stdout.close()
            except Exception:
                pass
        if proc.stderr is not None:
            try:
                proc.stderr.close()
            except Exception:
                pass


def export_database(
    cfg: dict[str, Any],
    db_name: str,
    path: str,
    *,
    gzip_output: bool = False,
    cancel_check: Optional[Callable[[], bool]] = None,
    proc_cb: Optional[Callable[[subprocess.Popen], None]] = None,
) -> None:
    """Export a full database as SQL (or gzipped SQL) using native dump tools."""
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
            if SSHTunnelForwarder is None:
                raise RuntimeError("sshtunnel package is not installed.")
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
        opener = (lambda p: gzip.open(p, "wb")) if gzip_output else (lambda p: open(p, "wb"))

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
            with opener(path) as out:
                _run_cancellable_dump(
                    cmd,
                    env,
                    out,
                    cancel_check=cancel_check,
                    proc_cb=proc_cb,
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
            with opener(path) as out:
                _run_cancellable_dump(
                    cmd,
                    env,
                    out,
                    cancel_check=cancel_check,
                    proc_cb=proc_cb,
                )

        elif backend == "sqlite":
            sqlite_path = cfg.get("sqlite_path", "")
            if not sqlite_path or not os.path.exists(sqlite_path):
                raise RuntimeError(f"SQLite file not found: {sqlite_path}")
            conn = sqlite3.connect(sqlite_path, timeout=15)
            try:
                if gzip_output:
                    with gzip.open(path, "wt", encoding="utf-8") as f:
                        for line in conn.iterdump():
                            if cancel_check is not None and cancel_check():
                                raise RuntimeError("Export cancelled by user.")
                            f.write(line + "\n")
                else:
                    with open(path, "w", encoding="utf-8") as f:
                        for line in conn.iterdump():
                            if cancel_check is not None and cancel_check():
                                raise RuntimeError("Export cancelled by user.")
                            f.write(line + "\n")
            finally:
                conn.close()
        else:
            raise RuntimeError(f"Unsupported backend for export: {backend}")
    finally:
        if tunnel:
            try:
                tunnel.stop()
            except Exception:
                pass


def _fetch_table_rows(
    cur: Any,
    backend,
    db_name: str,
    table_name: str,
) -> tuple[list[str], list[list[Any]]]:
    backend_name = backend.name
    if backend_name == "sqlite":
        target = backend.q(table_name)
    elif db_name:
        target = f"{backend.q(db_name)}.{backend.q(table_name)}"
    else:
        target = backend.q(table_name)

    sql = f"SELECT * FROM {target};"
    res = backend.run(cur, sql)
    return list(res[0] or []), list(res[1] or [])


def export_database_as(
    cfg: dict[str, Any],
    db_name: str,
    path: str,
    fmt: str = "sql",
    *,
    session_manager: Any = None,
    progress_cb: Optional[Callable[[int, str], None]] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
    proc_cb: Optional[Callable[[subprocess.Popen], None]] = None,
) -> str:
    """
    Export an entire database in any supported format:
    'sql', 'sql_gz', 'csv_zip', 'json_zip', 'xlsx', or 'sqlite'.
    """
    norm = str(fmt or "sql").lower().strip()
    if norm in ("sql", "sql_gz", "sql.gz"):
        if progress_cb:
            progress_cb(-1, f"Dumping database '{db_name}'...")
        export_database(
            cfg,
            db_name,
            path,
            gzip_output=(norm != "sql"),
            cancel_check=cancel_check,
            proc_cb=proc_cb,
        )
        return f"Database '{db_name}' exported ({norm.upper()}) → {path}"

    from admin_suite.db.backends import BACKENDS
    from admin_suite.db.session import DbSessionManager
    from admin_suite.db.worker import _CursorContext, _get_conn_lock

    backend_name = str(cfg.get("backend", "mysql")).lower()
    if backend_name in ("postgres", "postgresql"):
        backend_name = "postgresql"
    backend = BACKENDS.get(backend_name)
    if backend is None:
        raise RuntimeError(f"Unsupported backend: {backend_name}")

    if norm == "xlsx" and not OPENPYXL_AVAILABLE:
        raise RuntimeError("openpyxl is required for Excel (.xlsx) export.")

    own_mgr = session_manager is None
    mgr = session_manager or DbSessionManager()
    conn = None
    has_error = False

    try:
        conn = mgr.acquire(cfg)
        lock = _get_conn_lock(conn)
        with lock, _CursorContext(conn) as cur:
            if db_name and backend_name == "mysql":
                try:
                    cur.execute(f"USE `{str(db_name).replace('`', '``')}`")
                except Exception:
                    pass
            elif db_name and backend_name == "postgresql":
                try:
                    cur.execute(f'SET search_path TO "{str(db_name).replace(chr(34), chr(34) * 2)}"')
                except Exception:
                    pass

            tables = backend.tables(cur, db_name)
            total = len(tables)
            if total == 0:
                raise RuntimeError(f"No tables found in database '{db_name}'.")

            if norm in ("csv_zip", "json_zip"):
                inner_ext = "csv" if norm == "csv_zip" else "json"
                with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                    for idx, tbl in enumerate(tables, start=1):
                        if cancel_check and cancel_check():
                            raise RuntimeError("Export cancelled by user.")
                        if progress_cb:
                            progress_cb(int(((idx - 1) / total) * 100), f"Exporting {tbl} ({idx}/{total})...")
                        headers, rows = _fetch_table_rows(cur, backend, db_name, tbl)
                        buf = io.StringIO()
                        if inner_ext == "csv":
                            w = csv.writer(buf)
                            w.writerow([str(h) for h in headers])
                            for row in rows:
                                w.writerow(["" if v is None else _serialize_cell(v) for v in row])
                        else:
                            json.dump(_rows_to_records(headers, rows), buf, indent=2, default=str)
                        zf.writestr(f"{_safe_filename(tbl)}.{inner_ext}", buf.getvalue().encode("utf-8"))

            elif norm == "xlsx":
                wb = openpyxl.Workbook()
                # Remove initial empty sheet after writing tables
                first = True
                used_names: set[str] = set()
                for idx, tbl in enumerate(tables, start=1):
                    if cancel_check and cancel_check():
                        raise RuntimeError("Export cancelled by user.")
                    if progress_cb:
                        progress_cb(int(((idx - 1) / total) * 100), f"Writing sheet {tbl} ({idx}/{total})...")
                    headers, rows = _fetch_table_rows(cur, backend, db_name, tbl)
                    if first:
                        ws = wb.active
                        ws.title = _safe_sheet_title(tbl, used_names)
                        first = False
                    else:
                        ws = wb.create_sheet(title=_safe_sheet_title(tbl, used_names))
                    _write_xlsx_sheet(ws, headers, rows)
                wb.save(path)

            elif norm in ("sqlite", "db"):
                if os.path.exists(path):
                    os.unlink(path)
                out_conn = sqlite3.connect(path)
                try:
                    with out_conn:
                        for idx, tbl in enumerate(tables, start=1):
                            if cancel_check and cancel_check():
                                raise RuntimeError("Export cancelled by user.")
                            if progress_cb:
                                progress_cb(int(((idx - 1) / total) * 100), f"Exporting table {tbl} ({idx}/{total})...")
                            headers, rows = _fetch_table_rows(cur, backend, db_name, tbl)
                            _write_sqlite_table(out_conn, tbl, headers, rows)
                finally:
                    out_conn.close()
            else:
                raise ValueError(f"Unsupported database export format: {fmt}")

        if progress_cb:
            progress_cb(100, f"Completed ({total} tables)")
        return f"Database '{db_name}' ({total} tables) exported → {path}"
    except Exception:
        has_error = True
        raise
    finally:
        if conn is not None:
            try:
                mgr.release(cfg, conn, has_error=has_error)
            except Exception:
                pass
        if own_mgr:
            try:
                mgr.stop_all()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Asynchronous QThread Export Worker
# ---------------------------------------------------------------------------


class DbExportWorker(QThread):
    """
    Runs database, table, and dataset exports cleanly off the GUI thread.
    """

    progress_changed = pyqtSignal(int, str)
    export_finished = pyqtSignal(str, str)  # path, summary message
    export_failed = pyqtSignal(str)

    def __init__(
        self,
        *,
        mode: str,
        path: str,
        fmt: str,
        cfg: Optional[dict[str, Any]] = None,
        session_manager: Any = None,
        db_name: str = "",
        table_name: str = "",
        fqn: str = "",
        backend: str = "mysql",
        headers: Optional[list[str]] = None,
        rows: Optional[list[list[Any]]] = None,
        parent: Optional[Any] = None,
    ):
        super().__init__(parent)
        self.mode = mode
        self.path = path
        self.fmt = fmt
        self.cfg = cfg or {}
        self.session_manager = session_manager
        self.db_name = db_name
        self.table_name = table_name
        self.fqn = fqn
        self.backend = backend
        self.headers = list(headers or [])
        self.rows = list(rows or [])
        self._cancelled = False
        self._proc: Optional[subprocess.Popen] = None

    def cancel(self) -> None:
        self._cancelled = True
        if self._proc is not None:
            try:
                self._proc.terminate()
            except Exception:
                pass

    def _set_proc(self, proc: subprocess.Popen) -> None:
        self._proc = proc
        if self._cancelled:
            try:
                proc.terminate()
            except Exception:
                pass

    def run(self) -> None:
        try:
            if self.mode == "database":
                summary = export_database_as(
                    self.cfg,
                    self.db_name,
                    self.path,
                    fmt=self.fmt,
                    session_manager=self.session_manager,
                    progress_cb=lambda pct, msg: self.progress_changed.emit(pct, msg),
                    cancel_check=lambda: self._cancelled,
                    proc_cb=self._set_proc,
                )
                self.export_finished.emit(self.path, summary)
                return

            if self.mode == "dataset":
                self.progress_changed.emit(-1, f"Writing {len(self.rows)} rows ({self.fmt.upper()})...")
                export_dataset(
                    self.path,
                    self.fmt,
                    self.headers,
                    self.rows,
                    table_name=self.table_name or "results",
                    fqn=self.fqn or None,
                    backend=self.backend,
                )
                self.export_finished.emit(
                    self.path,
                    f"{len(self.rows)} rows exported ({self.fmt.upper()}) → {self.path}",
                )
                return

            if self.mode == "table":
                from admin_suite.db.backends import BACKENDS
                from admin_suite.db.session import DbSessionManager
                from admin_suite.db.worker import _CursorContext, _get_conn_lock

                bname = str(self.cfg.get("backend", self.backend or "mysql")).lower()
                if bname in ("postgres", "postgresql"):
                    bname = "postgresql"
                backend_impl = BACKENDS.get(bname)
                if backend_impl is None:
                    raise RuntimeError(f"Unsupported backend: {bname}")

                own_mgr = self.session_manager is None
                mgr = self.session_manager or DbSessionManager()
                conn = None
                has_error = False
                try:
                    self.progress_changed.emit(-1, f"Fetching table '{self.table_name}'...")
                    conn = mgr.acquire(self.cfg)
                    lock = _get_conn_lock(conn)
                    with lock, _CursorContext(conn) as cur:
                        if self.db_name and bname == "mysql":
                            try:
                                cur.execute(f"USE `{str(self.db_name).replace('`', '``')}`")
                            except Exception:
                                pass
                        elif self.db_name and bname == "postgresql":
                            try:
                                cur.execute(f'SET search_path TO "{str(self.db_name).replace(chr(34), chr(34) * 2)}"')
                            except Exception:
                                pass

                        headers, rows = _fetch_table_rows(cur, backend_impl, self.db_name, self.table_name)
                        if self._cancelled:
                            raise RuntimeError("Export cancelled by user.")
                        self.progress_changed.emit(60, f"Writing {len(rows)} rows ({self.fmt.upper()})...")
                        export_dataset(
                            self.path,
                            self.fmt,
                            headers,
                            rows,
                            table_name=self.table_name,
                            fqn=self.fqn or None,
                            backend=bname,
                            quote_ident=backend_impl.q,
                        )
                        self.export_finished.emit(
                            self.path,
                            f"{len(rows)} rows from '{self.table_name}' ({self.fmt.upper()}) → {self.path}",
                        )
                except Exception:
                    has_error = True
                    raise
                finally:
                    if conn is not None:
                        try:
                            mgr.release(self.cfg, conn, has_error=has_error)
                        except Exception:
                            pass
                    if own_mgr:
                        try:
                            mgr.stop_all()
                        except Exception:
                            pass
                return

            raise ValueError(f"Unknown DbExportWorker mode: {self.mode}")
        except Exception as exc:
            self.export_failed.emit(str(exc))
