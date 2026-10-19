"""
Database session and SSH tunnel management.
"""

from __future__ import annotations

import threading
from typing import Any

try:
    from sshtunnel import SSHTunnelForwarder
    SSHTUNNEL_AVAILABLE = True
except ImportError:
    SSHTunnelForwarder = None
    SSHTUNNEL_AVAILABLE = False

from admin_suite.db.backends import BACKENDS


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except Exception:
        return default


class TunnelManager:
    """
    Reuses SSH tunnels for database connections.
    """

    def __init__(self):
        self._tunnels: dict[tuple, SSHTunnelForwarder] = {}

    @staticmethod
    def _key(cfg: dict[str, Any]) -> tuple:
        return (
            cfg.get("ssh_host", ""),
            _safe_int(cfg.get("ssh_port", 22), 22),
            cfg.get("ssh_user", ""),
            cfg.get("ssh_key_path", "") or "",
            cfg.get("db_host", ""),
            _safe_int(cfg.get("db_port", 0), 0),
        )

    def get(self, cfg: dict[str, Any]) -> SSHTunnelForwarder:
        key = self._key(cfg)

        tunnel = self._tunnels.get(key)

        if tunnel is not None:
            try:
                if getattr(tunnel, "is_active", True):
                    return tunnel
            except Exception:
                pass

            try:
                tunnel.stop()
            except Exception:
                pass

        if not SSHTUNNEL_AVAILABLE or SSHTunnelForwarder is None:
            raise RuntimeError(
                "SSH tunneling for databases requires the 'sshtunnel' package. "
                "Install it via: pip install sshtunnel"
            )

        ssh_host = cfg.get("ssh_host", "")
        ssh_user = cfg.get("ssh_user", "")
        ssh_pass = cfg.get("ssh_pass") or None
        ssh_key = cfg.get("ssh_key_path") or None

        if ssh_key:
            import os
            ssh_key = os.path.expanduser(ssh_key)
            if not os.path.exists(ssh_key):
                ssh_key = None

        ssh_port = _safe_int(cfg.get("ssh_port", 22), 22)

        remote_host = cfg.get("db_host", "") or "127.0.0.1"
        remote_port = _safe_int(cfg.get("db_port", 0), 0)

        if remote_port <= 0:
            raise ValueError(
                "db_port is required when using an SSH tunnel."
            )

        tunnel_kwargs: dict[str, Any] = {
            "ssh_username": ssh_user,
            "remote_bind_address": (remote_host, remote_port),
        }
        if ssh_key:
            tunnel_kwargs["ssh_pkey"] = ssh_key
            if ssh_pass:
                tunnel_kwargs["ssh_private_key_password"] = ssh_pass
        elif ssh_pass:
            tunnel_kwargs["ssh_password"] = ssh_pass

        tunnel = SSHTunnelForwarder(
            (ssh_host, ssh_port),
            **tunnel_kwargs,
        )

        tunnel.start()

        try:
            transport = getattr(tunnel, "_transport", None)

            if transport is not None:
                transport.set_keepalive(30)

        except Exception:
            pass

        self._tunnels[key] = tunnel

        return tunnel

    def stop_all(self) -> None:
        for tunnel in list(self._tunnels.values()):
            try:
                tunnel.stop()
            except Exception:
                pass

        self._tunnels.clear()


