"""Plugin system — drop a .py file in the plugins folder and it becomes a tool.

Two locations are scanned:
    <app data>/WinGenius/plugins/*.py     (user plugins)
    <project>/plugins/*.py                (developer plugins)

A plugin is a normal Python module; it can either use the decorator API:

    from app.core.registry import tool
    from app.core.spec import K_TEXT, Field, RunResult

    @tool(id="hello", category="dev", name="سلام", name_en="Hello", ...)
    def hello(ctx, values):
        return RunResult(message="hi")

…or simply expose a list named TOOLS containing ToolSpec objects.
"""
from __future__ import annotations

import importlib.util
import hashlib
import json
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from .logger import get_logger
from .paths import data_dir, project_root
from .privacy import atomic_json_write

log = get_logger("plugins")
_LOADED: dict[str, str] = {}


def _trusted() -> dict:
    try:
        raw = json.loads((data_dir() / "trusted_plugins.json").read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, ValueError):
        return {}


def trust_plugin(path: Path) -> None:
    """Explicit approval for these exact bytes. Never called during discovery."""
    path = Path(path).resolve()
    approved = _trusted()
    approved[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    atomic_json_write(data_dir() / "trusted_plugins.json", approved)



@dataclass
class PluginInfo:
    path: Path
    name: str
    tools: int = 0
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


def plugins_dir(create: bool = True) -> Path:
    path = data_dir() / "plugins"
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def plugin_dirs() -> List[Path]:
    dirs = [plugins_dir(create=False)]
    builtin = project_root() / "plugins"
    if builtin.exists():
        dirs.append(builtin)
    return [path for path in dirs if path.exists()]


def discover() -> List[Path]:
    found: List[Path] = []
    for folder in plugin_dirs():
        found += sorted(p for p in folder.glob("*.py") if not p.name.startswith("_"))
    return found


def load_plugins(registry_module=None) -> List[PluginInfo]:
    """Import every plugin file; returns per-plugin status (never raises)."""
    from . import registry as registry_module_impl

    registry = registry_module or registry_module_impl
    results: List[PluginInfo] = []
    for path in discover():
        module_name = f"wingenius_plugin_{path.stem}"
        info = PluginInfo(path=path, name=path.stem)
        before = registry.count()
        try:
            identity = str(path.resolve())
            source = path.read_bytes()
            digest = hashlib.sha256(source).hexdigest()
            if _trusted().get(identity) != digest:
                info.error = "approval required: untrusted or changed plugin"
                results.append(info)
                continue
            if _LOADED.get(identity) == digest:
                results.append(info)
                continue
            if identity in _LOADED:
                info.error = "plugin changed: restart the app to load the approved update"
                results.append(info)
                continue
            spec = importlib.util.spec_from_file_location(module_name, path)
            if spec is None or spec.loader is None:
                raise ImportError("cannot create module spec")
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            with registry.protected_registration():
                # Execute the bytes that were approved, not a second file read.
                exec(compile(source, str(path), "exec"), module.__dict__)
                extra = getattr(module, "TOOLS", None)
                if extra:
                    for tool_spec in extra:
                        registry.register(tool_spec)
            _LOADED[identity] = digest
            info.tools = registry.count() - before
            if info.tools == 0:
                info.error = "no tools registered (missing @tool decorator or TOOLS list)"
        except Exception as exc:  # pragma: no cover - user code
            info.error = f"{type(exc).__name__}: {exc}"
            log.error("plugin %s failed:\n%s", path.name, traceback.format_exc())
            sys.modules.pop(module_name, None)
        results.append(info)
    return results


def write_example_plugin() -> Path:
    """Create a documented example plugin (used by Settings → Plugins)."""
    target = plugins_dir() / "example_hello.py"
    if target.exists():
        return target
    target.write_text(
        '''"""Example WinGenius plugin — returns a friendly greeting.

Reload the app (or Settings → Plugins → Reload) to see it in the "dev" category.
"""
from app.core.registry import tool
from app.core.spec import K_TEXT, Field, RunResult


@tool(
    id="plugin_hello",
    category="dev",
    name="پلاگین نمونه",
    name_en="Example plugin",
    desc="یک ابزار نمونه که نشان می‌دهد پلاگین‌ها چطور کار می‌کنند.",
    desc_en="A demo tool showing how plugins work.",
    icon="star",
    keywords=("plugin", "example", "plagin"),
    fields=[Field("name", K_TEXT, "نام شما", "Your name", default="دوست عزیز")],
)
def plugin_hello(ctx, values):
    name = str(values.get("name") or "")
    return RunResult(
        message=ctx.M(f"سلام {name}! این ابزار از یک پلاگین آمده.", f"Hello {name}! This tool came from a plugin."),
        details=ctx.M("برای ساخت پلاگین خودتان فایل plugins/example_hello.py را ببینید.",
                      "See plugins/example_hello.py to build your own."),
    )
''',
        encoding="utf-8",
    )
    return target


def open_plugins_folder() -> Path:
    from .utils import open_path

    folder = plugins_dir()
    open_path(folder)
    return folder
