"""
Terminal subsystem.

Includes:

- xterm.js asset handling
- Qt WebChannel bridge
- SSH terminal worker
- local PTY terminal worker
- terminal tabs
- split terminal tab
"""

from admin_suite.terminal.assets import (
    ensure_xterm_assets,
    shared_terminal_html_path,
)

from admin_suite.terminal.bridge import (
    WEBENGINE_AVAILABLE,
    TerminalBridge,
)

from admin_suite.terminal.ssh_worker import (
    SshTerminalWorker,
)

from admin_suite.terminal.local_worker import (
    LocalTerminalWorker,
)

from admin_suite.terminal.base_tab import (
    TerminalBaseTab,
    TerminalWebView,
    force_web_focus,
)

from admin_suite.terminal.ssh_tab import (
    SshTerminalTab,
)

from admin_suite.terminal.local_tab import (
    LocalTerminalTab,
)

from admin_suite.terminal.split_tab import (
    SplitTerminalTab,
)

from admin_suite.terminal.native_terminal import (
    NativeTerminalWidget,
)

__all__ = [
    "ensure_xterm_assets",
    "shared_terminal_html_path",
    "WEBENGINE_AVAILABLE",
    "TerminalBridge",
    "SshTerminalWorker",
    "LocalTerminalWorker",
    "TerminalBaseTab",
    "TerminalWebView",
    "NativeTerminalWidget",
    "force_web_focus",
    "SshTerminalTab",
    "LocalTerminalTab",
    "SplitTerminalTab",
]