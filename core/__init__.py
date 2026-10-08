"""
Core services:

- paths
- utilities
- config
- secrets
- logging
- notifications
- theme
"""

from admin_suite.core.paths import (
    APP_NAME,
    CONFIG_FILE,
    PROFILES_FILE,
    DB_PROFILES_FILE,
    SNIPPETS_FILE,
    RECENT_FILE,
    QUERY_HISTORY_FILE,
    QUERY_FAVORITES_FILE,
    ANSIBLE_HISTORY_FILE,
    ANSIBLE_COMMAND_SETS_FILE,
    LAST_SESSION_FILE,
    LOG_DIR,
    XTERM_DIR,
    HOST_KEYS_FILE,
    ensure_dirs,
    get_app_icon_path,
    get_app_icon,
)

from admin_suite.core.utils import (
    read_json,
    write_json_secure,
    safe_int,
    safe_float,
    human_bytes,
    human_duration,
    sanitize_for_log,
)

from admin_suite.core.config import (
    DEFAULT_CONFIG,
    ConfigStore,
)

from admin_suite.core.secrets import (
    SecretStore,
)

from admin_suite.core.logging import (
    DebugPipeline,
    EventLogPipeline,
)

from admin_suite.core.notifications import (
    NotificationHub,
)

from admin_suite.core.theme import (
    UI_THEMES,
    TERMINAL_THEMES,
    ThemeManager,
)

__all__ = [
    "APP_NAME",
    "CONFIG_FILE",
    "PROFILES_FILE",
    "DB_PROFILES_FILE",
    "SNIPPETS_FILE",
    "RECENT_FILE",
    "QUERY_HISTORY_FILE",
    "QUERY_FAVORITES_FILE",
    "ANSIBLE_HISTORY_FILE",
    "ANSIBLE_COMMAND_SETS_FILE",
    "LAST_SESSION_FILE",
    "LOG_DIR",
    "XTERM_DIR",
    "HOST_KEYS_FILE",
    "ensure_dirs",
    "get_app_icon_path",
    "get_app_icon",
    "read_json",
    "write_json_secure",
    "safe_int",
    "safe_float",
    "human_bytes",
    "human_duration",
    "sanitize_for_log",
    "DEFAULT_CONFIG",
    "ConfigStore",
    "SecretStore",
    "DebugPipeline",
    "EventLogPipeline",
    "NotificationHub",
    "UI_THEMES",
    "TERMINAL_THEMES",
    "ThemeManager",
]
