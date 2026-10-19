"""
SSH Connection Pool and Session Multiplexer.

Maintains active, persistent SSH transports per remote target to avoid the high latency
and connection spam of renegotiating SSH key-exchanges and authentications for every command.
Channels are multiplexed over the shared transport.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import paramiko

from admin_suite.ssh.client import ssh_kwargs
from admin_suite.ssh.credentials import SshCredentials, profile_creds
from admin_suite.ssh.hostkeys import create_ssh_client


@dataclass
class PooledConnection:
    """Represents an active persistent SSH connection."""
    key: str
    client: paramiko.SSHClient
    jump_client: Optional[paramiko.SSHClient] = None
    created_at: float = field(default_factory=time.time)
    last_used: float = field(default_factory=time.time)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def is_alive(self) -> bool:
        """Check if underlying SSH transport is active."""
        if not self.client:
            return False
        try:
            transport = self.client.get_transport()
            return transport is not None and transport.is_active()
        except Exception:
            return False

    def close(self) -> None:
        """Close SSH client and jump client."""
        for c in (self.client, self.jump_client):
            if c is not None:
                try:
                    c.close()
                except Exception:
                    pass
        self.client = None
        self.jump_client = None


class SshConnectionPool:
    """
    Thread-safe connection pool for SSH sessions.
    Multiplexes remote command execution over active transports.
    """

    _instance: Optional[SshConnectionPool] = None
    _instance_lock = threading.Lock()

    @classmethod
    def instance(cls) -> SshConnectionPool:
        """Singleton accessor for global connection pool."""
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def __init__(self, services: Optional[Any] = None):
        self.services = services
        self._pool: dict[str, PooledConnection] = {}
        self._pool_lock = threading.Lock()

    @staticmethod
    def make_key(profile_or_info: dict[str, Any]) -> str:
        """Generate a deterministic cache key for a host/profile target."""
        host = (
            profile_or_info.get("ssh_host")
            or profile_or_info.get("host")
            or "localhost"
        )
        port = str(profile_or_info.get("ssh_port") or profile_or_info.get("port") or 22)
        user = profile_or_info.get("ssh_user") or profile_or_info.get("user") or ""
        key_path = profile_or_info.get("ssh_key_path") or ""

        jump_host = profile_or_info.get("jump_host") or ""
        jump_port = str(profile_or_info.get("jump_port") or 22)

        return f"{user}@{host}:{port}|key={key_path}|jump={jump_host}:{jump_port}"

    def get_connection(
        self,
        profile_or_info: dict[str, Any],
        strict_host_keys: bool = False,
    ) -> PooledConnection:
        """
        Retrieve an active PooledConnection or establish a new persistent connection.
        """
        key = self.make_key(profile_or_info)

        with self._pool_lock:
            existing = self._pool.get(key)
            if existing and existing.is_alive():
                existing.last_used = time.time()
                return existing
            elif existing:
                existing.close()
                self._pool.pop(key, None)

            # Establish fresh connection under lock
            pooled = self._connect_raw(profile_or_info, key, strict_host_keys)
            self._pool[key] = pooled
            return pooled

    def _connect_raw(
        self,
        profile: dict[str, Any],
        key: str,
        strict_host_keys: bool,
    ) -> PooledConnection:
        """Internal helper to connect Paramiko client and optional jump host."""
        host = profile.get("ssh_host") or profile.get("host", "localhost")
        try:
            port = int(profile.get("ssh_port") or profile.get("port") or 22)
        except Exception:
            port = 22
        user = profile.get("ssh_user") or profile.get("user", "")

        raw_creds = profile.get("creds")
        if isinstance(raw_creds, SshCredentials):
            creds = raw_creds
        else:
            creds = profile_creds(profile)

        use_agent = bool(profile.get("use_agent", False))
        client = create_ssh_client(strict=strict_host_keys)

        kw = ssh_kwargs(
            host=host,
            port=port,
            user=user,
            creds=creds,
            use_agent=use_agent,
        )

        jump_client: Optional[paramiko.SSHClient] = None
        use_jump = bool(profile.get("use_jump", False))
        jump_host = profile.get("jump_host")

        if use_jump and jump_host:
            jump_client = create_ssh_client(strict=strict_host_keys)
            jump_port = int(profile.get("jump_port", 22) or 22)
            jump_user = profile.get("jump_user", "")
            raw_jump_creds = profile.get("jump_creds")
            if isinstance(raw_jump_creds, SshCredentials):
                jump_creds = raw_jump_creds
            else:
                jump_creds = profile_creds({
                    "ssh_pass": profile.get("jump_pass"),
                    "ssh_key_path": profile.get("jump_key_path"),
                    "auth_method": profile.get("jump_auth", "Password"),
                })

            jkw = ssh_kwargs(
                jump_host,
                jump_port,
                jump_user,
                jump_creds,
                use_agent=False,
            )
            jump_client.connect(**jkw)
            transport = jump_client.get_transport()
            if transport:
                sock = transport.open_channel(
                    "direct-tcpip",
                    (host, port),
                    ("127.0.0.1", 0),
                )
                kw["sock"] = sock

        client.connect(**kw)

        # Enable keepalive on the transport to prevent NAT/firewall drops
        transport = client.get_transport()
        if transport:
            try:
                transport.set_keepalive(30)
            except Exception:
                pass

        return PooledConnection(
            key=key,
            client=client,
            jump_client=jump_client,
            created_at=time.time(),
            last_used=time.time(),
        )

    def execute(
        self,
        profile_or_info: dict[str, Any],
        cmd: str,
        timeout: Optional[float] = None,
        sudo_password: Optional[str] = None,
        get_pty: bool = False,
    ) -> tuple[str, str, int]:
        """
        Execute a command on a pooled SSH connection.
        If the connection dropped, automatically reconnects once and retries.
        Returns (stdout, stderr, exit_code).
        """
        strict = bool(profile_or_info.get("strict_host_keys", False))
        max_attempts = 2

        for attempt in range(1, max_attempts + 1):
            pooled = self.get_connection(profile_or_info, strict_host_keys=strict)
            try:
                with pooled.lock:
                    client = pooled.client
                    if not client:
                        raise ConnectionError("Client disconnected")

                    req_pty = bool(sudo_password or get_pty)
                    stdin, stdout, stderr = client.exec_command(
                        cmd,
                        timeout=timeout,
                        get_pty=req_pty,
                    )

                    if sudo_password:
                        try:
                            stdin.write(sudo_password + "\n")
                            stdin.flush()
                        except Exception:
                            pass

                    out = stdout.read().decode("utf-8", errors="replace")
                    err = stderr.read().decode("utf-8", errors="replace")
                    rc = stdout.channel.recv_exit_status()

                    pooled.last_used = time.time()
                    return out, err, rc

            except (paramiko.SSHException, EOFError, OSError) as e:
                # Connection might have timed out on the remote end; evict and retry once
                key = pooled.key
                with self._pool_lock:
                    if self._pool.get(key) is pooled:
                        pooled.close()
                        self._pool.pop(key, None)

                if attempt == max_attempts:
                    raise e
                time.sleep(0.1)

    def close_profile(self, profile_or_name: str | dict[str, Any]) -> None:
        """Close connection corresponding to a profile."""
        with self._pool_lock:
            if isinstance(profile_or_name, dict):
                key = self.make_key(profile_or_name)
                conn = self._pool.pop(key, None)
                if conn:
                    conn.close()
            else:
                name_str = str(profile_or_name)
                keys_to_del = [k for k in self._pool if name_str in k]
                for k in keys_to_del:
                    conn = self._pool.pop(k, None)
                    if conn:
                        conn.close()

    def close_all(self) -> None:
        """Close all pooled connections."""
        with self._pool_lock:
            for conn in list(self._pool.values()):
                conn.close()
            self._pool.clear()

    def evict_idle(self, max_idle_seconds: float = 300) -> None:
        """Evict connections that haven't been used for max_idle_seconds."""
        now = time.time()
        with self._pool_lock:
            for key, conn in list(self._pool.items()):
                if now - conn.last_used > max_idle_seconds:
                    conn.close()
                    self._pool.pop(key, None)


def get_ssh_pool() -> SshConnectionPool:
    """Convenience accessor for global SSH connection pool."""
    return SshConnectionPool.instance()
