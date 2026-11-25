"""
Standard cross-platform font helpers for Admin Suite.

Qt's QFont(family_string, size) constructor treats comma-separated CSS strings
as a single literal family name rather than a fallback list. Using setFamilies()
with system-font fallbacks guarantees predictable rendering across Linux,
macOS, and Windows.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Iterable

from PyQt6.QtGui import QFont, QFontDatabase, QGuiApplication

UI_FONT_FAMILIES: tuple[str, ...] = (
    "Inter",
    "Cantarell",
    "Noto Sans",
    "Ubuntu",
    "DejaVu Sans",
    "Segoe UI",
    "Helvetica Neue",
    "Arial",
)

MONO_FONT_FAMILIES: tuple[str, ...] = (
    "JetBrains Mono",
    "Fira Code",
    "Cascadia Mono",
    "Noto Sans Mono",
    "DejaVu Sans Mono",
    "Ubuntu Mono",
    "Liberation Mono",
    "Consolas",
    "Courier New",
)


def system_ui_family() -> str:
    """Return the system default General font family name, or empty string."""
    if QGuiApplication.instance() is not None:
        return QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont).family()
    return ""


def system_mono_family() -> str:
    """Return the system default Fixed/Monospace font family name, or empty string."""
    if QGuiApplication.instance() is not None:
        return QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont).family()
    return ""


def system_ui_point_size(fallback: int = 10) -> int:
    """Return the system default General font point size."""
    if QGuiApplication.instance() is not None:
        pt = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont).pointSize()
        if pt > 0:
            return pt
    return fallback


def qss_font_family_ui() -> str:
    """Return a CSS/QSS font-family declaration string for UI sans-serif text using system font first."""
    sys_fam = system_ui_family()
    if sys_fam and sys_fam not in UI_FONT_FAMILIES:
        return f"'{sys_fam}', 'Inter', 'Cantarell', 'Noto Sans', 'Ubuntu', 'DejaVu Sans', 'Segoe UI', sans-serif"
    return "'Inter', 'Cantarell', 'Noto Sans', 'Ubuntu', 'DejaVu Sans', 'Segoe UI', sans-serif"


def qss_font_family_mono() -> str:
    """Return a CSS/HTML font-family declaration string for monospace text using system monospace first."""
    sys_fam = system_mono_family()
    if sys_fam and sys_fam not in MONO_FONT_FAMILIES:
        return f"'{sys_fam}', 'JetBrains Mono', 'Fira Code', 'Noto Sans Mono', 'DejaVu Sans Mono', Consolas, monospace"
    return "'JetBrains Mono', 'Fira Code', 'Noto Sans Mono', 'DejaVu Sans Mono', Consolas, monospace"


@lru_cache(maxsize=64)
def _cached_font(
    families: tuple[str, ...],
    point_size: int,
    bold: bool,
    italic: bool,
    style_hint: QFont.StyleHint,
    system_fixed: bool,
) -> QFont:
    font = QFont()
    if QGuiApplication.instance() is not None:
        sys_kind = (
            QFontDatabase.SystemFont.FixedFont
            if system_fixed
            else QFontDatabase.SystemFont.GeneralFont
        )
        sys_family = QFontDatabase.systemFont(sys_kind).family()
        ordered: list[str] = []
        if sys_family:
            ordered.append(sys_family)
        for fam in families:
            if fam not in ordered:
                ordered.append(fam)
    else:
        ordered = list(families)

    font.setFamilies(ordered)
    if point_size > 0:
        font.setPointSize(point_size)
    font.setBold(bold)
    font.setItalic(italic)
    font.setStyleHint(style_hint)
    return font


def ui_font(
    point_size: int | None = None,
    bold: bool = False,
    italic: bool = False,
    extra_families: Iterable[str] | None = None,
) -> QFont:
    """
    Create a standard sans-serif UI QFont with proper cross-platform fallbacks.
    Returns a fresh copy safe to mutate locally. Defaults to system font point size.
    """
    effective_size = point_size if (point_size is not None and point_size > 0) else system_ui_point_size(10)
    families = tuple(extra_families) + UI_FONT_FAMILIES if extra_families else UI_FONT_FAMILIES
    base = _cached_font(
        families,
        effective_size,
        bold,
        italic,
        QFont.StyleHint.SansSerif,
        False,
    )
    return QFont(base)


def mono_font(
    point_size: int | None = None,
    bold: bool = False,
    italic: bool = False,
    extra_families: Iterable[str] | None = None,
) -> QFont:
    """
    Create a standard monospace QFont for terminals, code editors, logs, and tables.
    Returns a fresh copy safe to mutate locally. Defaults to system monospace point size.
    """
    effective_size = point_size if (point_size is not None and point_size > 0) else system_ui_point_size(10)
    families = tuple(extra_families) + MONO_FONT_FAMILIES if extra_families else MONO_FONT_FAMILIES
    base = _cached_font(
        families,
        effective_size,
        bold,
        italic,
        QFont.StyleHint.Monospace,
        True,
    )
    return QFont(base)
