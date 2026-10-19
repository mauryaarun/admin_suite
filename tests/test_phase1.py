"""
Unit tests for Phase 1 enhancements:
- SSH Connection Pool & Transport Multiplexer
- Persistent DB Session Pooling & Management
- ActionRegistry & Command Decoupling
- Smart Fuzzy Command Palette Matching & Ranking
"""

import os
import sys
import tempfile
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pkg_parent = str(Path(__file__).resolve().parent.parent.parent)
if pkg_parent not in sys.path:
    sys.path.insert(0, pkg_parent)

from admin_suite.services import AppServices
from admin_suite.core.action_registry import Action, ActionRegistry
from admin_suite.ssh.pool import SshConnectionPool, PooledConnection, get_ssh_pool
from admin_suite.db.session import DbSessionManager
from admin_suite.ui.palette import fuzzy_score, highlight_matches, infer_category


@pytest.fixture(scope="session")
def qapp():
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance()
    if not app:
        app = QApplication(["--platform", "offscreen"])
    return app


@pytest.fixture
def services(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    import admin_suite.core.paths as paths
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(paths, "CONFIG_DIR", tmp_path / "config")
    srv = AppServices()
    srv.paths = paths
    return srv


# =============================================================================
# 1. ActionRegistry Tests
# =============================================================================

def test_action_registry_lifecycle():
    reg = ActionRegistry()
    called = []

    act = reg.register_action(
        id="test.ping",
        name="Ping Hosts",
        callback=lambda: called.append(True),
        category="Network",
        shortcut="Ctrl+P",
        hint="Ping all configured nodes",
    )
    assert act.id == "test.ping"
    assert reg.get("test.ping") is act

    # Execute via registry
    reg.execute("test.ping")
    assert len(called) == 1

    # Filter by category
    net_actions = reg.list_actions("Network")
    assert len(net_actions) == 1
    assert net_actions[0].id == "test.ping"

    other_actions = reg.list_actions("Database")
    assert len(other_actions) == 0

    # Palette formatting
    items = reg.to_palette_items()
    assert len(items) == 1
    assert items[0]["name"] == "Ping Hosts"
    assert items[0]["category"] == "Network"
    assert items[0]["shortcut"] == "Ctrl+P"

    # Unregister
    reg.unregister("test.ping")
    assert reg.get("test.ping") is None


# =============================================================================
# 2. SSH Connection Pool Tests
# =============================================================================

def test_ssh_pool_key_generation():
    pool = SshConnectionPool()
    key1 = pool.make_key({
        "ssh_host": "192.168.1.10",
        "ssh_port": 22,
        "ssh_user": "root",
        "ssh_key_path": "~/.ssh/id_rsa",
    })
    key2 = pool.make_key({
        "ssh_host": "192.168.1.10",
        "ssh_port": 22,
        "ssh_user": "root",
        "ssh_key_path": "~/.ssh/id_rsa",
    })
    key3 = pool.make_key({
        "ssh_host": "192.168.1.11",
        "ssh_port": 2222,
        "ssh_user": "admin",
        "ssh_key_path": "",
    })

    assert key1 == key2
    assert key1 != key3
    assert "root@192.168.1.10:22" in key1


def test_ssh_pool_reconnection_and_caching():
    pool = SshConnectionPool()
    prof = {"ssh_host": "test.local", "ssh_port": 22, "ssh_user": "user"}

    mock_client = MagicMock()
    mock_transport = MagicMock()
    mock_transport.is_active.return_value = True
    mock_client.get_transport.return_value = mock_transport

    with patch.object(pool, "_connect_raw") as mock_connect:
        key = pool.make_key(prof)
        mock_connect.return_value = PooledConnection(key=key, client=mock_client)

        conn1 = pool.get_connection(prof)
        assert conn1.client is mock_client
        assert mock_connect.call_count == 1

        # Second acquisition should reuse existing live connection without connecting again
        conn2 = pool.get_connection(prof)
        assert conn2 is conn1
        assert mock_connect.call_count == 1

        # If transport dies, pool should discard and reconnect
        mock_transport.is_active.return_value = False
        conn3 = pool.get_connection(prof)
        assert mock_connect.call_count == 2

        pool.close_all()
        assert len(pool._pool) == 0


def test_ssh_pool_execute_multiplexing():
    pool = SshConnectionPool()
    prof = {"ssh_host": "test.local", "ssh_port": 22, "ssh_user": "user"}

    mock_client = MagicMock()
    mock_channel = MagicMock()
    mock_channel.recv_exit_status.return_value = 0

    mock_stdout = MagicMock()
    mock_stdout.read.return_value = b"hello from remote\n"
    mock_stdout.channel = mock_channel

    mock_stderr = MagicMock()
    mock_stderr.read.return_value = b""

    mock_client.exec_command.return_value = (MagicMock(), mock_stdout, mock_stderr)
    mock_transport = MagicMock()
    mock_transport.is_active.return_value = True
    mock_client.get_transport.return_value = mock_transport

    key = pool.make_key(prof)
    pool._pool[key] = PooledConnection(key=key, client=mock_client)

    out, err, rc = pool.execute(prof, "uptime")
    assert out == "hello from remote\n"
    assert err == ""
    assert rc == 0
    mock_client.exec_command.assert_called_once_with("uptime", timeout=None, get_pty=False)


# =============================================================================
# 3. Persistent Database Session Pooling Tests
# =============================================================================

def test_db_session_pooling_sqlite(tmp_path):
    mgr = DbSessionManager()
    db_file = str(tmp_path / "test.db")

    # Initialize a test sqlite db
    with sqlite3.connect(db_file) as conn:
        conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")
        conn.execute("INSERT INTO users (name) VALUES ('Alice')")

    cfg = {
        "backend": "sqlite",
        "sqlite_path": db_file,
    }

    # Acquire connection
    conn1 = mgr.acquire(cfg)
    assert conn1 is not None
    cur = conn1.cursor()
    cur.execute("SELECT name FROM users")
    assert cur.fetchone()[0] == "Alice"

    # Releasing with has_error=False should keep connection in pool
    mgr.release(cfg, conn1, has_error=False)

    # Acquire again should return identical pooled connection
    conn2 = mgr.acquire(cfg)
    assert conn2 is conn1

    # Clean shutdown
    mgr.stop_all()
    assert len(mgr._connections) == 0


# =============================================================================
# 4. Smart Fuzzy Command Palette Tests
# =============================================================================

def test_fuzzy_score_exact_and_prefix():
    matched, score_exact, _ = fuzzy_score("terminal", "terminal")
    matched_pre, score_pre, _ = fuzzy_score("term", "terminal")
    matched_sub, score_sub, _ = fuzzy_score("min", "terminal")
    matched_fail, score_fail, _ = fuzzy_score("xyz", "terminal")

    assert matched and matched_pre and matched_sub
    assert not matched_fail
    assert score_exact > score_pre > score_sub


def test_fuzzy_score_word_boundaries():
    # 'vapt' should score highly on 'Open VAPT & Web Security'
    matched, score, indices = fuzzy_score("vapt", "Open VAPT & Web Security")
    assert matched
    assert score > 100
    assert len(indices) == 4

    # 'st' should match 'System Tools' at word boundaries
    matched_st, score_st, _ = fuzzy_score("st", "System Tools")
    assert matched_st
    assert score_st > 50


def test_highlight_matches():
    html = highlight_matches("Terminal", [0, 1, 2, 3], "#3daee9")
    assert "<b style='color:#3daee9;'>T</b>" in html
    assert "<b style='color:#3daee9;'>e</b>" in html
    assert "inal" in html


def test_infer_category():
    assert infer_category({"name": "Connect: prod-web"}) == "Profiles"
    assert infer_category({"name": "Open Database Manager"}) == "Database"
    assert infer_category({"name": "WireGuard VPN Server"}) == "VPN & Mesh"
    assert infer_category({"name": "Ansible Playbook"}) == "Automation"
    assert infer_category({"name": "New Terminal"}) == "Terminal"
    assert infer_category({"name": "Security Hub"}) == "Admin & Security"
    assert infer_category({"name": "Theme Manager"}) == "Settings"
    assert infer_category({"name": "Custom Action"}) == "General"
