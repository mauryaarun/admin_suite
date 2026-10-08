"""
Debug and event logging pipeline with auditing support.
"""

from __future__ import annotations

import collections
import datetime
from typing import Any, Optional

from PyQt6.QtCore import QObject, pyqtSignal

from admin_suite.core.utils import sanitize_for_log


class EventLogPipeline(QObject):
    """
    Qt signal-based event and audit logging pipeline.

    Supports structured event records (level, source, timestamp, message),
    color-coded filtering, auditing trails, and backwards-compatible log_emitted signals.
    """

    event_emitted = pyqtSignal(dict)  # Emits structured dict: {timestamp, iso_time, level, source, message, details}
    log_emitted = pyqtSignal(str)     # Emits sanitized formatted string for legacy widgets

    def __init__(self, max_history: int = 5000):
        super().__init__()
        self._history: collections.deque[dict[str, Any]] = collections.deque(maxlen=max_history)

    def emit(
        self,
        source: str,
        message: str,
        level: str = "INFO",
        details: Optional[Any] = None,
    ) -> None:
        """
        Emit sanitized log line and structured event.
        Levels: INFO, SUCCESS, WARN, ERROR, AUDIT
        """
        now = datetime.datetime.now()
        ts = now.strftime("%H:%M:%S")
        iso = now.isoformat()
        clean_msg = sanitize_for_log(str(message))
        norm_source = (source or "SYSTEM").upper().strip()

        raw_level = (level or "INFO").upper().strip()
        if raw_level in ("OK", "PASSED", "DONE"):
            norm_level = "SUCCESS"
        elif raw_level in ("WARNING",):
            norm_level = "WARN"
        elif raw_level in ("CRITICAL", "FATAL"):
            norm_level = "ERROR"
        elif raw_level in ("SEC", "SECURITY"):
            norm_level = "AUDIT"
        elif raw_level in ("INFO", "SUCCESS", "WARN", "ERROR", "AUDIT"):
            norm_level = raw_level
        else:
            norm_level = "INFO"

        event = {
            "timestamp": ts,
            "iso_time": iso,
            "level": norm_level,
            "source": norm_source,
            "message": clean_msg,
            "details": details,
        }

        self._history.append(event)

        # Formatted line for text loggers
        line = f"[{ts}] [{norm_level}] [{norm_source}] {clean_msg}"
        self.log_emitted.emit(line)
        self.event_emitted.emit(event)

    # Compatibility alias
    def emit_log(self, source: str, message: str, level: str = "INFO") -> None:
        self.emit(source, message, level=level)

    def info(self, source: str, message: str) -> None:
        self.emit(source, message, level="INFO")

    def success(self, source: str, message: str) -> None:
        self.emit(source, message, level="SUCCESS")

    def ok(self, source: str, message: str) -> None:
        self.emit(source, message, level="SUCCESS")

    def warn(self, source: str, message: str) -> None:
        self.emit(source, message, level="WARN")

    def warning(self, source: str, message: str) -> None:
        self.emit(source, message, level="WARN")

    def error(self, source: str, message: str) -> None:
        self.emit(source, message, level="ERROR")

    def audit(self, source: str, message: str, details: Optional[Any] = None) -> None:
        self.emit(source, message, level="AUDIT", details=details)

    @property
    def history(self) -> list[dict[str, Any]]:
        return list(self._history)

    def get_history(self) -> list[dict[str, Any]]:
        return list(self._history)

    def clear_history(self) -> None:
        self._history.clear()


# Compatibility alias
DebugPipeline = EventLogPipeline
