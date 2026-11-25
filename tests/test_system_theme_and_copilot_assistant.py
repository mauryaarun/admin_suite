"""
Unit tests for:
1. System Theme as default (dynamic palette extraction, system fonts, ThemeDialog, ThemeManager)
2. Enhanced Ollama Connection Settings (URL normalization, dual-stack candidates, proxy bypass, detailed diagnostics)
3. Red Hat Command-Line Assistant & Debian/Ubuntu Server Assistant in Copilot (OS detection, tool probing, default assistant selection, 1-click install banner, query formatting)
"""

import sys
import unittest.mock as mock
from pathlib import Path

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont, QPalette
from PyQt6.QtWidgets import QApplication

pkg_root = str(Path(__file__).resolve().parent.parent)
if pkg_root not in sys.path:
    sys.path.insert(0, pkg_root)

from admin_suite.services import AppServices
from admin_suite.core.config import ConfigStore
from admin_suite.core.theme import ThemeManager, get_system_ui_theme, is_system_dark
from admin_suite.core.fonts import (
    system_ui_family,
    system_mono_family,
    system_ui_point_size,
    qss_font_family_ui,
    qss_font_family_mono,
    ui_font,
    mono_font,
)
from admin_suite.ui.dialogs import ThemeDialog
from admin_suite.ai.llm_client import UniversalLlmClient
from admin_suite.ai.ollama_client import OllamaClient
from admin_suite.ai.assistant_tab import AIAssistantTab, _ServerAssistantBanner, AiSettingsDialog


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if not app:
        app = QApplication(["--platform", "offscreen"])
    return app


@pytest.fixture
def services(tmp_path, monkeypatch):
    cfg_dir = tmp_path / "config" / "admin_suite"
    data_dir = tmp_path / "data" / "admin_suite"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    import admin_suite.core.paths as paths
    monkeypatch.setattr(paths, "DATA_DIR", data_dir)
    monkeypatch.setattr(paths, "CONFIG_DIR", cfg_dir)
    monkeypatch.setattr(paths, "CONFIG_FILE", cfg_dir / "config.json")

    srv = AppServices()
    srv.config = ConfigStore(cfg_dir / "config.json")
    srv.paths = paths
    return srv


# =============================================================================
# 1. System Theme by Default Tests
# =============================================================================

def test_default_theme_is_system(tmp_path, monkeypatch):
    """ConfigStore defaults 'ui_theme' to 'System'."""
    cfg_file = tmp_path / "test_config.json"
    cfg = ConfigStore(cfg_file)
    assert cfg.get("ui_theme") == "System"


def test_system_fonts_resolution(qapp):
    """System font helpers return valid font families and sensible point sizes."""
    ui_fam = system_ui_family()
    mono_fam = system_mono_family()
    pt_size = system_ui_point_size()

    assert isinstance(ui_fam, str) and len(ui_fam) > 0
    assert isinstance(mono_fam, str) and len(mono_fam) > 0
    assert isinstance(pt_size, int) and pt_size >= 8

    # QSS font families prioritize system fonts
    qss_ui = qss_font_family_ui()
    qss_mono = qss_font_family_mono()
    assert ui_fam in qss_ui
    assert mono_fam in qss_mono

    # Calling ui_font() and mono_font() without point_size uses system size
    f_ui = ui_font()
    assert f_ui.pointSize() == pt_size
    f_mono = mono_font()
    assert f_mono.pointSize() == pt_size


def test_theme_manager_system_palette(qapp):
    """ThemeManager.get_theme('System') extracts native OS palette colors."""
    tm = ThemeManager()
    theme = tm.get_theme("System")
    assert isinstance(theme, dict)
    assert "win" in theme
    assert "panel" in theme
    assert "text" in theme
    assert "accent" in theme
    assert "border" in theme
    assert theme["win"].startswith("#")
    assert theme["text"].startswith("#")

    # System theme is in list of theme names
    names = list(tm.themes.keys())
    assert "System" in names
    assert names[0] == "System"


def test_theme_dialog_defaults_to_system(qapp, services):
    """ThemeDialog displays 'System (Auto)' as first entry and selects System."""
    dlg = ThemeDialog(None, services)
    assert dlg.list.count() > 0
    first_item = dlg.list.item(0)
    assert "System" in first_item.text()
    assert dlg.chosen() == "System"
    dlg.close()


