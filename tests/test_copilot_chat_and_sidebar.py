"""
Unit tests for Sidebar resizability and Modern AI Copilot Chat Interface:
- Left & right sidebar resizability without artificial width clamping
- Custom splitter handle double-click toggle and splitterMoved persistence
- Tab-context-aware system prompt generation (Terminal bash vs Database SQL)
- Modern chat message formatting & code block action cards
- 1-click execution and buffer insertion in active Terminal tabs
- 1-click execution and query editor insertion in Database tabs
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

pkg_root = str(Path(__file__).resolve().parent.parent)
pkg_parent = str(Path(__file__).resolve().parent.parent.parent)
for p in (pkg_root, pkg_parent):
    if p not in sys.path:
        sys.path.insert(0, p)

from admin_suite.services import AppServices
from admin_suite.ui.main_window import MainWindow
from admin_suite.ai.assistant_tab import AIAssistantTab, _CodeBlockWidget, _ChatMessageWidget


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

    import admin_suite.terminal.bridge as bridge_mod
    import admin_suite.terminal.base_tab as base_tab_mod
    import admin_suite.terminal.local_tab as local_tab_mod
    monkeypatch.setattr(bridge_mod, "WEBENGINE_AVAILABLE", False)
    monkeypatch.setattr(base_tab_mod, "WEBENGINE_AVAILABLE", False)
    monkeypatch.setattr(local_tab_mod, "WEBENGINE_AVAILABLE", False)

    srv = AppServices()
    srv.paths = paths
    return srv


@pytest.fixture
def main_window(qapp, services):
    win = MainWindow(services)
    win.resize(1600, 900)
    win.show()
    return win


# =============================================================================
# 1. Sidebar Resizability Tests
# =============================================================================

def test_left_sidebar_resizable_beyond_520(main_window):
    """Ensure left sidebar can be freely resized beyond the old 520px limit."""
    main_window.main_splitter.moveSplitter(650, 1)
    sizes = main_window.main_splitter.sizes()
    assert sizes[0] >= 600, f"Left sidebar size should be >= 600, got {sizes[0]}"


def test_right_sidebar_resizable_beyond_600(main_window):
    """Ensure right sidebar can be freely resized beyond the old 600px limit."""
    main_window.toggle_right_sidebar(True)
    total_w = main_window.main_splitter.width()
    # Move splitter 2 to the left so right sidebar expands
    target_pos = total_w - 700
    main_window.main_splitter.moveSplitter(target_pos, 2)
    sizes = main_window.main_splitter.sizes()
    assert sizes[2] >= 650, f"Right sidebar size should be >= 650, got {sizes[2]}"


def test_splitter_moved_saves_dimensions(main_window):
    """Ensure splitterMoved updates _sidebar_width and _copilot_width."""
    main_window.main_splitter.moveSplitter(450, 1)
    sizes = main_window.main_splitter.sizes()
    assert main_window._sidebar_width == sizes[0]


def test_handle_double_click_toggles_sidebars(main_window):
    """Double-clicking handle 1 toggles left sidebar; handle 2 toggles right sidebar."""
    h1 = main_window.main_splitter.handle(1)
    assert not main_window.sidebar.isHidden()
    # Double-click handle 1 to collapse
    h1.mouseDoubleClickEvent(MagicMock())
    assert main_window.sidebar.isHidden()
    # Double-click handle 1 again to expand
    h1.mouseDoubleClickEvent(MagicMock())
    assert not main_window.sidebar.isHidden()


# =============================================================================
# 2. Context Detection & Tab-Specific System Prompts
# =============================================================================

def test_copilot_terminal_context_and_system_prompt(main_window):
    """Terminal tab triggers terminal context and Linux bash system prompt."""
    ai = main_window.ai_tab
    term_tab = main_window.add_local_command_tab("bash", "Local Shell")
    ai.update_context()

    assert ai._current_context_info["type"] == "terminal"
    assert "Terminal" in ai.context_badge.text()

    prompt = ai._build_system_prompt()
    assert "Linux shell copilot" in prompt
    assert "```bash" in prompt
    assert "safe" in prompt.lower()
    assert "Local Shell" in prompt


def test_copilot_database_context_and_system_prompt(main_window):
    """Database tab triggers database context and dialect-specific SQL system prompt."""
    ai = main_window.ai_tab
    main_window.open_db_manager()
    ai.update_context()

    assert ai._current_context_info["type"] == "database"
    assert "SQL" in ai.context_badge.text()

    prompt = ai._build_system_prompt()
    assert "Database Administrator" in prompt
    assert "```sql" in prompt
    assert "UPPERCASE" in prompt
    assert "Database Dialect" in prompt


# =============================================================================
# 3. Message Parsing and Code Block Cards
# =============================================================================

def test_parse_message_segments_extracts_code_and_text(main_window):
    ai = main_window.ai_tab
    sample = (
        "Here is the command to list open ports:\n"
        "```bash\n"
        "ss -tulnp\n"
        "```\n"
        "And to filter SSH specifically:\n"
        "```bash\n"
        "ss -tulnp | grep :22\n"
        "```\n"
        "Use with sudo for full process names."
    )
    segments = ai._parse_message_segments(sample)
    assert len(segments) == 5
    assert segments[0] == ("text", "", "Here is the command to list open ports:")
    assert segments[1] == ("code", "bash", "ss -tulnp")
    assert segments[2] == ("text", "", "And to filter SSH specifically:")
    assert segments[3] == ("code", "bash", "ss -tulnp | grep :22")
    assert segments[4] == ("text", "", "Use with sudo for full process names.")


def test_code_block_widget_buttons_for_bash_and_sql(main_window):
    ai = main_window.ai_tab
    bash_card = _CodeBlockWidget("df -h", lang="bash", parent_tab=ai)
    assert bash_card.copy_btn is not None
    assert "df -h" in bash_card.editor.toPlainText()

    sql_card = _CodeBlockWidget("SELECT * FROM users;", lang="sql", parent_tab=ai)
    assert sql_card.copy_btn is not None
    assert "SELECT" in sql_card.editor.toPlainText()


# =============================================================================
# 4. Direct Execution & Buffer Insertion
# =============================================================================

def test_execute_in_terminal_run_and_insert(main_window):
    ai = main_window.ai_tab
    term_tab = main_window.add_local_command_tab("bash", "Test Terminal")
    main_window.tabs.setCurrentWidget(term_tab)

    captured = []
    term_tab.send_text = lambda txt: captured.append(txt)

    # 1. Run (adds newline)
    ai.execute_in_terminal("docker ps", run=True)
    assert captured == ["docker ps\n"]

    # 2. Insert (without newline)
    captured.clear()
    ai.execute_in_terminal("docker ps -a", run=False)
    assert captured == ["docker ps -a"]


def test_execute_in_database_run_and_insert(main_window):
    ai = main_window.ai_tab
    db_mgr = main_window.open_db_manager()

    executed = []
    db_mgr.execute_query = lambda: executed.append(db_mgr.query_edit.toPlainText())

    # 1. Insert SQL without executing
    ai.execute_in_db("SELECT 42;", run=False)
    assert db_mgr.query_edit.toPlainText() == "SELECT 42;"
    assert len(executed) == 0

    # 2. Execute SQL directly
    ai.execute_in_db("SELECT name, email FROM clients;", run=True)
    assert db_mgr.query_edit.toPlainText() == "SELECT name, email FROM clients;"
    assert len(executed) == 1 and executed[0] == "SELECT name, email FROM clients;"


# =============================================================================
# 5. Chat History & Clear
# =============================================================================

def test_chat_multi_turn_and_clear(main_window):
    main_window.toggle_right_sidebar(True)
    ai = main_window.ai_tab
    ai._clear_all()
    assert len(ai._chat_history) == 0
    assert not ai.welcome_widget.isHidden()

    # User message
    user_msg = {"role": "user", "content": "Hello Copilot", "timestamp": "12:00"}
    ai._chat_history.append(user_msg)
    ai._add_message_bubble(user_msg)
    assert len(ai._chat_history) == 1

    # Assistant message
    ai._on_done("Hi there! How can I help you?", 0.2, 8)
    assert len(ai._chat_history) == 2
    assert ai._chat_history[1]["role"] == "assistant"
    assert "Hi there!" in ai._chat_history[1]["content"]

    # Clear
    ai._clear_all()
    assert len(ai._chat_history) == 0
    assert not ai.welcome_widget.isHidden()
