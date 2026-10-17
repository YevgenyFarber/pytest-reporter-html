"""Tests for pytest_reporter_html.helpers."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from pytest_reporter_html.helpers import (
    _extract_failure,
    _extract_skip_reason,
    _format_json_for_display,
    _module_label,
    _now_millis,
    _worse,
)


class TestNowMillis:
    def test_returns_int(self) -> None:
        result = _now_millis()
        assert isinstance(result, int), f"Expected int, got {type(result)}"

    def test_is_close_to_current_time(self) -> None:
        before = int(time.time() * 1000)
        result = _now_millis()
        after = int(time.time() * 1000)
        assert before <= result <= after, f"_now_millis() {result} is not between {before} and {after}"

    def test_monotonically_increases(self) -> None:
        t1 = _now_millis()
        t2 = _now_millis()
        assert t2 >= t1, f"Second call {t2} should be >= first call {t1}"


class TestWorse:
    @pytest.mark.parametrize(
        "a,b,expected",
        [
            ("PASSED", "FAILED", "FAILED"),
            ("FAILED", "PASSED", "FAILED"),
            ("PASSED", "SKIPPED", "SKIPPED"),
            ("SKIPPED", "PASSED", "SKIPPED"),
            ("FAILED", "ERROR", "ERROR"),
            ("ERROR", "FAILED", "ERROR"),
            ("PASSED", "PASSED", "PASSED"),
            ("ERROR", "ERROR", "ERROR"),
            ("PASSED", "ERROR", "ERROR"),
        ],
    )
    def test_returns_more_severe_status(self, a: str, b: str, expected: str) -> None:
        result = _worse(a, b)
        assert result == expected, f"_worse({a!r}, {b!r}) should be {expected!r}, got {result!r}"


class TestExtractFailure:
    def test_extracts_from_repr_crash(self) -> None:
        mock_crash = MagicMock()
        mock_crash.message = "AssertionError: expected True"
        longrepr = MagicMock()
        longrepr.reprcrash = mock_crash

        report = MagicMock(spec=pytest.TestReport)
        report.longrepr = longrepr

        msg, trace = _extract_failure(report)
        assert msg == "AssertionError: expected True", f"Expected crash message, got {msg!r}"
        assert trace == str(longrepr), f"Expected str(longrepr) as trace, got {trace!r}"

    def test_extracts_from_string_longrepr(self) -> None:
        report = MagicMock(spec=pytest.TestReport)
        report.longrepr = "plain string traceback"

        msg, trace = _extract_failure(report)
        assert msg == "plain string traceback", f"Expected string repr, got {msg!r}"
        assert trace == "plain string traceback", f"Expected string repr as trace, got {trace!r}"

    def test_returns_tuple_of_two_strings(self) -> None:
        report = MagicMock(spec=pytest.TestReport)
        report.longrepr = "some error"
        result = _extract_failure(report)
        assert isinstance(result, tuple), f"Expected tuple, got {type(result)}"
        assert len(result) == 2, f"Expected 2-tuple, got length {len(result)}"
        assert all(isinstance(s, str) for s in result), "Both elements must be strings"


class TestModuleLabel:
    def test_returns_class_label_when_cls_is_set(self) -> None:
        item = MagicMock()
        item.cls = MagicMock()
        item.cls.__name__ = "MyTestClass"
        item.module.__name__ = "tests.test_module"

        result = _module_label(item)
        assert result == "tests.test_module.MyTestClass", f"Expected 'tests.test_module.MyTestClass', got {result!r}"

    def test_returns_module_name_when_no_cls(self) -> None:
        item = MagicMock()
        item.cls = None
        item.module.__name__ = "tests.test_module"

        result = _module_label(item)
        assert result == "tests.test_module", f"Expected 'tests.test_module', got {result!r}"

    def test_returns_none_when_module_has_no_name(self) -> None:
        item = MagicMock()
        item.cls = None
        # Use a spec-constrained mock with no __name__ attribute
        item.module = MagicMock(spec=[])

        result = _module_label(item)
        assert result is None, f"Expected None when module has no __name__, got {result!r}"


# ---------------------------------------------------------------------------
# Regression tests: JSON highlighting
# ---------------------------------------------------------------------------


class TestJsonHighlighting:
    def test_keys_and_strings_are_highlighted(self) -> None:
        out = _format_json_for_display('{"name": "bob", "n": 1.5, "ok": true, "x": null}')
        assert "<span class='json-key'>&quot;name&quot;</span>" in out, out
        assert "<span class='json-string'>&quot;bob&quot;</span>" in out, out
        assert "<span class='json-number'>1.5</span>" in out, out
        assert "<span class='json-literal'>true</span>" in out, out
        assert "<span class='json-literal'>null</span>" in out, out

    def test_numbers_inside_strings_are_not_highlighted(self) -> None:
        out = _format_json_for_display('{"a": "x 12 y"}')
        assert "json-number" not in out, out

    def test_apostrophe_entity_is_not_corrupted(self) -> None:
        out = _format_json_for_display('{"a": "it\'s"}')
        assert "it&#39;s" in out and "json-number" not in out, out

    def test_markup_in_values_is_escaped(self) -> None:
        out = _format_json_for_display('{"a": "<script>"}')
        assert "<script>" not in out and "&lt;script&gt;" in out, out


class TestRobustHelpers:
    def test_extract_failure_handles_missing_reprcrash(self) -> None:
        report = MagicMock()
        report.longrepr = MagicMock(reprcrash=None)
        msg, trace = _extract_failure(report)
        assert msg and trace, (msg, trace)

    def test_module_label_handles_items_without_module(self) -> None:
        item = MagicMock(spec=[])  # e.g. doctest / non-python collectors: no .module / .cls
        assert _module_label(item) is None

    def test_skip_reason_from_tuple_longrepr(self) -> None:
        report = MagicMock(spec=["longrepr"])
        report.longrepr = ("f.py", 3, "Skipped: not today")
        assert _extract_skip_reason(report) == "not today"

    def test_skip_reason_for_xfail(self) -> None:
        report = MagicMock()
        report.wasxfail = "known bug"
        assert _extract_skip_reason(report) == "xfail: known bug"