# =============================================================================
# 2. Enhanced Ollama Connection Settings Tests
# =============================================================================

def test_ollama_url_normalization():
    """URL normalization handles bare hosts, ports, protocols, and trailing slashes."""
    assert UniversalLlmClient.normalize_url("localhost:11434") == "http://localhost:11434"
    assert UniversalLlmClient.normalize_url("127.0.0.1:11434/") == "http://127.0.0.1:11434"
    assert UniversalLlmClient.normalize_url("http://192.168.1.50:11434") == "http://192.168.1.50:11434"
    assert UniversalLlmClient.normalize_url("") == "http://127.0.0.1:11434"


def test_ollama_dual_stack_candidates():
    """Candidate URLs generate dual-stack fallback for 127.0.0.1 and localhost."""
    client1 = UniversalLlmClient(provider="ollama", base_url="http://127.0.0.1:11434")
    c1 = client1._get_candidate_urls()
    assert "http://127.0.0.1:11434" in c1
    assert "http://localhost:11434" in c1

    client2 = UniversalLlmClient(provider="ollama", base_url="http://localhost:11434")
    c2 = client2._get_candidate_urls()
    assert "http://localhost:11434" in c2
    assert "http://127.0.0.1:11434" in c2


def test_ollama_detailed_diagnostics(monkeypatch):
    """test_connection_detailed returns structured diagnostic report."""
    client = UniversalLlmClient(provider="ollama", base_url="http://127.0.0.1:11434")

    # Mock successful version and model tags
    def fake_open_url(url, timeout=5.0):
        mock_resp = mock.MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__.return_value = mock_resp
        req_url = getattr(url, "full_url", str(url))
        if "/api/version" in req_url:
            mock_resp.read.return_value = b'{"version":"0.5.4"}'
        elif "/api/tags" in req_url:
            mock_resp.read.return_value = b'{"models":[{"name":"llama3.2:latest"},{"name":"qwen2.5:7b"}]}'
        else:
            mock_resp.read.return_value = b"Ollama is running"
        return mock_resp

    monkeypatch.setattr(client, "_open_url", fake_open_url)

    diag = client.test_connection_detailed()
    assert diag["ok"] is True
    assert diag["version"] == "0.5.4"
    assert len(diag["models"]) == 2
    assert "llama3.2:latest" in diag["models"]
    assert "qwen2.5:7b" in diag["models"]
    assert "Connected" in diag["message"]


def test_ollama_detailed_diagnostics_failure(monkeypatch):
    """test_connection_detailed provides troubleshooting steps on connection error."""
    client = UniversalLlmClient(provider="ollama", base_url="http://127.0.0.1:11434")

    def fake_open_url_fail(url, timeout=5.0):
        raise ConnectionRefusedError("Connection refused")

    monkeypatch.setattr(client, "_open_url", fake_open_url_fail)

    diag = client.test_connection_detailed()
    assert diag["ok"] is False
    assert "Troubleshooting" in diag["message"]
    assert "ollama serve" in diag["message"]


# =============================================================================
# 3. Red Hat and Debian/Ubuntu Server Assistant in Copilot
# =============================================================================

def test_parse_os_and_tools_rhel_installed():
    """Detects Red Hat system with command-line-assistant installed (c or cla)."""
    raw_out = (
        'NAME="Red Hat Enterprise Linux"\n'
        'VERSION="9.4 (Plow)"\n'
        'ID="rhel"\n'
        'ID_LIKE="fedora"\n'
        'PRETTY_NAME="Red Hat Enterprise Linux 9.4 (Plow)"\n'
        '---TOOLS---\n'
        '/usr/bin/c\n'
        '/usr/bin/cla\n'
    )
    parsed = AIAssistantTab._parse_os_and_tools(raw_out, is_remote=True, profile_info={}, target_key="root@rhel9:22")
    assert parsed["family"] == "rhel"
    assert parsed["os_id"] == "rhel"
    assert "Red Hat" in parsed["pretty_name"]
    assert parsed["installed_tool"] == "c"
    assert "c" in parsed["tools"]
    assert "cla" in parsed["tools"]


