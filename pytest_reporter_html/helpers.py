"""
Shared helpers for pytest-reporter-html.

All pure utility functions live here so plugin.py and reporter.py stay
focused on their own responsibilities.
"""
from __future__ import annotations

import time

import pytest

from .const import TestStatus


# ---------------------------------------------------------------------------
# Timing
# ---------------------------------------------------------------------------


def _now_millis() -> int:
    """Return the current UTC time in milliseconds."""
    return int(time.time() * 1000)


# ---------------------------------------------------------------------------
# Test status
# ---------------------------------------------------------------------------


def _worse(a: str, b: str) -> str:
    """Return whichever status is more severe."""
    return b if TestStatus[b] > TestStatus[a] else a


# ---------------------------------------------------------------------------
# Pytest report helpers
# ---------------------------------------------------------------------------


def _extract_failure(report: pytest.TestReport) -> tuple[str, str]:
    """Extract (failure_message, stack_trace) from a failed TestReport."""
    longrepr = report.longrepr
    if hasattr(longrepr, "reprcrash"):
        msg = str(longrepr.reprcrash.message)
    else:
        msg = str(longrepr)
    return msg, str(longrepr)


def _module_label(item: pytest.Item) -> str | None:
    """Return a human-readable class/module label for the test item."""
    if item.cls is not None:  # type: ignore[attr-defined]
        return f"{item.module.__name__}.{item.cls.__name__}"  # type: ignore[attr-defined]
    return getattr(item.module, "__name__", None)  # type: ignore[attr-defined]
