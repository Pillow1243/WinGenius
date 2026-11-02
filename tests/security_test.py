#!/usr/bin/env python3
"""Security + consistency regression suite.

These are the checks that were missing when the audit found real bugs in the
redaction of ``crash_report`` and in ``vault.extract``.  Every check here is a
regression test for a concrete defect, not a generic smoke test.

    python tests/security_test.py       # standalone (same style as the others)
    pytest -q tests/security_test.py    # also works
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("WINGENIUS_NO_LAUNCH", "1")

from app.core.registry import load_all  # noqa: E402

PASS = 0
FAIL = 0
FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        FAILURES.append(f"{name} — {detail}")
        print(f"  FAIL  {name}  {detail}")
        # pytest must see a real failure too (not just a counter).
        raise AssertionError(f"{name} — {detail}")


def skip(name: str, why: str) -> None:
    print(f"  SKIP  {name}  ({why})")


# --------------------------------------------------------------- redaction
def test_redaction() -> None:
    print("\n[crash-report redaction]")
    from app.tools.system_extra2 import _redact

    cases = [
        (r"C:\Users\Ali\Desktop\private.pdf", "Ali", "Windows profile path"),
        ("/home/someone/private.txt", "someone", "POSIX profile path"),
        ("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9", "eyJhbGciOi", "Bearer token"),
        ("password=hello world", "world", "password with spaces"),
        ("api_key = 12345", "12345", "API key"),
        ("user email: ali.reza@gmail.com", "ali.reza", "email address"),
        ("peer 192.168.1.42 responded", "192.168.1.42", "IPv4 address"),
        ("رمز عبور: my-secret-123", "my-secret-123", "Persian secret hint"),
    ]
    for raw, secret, label in cases:
        cleaned = _redact(raw)
        check(f"redaction removes {label}", secret not in cleaned, repr(cleaned))

    home = str(Path.home())
    check("redaction removes the current home directory",
          _redact(f"log at {home}/notes.txt").find(home) == -1,
          home)
    check("redaction keeps harmless diagnostics intact",
          "WinGenius 5.0.0" in _redact("App: WinGenius 5.0.0"), "over-redaction")


# ------------------------------------------------------------------- vault
def test_vault_paths() -> None:
    print("\n[vault entry names]")
    from app.core.vault import Vault, _safe_entry_name

    check("vault normalises a traversal name", _safe_entry_name("../../escaped.txt") == "escaped.txt")
    check("vault normalises Windows separators",
          _safe_entry_name(r"..\\Windows\\System32\\x.dll") == "x.dll")
    check("vault rejects an empty name", _safe_entry_name("") == "entry.bin")

    sandbox = Path(tempfile.mkdtemp(prefix="wg_sec_vault_"))
    vault = Vault(sandbox / "test.wgv", password="correct horse")
    vault.create()
    vault.add_text("../../escaped.txt", "PWNED")
    out = sandbox / "out"
    written = vault.extract_all(out)
    check("vault extraction stays inside the destination",
          all(str(p.resolve()).startswith(str(out.resolve())) for p in written),
          str(written))
    escaped = sandbox / "escaped.txt"
    check("vault extraction writes no sibling file", not escaped.exists(), str(escaped))
    check("vault round-trips the content", written[0].read_text() == "PWNED", "")


# ------------------------------------------------------------------- hosts
def test_hosts_validation() -> None:
    print("\n[hosts-file input validation]")
    from app.tools.network_extra import valid_domain

    good = ("example.com", "ads.example.com", "my-host.co.uk", "localhost")
    bad = ("evil.com\n1.2.3.4 bank.com", "evil.com\r\n1.2.3.4 bank.com",
           "https://example.com", "*.example.com", "two words.com", "", "-bad.com")
    for value in good:
        check(f"hosts accepts {value!r}", valid_domain(value))
    for value in bad:
        check(f"hosts rejects {value!r}", not valid_domain(value))


# -------------------------------------------------------------- local API
def test_api_hardening() -> None:
    print("\n[local API]")
    from app.core.api_server import ApiServer

    server = ApiServer(port=0, token="unit-token", lang="en")
    port = server.start()

    def call(host_header=None, path="/health", headers=None):
        request = urllib.request.Request(f"http://127.0.0.1:{port}{path}")
        request.add_header("X-WinGenius-Token", "unit-token")
        if host_header:
            request.add_header("Host", host_header)
        for key, value in (headers or {}).items():
            request.add_header(key, value)
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, response.read(), dict(response.headers)
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read(), dict(exc.headers)

    try:
        status, _, _ = call()
        check("API accepts its own loopback Host", status == 200, str(status))
        status, _, _ = call(host_header="evil.example.com")
        check("API refuses a rebounded Host header", status == 403, str(status))
        status, _, hdrs = call()
        check("API sends nosniff", hdrs.get("X-Content-Type-Options") == "nosniff", str(hdrs))
        check("query tokens are off by default", server.allow_query_token is False, "")
        status, body, _ = call(path="/undo", headers={"Content-Type": "application/json"})
        # GET /undo is not a route; use POST below instead
        check("API has no GET /undo route", status == 404, str(status))

        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/undo",
            data=json.dumps({"count": "not-a-number"}).encode(),
            method="POST",
            headers={"X-WinGenius-Token": "unit-token", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                bad_status = response.status
        except urllib.error.HTTPError as exc:
            bad_status = exc.code
        check("API rejects a non-integer undo count", bad_status == 400, str(bad_status))
    finally:
        server.stop()

    # Force a fresh token into an isolated data folder and inspect its mode.
    from app.core.api_server import api_token, token_path

    previous = os.environ.get("XDG_CONFIG_HOME")
    previous_appdata = os.environ.get("APPDATA")
    isolated = tempfile.mkdtemp(prefix="wg_sec_cfg_")
    os.environ["XDG_CONFIG_HOME"] = isolated
    os.environ["APPDATA"] = isolated          # Windows data_dir() uses APPDATA
    try:
        fresh = token_path()
        fresh.unlink(missing_ok=True)
        api_token()
        if os.name != "nt":
            mode = stat.S_IMODE(fresh.stat().st_mode)
            check("API token file is not world-readable", not (mode & 0o077), oct(mode))
        else:
            skip("API token file mode", "Windows ACLs are not POSIX bits")
    finally:
        if previous is None:
            os.environ.pop("XDG_CONFIG_HOME", None)
        else:
            os.environ["XDG_CONFIG_HOME"] = previous
        if previous_appdata is None:
            os.environ.pop("APPDATA", None)
        else:
            os.environ["APPDATA"] = previous_appdata


# ------------------------------------------------------------- packaging
def test_package_consistency() -> None:
    print("\n[packaging & docs consistency]")
    import tomllib

    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    check("pyproject declares a build backend", "build-system" in pyproject)
    check("pyproject uses an SPDX license string",
          pyproject["project"]["license"] == "AGPL-3.0-or-later",
          str(pyproject["project"]["license"]))
    check("pyproject pins the Python minimum to 3.12",
          pyproject["project"]["requires-python"] == ">=3.12")
    check("ruff targets the same Python version",
          pyproject["tool"]["ruff"]["target-version"] == "py312",
          pyproject["tool"]["ruff"]["target-version"])
    check("coverage has a real gate", pyproject["tool"]["coverage"]["report"]["fail_under"] >= 70)

    names = {dep.split(";")[0].split(">")[0].split("=")[0].split("<")[0].strip().lower()
             for dep in pyproject["project"]["dependencies"]}
    check("pyHanko is a runtime dependency (signature tools work in the exe)",
          "pyhanko" in names, str(sorted(names)))
    check("mss is declared in pyproject", "mss" in names)
    check("opencv is declared in pyproject", "opencv-python-headless" in names)

    # Check the *git* mode: Windows filesystems have no POSIX execute bit.
    mode = subprocess.run(["git", "ls-files", "-s", "run.sh"], cwd=ROOT,
                          capture_output=True, text=True).stdout.strip()
    check("run.sh is executable in git", mode.startswith("100755"), mode)

    installer = (ROOT / "packaging" / "installer.iss").read_text(encoding="utf-8")
    check("installer has no placeholder publisher URL", "example.invalid" not in installer)
    check("installer links to the real repository", "github.com/Pillow1243/WinGenius" in installer)

    spec = (ROOT / "packaging" / "WinGenius.spec").read_text(encoding="utf-8")
    check("PyInstaller collects the lazy signing engine", '"pyhanko"' in spec)

    build_workflow = (ROOT / ".github" / "workflows" / "build-windows.yml").read_text(encoding="utf-8")
    check("release_tag input is actually used", "inputs.release_tag" in build_workflow)
    check("build workflow serialises concurrent runs", "concurrency:" in build_workflow)


# ---------------------------------------------------------------- updates
def test_version_helpers() -> None:
    print("\n[version helpers]")
    from app.core.updates import version_key

    check("versions compare numerically", version_key("5.0.10") > version_key("5.0.9"))
    check("v-prefix is ignored", version_key("v5.0.0") == version_key("5.0.0"))
    check("short versions are padded", version_key("5.1") == (5, 1, 0))


# --------------------------------------------------------------- registry
def test_registry_safety() -> None:
    print("\n[registry safety]")
    from app.core.registry import get
    from app.core.spec import Field, K_TEXT

    failures = load_all()
    check("every tool module imports cleanly", not failures, str(failures))
    for tool_id in ("vault_remove", "pdf_delete_pages", "trash_cleanup", "services_manager"):
        spec = get(tool_id)
        check(f"{tool_id} asks for confirmation", bool(spec and spec.danger), tool_id)
    field = Field("x", K_TEXT, "پرسش", "Question", help="فقط فارسی")
    check("English help falls back instead of disappearing", field.help_for("en") == "فقط فارسی")


def main(argv: list[str] | None = None) -> int:
    suites = [test_redaction, test_vault_paths, test_hosts_validation,
              test_api_hardening, test_package_consistency,
              test_version_helpers, test_registry_safety]
    for suite in suites:
        try:
            suite()
        except AssertionError:
            pass                    # already recorded by check()
        except Exception as exc:    # noqa: BLE001
            global FAIL
            FAIL += 1
            FAILURES.append(f"{suite.__name__} crashed: {type(exc).__name__}: {exc}")
            print(f"  CRASH {suite.__name__}: {type(exc).__name__}: {exc}")
    print("\n" + "=" * 66)
    print(f"  SECURITY CHECK  PASS {PASS}   FAIL {FAIL}")
    print("=" * 66)
    if FAILURES:
        print("Failures:")
        for item in FAILURES:
            print(f"  - {item}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