class DbSessionManager:
    """
    Creates and manages persistent database connections using backend + optional SSH tunnel.
    Supports connection pooling, health-checking, and keepalives.
    """

    def __init__(self):
        self.tunnels = TunnelManager()
        self._connections: dict[tuple, Any] = {}
        self._available: dict[tuple, list[Any]] = {}
        self._in_use: dict[tuple, set[Any]] = {}
        self._lock = threading.Lock()

    def _conn_key(self, cfg: dict[str, Any]) -> tuple:
        return (
            cfg.get("backend", "mysql"),
            cfg.get("db_host", ""),
            _safe_int(cfg.get("db_port", 0), 0),
            cfg.get("db_user", ""),
            cfg.get("db_name", ""),
            cfg.get("sqlite_path", ""),
            bool(cfg.get("use_tunnel")),
            cfg.get("ssh_host", ""),
            _safe_int(cfg.get("ssh_port", 22), 22),
        )

    def is_alive(self, conn: Any, backend_name: str) -> bool:
        if conn is None:
            return False
        try:
            if backend_name == "mysql":
                conn.ping(reconnect=False)
                return True
            elif backend_name == "postgresql":
                return getattr(conn, "closed", 1) == 0
            elif backend_name == "sqlite":
                conn.execute("SELECT 1")
                return True
        except Exception:
            return False
        return True

    def acquire(self, cfg: dict[str, Any]):
        """Acquire a live pooled database connection or establish a new one."""
        key = self._conn_key(cfg)
        backend_name = cfg.get("backend", "mysql")
        with self._lock:
            avail = self._available.setdefault(key, [])
            while avail:
                conn = avail.pop()
                if self.is_alive(conn, backend_name):
                    self._in_use.setdefault(key, set()).add(conn)
                    self._connections[key] = conn
                    return conn
                try:
                    conn.close()
                except Exception:
                    pass

            # Connect fresh
            new_conn = self.connect(cfg)
            self._in_use.setdefault(key, set()).add(new_conn)
            self._connections[key] = new_conn
            return new_conn

    def release(self, cfg: dict[str, Any], conn: Any, has_error: bool = False) -> None:
        """Release connection back to pool, discarding if broken."""
        if not conn:
            return
        key = self._conn_key(cfg)
        backend_name = cfg.get("backend", "mysql")
        with self._lock:
            in_use = self._in_use.setdefault(key, set())
            in_use.discard(conn)

            if has_error or not self.is_alive(conn, backend_name):
                if self._connections.get(key) is conn:
                    self._connections.pop(key, None)
                try:
                    conn.close()
                except Exception:
                    pass
            else:
                avail = self._available.setdefault(key, [])
                if len(avail) < 8:
                    avail.append(conn)
                    self._connections[key] = conn
                else:
                    if self._connections.get(key) is conn:
                        self._connections.pop(key, None)
                    try:
                        conn.close()
                    except Exception:
                        pass

    def close_connection(self, cfg: dict[str, Any]) -> None:
        """Close connection for a given configuration."""
        key = self._conn_key(cfg)
        with self._lock:
            self._connections.pop(key, None)
            for conn in self._available.pop(key, []):
                try:
                    conn.close()
                except Exception:
                    pass
            for conn in self._in_use.pop(key, set()):
                try:
                    conn.close()
                except Exception:
                    pass

    def get_session(self, cfg: dict[str, Any]):
        """Helper to get a direct cursor/session for DDL or manual execution."""
        conn = self.acquire(cfg)
        manager = self
        class _DirectSession:
            def __init__(self, c):
                self.conn = c
            def execute(self, sql, params=None):
                cur = self.conn.cursor()
                try:
                    if params:
                        cur.execute(sql, params)
                    else:
                        cur.execute(sql)
                    if hasattr(self.conn, "commit"):
                        self.conn.commit()
                finally:
                    try:
                        cur.close()
                    except Exception:
                        pass
            def close(self):
                manager.release(cfg, self.conn)
            def __enter__(self):
                return self
            def __exit__(self, exc_type, exc_val, exc_tb):
                self.close()
        return _DirectSession(conn)

    def connect(self, cfg: dict[str, Any]):
        backend_name = cfg.get("backend", "mysql")

        backend = BACKENDS.get(backend_name)

        if not backend:
            raise RuntimeError(
                f"Backend '{backend_name}' not available"
            )

        if backend.name == "sqlite":
            return backend.connect_direct(cfg, None, None)

        remote_host = cfg.get("db_host", "") or "127.0.0.1"

        if backend.name == "mysql":
            default_port = 3306
        elif backend.name == "postgresql":
            default_port = 5432
        else:
            default_port = 0

        remote_port = _safe_int(
            cfg.get("db_port", default_port),
            default_port,
        )

        if remote_port <= 0:
            raise ValueError(
                "db_port is required for this database backend."
            )

        host = remote_host
        port = remote_port

        tunnel = None

        if cfg.get("use_tunnel") and cfg.get("ssh_host"):
            try:
                tunnel = self.tunnels.get(cfg)
            except Exception as e:
                raise RuntimeError(
                    "SSH tunnel setup failed.\n"
                    f"SSH host: {cfg.get('ssh_host', '')}\n"
                    f"Tunnel remote target: {remote_host}:{remote_port}\n"
                    f"Error: {e}"
                ) from e

            host = "127.0.0.1"
            port = tunnel.local_bind_port

        try:
            return backend.connect_direct(cfg, host, port)

        except Exception as e:
            msg = str(e).lower()

            if tunnel is not None and any(
                x in msg
                for x in (
                    "refused",
                    "connect failed",
                    "2003",
                    "can't connect",
                    "timed out",
                    "connection reset",
                    "broken pipe",
                )
            ):
                raise RuntimeError(
                    "Database connection through SSH tunnel failed.\n\n"
                    f"SSH host: {cfg.get('ssh_host', '')}\n"
                    f"Tunnel remote target: {remote_host}:{remote_port}\n\n"
                    "The SSH connection succeeded, but the remote TCP target "
                    "refused the connection.\n\n"
                    "This usually means:\n"
                    "1. MySQL/PostgreSQL is not listening on that host/port.\n"
                    "2. DB Host is wrong.\n"
                    "3. The database is inside Docker and the chosen address is not reachable.\n"
                    "4. The SSH host is a bastion and DB Host should be the internal DB address.\n\n"
                    f"Check on the SSH host:\n"
                    f"    ss -lntp | grep {remote_port}\n"
                    f"or:\n"
                    f"    nc -vz {remote_host} {remote_port}\n"
                ) from e

            raise

    def stop_all(self) -> None:
        """Close all pooled database connections and SSH tunnels."""
        with self._lock:
            for conns in list(self._available.values()):
                for conn in conns:
                    try:
                        conn.close()
                    except Exception:
                        pass
            self._available.clear()

            for conns in list(self._in_use.values()):
                for conn in conns:
                    try:
                        conn.close()
                    except Exception:
                        pass
            self._in_use.clear()

            for conn in list(self._connections.values()):
                try:
                    conn.close()
                except Exception:
                    pass
            self._connections.clear()
        self.tunnels.stop_all()