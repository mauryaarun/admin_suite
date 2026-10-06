"""
Database session and SSH tunnel management.
"""

from __future__ import annotations

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
    Creates database connections using backend + optional SSH tunnel.
    """

    def __init__(self):
        self.tunnels = TunnelManager()

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
        self.tunnels.stop_all()