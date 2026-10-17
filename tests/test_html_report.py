"""Tests for pytest_reporter_html.html_report."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from pytest_reporter_html.helpers import (
    _format_timestamp_hms,
    _format_ts,
)
from pytest_reporter_html.html_report import (
    _parse_test_result,
    generate_report,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_json(directory: Path, filename: str, data: dict) -> Path:
    """Write a JSON test-result file into directory/json/."""
    json_dir = directory / "json"
    json_dir.mkdir(parents=True, exist_ok=True)
    path = json_dir / filename
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _minimal_result(name: str = "test_foo", status: str = "PASSED") -> dict:
    return {
        "testStatus": status,
        "className": "tests.MyClass",
        "steps": [
            {
                "name": name,
                "startTime": 1_700_000_000_000,
                "endTime": 1_700_000_001_000,
                "status": status,
                "events": [],
            }
        ],
    }


# ---------------------------------------------------------------------------
# _format_ts
# ---------------------------------------------------------------------------


class TestFormatTs:
    def test_format_includes_milliseconds(self) -> None:
        dt = datetime(2024, 6, 15, 12, 30, 45, 123_000)
        result = _format_ts(dt)
        assert result == "2024.06.15_12.30.45.123", f"Expected '2024.06.15_12.30.45.123', got {result!r}"

    def test_format_pads_milliseconds_to_three_digits(self) -> None:
        dt = datetime(2024, 1, 1, 0, 0, 0, 5_000)
        result = _format_ts(dt)
        assert result.endswith(".005"), f"Expected '.005' suffix, got {result!r}"

    def test_format_zero_microseconds(self) -> None:
        dt = datetime(2024, 1, 1, 0, 0, 0, 0)
        result = _format_ts(dt)
        assert result.endswith(".000"), f"Expected '.000' suffix, got {result!r}"


# ---------------------------------------------------------------------------
# _format_timestamp_hms
# ---------------------------------------------------------------------------


class TestFormatTimestampHms:
    def test_format_epoch_millis_to_hms(self) -> None:
        dt = datetime(2024, 1, 1, 10, 20, 30, 456_000)
        epoch_ms = int(dt.timestamp() * 1000)
        result = _format_timestamp_hms(epoch_ms)
        assert result == "10:20:30.456", f"Expected '10:20:30.456', got {result!r}"


# ---------------------------------------------------------------------------
# _parse_test_result
# ---------------------------------------------------------------------------


class TestParseTestResult:
    def test_parses_minimal_result(self) -> None:
        data = _minimal_result("test_foo", "PASSED")
        result = _parse_test_result("test_foo_1700000000000.json", data)
        assert result.status == "PASSED", f"status mismatch: {result.status!r}"
        assert result.className == "tests.MyClass", f"className mismatch: {result.className!r}"

    def test_parses_failed_result(self) -> None:
        data = _minimal_result("test_bar", "FAILED")
        data["failureMessage"] = "AssertionError: oops"
        data["stackTrace"] = "traceback..."
        result = _parse_test_result("test_bar_1700000000000.json", data)
        assert result.status == "FAILED", f"status mismatch: {result.status!r}"
        assert result.failureMessage == "AssertionError: oops", f"failureMessage mismatch: {result.failureMessage!r}"

    def test_parses_step_name_with_dash_separator(self) -> None:
        data = {
            "testStatus": "PASSED",
            "steps": [
                {
                    "name": "com.example - my_test_method",
                    "startTime": 1_700_000_000_000,
                    "endTime": 1_700_000_001_000,
                    "status": "PASSED",
                    "events": [],
                }
            ],
        }
        result = _parse_test_result("some_file.json", data)
        assert result.className == "com.example", f"className should be 'com.example', got {result.className!r}"
        assert (
            result.methodName == "my_test_method"
        ), f"methodName should be 'my_test_method', got {result.methodName!r}"

    def test_parses_event_count(self) -> None:
        data = {
            "testStatus": "PASSED",
            "steps": [
                {
                    "name": "test_step",
                    "startTime": 1_700_000_000_000,
                    "endTime": 1_700_000_001_000,
                    "status": "PASSED",
                    "events": [
                        {"level": "INFO", "event": "msg1"},
                        {"level": "DEBUG", "event": "msg2"},
                    ],
                }
            ],
        }
        result = _parse_test_result("test_events.json", data)
        assert result.eventCount == 2, f"Expected 2 events, got {result.eventCount}"

    def test_returns_default_result_when_no_steps(self) -> None:
        data = {"testStatus": "PASSED", "steps": []}
        result = _parse_test_result("empty.json", data)
        assert result.filename == "empty.json", f"filename should be 'empty.json', got {result.filename!r}"
        assert not result.steps, "Steps should be empty list"


# ---------------------------------------------------------------------------
# generate_report (integration)
# ---------------------------------------------------------------------------


class TestGenerateReport:
    def test_returns_none_when_directory_does_not_exist(self, tmp_path: Path) -> None:
        result = generate_report(str(tmp_path / "nonexistent"))
        assert result is None, f"Expected None for missing directory, got {result!r}"

    def test_returns_none_when_no_json_files(self, tmp_path: Path) -> None:
        (tmp_path / "json").mkdir()
        result = generate_report(str(tmp_path))
        assert result is None, f"Expected None when no JSON files, got {result!r}"

    def test_returns_html_path_when_json_files_exist(self, tmp_path: Path) -> None:
        _write_json(tmp_path, "test_foo_1700000000000.json", _minimal_result())
        result = generate_report(str(tmp_path))
        assert result is not None, "Expected a file path, got None"
        assert Path(result).exists(), f"HTML file not found at {result!r}"
        assert result.endswith(".html"), f"Expected .html file, got {result!r}"

    def test_generated_html_contains_test_name(self, tmp_path: Path) -> None:
        _write_json(tmp_path, "test_foo_1700000000000.json", _minimal_result("test_foo"))
        result = generate_report(str(tmp_path))
        assert result is not None, "Expected a file path"
        content = Path(result).read_text(encoding="utf-8")
        assert "test_foo" in content, "Expected test name 'test_foo' in HTML output"

    def test_generates_latest_html_file(self, tmp_path: Path) -> None:
        _write_json(tmp_path, "test_bar_1700000000000.json", _minimal_result("test_bar"))
        generate_report(str(tmp_path))
        assert (tmp_path / "TestReport_Latest.html").exists(), "TestReport_Latest.html should be created"

    def test_skips_unparseable_json_files(self, tmp_path: Path) -> None:
        json_dir = tmp_path / "json"
        json_dir.mkdir(parents=True)
        (json_dir / "broken.json").write_text("not valid json", encoding="utf-8")
        _write_json(tmp_path, "valid_1700000000000.json", _minimal_result())
        result = generate_report(str(tmp_path))
        assert result is not None, "Expected report to succeed even with one broken JSON file"

    def test_custom_title_appears_in_html(self, tmp_path: Path) -> None:
        _write_json(tmp_path, "test_foo_1700000000000.json", _minimal_result())
        result = generate_report(str(tmp_path), title="My Custom Title")
        assert result is not None, "Expected a file path"
        content = Path(result).read_text(encoding="utf-8")
        assert "My Custom Title" in content, "Custom title should appear in HTML output"


# ---------------------------------------------------------------------------
# Regression tests: rendering
# ---------------------------------------------------------------------------


def _step(name: str, start: int, events: list[dict] | None = None, status: str = "PASSED") -> dict:
    return {"name": name, "startTime": start, "endTime": start + 10, "status": status, "events": events or []}


def _render(tmp_path: Path, steps: list[dict], status: str = "PASSED", **extra) -> str:
    _write_json(
        tmp_path,
        "test_x_1.json",
        {"testStatus": status, "className": "c", "testName": "test_x", "steps": steps, **extra},
    )
    return Path(generate_report(str(tmp_path))).read_text(encoding="utf-8")


class TestStepRendering:
    def test_steps_without_events_are_still_rendered(self, tmp_path: Path) -> None:
        html = _render(
            tmp_path,
            [
                _step("test_x", 1000),
                _step("Step 1: one", 1100, [{"level": "INFO", "event": "hi"}]),
                _step("Step 2: silent", 1200),
                _step("Step 3: three", 1300, [{"level": "INFO", "event": "yo"}]),
            ],
        )
        for name in ("Step 1: one", "Step 2: silent", "Step 3: three"):
            assert f"<span class='step-name'>{name}</span>" in html, f"{name} missing from HTML"

    def test_unexpandable_step_has_no_click_handler(self, tmp_path: Path) -> None:
        html = _render(tmp_path, [_step("Step 1: silent", 1000)])
        assert "step-header no-toggle'>" in html

    def test_uses_stored_test_name_not_file_name(self, tmp_path: Path) -> None:
        _write_json(
            tmp_path,
            "weird_name_1_2.json",
            {"testStatus": "PASSED", "testName": "test_real_name", "steps": [_step("t", 1)]},
        )
        html = Path(generate_report(str(tmp_path))).read_text(encoding="utf-8")
        assert "test_real_name" in html


class TestStatusRendering:
    def test_skipped_is_not_counted_as_failed(self, tmp_path: Path) -> None:
        html = _render(tmp_path, [_step("t", 1)], status="SKIPPED", failureMessage="not today")
        assert "<strong>1</strong> skipped" in html
        assert "failed</span>" not in html.split("</header>")[0]
        assert "data-status='SKIPPED'" in html and "test-item skipped" in html
        assert "<div class='failure-label'>SKIPPED</div>" in html

    def test_error_is_expanded_and_filterable(self, tmp_path: Path) -> None:
        html = _render(
            tmp_path,
            [_step("Setup", 1, [{"level": "INFO", "event": "e"}], "FAILED")],
            status="ERROR",
            failureMessage="boom",
        )
        assert "data-status='ERROR'" in html
        assert "<option value='ERROR'>Error</option>" in html
        assert "<option value='SKIPPED'>Skipped</option>" in html
        assert "<div class='failure-label'>ERROR</div>" in html

    def test_status_without_steps_is_preserved(self, tmp_path: Path) -> None:
        html = _render(tmp_path, [], status="SKIPPED", failureMessage="why")
        assert "data-status='SKIPPED'" in html

    def test_tests_listed_in_execution_order(self, tmp_path: Path) -> None:
        _write_json(
            tmp_path, "b_1.json", {"testStatus": "PASSED", "testName": "test_second", "steps": [_step("x", 2000)]}
        )
        _write_json(
            tmp_path, "a_1.json", {"testStatus": "PASSED", "testName": "test_first", "steps": [_step("x", 1000)]}
        )
        html = Path(generate_report(str(tmp_path))).read_text(encoding="utf-8")
        assert html.index("test_first") < html.index("test_second")
