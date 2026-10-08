"""
Database backend registry.
"""

from admin_suite.db.backends.mysql import MySQLBackend
from admin_suite.db.backends.sqlite import SQLiteBackend
from admin_suite.db.backends.postgres import PGBackend, PG_AVAILABLE

BACKENDS = {
    "mysql": MySQLBackend,
    "sqlite": SQLiteBackend,
    "postgresql": PGBackend,
    "postgres": PGBackend,
}

__all__ = [
    "BACKENDS",
    "MySQLBackend",
    "SQLiteBackend",
    "PGBackend",
    "PG_AVAILABLE",
]
