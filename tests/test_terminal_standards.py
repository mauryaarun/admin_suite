"""Regression tests for standard terminal emulation, PTY sizing, and vi-compatibility."""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QApplication

pkg_parent = str(Path(__file__).resolve().parent.parent.parent)
if pkg_parent not in sys.path:
    sys.path.insert(0, pkg_parent)


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if not app:
        app = QApplication(["admin_suite_test", "-platform", "offscreen"])
    return app


def test_xterm_assets_follow_standard_pty_settings():
    from admin_suite.terminal.assets import SHARED_TERMINAL_HTML

    assert "convertEol: false" in SHARED_TERMINAL_HTML
    assert "convertEol: true" not in SHARED_TERMINAL_HTML
    assert "cursorStyle: 'block'" in SHARED_TERMINAL_HTML
    assert "ResizeObserver" in SHARED_TERMINAL_HTML
    assert "document.fonts" in SHARED_TERMINAL_HTML


def test_ssh_worker_uses_tracked_pty_dimensions_and_buffers_early_resize(monkeypatch):
    from admin_suite.ssh.credentials import SshCredentials
    from admin_suite.terminal.ssh_worker import SshTerminalWorker

    mock_channel = MagicMock()
    mock_channel.recv_ready.return_value = False
    mock_channel.recv_stderr_ready.return_value = False
    mock_channel.exit_status_ready.return_value = True

    mock_client = MagicMock()
    mock_client.invoke_shell.return_value = mock_channel

    monkeypatch.setattr(
        "admin_suite.terminal.ssh_worker.create_ssh_client",
        lambda strict=False: mock_client,
    )

    worker = SshTerminalWorker(
        "127.0.0.1",
        22,
        "user",
        SshCredentials(),
        cols=90,
        rows=30,
    )
    # Resize arrives before channel is opened (during SSH handshake).
    worker.resize_pty(148, 44)
    worker.run()

    mock_client.invoke_shell.assert_called_once_with(
        term="xterm-256color",
        width=148,
        height=44,
    )


def test_local_pty_worker_starts_with_initial_window_size(qapp):
    from admin_suite.terminal.local_worker import LocalTerminalWorker

    outputs: list[str] = []
    done = []

    worker = LocalTerminalWorker(
        command="stty size",
        cols=132,
        rows=36,
    )
    worker.output_ready.connect(outputs.append)
    worker.connection_closed.connect(lambda: done.append(True))
    worker.start()

    deadline = time.monotonic() + 5.0
    while not done and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.02)

    worker.wait(2000)
    combined = "".join(outputs)
    assert re.search(r"\b36\s+132\b", combined), f"Unexpected stty size output: {combined!r}"


def test_native_terminal_renders_colors_reverse_and_alt_screen(qapp):
    from admin_suite.terminal.native_terminal import PYTE_AVAILABLE, NativeTerminalWidget

    term = NativeTerminalWidget(cols=80, lines=24)
    term.resize(700, 420)
    term.show()
    qapp.processEvents()

    # Main screen output
    term.write("main-line-1\r\n")
    # Enter alternate screen (like vi), draw 256-color and reverse text, then paint
    term.write("\x1b[?1049h\x1b[38;5;196mRED\x1b[7mREV\x1b[0m\nDOWN")
    term.setFocus()
    qapp.processEvents()

    # Painting must succeed without AttributeError on StaticDefaultDict keys
    pixmap = term.grab()
    assert not pixmap.isNull()

    if PYTE_AVAILABLE and term.screen is not None:
        # Exiting alternate screen restores original main screen content
        term.write("\x1b[?1049l")
        qapp.processEvents()
        assert "main-line-1" in term.screen.display[0]
        assert "RED" not in term.screen.display[0]

    term.close()


def test_native_terminal_decckm_and_special_keys(qapp):
    from admin_suite.terminal.native_terminal import NativeTerminalWidget

    term = NativeTerminalWidget(cols=80, lines=24)
    sent: list[str] = []
    term.send_input.connect(sent.append)

    # Normal cursor mode
    term.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Up, Qt.KeyboardModifier.NoModifier))
    term.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_End, Qt.KeyboardModifier.NoModifier))
    assert sent[-2:] == ["\x1b[A", "\x1b[F"]

    # Application cursor keys mode (DECCKM) as used by vi/vim
    term.write("\x1b[?1h")
    term.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Up, Qt.KeyboardModifier.NoModifier))
    term.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_End, Qt.KeyboardModifier.NoModifier))
    assert sent[-2:] == ["\x1bOA", "\x1bOF"]

    # Reset DECCKM
    term.write("\x1b[?1l")
    term.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Down, Qt.KeyboardModifier.NoModifier))
    assert sent[-1] == "\x1b[B"

    # Ctrl+[ -> ESC, Shift+Tab -> CSI Z, Alt+x -> ESC x
    term.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_BracketLeft, Qt.KeyboardModifier.ControlModifier))
    term.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Backtab, Qt.KeyboardModifier.ShiftModifier))
    term.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_X, Qt.KeyboardModifier.AltModifier, "x"))
    assert sent[-3:] == ["\x1b", "\x1b[Z", "\x1bx"]

    # Bracketed paste mode
    term.write("\x1b[?2004h")
    QApplication.clipboard().setText("hello")
    term.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier))
    assert sent[-1] == "\x1b[200~hello\x1b[201~"

    term.deleteLater()
