"""
Package-wide constants for pytest-reporter-html.
"""

from __future__ import annotations

from enum import IntEnum

DEFAULT_OUTPUT_DIR = "logs/test-reports"
DEFAULT_TITLE = "Test Report"


class TestStatus(IntEnum):
    """
    Test result statuses, ordered by severity (lowest → highest).

    Because ``TestStatus`` is an ``IntEnum``, statuses can be compared
    directly with ``>``, ``>=``, etc., which removes the need for a
    separate rank-lookup dict::

        TestStatus["FAILED"] > TestStatus["PASSED"]  # True
        TestStatus["ERROR"]  > TestStatus["FAILED"]  # True
    """

    PASSED = 0
    SKIPPED = 1
    FAILED = 2
    ERROR = 3
