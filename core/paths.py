"""
Central filesystem paths for Admin Suite following Linux XDG Base Directory specification.
Includes automatic backward-compatibility migration from legacy ~/.admin_suite_v5_* paths.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

HOME = Path.home()

APP_NAME = "Admin_Suite_v5"
APP_DIR_NAME = "admin_suite"

# XDG Base Directory Paths
XDG_CONFIG_HOME = Path(os.environ.get("XDG_CONFIG_HOME") or (HOME / ".config"))
XDG_DATA_HOME = Path(os.environ.get("XDG_DATA_HOME") or (HOME / ".local" / "share"))
XDG_STATE_HOME = Path(os.environ.get("XDG_STATE_HOME") or (HOME / ".local" / "state"))
XDG_CACHE_HOME = Path(os.environ.get("XDG_CACHE_HOME") or (HOME / ".cache"))

CONFIG_DIR = XDG_CONFIG_HOME / APP_DIR_NAME
DATA_DIR = XDG_DATA_HOME / APP_DIR_NAME
STATE_DIR = XDG_STATE_HOME / APP_DIR_NAME
LOG_DIR = STATE_DIR / "sessions"
CACHE_DIR = XDG_CACHE_HOME / APP_DIR_NAME
XTERM_DIR = CACHE_DIR / "xterm"

# Active State Files (XDG compliant)
CONFIG_FILE = CONFIG_DIR / "config.json"
PROFILES_FILE = DATA_DIR / "profiles.json"
DB_PROFILES_FILE = DATA_DIR / "db_profiles.json"
SNIPPETS_FILE = DATA_DIR / "snippets.json"
RECENT_FILE = DATA_DIR / "recent.json"

# Database query history & favorites
QUERY_HISTORY_FILE = DATA_DIR / "query_history.json"
QUERY_FAVORITES_FILE = DATA_DIR / "query_favorites.json"

# Automation / command sets
ANSIBLE_HISTORY_FILE = DATA_DIR / "ansible_history.json"
ANSIBLE_COMMAND_SETS_FILE = DATA_DIR / "ansible_command_sets.json"

# Session restore
LAST_SESSION_FILE = DATA_DIR / "last_session.json"

# SSH known hosts
HOST_KEYS_FILE = DATA_DIR / "known_hosts"

# Legacy files mapping for seamless migration
_LEGACY_MAP = {
    HOME / ".admin_suite_v5_config.json": CONFIG_FILE,
    HOME / ".admin_suite_v5_profiles.json": PROFILES_FILE,
    HOME / ".admin_suite_v5_db_profiles.json": DB_PROFILES_FILE,
    HOME / ".admin_suite_v5_snippets.json": SNIPPETS_FILE,
    HOME / ".admin_suite_v5_recent.json": RECENT_FILE,
    HOME / ".admin_suite_v5_query_history.json": QUERY_HISTORY_FILE,
    HOME / ".admin_suite_v5_query_favorites.json": QUERY_FAVORITES_FILE,
    HOME / ".admin_suite_v5_ansible_history.json": ANSIBLE_HISTORY_FILE,
    HOME / ".admin_suite_v5_ansible_command_sets.json": ANSIBLE_COMMAND_SETS_FILE,
    HOME / ".admin_suite_v5_last_session.json": LAST_SESSION_FILE,
    HOME / ".admin_suite_v5_known_hosts": HOST_KEYS_FILE,
}


def _migrate_legacy_files() -> None:
    """Migrate legacy ~/.admin_suite_v5_* files to XDG directory structure if present."""
    for old_path, new_path in _LEGACY_MAP.items():
        if old_path.exists() and not new_path.exists():
            try:
                new_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(old_path, new_path)
                try:
                    os.chmod(new_path, 0o600)
                except Exception:
                    pass
            except Exception:
                pass

    # Migrate legacy session logs directory if present
    legacy_log_dir = HOME / ".admin_suite_sessions"
    if legacy_log_dir.is_dir() and not any(LOG_DIR.iterdir() if LOG_DIR.is_dir() else []):
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            for f in legacy_log_dir.glob("*.log"):
                dest = LOG_DIR / f.name
                if not dest.exists():
                    shutil.copy2(f, dest)
        except Exception:
            pass


def ensure_dirs() -> None:
    """Create required directories with secure permissions and migrate legacy files."""
    for d in (CONFIG_DIR, DATA_DIR, STATE_DIR, LOG_DIR, CACHE_DIR, XTERM_DIR):
        try:
            d.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(d, 0o700)
            except Exception:
                pass
        except OSError:
            pass

    _migrate_legacy_files()


def get_app_icon_path() -> Path:
    """Return Path to the application icon file (PNG or ICO)."""
    import sys
    base = getattr(sys, "_MEIPASS", None)
    candidates: list[Path] = []
    if base:
        base_p = Path(base)
        candidates.extend([
            base_p / "icon.png",
            base_p / "resources" / "icon.png",
            base_p / "icon.ico",
            base_p / "resources" / "icon.ico",
        ])

    pkg_root = Path(__file__).resolve().parent.parent
    candidates.extend([
        pkg_root / "resources" / "icon.png",
        pkg_root / "icon.png",
        pkg_root / "resources" / "icon.ico",
        pkg_root / "icon.ico",
    ])

    for p in candidates:
        if p.is_file():
            return p
    return pkg_root / "icon.png"


def get_app_icon():
    """Return QIcon initialized with the application icon."""
    try:
        from PyQt6.QtGui import QIcon
        p = get_app_icon_path()
        if p.is_file():
            return QIcon(str(p))
        return QIcon()
    except Exception:
        return None
