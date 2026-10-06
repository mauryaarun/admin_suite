"""
SFTP subsystem.
"""

from admin_suite.sftp.models import (
    SftpAction,
    SftpTask,
)

from admin_suite.sftp.worker import (
    SftpWorker,
)

from admin_suite.sftp.exec_worker import (
    RemoteExecThread,
)

from admin_suite.sftp.dialogs import (
    ChmodDialog,
)

from admin_suite.sftp.file_browser import (
    FileBrowserPanel,
)

from admin_suite.sftp.editor import (
    RemoteEditorTab,
)

from admin_suite.sftp.search import (
    RemoteSearchDialog,
)

from admin_suite.sftp.tab import (
    SFTPTab,
)
from admin_suite.sftp.external_editor import (
    ExternalEditorManager,
)

__all__ = [
    "SftpAction",
    "SftpTask",
    "SftpWorker",
    "RemoteExecThread",
    "ChmodDialog",
    "FileBrowserPanel",
    "RemoteEditorTab",
    "RemoteSearchDialog",
    "SFTPTab",
    "ExternalEditorManager",
]
