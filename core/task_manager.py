"""
Global Background Task Manager for Admin Suite.

Tracks long-running operations (SFTP transfers, VAPT audits, Ansible runs,
profile pings, and DB queries) with progress tracking and universal cancellation.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from PyQt6.QtCore import QObject, pyqtSignal

logger = logging.getLogger(__name__)


@dataclass
class BackgroundTask:
    """Represents an active or finished background operation."""
    id: str
    title: str
    description: str = ""
    category: str = "General"
    progress: int = -1  # -1 for indeterminate, 0..100 for percent
    status: str = "running"  # "running", "completed", "failed", "cancelled"
    cancel_cb: Optional[Callable[[], None]] = None
    started_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None
    error: Optional[str] = None
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def is_active(self) -> bool:
        return self.status == "running"

    @property
    def elapsed_seconds(self) -> float:
        end = self.finished_at if self.finished_at else time.time()
        return max(0.0, end - self.started_at)


class TaskManager(QObject):
    """
    Central event-driven registry and controller for background tasks.
    """

    task_added = pyqtSignal(object)       # Emits BackgroundTask
    task_updated = pyqtSignal(object)     # Emits BackgroundTask
    task_finished = pyqtSignal(object)    # Emits BackgroundTask
    task_cancelled = pyqtSignal(object)   # Emits BackgroundTask
    active_count_changed = pyqtSignal(int)

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._tasks: dict[str, BackgroundTask] = {}
        self._lock = threading.Lock()
        self._counter = 0

    def start_task(
        self,
        title: str,
        description: str = "",
        category: str = "General",
        cancel_cb: Optional[Callable[[], None]] = None,
        progress: int = -1,
        details: Optional[dict[str, Any]] = None,
    ) -> BackgroundTask:
        """Register and start a new background task."""
        with self._lock:
            self._counter += 1
            task_id = f"task_{int(time.time())}_{self._counter}"
            task = BackgroundTask(
                id=task_id,
                title=title,
                description=description,
                category=category,
                progress=progress,
                status="running",
                cancel_cb=cancel_cb,
                started_at=time.time(),
                details=details or {},
            )
            self._tasks[task_id] = task
            active_cnt = sum(1 for t in self._tasks.values() if t.is_active)

        self.task_added.emit(task)
        self.active_count_changed.emit(active_cnt)
        return task

    def update_task(
        self,
        task_id: str,
        progress: Optional[int] = None,
        description: Optional[str] = None,
    ) -> Optional[BackgroundTask]:
        """Update progress or description of a running task."""
        with self._lock:
            task = self._tasks.get(task_id)
            if not task or not task.is_active:
                return None
            if progress is not None:
                task.progress = max(-1, min(100, int(progress)))
            if description is not None:
                task.description = description

        self.task_updated.emit(task)
        return task

    def finish_task(
        self,
        task_id: str,
        error: Optional[str] = None,
    ) -> Optional[BackgroundTask]:
        """Mark task as completed or failed."""
        with self._lock:
            task = self._tasks.get(task_id)
            if not task or not task.is_active:
                return None

            task.finished_at = time.time()
            if error:
                task.status = "failed"
                task.error = str(error)
            else:
                task.status = "completed"
                task.progress = 100

            active_cnt = sum(1 for t in self._tasks.values() if t.is_active)

        self.task_finished.emit(task)
        self.active_count_changed.emit(active_cnt)
        return task

    def cancel_task(self, task_id: str) -> bool:
        """Cancel a running task using its cancel callback."""
        cb = None
        with self._lock:
            task = self._tasks.get(task_id)
            if not task or not task.is_active:
                return False

            task.status = "cancelled"
            task.finished_at = time.time()
            cb = task.cancel_cb
            active_cnt = sum(1 for t in self._tasks.values() if t.is_active)

        if callable(cb):
            try:
                cb()
            except Exception as e:
                logger.error(f"Error executing cancel callback for {task_id}: {e}")

        self.task_cancelled.emit(task)
        self.active_count_changed.emit(active_cnt)
        return True

    def cancel_all(self) -> None:
        """Cancel all running tasks."""
        active_ids = [t.id for t in self.get_active_tasks()]
        for tid in active_ids:
            self.cancel_task(tid)

    def get_active_tasks(self) -> list[BackgroundTask]:
        """Return all currently running tasks."""
        with self._lock:
            return [t for t in self._tasks.values() if t.is_active]

    def get_all_tasks(self) -> list[BackgroundTask]:
        """Return all tasks ordered newest first."""
        with self._lock:
            return sorted(self._tasks.values(), key=lambda t: t.started_at, reverse=True)

    def clear_finished(self) -> None:
        """Remove completed, failed, or cancelled tasks."""
        with self._lock:
            self._tasks = {k: v for k, v in self._tasks.items() if v.is_active}
