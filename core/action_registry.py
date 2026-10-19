"""
Central Action & Command Registry for Admin Suite.

Decouples action definition, keyboard shortcuts, and capabilities
from the monolithic MainWindow UI, allowing modular subsystems to
register and discover executable actions dynamically.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


@dataclass
class Action:
    """Action executable by the command palette, menus, or keyboard shortcuts."""
    id: str
    name: str
    callback: Callable[[], Any]
    category: str = "General"
    shortcut: str = ""
    hint: str = ""
    icon: str = "⚡"
    enabled: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_palette_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "hint": self.hint,
            "shortcut": self.shortcut,
            "category": self.category,
            "icon": self.icon,
            "cb": self.callback,
        }


class ActionRegistry:
    """Registry of application actions."""

    def __init__(self):
        self._actions: dict[str, Action] = {}

    def register(self, action: Action) -> None:
        """Register an action object."""
        self._actions[action.id] = action

    def register_action(
        self,
        id: str,
        name: str,
        callback: Callable[[], Any],
        category: str = "General",
        shortcut: str = "",
        hint: str = "",
        icon: str = "⚡",
        metadata: Optional[dict[str, Any]] = None,
    ) -> Action:
        """Convenience registration method."""
        action = Action(
            id=id,
            name=name,
            callback=callback,
            category=category,
            shortcut=shortcut,
            hint=hint,
            icon=icon,
            metadata=metadata or {},
        )
        self._actions[id] = action
        return action

    def unregister(self, action_id: str) -> None:
        """Unregister an action by ID."""
        self._actions.pop(action_id, None)

    def get(self, action_id: str) -> Optional[Action]:
        """Retrieve an action by ID."""
        return self._actions.get(action_id)

    def execute(self, action_id: str) -> Any:
        """Execute an action by ID."""
        action = self.get(action_id)
        if not action:
            raise KeyError(f"Action '{action_id}' not found")
        if not action.enabled:
            logger.warning(f"Action '{action_id}' is disabled.")
            return None
        return action.callback()

    def list_actions(self, category: Optional[str] = None) -> list[Action]:
        """List all actions, optionally filtered by category."""
        actions = list(self._actions.values())
        if category:
            actions = [a for a in actions if a.category.lower() == category.lower()]
        return actions

    def get_all(self) -> list[Action]:
        """Convenience method returning all actions."""
        return list(self._actions.values())

    def get_all_actions(self) -> list[Action]:
        """Convenience alias returning all actions."""
        return list(self._actions.values())

    def to_palette_items(self) -> list[dict[str, Any]]:
        """Return all actions formatted as command palette items."""
        return [a.to_palette_dict() for a in self._actions.values() if a.enabled]
