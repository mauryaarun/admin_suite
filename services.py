"""
Central application services.

Later UI modules should receive this service container instead of using
global variables.
"""

from __future__ import annotations

from typing import Any

from admin_suite.core import (
    ConfigStore,
    DebugPipeline,
    EventLogPipeline,
    NotificationHub,
    SecretStore,
    ThemeManager,
    ensure_dirs,
)

from admin_suite.core import paths


COMMON_PASSWORD_KEYS = (
    "ssh_pass",
    "db_pass",
    "vpn_cert_pass",
    "vpn_pass",
)


class AppServices:
    """
    Dependency container for the application.
    """

    def __init__(self):
        ensure_dirs()

        self.paths = paths

        self.config = ConfigStore()
        self.secrets = SecretStore()

        self.events = EventLogPipeline()
        self.debug = self.events  # Compatibility
        self.notifications = NotificationHub()

        self.theme = ThemeManager()

        # In-memory session cache for sudo/root passwords
        self._sudo_passwords: dict[str, str] = {}

    def emit_log(
        self,
        source: str,
        message: str,
        level: str = "INFO",
        details: Any = None,
    ) -> None:
        """
        Convenience logger with level and details support.
        """
        self.events.emit(source, message, level=level, details=details)

    def audit(self, source: str, message: str, details: Any = None) -> None:
        """
        Log an audit trail entry for security-relevant or mutating operations.
        """
        self.events.audit(source, message, details=details)

    def log_info(self, source: str, message: str) -> None:
        self.events.info(source, message)

    def log_warn(self, source: str, message: str) -> None:
        self.events.warn(source, message)

    def log_error(self, source: str, message: str) -> None:
        self.events.error(source, message)

    def log_success(self, source: str, message: str) -> None:
        self.events.success(source, message)

    def apply_theme(self, app: Any) -> None:
        """
        Apply configured UI theme to a QApplication instance.
        """
        theme_name = self.config.get("ui_theme", "Breeze Dark")
        self.theme.apply(app, theme_name)

    def load_passwords(self) -> dict[str, str]:
        """
        Load common global password fields from keyring.
        """
        return {
            key: self.secrets.get(key, "")
            for key in COMMON_PASSWORD_KEYS
        }

    def save_passwords(self, values: dict[str, str]) -> None:
        """
        Save/delete common global password fields in keyring.
        """
        for key in COMMON_PASSWORD_KEYS:
            self.secrets.set(key, values.get(key, ""))

    def get_sudo_password(self, profile_name: str) -> str | None:
        """
        Get cached in-memory sudo password or persistent profile secret for profile.
        Returns empty string if explicitly marked passwordless, or None if not set.
        """
        if profile_name in self._sudo_passwords:
            return self._sudo_passwords[profile_name]

        stored = self.secrets.get(f"prof_sudo_{profile_name}", "")
        if stored:
            self._sudo_passwords[profile_name] = stored
            return stored

        return None

    def set_sudo_password(
        self,
        profile_name: str,
        password: str,
        persist: bool = False,
    ) -> None:
        """
        Cache sudo password for the session and optionally persist to secret store.
        """
        self._sudo_passwords[profile_name] = password
        if persist:
            self.secrets.set(f"prof_sudo_{profile_name}", password)

    def clear_sudo_password(self, profile_name: str) -> None:
        """
        Clear cached sudo password from session and persistent storage.
        """
        self._sudo_passwords.pop(profile_name, None)
        try:
            self.secrets.set(f"prof_sudo_{profile_name}", "")
        except Exception:
            pass