def test_parse_os_and_tools_rhel_not_installed():
    """Detects Red Hat system without command-line-assistant installed."""
    raw_out = (
        'NAME="Rocky Linux"\n'
        'ID="rocky"\n'
        'ID_LIKE="rhel centos fedora"\n'
        'PRETTY_NAME="Rocky Linux 9.3 (Blue Onyx)"\n'
        '---TOOLS---\n'
    )
    parsed = AIAssistantTab._parse_os_and_tools(raw_out, is_remote=True, profile_info={}, target_key="admin@rocky:22")
    assert parsed["family"] == "rhel"
    assert parsed["os_id"] == "rocky"
    assert parsed["installed_tool"] == ""


def test_parse_os_and_tools_debian_installed():
    """Detects Ubuntu/Debian system with tgpt or sgpt installed."""
    raw_out = (
        'NAME="Ubuntu"\n'
        'ID=ubuntu\n'
        'ID_LIKE=debian\n'
        'PRETTY_NAME="Ubuntu 22.04.4 LTS"\n'
        '---TOOLS---\n'
        '/usr/local/bin/tgpt\n'
    )
    parsed = AIAssistantTab._parse_os_and_tools(raw_out, is_remote=False, profile_info={}, target_key="local")
    assert parsed["family"] == "debian"
    assert parsed["os_id"] == "ubuntu"
    assert parsed["installed_tool"] == "tgpt"


def test_format_assistant_exec_cmd():
    """Escapes user prompts safely for bash execution of assistant binaries."""
    cmd_c = AIAssistantTab._format_assistant_exec_cmd("c", "How do I list firewalld ports?")
    assert cmd_c == "c 'How do I list firewalld ports?'"

    cmd_cla = AIAssistantTab._format_assistant_exec_cmd("cla", "Check systemd logs for errors")
    assert cmd_cla == "cla chat 'Check systemd logs for errors'"

    cmd_tgpt = AIAssistantTab._format_assistant_exec_cmd("tgpt", "Show top memory processes")
    assert cmd_tgpt == "tgpt 'Show top memory processes'"

    # Handles single quote escaping safely
    cmd_quote = AIAssistantTab._format_assistant_exec_cmd("c", "what's the disk usage?")
    assert cmd_quote == "c 'what'\"'\"'s the disk usage?'"


def test_server_assistant_detected_rhel_sets_default(qapp, services):
    """When RHEL with command-line-assistant is detected, it becomes default assistant at index 0."""
    tab = AIAssistantTab(services)
    os_info = {
        "family": "rhel",
        "installed_tool": "c",
        "pretty_name": "Red Hat Enterprise Linux 9",
        "target_key": "user@rhel:22",
    }
    tab._on_server_assistant_detected(os_info)

    assert tab._server_asst_label == "🔴 RHEL Assistant (c)"
    assert tab.model_combo.itemText(0) == "🔴 RHEL Assistant (c)"
    assert tab.model_combo.currentIndex() == 0
    assert not tab.server_banner.isVisible()


def test_server_assistant_detected_rhel_uninstalled_shows_banner(qapp, services):
    """When RHEL lacks command-line-assistant, notification banner is shown with 1-click install."""
    tab = AIAssistantTab(services)
    os_info = {
        "family": "rhel",
        "installed_tool": "",
        "pretty_name": "Red Hat Enterprise Linux 9",
        "target_key": "user@rhel:22",
    }
    tab._on_server_assistant_detected(os_info)

    assert not tab.server_banner.isHidden()
    assert "Red Hat" in tab.server_banner.title_lbl.text()
    assert "command-line-assistant" in tab.server_banner.desc_lbl.text()
    assert "dnf install -y command-line-assistant" in tab.server_banner.install_cmd


def test_server_assistant_detected_debian_uninstalled_shows_banner(qapp, services):
    """When Debian/Ubuntu lacks an assistant, banner offers tgpt install."""
    tab = AIAssistantTab(services)
    os_info = {
        "family": "debian",
        "installed_tool": "",
        "pretty_name": "Ubuntu 22.04 LTS",
        "target_key": "user@ubuntu:22",
    }
    tab._on_server_assistant_detected(os_info)

    assert not tab.server_banner.isHidden()
    assert "Ubuntu" in tab.server_banner.title_lbl.text()
    assert "tgpt" in tab.server_banner.install_cmd
