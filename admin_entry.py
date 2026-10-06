# admin_entry.py

import os

# Helpful for PyInstaller / QtWebEngine packaged builds.
os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")

import sys
from pathlib import Path

_pkg_parent = str(Path(__file__).resolve().parent.parent)
if _pkg_parent not in sys.path:
    sys.path.insert(0, _pkg_parent)

from admin_suite.app import main

if __name__ == "__main__":
    raise SystemExit(main())
