#!/usr/bin/env python3
"""Fail CI when docs drift away from the code.

README, the English docs, SECURITY.md, the showcase and the release notes used
to quote five different versions (4.0, 4.1, 5.0, 247 tools, 1,829 checks).
`docs/stats.json` is now canonical; this check enforces it.
"""
from __future__ import annotations

import json
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATS = json.loads((ROOT / "docs" / "stats.json").read_text(encoding="utf-8"))

errors: list[str] = []
checks = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global checks
    checks += 1
    if not condition:
        errors.append(f"{label}: {detail}")
        print(f"  FAIL  {label}  {detail}")
    else:
        print(f"  PASS  {label}")


def text(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def main() -> int:
    version = STATS["version"]
    total = STATS["checks"]
    formatted = f"{total:,}"
    encoded = formatted.replace(",", "%2C")

    # ---------------------------------------------------------- version sync
    paths = text("app/core/paths.py")
    check("paths.py version", f'APP_VERSION = "{version}"' in paths)
    project = tomllib.loads(text("pyproject.toml"))
    check("pyproject version", project["project"]["version"] == version,
          project["project"]["version"])
    check("installer version", f'#define AppVersion "{version}"' in text("packaging/installer.iss"))
    check("stats total matches suites", total == sum(STATS["suites"].values()))

    # ----------------------------------------------------------- numbers
    readme = text("README.md")
    check("README badge", f"checks-{encoded}%20passing" in readme, formatted)
    check("README tool count", "tools-254-5B8CFF" in readme)
    check("README totals", formatted in readme, formatted)
    english = text("docs/README.en.md")
    check("English docs totals", formatted in english, formatted)
    check("English docs suite lines", str(STATS["suites"]["security"]) in english)
    showcase = text("docs/showcase.html")
    showcase_prose = re.sub(r"data:image/png;base64,[A-Za-z0-9+/=]+", "[IMG]", showcase)
    check("showcase totals", formatted in showcase_prose, formatted)
    check("showcase Persian totals", f"{total:,}".translate(
        str.maketrans("0123456789,", "۰۱۲۳۴۵۶۷۸۹٬")) in showcase_prose)
    check("showcase PDF count", "PDF category — 37 tools" in showcase_prose)
    check("showcase no stale 34", "34 tools" not in showcase_prose)
    check("showcase no stale checks",
          "1,829" not in showcase_prose and "1829" not in showcase_prose)

    # --------------------------------------------------- forbidden old claims
    forbidden = [
        (r"WinGenius\s*4\.[0-9]", "an old WinGenius version"),
        (r"247\s*(?:tools|ابزار|Tool)", "247 tools"),
        (r"1[,\u066c]829|1[,\u066c]828", "the old 1,829 check total"),
        (r"Python\s*3\.11|پایتون\s*۳\.۱۱\+", "Python 3.11 as a supported version"),
        (r"example\.invalid", "a placeholder URL"),
        (r"کاملاً آفلاین کار می\u200cکند|Everything runs locally — no telemetry", "a false offline claim"),
        (r"۴۵۹ موتور|459 functional", "the old functional count"),
    ]
    doc_files = ["README.md", "docs/README.en.md", "docs/install_fa.md", "docs/run_fa.md",
                 "docs/summary_v5_fa.md", "SECURITY.md", "CHANGELOG.md",
                 "THIRD-PARTY-NOTICES.md", "CONTRIBUTING.md", "docs/showcase.html",
                 "packaging/make_showcase.py"]
    for rel in doc_files:
        body = text(rel)
        if rel == "docs/showcase.html":
            # Screenshots are inlined as base64; random digits in them are not prose.
            body = re.sub(r"data:image/png;base64,[A-Za-z0-9+/=]+", "[IMG]", body)
        for pattern, label in forbidden:
            # Historical release notes legitimately mention old versions, and
            # two files explain *why* 3.11 is unsupported / why a placeholder
            # URL was removed.
            if rel == "CHANGELOG.md" and label in (
                    "an old WinGenius version", "a placeholder URL",
                    "Python 3.11 as a supported version"):
                continue
            if rel == "docs/run_fa.md" and label == "Python 3.11 as a supported version":
                continue
            if re.search(pattern, body):
                check(f"{rel} has no {label}", False, f"matches /{pattern}/")

    # ------------------------------------------------------- required content
    check("SECURITY.md explains network tools", "--check-update" in text("SECURITY.md"))
    check("CHANGELOG has Unreleased", "## [Unreleased]" in text("CHANGELOG.md"))
    check("README documents --check-update", "--check-update" in readme)
    check("README links to the architecture", "ARCHITECTURE.md" in readme)
    check("README links to the OpenAPI spec", "docs/openapi.yaml" in readme)
    check("run_fa.md does not recommend query tokens",
          "?token=…" not in text("docs/run_fa.md") or
          "پیش\u200cفرض **رد می\u200cشود**" in text("docs/run_fa.md"))
    check("notices mention pyHanko", "`pyHanko`" in text("THIRD-PARTY-NOTICES.md"))
    check("stats.json is valid", isinstance(STATS["suites"], dict) and total > 0)

    print(f"\n  {checks - len(errors)}/{checks} documentation checks passed")
    if errors:
        print("\n".join("  - " + error for error in errors))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
