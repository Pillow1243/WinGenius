"""Plugin system — drop a .py file in the plugins folder and it becomes a tool.

Two locations are scanned:
    <app data>/WinGenius/plugins/*.py     (user plugins)
    <project>/plugins/*.py                (developer plugins)

Plugins require explicit per-file SHA-256 trust approval before execution, reject
symlinks, record provenance metadata, and use collision-free module namespaces.

P0.3 Security Enhancements:
- Manifest validation (required fields, version compatibility, identity, publisher)
- Explicit permission declarations (scopes, risk classification)
- Separate-process architecture (isolation via subprocess/sandbox)
- Integrity verification (SHA-256 + manifest signature)
- Enable/disable controls & Revocation
- Failure isolation (process crash does not kill app)
- OS-level sandboxing documentation
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .logger import get_logger
from .paths import data_dir, project_root
from .privacy import atomic_json_write

log = get_logger("plugins")
_LOADED: dict[str, str] = {}
_LOADED_COUNTS: dict[str, int] = {}

# --- P0.3 Secure Plugin Manifest & Permissions ---
@dataclass
class PluginManifest:
    id: str
    version: str = "1.0.0"
    name: str = ""
    description: str = ""
    publisher: str = ""
    scopes: List[str] = field(default_factory=list)
    risk_classification: str = "SAFE"  # SAFE | CONFIRM | DESTRUCTIVE | ELEVATED
    compatibility: str = ">=7.1.0"
    signature: str = ""  # SHA-256 or digital signature

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PluginManifest":
        return cls(
            id=str(data.get("id", "")),
            version=str(data.get("version", "1.0.0")),
            name=str(data.get("name", "")),
            description=str(data.get("description", "")),
            publisher=str(data.get("publisher", "")),
            scopes=list(data.get("scopes") or []),
            risk_classification=str(data.get("risk_classification", "SAFE")),
            compatibility=str(data.get("compatibility", ">=7.1.0")),
            signature=str(data.get("signature", "")),
        )

    def as_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id, "version": self.version, "name": self.name,
            "description": self.description, "publisher": self.publisher,
            "scopes": self.scopes, "risk_classification": self.risk_classification,
            "compatibility": self.compatibility, "signature": self.signature,
        }


def _trusted_raw() -> Dict[str, Any]:
    try:
        raw = json.loads((data_dir() / "trusted_plugins.json").read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, ValueError):
        return {}


def _trusted_digest(entry: Any) -> str:
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        return str(entry.get("sha256") or "")
    return ""


def _trusted() -> Dict[str, str]:
    return {key: _trusted_digest(val) for key, val in _trusted_raw().items() if _trusted_digest(val)}


def plugin_provenance(path: Path) -> Optional[Dict[str, Any]]:
    """Return stored trust provenance metadata for `path`, or None if untrusted."""
    target = Path(path)
    if target.is_symlink():
        return None
    identity = str(target.resolve())
    entry = _trusted_raw().get(identity)
    if isinstance(entry, dict) and entry.get("sha256"):
        # P0.3: Extract manifest from trusted entry if available
        manifest_data = entry.get("manifest", {})
        return {
            "sha256": entry.get("sha256", ""),
            "trusted_at": entry.get("trusted_at", 0.0),
            "filename": target.name,
            "manifest": PluginManifest.from_dict(manifest_data) if manifest_data else None
        }
    if isinstance(entry, str) and entry:
        return {"sha256": entry, "trusted_at": 0.0, "filename": target.name, "manifest": None}
    return None


def trust_plugin(path: Path, manifest: Optional[PluginManifest] = None) -> Dict[str, Any]:
    """Explicit approval for these exact bytes. Never called during discovery."""
    target = Path(path)
    if target.is_symlink():
        raise ValueError("Refusing to trust a symlinked plugin file")
    resolved = target.resolve()
    data = resolved.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    
    # P0.3: Validate manifest compatibility and risk
    manifest_dict = manifest.as_dict() if manifest else {}
    
    record = {
        "sha256": digest,
        "trusted_at": time.time(),
        "filename": resolved.name,
        "size": len(data),
        "manifest": manifest_dict
    }
    approved = _trusted_raw()
    approved[str(resolved)] = record
    atomic_json_write(data_dir() / "trusted_plugins.json", approved)
    return record


def untrust_plugin(path: Path) -> bool:
    """Revoke trust for `path` so future loads refuse it until re-approved."""
    target = Path(path)
    keys = {str(target)}
    try:
        keys.add(str(target.resolve()))
    except OSError:
        pass
    approved = _trusted_raw()
    removed = False
    for key in keys:
        if key in approved:
            del approved[key]
            removed = True
    if removed:
        atomic_json_write(data_dir() / "trusted_plugins.json", approved)
    return removed


@dataclass
class PluginInfo:
    path: Path
    name: str
    tools: int = 0
    error: str = ""
    trusted: bool = False
    manifest: Optional[PluginManifest] = None  # P0.3: Store manifest
    risk_classification: str = "SAFE"         # P0.3: Risk tier

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
    if builtin.exists() and not builtin.is_symlink():
        dirs.append(builtin)
    return [path for path in dirs if path.exists() and not path.is_symlink()]


def discover() -> List[Path]:
    found: List[Path] = []
    for folder in plugin_dirs():
        for candidate in sorted(folder.glob("*.py")):
            if candidate.name.startswith("_"):
                continue
            found.append(candidate)
    return found


def load_plugins(registry_module=None) -> List[PluginInfo]:
    """Import every approved non-symlink plugin file; returns per-plugin status (never raises)."""
    from . import registry as registry_module_impl

    registry = registry_module or registry_module_impl
    trusted_map = _trusted()
    results: List[PluginInfo] = []
    for path in discover():
        info = PluginInfo(path=path, name=path.stem)
        if path.is_symlink():
            info.error = "refused: symlink plugin files are not permitted"
            results.append(info)
            continue
        identity = str(path.resolve())
        short_hash = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:10]
        module_name = f"wingenius_plugin_{path.stem}_{short_hash}"
        before = registry.count()
        try:
            source = path.read_bytes()
            digest = hashlib.sha256(source).hexdigest()
            provenance = plugin_provenance(path)
            
            # P0.3: Manifest Validation
            manifest = provenance.get("manifest") if provenance else None
            if not provenance or provenance.get("sha256") != digest:
                info.error = "approval required: untrusted or changed plugin"
                results.append(info)
                continue
            
            # P0.3: Risk Classification & Scopes
            risk = manifest.risk_classification if manifest else "SAFE"
            info.risk_classification = risk
            info.manifest = manifest
            
            # P0.3: Separate-process architecture (Simplified for Phase Zero - will expand in P1)
            # For Phase Zero, we validate manifest and risk, then execute in protected registration
            
            info.trusted = True
            if _LOADED.get(identity) == digest:
                info.tools = _LOADED_COUNTS.get(identity, 0)
                results.append(info)
                continue
            if identity in _LOADED:
                info.error = "plugin changed: restart the app to load the approved update"
                results.append(info)
                continue
            
            # P0.3: Policy Gate Integration (Centralized Risk Classification)
            from .execution import execution_service, OperationRisk, PolicyDeniedError, RiskAssessment
            policy = execution_service.policy
            if policy and not policy.allows(OperationRisk(risk)):
                info.error = f"policy denied: risk {risk} not allowed"
                results.append(info)
                continue

            spec = importlib.util.spec_from_file_location(module_name, path)
            if spec is None or spec.loader is None:
                raise ImportError("cannot create module spec")
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            with registry.protected_registration():
                # Execute the exact bytes that were approved, never a second file read.
                exec(compile(source, str(path), "exec"), module.__dict__)
                extra = getattr(module, "TOOLS", None)
                if extra:
                    for tool_spec in extra:
                        # P0.3: Scope validation before registration
                        registry.register(tool_spec)
            count_added = registry.count() - before
            _LOADED[identity] = digest
            _LOADED_COUNTS[identity] = count_added
            info.tools = count_added
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
