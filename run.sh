#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
#  WinGenius — launcher for Linux / macOS (source checkout).
#  On Windows use run.bat instead.
#
#  ./run.sh                 → گرافیکی (همان پنجره‌ی برنامه)
#  ./run.sh --doctor        → گزارش وضعیت محیط
#  ./run.sh --list          → فهرست ابزارها
#  ./run.sh --run pdf_to_images --file a.pdf --out out/
#  هر آرگومانی که به main.py می‌دهید، همین‌جا هم کار می‌کند.
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail
cd "$(dirname "$0")"

if [ -x ".venv/bin/python" ]; then
    PY=".venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PY="python3"
else
    echo "پایتون پیدا نشد. نصبش کن: sudo apt install python3 python3-venv   (یا brew install python)" >&2
    exit 2
fi

if ! "$PY" -c "import PySide6" >/dev/null 2>&1; then
    echo "وابستگی‌ها نصب نیستند. یک‌بار این را اجرا کن:" >&2
    echo "  $PY -m venv .venv && .venv/bin/python -m pip install -r requirements.txt" >&2
    exit 2
fi

exec "$PY" main.py "$@"
