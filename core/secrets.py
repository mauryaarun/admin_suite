"""
Secret storage using keyring.
"""

from __future__ import annotations

from typing import Optional

try:
    import keyring
except ImportError:
    keyring = None

from admin_suite.core.paths import APP_NAME


class SecretStore:
    """
    Store secrets in the operating system keyring.

    Important behavior:
    - set(key, value) stores value if non-empty
    - set(key, "") deletes the old secret
    """

    def __init__(self, app_name: str = APP_NAME):
        self.app_name = app_name
        self._memory: dict[str, str] = {}

    def get(self, key: str, default: str = "") -> str:
        """
        Get secret. Returns default if unavailable.
        """
        if keyring is not None:
            try:
                value = keyring.get_password(self.app_name, key)
                if value is not None:
                    return value
            except Exception:
                pass

        return self._memory.get(key, default)

    def set(self, key: str, value: Optional[str]) -> None:
        """
        Store or delete secret.
        """
        if not value:
            self.delete(key)
            return

        stored_in_keyring = False
        if keyring is not None:
            try:
                keyring.set_password(self.app_name, key, value)
                stored_in_keyring = True
            except Exception:
                pass

        # In-memory fallback if keyring is unavailable or failed
        self._memory[key] = value

    def delete(self, key: str) -> None:
        """
        Delete secret if it exists.
        """
        self._memory.pop(key, None)
        if keyring is None:
            return

        try:
            keyring.delete_password(self.app_name, key)
        except Exception:
            pass
