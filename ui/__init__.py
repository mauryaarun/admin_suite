"""
UI subsystem.
"""

from admin_suite.ui.toasts import (
    Toast,
    NotificationCenterDialog,
)

from admin_suite.ui.palette import (
    CommandPaletteDialog,
)

from admin_suite.ui.dialogs import (
    ProfileDialog,
    DbProfileDialog,
    ConnectionManagerDialog,
    ThemeDialog,
    KeyManagerDialog,
    SessionLogViewerDialog,
    SnippetDialog,
    SnippetManagerDialog,
    SudoCredentialsDialog,
    ensure_sudo_credentials,
)

def __getattr__(name: str):
    if name == "MainWindow":
        from admin_suite.ui.main_window import MainWindow
        return MainWindow
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")

from admin_suite.ui.event_logs import (
    EventLogsWidget,
)

from admin_suite.ui.welcome import (
    WelcomeWidget,
)

__all__ = [
    "Toast",
    "NotificationCenterDialog",
    "CommandPaletteDialog",
    "ProfileDialog",
    "DbProfileDialog",
    "ConnectionManagerDialog",
    "ThemeDialog",
    "KeyManagerDialog",
    "SessionLogViewerDialog",
    "SnippetDialog",
    "SnippetManagerDialog",
    "SudoCredentialsDialog",
    "ensure_sudo_credentials",
    "MainWindow",
    "EventLogsWidget",
    "WelcomeWidget",
]
