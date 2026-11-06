"""F-08 regression test — 2026-11 independent audit.

security_headers must score header *values* (not mere presence) and a
plain-HTTP site can never reach a top grade.
"""
from __future__ import annotations

import os
import threading

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("WINGENIUS_NO_LAUNCH", "1")

# --------------------------------------------------------------------- F-08
# A site with every security header set to a strong value …
STRONG_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; frame-ancestors 'none'",
    "Strict-Transport-Security": "max-age=63072000; includeSubDomains",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "geolocation=(), camera=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}

# …and one where the same headers are present but useless (the old scoring
# awarded full marks for mere presence, which is finding F-08)
WEAK_HEADERS = {
    "Content-Security-Policy": "default-src * 'unsafe-inline' 'unsafe-eval'",
    "Strict-Transport-Security": "max-age=0",
    "X-Content-Type-Options": "sniff",
    "X-Frame-Options": "ALLOWALL",
    "Referrer-Policy": "unsafe-url",
    "Permissions-Policy": "geolocation=*",
    "Cross-Origin-Opener-Policy": "unsafe-none",
}


from tests.audit_helpers import _HeaderServer  # local HTTP test server


def _grade(result):
    import re

    match = re.search(r"grade ([A-F][+-]?)", result.message)
    score = re.search(r"Score (\d+)/(\d+)", result.message)
    return (match.group(1) if match else None,
            (int(score.group(1)), int(score.group(2))) if score else None)


def test_f08_weak_headers_do_not_get_top_grade():
    from app.core.spec import RunContext
    from app.tools.network_extra2 import security_headers

    server = _HeaderServer(WEAK_HEADERS)
    server.start()
    try:
        ctx = RunContext(lang="en", output_dir="/tmp")
        result = security_headers(ctx, {"url": server.url, "timeout": 5})
    finally:
        server.stop()
    grade, score = _grade(result)
    assert grade not in ("A", "A+"), f"weak headers must not score A/A+: {result.message}"
    assert score and score[0] < score[1] * 0.7, result.message
    # details must say *which* headers are present but invalid
    assert "invalid" in result.details.lower() or "ضعیف" in result.details or "✖" in result.details


def test_f08_strong_header_values_score_full_marks():
    """Unit level: each strong header value earns its full weight (the local test
    server is plain HTTP, where the overall grade is capped by design)."""
    from app.tools.network_extra2 import SECURITY_HEADERS, _header_value_score

    strong_by_key = {k.lower(): v for k, v in STRONG_HEADERS.items()}
    for key, _label, weight, _fa, _en in SECURITY_HEADERS:
        value = strong_by_key[key]
        points, note = _header_value_score(key, value, strong_by_key, "en")
        assert points == weight, f"{key}: {points}/{weight} ({note})"
    # …and every weak value earns strictly less than full marks
    weak_by_key = {k.lower(): v for k, v in WEAK_HEADERS.items()}
    for key, _label, weight, _fa, _en in SECURITY_HEADERS:
        if key in weak_by_key:
            points, note = _header_value_score(key, weak_by_key[key], weak_by_key, "en")
            assert points < weight, f"{key} must not earn full marks when weak"


def test_f08_strong_headers_over_http_still_capped():
    from app.core.spec import RunContext
    from app.tools.network_extra2 import security_headers

    server = _HeaderServer(STRONG_HEADERS)
    server.start()
    try:
        ctx = RunContext(lang="en", output_dir="/tmp")
        result = security_headers(ctx, {"url": server.url, "timeout": 5})
    finally:
        server.stop()
    grade, score = _grade(result)
    assert score == (90, 90), result.message          # headers themselves are perfect
    assert grade not in ("A", "A+"), f"plain HTTP must be capped: {result.message}"


def test_f08_http_is_capped_below_https():
    from app.core.spec import RunContext
    from app.tools.network_extra2 import security_headers

    server = _HeaderServer(STRONG_HEADERS)
    server.start()
    try:
        ctx = RunContext(lang="en", output_dir="/tmp")
        result = security_headers(ctx, {"url": server.url, "timeout": 5})  # plain http
    finally:
        server.stop()
    grade, score = _grade(result)
    assert grade not in ("A", "A+"), f"plain HTTP must not get A/A+: {result.message}"
    assert "HTTP" in result.details
