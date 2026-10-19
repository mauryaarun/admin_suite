"""
Redis & KeyDB NoSQL Backend for Admin Suite Database Manager.
Supports:
  - Direct connection to Redis / KeyDB servers (standalone, authentication, SSL, databases 0-15)
  - Key space inspection (KEYS, SCAN, namespaces)
  - Key details & schemas (TTL, Type, Size, Value)
  - Interactive Redis command runner (GET, SET, HGETALL, INFO, LRANGE, etc.)
  - Tabular formatting for GUI consumption
"""

from __future__ import annotations

import shlex
from typing import Any, Optional

try:
    import redis
    REDIS_AVAILABLE = True
except ImportError:
    redis = None  # type: ignore
    REDIS_AVAILABLE = False


class RedisCursorWrapper:
    """
    Adapter giving redis.Redis connection a DB-API cursor-like interface
    for compatibility with DbWorker and DbSessionManager.
    """

    def __init__(self, client: Any):
        self.client = client
        self.description = None
        self._last_result: list[list[Any]] = []
        self._last_headers: list[str] = []

    def execute(self, command: str, params: Optional[list[Any]] = None) -> None:
        cmd_str = command.strip()
        if not cmd_str:
            self.description = None
            self._last_result = []
            self._last_headers = []
            return

        parts = shlex.split(cmd_str)
        cmd_name = parts[0].lower()
        args = parts[1:]

        # Handle 'SELECT <db>'
        if cmd_name == "select" and args:
            try:
                db_idx = int(args[0].replace("db", ""))
                self.client.select(db_idx)
                self._last_headers = ["Status"]
                self._last_result = [["OK"]]
                self.description = [("Status",)]
                return
            except Exception as exc:
                raise ValueError(f"Invalid database index: {exc}")

        # Execute command on redis client
        func = getattr(self.client, cmd_name, None)
        if not func or not callable(func):
            # Try raw execute_command
            res = self.client.execute_command(cmd_name.upper(), *args)
        else:
            res = func(*args)

        # Format output into tabular representation
        if isinstance(res, dict):
            self._last_headers = ["Key / Field", "Value"]
            self._last_result = [[str(k), str(v)] for k, v in res.items()]
        elif isinstance(res, (list, tuple, set)):
            self._last_headers = ["Index", "Value"]
            self._last_result = [[str(i), str(val)] for i, val in enumerate(res)]
        elif res is None:
            self._last_headers = ["Result"]
            self._last_result = [["(nil)"]]
        else:
            self._last_headers = ["Result"]
            self._last_result = [[str(res)]]

        self.description = [(h,) for h in self._last_headers]

    def fetchall(self) -> list[list[Any]]:
        return self._last_result

    def fetchone(self) -> Optional[list[Any]]:
        return self._last_result[0] if self._last_result else None

    def close(self) -> None:
        pass


class RedisConnectionWrapper:
    """
    Adapter giving redis.Redis client a connection-like interface with cursor() support.
    """

    def __init__(self, client: Any):
        self.client = client

    def cursor(self) -> RedisCursorWrapper:
        return RedisCursorWrapper(self.client)

    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        pass

    def close(self) -> None:
        if hasattr(self.client, "close"):
            self.client.close()


class RedisBackend:
    """
    Redis / KeyDB backend adapter for Admin Suite.
    """

    name = "redis"

    @staticmethod
    def connect_direct(
        cfg: dict[str, Any],
        host: Optional[str] = None,
        port: Optional[int] = None,
    ) -> RedisConnectionWrapper:
        if not REDIS_AVAILABLE:
            raise RuntimeError(
                "Redis python library is not installed. Run 'pip install redis' to enable Redis / KeyDB support."
            )

        actual_host = host or cfg.get("host") or cfg.get("redis_host") or "127.0.0.1"
        actual_port = int(port or cfg.get("port") or cfg.get("redis_port") or 6379)
        password = cfg.get("password") or cfg.get("redis_password") or None
        db_num = int(cfg.get("database") or cfg.get("redis_db") or 0)
        ssl_enabled = bool(cfg.get("ssl", False))

        client = redis.Redis(
            host=actual_host,
            port=actual_port,
            db=db_num,
            password=password,
            ssl=ssl_enabled,
            decode_responses=True,
            socket_connect_timeout=6,
            socket_timeout=15,
        )

        # Verify connectivity
        client.ping()
        return RedisConnectionWrapper(client)

    @staticmethod
    def schemas(cur: RedisCursorWrapper) -> list[str]:
        """Return list of standard Redis databases (db0 - db15)."""
        return [f"db{i}" for i in range(16)]

    @staticmethod
    def tables(cur: RedisCursorWrapper, db_name: str) -> list[str]:
        """
        List active keys or key patterns in current database.
        Switches to db_name if needed.
        """
        client = cur.client
        if db_name and db_name.startswith("db"):
            try:
                db_idx = int(db_name.replace("db", ""))
                client.select(db_idx)
            except Exception:
                pass

        keys = []
        try:
            # Use SCAN to retrieve up to 300 keys safely without locking server
            cursor = 0
            while len(keys) < 300:
                cursor, batch = client.scan(cursor=cursor, count=100)
                keys.extend(batch)
                if cursor == 0:
                    break
        except Exception:
            try:
                keys = client.keys("*")[:300]
            except Exception:
                keys = []

        keys.sort()
        return [str(k) for k in keys]

    @staticmethod
    def columns(cur: RedisCursorWrapper, db_name: str, table_name: str) -> list[dict[str, Any]]:
        """
        Returns metadata / schema for a specific Redis key:
        Field (Name), Type (string/hash/list/set/zset), Key (PRI), TTL, Size.
        """
        client = cur.client
        key = table_name

        key_type = "unknown"
        ttl = -1
        size = 0

        try:
            key_type = client.type(key) or "none"
            ttl = client.ttl(key)
            if key_type == "string":
                size = client.strlen(key) or 0
            elif key_type == "hash":
                size = client.hlen(key) or 0
            elif key_type == "list":
                size = client.llen(key) or 0
            elif key_type == "set":
                size = client.scard(key) or 0
            elif key_type == "zset":
                size = client.zcard(key) or 0
        except Exception:
            pass

        return [
            {
                "Field": "Key",
                "Type": "string",
                "Null": "NO",
                "Key": "PRI",
                "Default": key,
                "Extra": "",
            },
            {
                "Field": "Data Type",
                "Type": str(key_type),
                "Null": "NO",
                "Key": "",
                "Default": "",
                "Extra": "",
            },
            {
                "Field": "TTL (Seconds)",
                "Type": "integer",
                "Null": "YES",
                "Key": "",
                "Default": str(ttl),
                "Extra": "Persistent" if ttl == -1 else ("Expired" if ttl == -2 else f"{ttl}s"),
            },
            {
                "Field": "Elements / Bytes",
                "Type": "integer",
                "Null": "NO",
                "Key": "",
                "Default": str(size),
                "Extra": "",
            },
        ]

    @staticmethod
    def run(cur: RedisCursorWrapper, sql: str, params: Optional[list[Any]] = None) -> tuple[list[str], list[list[Any]], bool]:
        """
        Execute Redis command and return (headers, rows, is_result_set).
        """
        cur.execute(sql, params)
        if cur.description:
            headers = [d[0] for d in cur.description]
            rows = cur.fetchall()
            return headers, rows, True
        return [], [], False

    @staticmethod
    def q(ident: str) -> str:
        return str(ident)
