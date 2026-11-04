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


CATEGORIES = re.findall(
    r'Category\("([a-z]+)",\s*"([^"]+)",\s*"([^"]+)"',
    (Path(__file__).resolve().parents[1] / "app" / "core" / "registry.py").read_text(encoding="utf-8"))
PER_CATEGORY = STATS.get("category_counts") or {}


def main() -> int:
    version = STATS["version"]
    total = STATS["checks"]
    tools = STATS["tools"]
    formatted = f"{total:,}"
    encoded = formatted.replace(",", "%2C")
    persian_total = f"{total:,}".translate(str.maketrans("0123456789,", "۰۱۲۳۴۵۶۷۸۹٬"))
    persian_tools = f"{tools:,}".translate(str.maketrans("0123456789,", "۰۱۲۳۴۵۶۷۸۹٬"))

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
    # The tool badge used to be hard-coded to 254; it now follows stats.json, so
    # adding tools can never leave the README quoting a number that is not real.
    check("README tool count", f"tools-{tools}-5B8CFF" in readme, str(tools))
    check("README totals", formatted in readme, formatted)
    check("README Persian totals", persian_total in readme, persian_total)
    check("README Persian tool count", persian_tools in readme, persian_tools)
    categories_fa = f"{STATS['categories']}".translate(
        str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")) + " دسته"
    check("README quotes the category count",
          categories_fa in readme or f"{STATS['categories']} دسته" in readme, categories_fa)
    missing_categories = [name for key, name, _en in CATEGORIES if key != "home" and name not in readme]
    check("README lists every category", not missing_categories, ", ".join(missing_categories))
    check("category counts add up", sum(PER_CATEGORY.values()) == tools,
          f"{sum(PER_CATEGORY.values())} != {tools}")
    # The ASCII-only guards above cannot see Persian digits: the first pass
    # shipped a Persian README that still said ۲۴۷ ابزار / ۱٬۸۲۹ بررسی /
    # ۴۵۹ تست / پایتون ۳.۱۱. Keep watching those too.
    stale_fa = [r"۲۴۷\s*ابزار", r"۱[٬,]۸۲[۰-۹]",
                r"۱۸[٬,]۷۸۷", r"پایتون\s*۳\.۱۱\s*و"]
    hits = [pattern for pattern in stale_fa if re.search(pattern, readme)]
    check("README has no stale Persian numbers", not hits, ", ".join(hits))
    english = text("docs/README.en.md")
    check("English docs totals", formatted in english, formatted)
    check("English docs suite lines", str(STATS["suites"]["security"]) in english)
    showcase = text("docs/showcase.html")
    showcase_prose = re.sub(r"data:image/png;base64,[A-Za-z0-9+/=]+", "[IMG]", showcase)
    check("showcase totals", formatted in showcase_prose, formatted)
    check("showcase Persian totals", persian_total in showcase_prose)
    check("showcase headline tool count", f"همه‌ی ابزارها ({tools})" in showcase_prose, str(tools))
    pdf_count = PER_CATEGORY.get("pdf")
    if pdf_count:
        check("showcase PDF count", f"PDF category — {pdf_count} tools" in showcase_prose,
              str(pdf_count))
    missing_showcase = [name for key, name, _en in CATEGORIES
                        if key != "home" and name not in showcase_prose]
    check("showcase lists every category", not missing_showcase, ", ".join(missing_showcase))
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
    check("CHANGELOG has a 6.0.0 entry", "## [6.0.0]" in text("CHANGELOG.md"))
    check("CHANGELOG has a 7.0.0 entry", "## [7.0.0]" in text("CHANGELOG.md"))
    check("ARCHITECTURE documents the Persian PDF engine", "persian_pdf" in text("ARCHITECTURE.md"))
    check("README documents the flow engine", "--run-flow" in readme)
    check("README links to the flow guide", "docs/v6_flow_fa.md" in readme)
    check("ARCHITECTURE documents flow conditions", "foreach" in text("ARCHITECTURE.md"))
    check("the flow guide exists", (ROOT / "docs" / "v6_flow_fa.md").is_file())
    check("stats.json is valid", isinstance(STATS["suites"], dict) and total > 0)

    print(f"\n  {checks - len(errors)}/{checks} documentation checks passed")
    if errors:
        print("\n".join("  - " + error for error in errors))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
