"""Keep standalone and pytest suites away from the developer's real profile."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

_sandbox: Path | None = None


def isolate_profile() -> Path:
    global _sandbox
    if _sandbox is None:
        _sandbox = Path(tempfile.mkdtemp(prefix="wg-test-profile-"))
        home = _sandbox / "home"
        home.mkdir()
        config = _sandbox / "config"
        config.mkdir()
        os.environ["HOME"] = str(home)
        os.environ["USERPROFILE"] = str(home)
        os.environ["XDG_CONFIG_HOME"] = str(config)
        os.environ["APPDATA"] = str(config)
        os.environ["LOCALAPPDATA"] = str(_sandbox / "local")
        os.environ.pop("WINGENIUS_PORTABLE", None)
    return _sandbox
