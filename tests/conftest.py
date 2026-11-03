"""pytest fixtures for the legacy suites.

The four original suites were written as standalone scripts with their own
``main()`` builders.  pytest still *collected* their ``test_*`` functions and
then failed with ``fixture 'f' not found``.  These fixtures make `pytest -q`
work without changing how CI runs the scripts.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("WINGENIUS_NO_LAUNCH", "1")


def _legacy_failures() -> int:
    from tests import functional_test, smoke_test

    smoke = sum(1 for _name, status, _detail in smoke_test.results if status == smoke_test.FAIL)
    return int(functional_test.FAIL) + smoke


@pytest.fixture(autouse=True)
def _strict_legacy_checks():
    """The legacy ``check()`` helpers only increment a counter; make pytest fail."""
    before = _legacy_failures()
    yield
    if _legacy_failures() > before:
        pytest.fail("one or more legacy checks failed — see captured stdout")


@pytest.fixture(scope="session", autouse=True)
def _registry():
    from app.core.registry import load_all

    failures = load_all()
    assert not failures, f"tool modules failed to import: {failures}"
    return True


@pytest.fixture(scope="session")
def fixtures():
    """The dict built by functional_test.make_fixtures(), shared by its tests."""
    from tests import functional_test

    return functional_test.make_fixtures()


@pytest.fixture(scope="session")
def f():
    """The dict built by smoke_test.make_fixtures(base), shared by its tests."""
    from tests import smoke_test

    base = Path(tempfile.mkdtemp(prefix="wg_pytest_"))
    return smoke_test.make_fixtures(base)


@pytest.fixture(scope="session")
def ctx_factory(f):
    """Callable that creates a fresh RunContext, as smoke_test.main() does."""
    from app.core.spec import RunContext

    def factory():
        return RunContext(lang="en", output_dir=str(f["out"]))

    return factory


@pytest.fixture(scope="session", autouse=True)
def qapplication():
    """Create QApplication before legacy tests can create QCoreApplication.

    Qt cannot upgrade an existing QCoreApplication to QApplication. Running
    headless queue tests before widget regressions used to abort the process.
    Keep the Python wrapper alive for the entire test session as well.
    """
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    assert isinstance(app, QApplication), "QApplication must be created before core-only tests"
    yield app
