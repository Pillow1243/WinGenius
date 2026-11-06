"""Stream a test command and expose concise failure details as CI annotations."""
from __future__ import annotations

import collections
import subprocess
import sys


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: ci_capture.py command [arguments...]", file=sys.stderr)
        return 2
    recent = collections.deque(maxlen=80)
    failures = []
    process = subprocess.Popen(sys.argv[1:], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, encoding="utf-8", errors="replace", bufsize=1)
    assert process.stdout is not None
    for line in process.stdout:
        print(line, end="", flush=True)
        recent.append(line.rstrip())
        if "FAIL" in line or "Traceback" in line:
            failures.append(line.strip())
    status = process.wait()
    if status:
        details = "\n".join((failures[-20:] or list(recent)[-20:]))[-6000:]
        escaped = details.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        print(f"::error title=Test failure::{escaped}", flush=True)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
