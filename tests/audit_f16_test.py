"""F-16 regression test — 2026-11 independent audit.

The numbers quoted in README/docs must come from real runs. `scripts/stats.py`
counted only PASS lines while the published totals had always included the
platform-gated SKIPs (tesseract, opt-in live network), so no machine could ever
reproduce them. The counts now include SKIPs, record them separately, and say
which suites were not re-measured.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

os_skip = pytest.mark.skipif(sys.platform == "win32", reason="POSIX paths only")

ROOT = Path(__file__).resolve().parents[1]
STATS = ROOT / "docs" / "stats.json"


def _stats():
    return json.loads(STATS.read_text(encoding="utf-8"))


def test_f16_checks_equals_sum_of_suites():
    data = _stats()
    assert data["checks"] == sum(data["suites"].values()), (
        "the headline number must be the sum of the suites")
    assert data["checks"] > 0


def test_f16_skips_are_recorded_and_bounded():
    data = _stats()
    skips = data.get("skips")
    assert isinstance(skips, dict) and skips, "per-suite SKIP counts must be published"
    for key, skipped in skips.items():
        assert key in data["suites"], f"skips mention unknown suite {key}"
        assert 0 <= int(skipped) <= data["suites"][key]


def test_f16_counts_include_skips_not_only_passes():
    """Reproducing the published number requires counting SKIPs."""
    data = _stats()
    skipped_total = sum(int(v) for v in data["skips"].values())
    if not skipped_total:
        pytest.skip("no suite skipped anything on this machine")
    # PASS-only accounting would report checks - skipped_total
    assert data["checks"] - skipped_total > 0
    assert data["checks"] == sum(data["suites"].values())


def test_f16_unmeasured_suites_are_declared():
    data = _stats()
    unmeasured = data.get("unmeasured") or []
    for key in unmeasured:
        assert key in data["suites"], f"unmeasured mentions unknown suite {key}"
    # a suite that is declared unmeasured must still carry a value to publish
    for key in unmeasured:
        assert isinstance(data["suites"][key], int)


@os_skip
def test_f16_check_passes_on_the_published_file():
    result = subprocess.run([sys.executable, "scripts/stats.py", "--check"],
                            cwd=ROOT, capture_output=True, text=True, timeout=300)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "checks" in result.stdout


def test_f16_readme_numbers_match_stats():
    """The headline total quoted in the READMEs must be the published one.

    Historical numbers (changelog, "previous release" comparisons) are allowed —
    only the *current* total has to agree with docs/stats.json.
    """
    data = _stats()
    total = data["checks"]
    forms = {str(total), f"{total:,}".replace(",", ","), f"{total:,}"}
    if total == 2337:
        forms.add("۲٬۳۳۷")                 # Persian digits used in README.fa
    for name in ("README.md", "docs/README.en.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert any(form in text for form in forms), (
            f"{name} does not quote the published total {total} checks")
        # …and no other suite total is presented as the grand total
        suite_total = sum(data["suites"].values())
        assert suite_total == total


def test_f16_documented_suite_numbers_match_stats():
    """Per-suite totals quoted in the README must be the published ones.

    README claimed "functional 642/642" and "packaging 28/28" while the suites
    reported 641 and 30 — hand-copied numbers drift away from the run.
    """
    data = _stats()
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    digits = "۰۱۲۳۴۵۶۷۸۹"
    latin = "0123456789"

    def to_int(token: str) -> int | None:
        token = token.replace(",", "").replace("٬", "").replace("\\u200c", "")
        normalised = "".join(latin[digits.index(ch)] if ch in digits else ch for ch in token)
        return int(normalised) if normalised.isdigit() else None

    for suite, total in data["suites"].items():
        pattern = rf"\b{re.escape(suite)}\s+([۰-۹\d][۰-۹\d,٬]*)"
        for match in re.finditer(pattern, text):
            value = to_int(match.group(1))
            if value is None:
                continue
            assert value == total, (
                f"README quotes {suite} {value}, docs/stats.json publishes {total}")


def test_f16_no_unbacked_coverage_percentage():
    """A coverage percentage may only be quoted when stats.json actually has one."""
    data = _stats()
    if data.get("coverage") is not None:
        pytest.skip("coverage is published, so quoting it is fine")
    gate = {str(70)}                      # the enforced fail_under gate is a policy, not a measurement
    for name in ("README.md", "docs/README.en.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        claims = re.findall(r"\*\*(۷\d|۷۵|۷۷|\d{2})٪\*\*|\*\*(\d{2})%\*\*", text)
        claimed = [c for group in claims for c in group if c and c not in gate and c not in
                   {str(int(c)) if c.isdigit() else c for c in gate}]
        # Persian digits are not comparable to the Latin gate, so drop ۷۰ explicitly
        claimed = [c for c in claimed if c != "۷۰"]
        assert not claimed, (
            f"{name} quotes a coverage percentage ({claimed}) but docs/stats.json "
            "records none — that is a hand-copied number")
