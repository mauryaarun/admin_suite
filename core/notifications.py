"""
Application notification hub.
"""

from __future__ import annotations

import datetime
import threading
from typing import Any

from PyQt6.QtCore import QObject, pyqtSignal


class NotificationHub(QObject):
    """
    Thread-safe notification store.

    UI toast/notification widgets should connect to pushed.
    """

    pushed = pyqtSignal(dict)

    def __init__(self, max_items: int = 200):
        super().__init__()

        self._items: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self.max_items = max_items

    @property
    def items(self) -> list[dict[str, Any]]:
        """
        Return a snapshot of current notifications.
        """
        with self._lock:
            return list(self._items)

    def push(
        self,
        *args: Any,
        level: Optional[str] = None,
        title: Optional[str] = None,
        message: Optional[str] = None,
        msg: Optional[str] = None,
        **kwargs: Any,
    ) -> None:
        """
        Push a notification.

        Supports:
        - push("ok", "Title", "Message")               (canonical 3 positional args)
        - push("Title", "Message", level="ok")         (2 positional + level kwarg)
        - push("Message", title="Title", level="ok")   (1 positional + kwargs)
        - push("Message", level="success")             (1 positional message/title)
        - push(level="ok", title="Title", message="Msg") (all kwargs)
        """
        final_level = level or kwargs.get("lvl")
        final_title = title
        final_msg = message or msg or kwargs.get("body", "")

        level_map = {
            "success": "ok",
            "warning": "warn",
            "danger": "error",
            "critical": "error",
            "err": "error",
            "information": "info",
        }

        if len(args) >= 3:
            final_level = str(args[0])
            final_title = str(args[1])
            final_msg = str(args[2])
        elif len(args) == 2:
            if final_level is not None:
                final_title = str(args[0])
                final_msg = str(args[1])
            else:
                arg0_lower = str(args[0]).lower()
                if arg0_lower in ("ok", "warn", "warning", "error", "info", "success", "danger"):
                    final_level = arg0_lower
                    final_title = str(args[1])
                    final_msg = ""
                else:
                    final_level = "info"
                    final_title = str(args[0])
                    final_msg = str(args[1])
        elif len(args) == 1:
            val = str(args[0])
            if final_title is not None and not final_msg:
                final_msg = val
            elif final_msg and final_title is None:
                final_title = val
            elif final_title is None:
                final_title = val
                final_msg = ""
            else:
                final_msg = val

        if not final_level:
            final_level = "info"
        final_level = str(final_level).lower()
        final_level = level_map.get(final_level, final_level)
        if final_level not in ("ok", "warn", "error", "info"):
            final_level = "info"

        final_title = str(final_title or "Notification")
        final_msg = str(final_msg or "")

        item = {
            "ts": datetime.datetime.now().strftime("%H:%M:%S"),
            "level": final_level,
            "title": final_title,
            "msg": final_msg,
        }

        with self._lock:
            self._items.insert(0, item)
            self._items = self._items[: self.max_items]

        self.pushed.emit(item)

    def clear(self) -> None:
        """
        Clear all notifications.
        """
        with self._lock:
            self._items.clear()
