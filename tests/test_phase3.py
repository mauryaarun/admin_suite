"""
Unit tests for Phase 3 & Phase 4 enhancements:
- Editable SQL Grid & Dirty-State Tracking (SqlResultModel & EnhancedTableDataWidget)
- Redis / KeyDB NoSQL Backend
- Lightweight Native Qt Terminal Engine (pyte & QPainter)
- Universal AI LLM Copilot (Multi-provider gateway & SecretStore)
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QApplication

pkg_parent = str(Path(__file__).resolve().parent.parent.parent)
if pkg_parent not in sys.path:
    sys.path.insert(0, pkg_parent)

from admin_suite.services import AppServices
from admin_suite.db.models import SqlResultModel
from admin_suite.db.table_view import EnhancedTableDataWidget
from admin_suite.db.backends.redis import RedisBackend, RedisCursorWrapper, RedisConnectionWrapper
from admin_suite.terminal.native_terminal import NativeTerminalWidget, PYTE_AVAILABLE
from admin_suite.ai.llm_client import UniversalLlmClient


@pytest.fixture(scope="session")
def qapp():
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
# 1. Editable SQL Grid & Dirty Tracking Tests
# =============================================================================

def test_sql_result_model_editing_and_dirty_tracking(qapp):
    headers = ["id", "username", "email", "status"]
    rows = [
        [1, "alice", "alice@example.com", "active"],
        [2, "bob", "bob@example.com", "inactive"],
    ]

    model = SqlResultModel(headers, rows)
    assert not model.editable
    assert not model.is_dirty()
    assert model.dirty_count() == 0

    # Before editable mode: setData is rejected
    idx_email = model.index(0, 2)
    assert not model.setData(idx_email, "alice_new@example.com")

    # Enable editable mode
    model.set_editable(True)
    assert model.editable
    assert model.flags(idx_email) & Qt.ItemFlag.ItemIsEditable

    dirty_counts = []
    model.dirty_changed.connect(lambda count: dirty_counts.append(count))

    # Edit cell
    assert model.setData(idx_email, "alice_new@example.com")
    assert model.is_dirty()
    assert model.dirty_count() == 1
    assert dirty_counts[-1] == 1
    assert model.data(idx_email, Qt.ItemDataRole.DisplayRole) == "alice_new@example.com"

    # Dirty background highlight verification
    bg_color = model.data(idx_email, Qt.ItemDataRole.BackgroundRole)
    assert isinstance(bg_color, QColor)

    # Edit second cell in same row
    idx_status = model.index(0, 3)
    assert model.setData(idx_status, "pending")
    assert model.dirty_count() == 2

    # Verify changes list
    changes = model.get_dirty_changes()
    assert len(changes) == 2
    cols_changed = [c["column_name"] for c in changes]
    assert "email" in cols_changed
    assert "status" in cols_changed

    # Generate SQL statements
    stmts = model.generate_update_sql("users", pk_column="id")
    assert len(stmts) == 1  # Grouped into a single UPDATE for row 1
    stmt = stmts[0]
    assert 'UPDATE "users"' in stmt
    assert '"email" = \'alice_new@example.com\'' in stmt
    assert '"status" = \'pending\'' in stmt
    assert 'WHERE "id" = 1' in stmt

    # Test revert
    model.revert_changes()
    assert not model.is_dirty()
    assert model.dirty_count() == 0
    assert model.data(idx_email, Qt.ItemDataRole.DisplayRole) == "alice@example.com"


def test_enhanced_table_data_widget_edit_flow(qapp, services):
    widget = EnhancedTableDataWidget(services)
    headers = ["id", "val"]
    rows = [[1, "old"]]
    widget.set_result(headers, rows)
    widget.set_table_context("test_table", pk_column="id")

    assert not widget.btn_commit.isEnabled()
    assert not widget.btn_revert.isEnabled()

    # Toggle edit grid mode
    widget.btn_edit_mode.setChecked(True)
    assert widget.source_model.editable

    # Modify cell via model
    src_idx = widget.source_model.index(0, 1)
    widget.source_model.setData(src_idx, "new_val")

    assert widget.source_model.is_dirty()
    assert widget.btn_commit.isEnabled()
    assert widget.btn_revert.isEnabled()
    assert "Commit (1)" in widget.btn_commit.text()


# =============================================================================
# 2. Redis & KeyDB NoSQL Backend Tests
# =============================================================================

def test_redis_backend_cursor_wrapper():
    mock_client = MagicMock()
    mock_client.ping.return_value = "PONG"
    mock_client.execute_command.return_value = "PONG"
    mock_client.hgetall.return_value = {"field1": "val1", "field2": "val2"}
    mock_client.keys.return_value = ["session:1", "session:2"]
    mock_client.type.return_value = "hash"
    mock_client.ttl.return_value = 3600
    mock_client.hlen.return_value = 2

    cur = RedisCursorWrapper(mock_client)

    # 1. Test PING
    cur.execute("PING")
    assert cur.description == [("Result",)]
    assert cur.fetchall() == [["PONG"]]

    # 2. Test HGETALL
    cur.execute("HGETALL user:1001")
    assert cur.description == [("Key / Field",), ("Value",)]
    res = cur.fetchall()
    assert len(res) == 2
    assert ["field1", "val1"] in res

    # 3. Test schemas
    schemas = RedisBackend.schemas(cur)
    assert len(schemas) == 16
    assert "db0" in schemas
    assert "db15" in schemas

    # 4. Test columns / metadata
    cols = RedisBackend.columns(cur, "db0", "user:1001")
    fields = {c["Field"]: c for c in cols}
    assert fields["Key"]["Default"] == "user:1001"
    assert fields["Data Type"]["Type"] == "hash"
    assert "3600s" in fields["TTL (Seconds)"]["Extra"]


# =============================================================================
# 3. Lightweight Native Terminal Tests
# =============================================================================

def test_native_terminal_widget(qapp):
    term = NativeTerminalWidget(cols=80, lines=24)
    assert term is not None

    received_input = []
    term.send_input.connect(lambda data: received_input.append(data))

    # Test feed output
    term.write("Hello Admin Suite\r\n")

    if PYTE_AVAILABLE and term.screen:
        assert "Hello Admin Suite" in term.screen.display[0]

    # Test font resizing
    term.set_font_size(14)
    assert term._font_size == 14

    term.zoom_in()
    assert term._font_size == 15

    term.zoom_out()
    assert term._font_size == 14

    # Test clear
    term.clear()
    if PYTE_AVAILABLE and term.screen:
        assert term.screen.display[0].strip() == ""


# =============================================================================
# 4. Universal AI Copilot Tests
# =============================================================================

def test_universal_llm_client_configuration(services):
    client = UniversalLlmClient(provider="ollama")
    assert client.provider == "ollama"
    assert client.base_url == "http://127.0.0.1:11434"

    # Configure OpenAI
    client.configure(provider="openai", api_key="sk-testkey123")
    assert client.provider == "openai"
    assert client.base_url == "https://api.openai.com/v1"
    assert client.api_key == "sk-testkey123"

    # Configure custom Gateway (e.g. Groq or LocalAI)
    client.configure(provider="openai", base_url="https://api.groq.com/openai/v1", api_key="gsk-xyz")
    assert client.base_url == "https://api.groq.com/openai/v1"

    # Model list fallback for offline test
    models = client.list_models()
    assert len(models) > 0
    assert any("gpt" in m.get("name", "") for m in models)
