"""
Application configuration store.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from admin_suite.core.paths import CONFIG_FILE, ensure_dirs
from admin_suite.core.utils import read_json, write_json_secure

DEFAULT_CONFIG: dict[str, Any] = {
    # Default quick-connect settings.
    "ssh_host": "",
    "ssh_user": "",
    "ssh_port": "22",

    # Database defaults.
    "db_backend": "mysql",
    "db_host": "127.0.0.1",
    "db_port": "3306",
    "db_user": "root",
    "db_name": "",
    "db_use_tunnel": True,
    "sqlite_path": "",
    "db_page_size": 200,
    "db_schema_cache_ttl": 300,
    "db_result_page_size": 500,

    # VPN.
    "vpn_cli": "/opt/cisco/secureclient/bin/vpn",
    "vpn_host": "",

    # Terminal.
    "terminal_font_size": 13,
    "terminal_theme": "dark",
    "terminal_scrollback": 10000,
    "auto_reconnect": True,
    "session_logging": True,

    # UI.
    "ui_theme": "Breeze Dark",
    "auto_open_mysql_status": False,

    # Ansible.
    "playbook_dir": "",

    # SFTP.
    "sftp_default_local": str(Path.home()),

    # SSH security.
    "ssh_strict_host_keys": False,

    # Profile ping concurrency.
    "ping_max_concurrency": 8,

    # Linux & Distro Customization (Debian, RedHat/CentOS/Fedora, Arch, SUSE, or Auto)
    "os_family": "auto",
    "firewall_backend": "auto",  # "auto", "ufw", "firewalld", "iptables"
    "firewall_zone": "public",
    "custom_access_log_paths": [],
    "custom_error_log_paths": [],
}


class ConfigStore:
    """
    Simple JSON-backed configuration store.

    Secrets are not stored here. Secrets should be stored through SecretStore.
    """

    def __init__(self, path: str | Path = CONFIG_FILE):
        ensure_dirs()

        self.path = Path(path)
        self.data: dict[str, Any] = dict(DEFAULT_CONFIG)

        self.load()

    def load(self) -> None:
        """
        Load config from disk and merge over defaults.
        """
        loaded = read_json(self.path, {})

        if isinstance(loaded, dict):
            self.data.update(loaded)

    def save(self) -> None:
        """
        Save config to disk with secure permissions.
        """
        write_json_secure(self.path, self.data)

    def get(self, key: str, default: Any = None) -> Any:
        """
        Get config value.
        """
        return self.data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """
        Set config value.
        """
        self.data[key] = value

    def update(self, values: dict[str, Any]) -> None:
        """
        Update multiple config values.
        """
        self.data.update(values)

    def as_dict(self) -> dict[str, Any]:
        """
        Return a copy of the configuration dictionary.
        """
        return dict(self.data)

    # Compatibility helpers for code that expects dict-like access.

    def __getitem__(self, key: str) -> Any:
        return self.data[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.data[key] = value

    def __contains__(self, key: str) -> bool:
        return key in self.data
