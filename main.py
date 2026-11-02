#!/usr/bin/env python3
"""WinGenius — دستیار همه‌کاره ویندوز

Run from source:      python main.py
Build a Windows exe:  see README.md  (python packaging/build_exe.py)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# make `app` importable when the script is launched from anywhere
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.bootstrap import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
