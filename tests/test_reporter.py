"""Tests for pytest_reporter_html.reporter."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pytest_reporter_html.reporter import (
    JsonReport,
    ReportEvent,
    ReportStep,
    TestReporter,
    _active_reporter,
    step,
)

# ---------------------------------------------------------------------------
# ReportEvent
# ---------------------------------------------------------------------------


class TestReportEvent:
    def test_to_dict_required_fields(self) -> None:
        event = ReportEvent(startTime=1000, level="INFO", event="something happened")
        d = event.to_dict()
        assert d["startTime"] == 1000, f"startTime mismatch: {d}"
        assert d["level"] == "INFO", f"level mismatch: {d}"
        assert d["event"] == "something happened", f"event mismatch: {d}"

    def test_to_dict_excludes_none_optional_fields(self) -> None:
        event = ReportEvent(startTime=1000, level="DEBUG", event="msg")
        d = event.to_dict()
        assert "type" not in d, "None 'type' should be excluded"
        assert "sourceFileName" not in d, "None 'sourceFileName' should be excluded"
        assert "sourceLineNumber" not in d, "None 'sourceLineNumber' should be excluded"

    def test_to_dict_includes_optional_fields_when_set(self) -> None:
        event = ReportEvent(
            startTime=2000,
            level="ERROR",
            event="err",
            type="log",
            sourceFileName="test_foo.py",
            sourceLineNumber=42,
        )
        d = event.to_dict()
        assert d["type"] == "log", f"type should be included: {d}"
        assert d["sourceFileName"] == "test_foo.py", f"sourceFileName should be included: {d}"
        assert d["sourceLineNumber"] == 42, f"sourceLineNumber should be included: {d}"


# ---------------------------------------------------------------------------
# ReportStep
# ---------------------------------------------------------------------------


class TestReportStep:
    def test_to_dict_contains_required_keys(self) -> None:
        step_obj = ReportStep(startTime=100, endTime=200, name="Login", status="PASSED")
        d = step_obj.to_dict()
        for key in ("startTime", "endTime", "name", "status", "events"):
            assert key in d, f"Key {key!r} missing from ReportStep.to_dict()"

    def test_to_dict_excludes_none_failure_fields(self) -> None:
        step_obj = ReportStep(startTime=100, endTime=200, name="Step")
        d = step_obj.to_dict()
        assert "failureMessage" not in d, "None failureMessage should be excluded"
        assert "stackTrace" not in d, "None stackTrace should be excluded"

    def test_to_dict_includes_failure_fields_when_set(self) -> None:
        step_obj = ReportStep(
            startTime=100,
            endTime=200,
            name="Step",
            status="FAILED",
            failureMessage="AssertionError",
            stackTrace="traceback...",
        )
        d = step_obj.to_dict()
        assert d["failureMessage"] == "AssertionError", f"failureMessage mismatch: {d}"
        assert d["stackTrace"] == "traceback...", f"stackTrace mismatch: {d}"

    def test_events_serialized_in_to_dict(self) -> None:
        event = ReportEvent(startTime=150, level="INFO", event="log line")
        step_obj = ReportStep(startTime=100, endTime=200, name="Step", events=[event])
        d = step_obj.to_dict()
        assert len(d["events"]) == 1, f"Expected 1 event, got {len(d['events'])}"
        assert d["events"][0]["event"] == "log line", "Event not serialized correctly"


# ---------------------------------------------------------------------------
# JsonReport
# ---------------------------------------------------------------------------


class TestJsonReport:
    def test_to_dict_contains_required_keys(self) -> None:
        report = JsonReport()
        d = report.to_dict()
        assert "steps" in d, "steps key missing"
        assert "testStatus" in d, "testStatus key missing"

    def test_to_dict_excludes_none_class_name(self) -> None:
        report = JsonReport(className=None)
        d = report.to_dict()
        assert "className" not in d, "None className should be excluded"

    def test_to_dict_includes_class_name_when_set(self) -> None:
        report = JsonReport(className="tests.MyClass")
        d = report.to_dict()
        assert d["className"] == "tests.MyClass", f"className mismatch: {d}"

    def test_to_dict_excludes_none_failure_fields(self) -> None:
        report = JsonReport()
        d = report.to_dict()
        assert "failureMessage" not in d, "None failureMessage should be excluded"
        assert "stackTrace" not in d, "None stackTrace should be excluded"

    def test_steps_serialized(self) -> None:
        step_obj = ReportStep(startTime=1, endTime=2, name="S1")
        report = JsonReport(steps=[step_obj])
        d = report.to_dict()
        assert len(d["steps"]) == 1, f"Expected 1 step, got {len(d['steps'])}"


# ---------------------------------------------------------------------------
# TestReporter lifecycle
# ---------------------------------------------------------------------------


class TestTestReporter:
    def test_begin_and_end_phase_adds_step(self, reporter: TestReporter) -> None:
        reporter.begin_phase("Setup")
        reporter.add_event(ReportEvent(startTime=1, level="INFO", event="log"))
        reporter.end_phase()
        assert len(reporter.report.steps) == 1, f"Expected 1 step after end_phase, got {len(reporter.report.steps)}"

    def test_empty_passing_setup_teardown_phases_are_dropped(self, reporter: TestReporter) -> None:
        reporter.begin_phase("Setup")
        reporter.end_phase()
        assert len(reporter.report.steps) == 0, "Empty passing Setup phase should be dropped"

    def test_begin_step_increments_counter(self, reporter: TestReporter) -> None:
        reporter.begin_phase("Setup")
        reporter.end_phase()
        reporter.begin_phase("test_example")
        reporter.begin_step("step one")
        assert (
            reporter.step_counter == 1
        ), f"Step counter should be 1 after first begin_step, got {reporter.step_counter}"
        reporter.end_step()
        reporter.begin_step("step two")
        assert (
            reporter.step_counter == 2
        ), f"Step counter should be 2 after second begin_step, got {reporter.step_counter}"

    def test_step_name_formatted_with_counter(self, reporter: TestReporter) -> None:
        reporter.begin_phase("test_example")
        reporter.begin_step("my step")
        assert reporter.current_step is not None, "Current step should not be None"
        assert (
            reporter.current_step.name == "Step 1: my step"
        ), f"Expected 'Step 1: my step', got {reporter.current_step.name!r}"

    def test_end_step_closes_current_step(self, reporter: TestReporter) -> None:
        reporter.begin_phase("test_example")
        reporter.begin_step("login")
        reporter.end_step()
        assert reporter.current_step is None, "current_step should be None after end_step"

    def test_end_step_with_failure_marks_step_failed(self, reporter: TestReporter) -> None:
        reporter.begin_phase("test_example")
        reporter.begin_step("login")
        reporter.end_step(failure_message="boom", stack_trace="trace")
        last = reporter.report.steps[-1]
        assert last.status == "FAILED", f"Step status should be FAILED, got {last.status!r}"
        assert last.failureMessage == "boom", f"failureMessage mismatch: {last.failureMessage!r}"

    def test_add_event_appends_to_current_step(self, reporter: TestReporter) -> None:
        reporter.begin_phase("test_example")
        reporter.begin_step("step")
        event = ReportEvent(startTime=1, level="DEBUG", event="msg")
        reporter.add_event(event)
        assert len(reporter.current_step.events) == 1, f"Expected 1 event, got {len(reporter.current_step.events)}"

    def test_add_event_without_current_step_creates_orphan_step(self, reporter: TestReporter) -> None:
        # reporter.current_step is already None from the fixture
        event = ReportEvent(startTime=100, level="INFO", event="orphan")
        reporter.add_event(event)
        assert len(reporter.report.steps) == 1, "Expected orphan step to be created"
        assert reporter.report.steps[0].events[0].event == "orphan", "Orphan event not stored"

    def test_finalize_writes_json_file(self, reporter: TestReporter, fixed_timestamp: None, tmp_path: Path) -> None:
        reporter.begin_phase("test_example")
        reporter.end_phase()
        path = reporter.finalize(status="PASSED")
        assert path is not None, "finalize should return a file path on success"
        written = Path(path)
        assert written.exists(), f"JSON file not found at {path}"

    def test_finalize_json_content_is_valid(
        self, reporter: TestReporter, fixed_timestamp: None, tmp_path: Path
    ) -> None:
        reporter.begin_phase("test_example")
        reporter.begin_step("do something")
        reporter.end_step()
        reporter.end_phase()
        path = reporter.finalize(status="PASSED")
        assert path is not None, "finalize should return a path"
        content = json.loads(Path(path).read_text())
        assert content["testStatus"] == "PASSED", f"testStatus mismatch: {content['testStatus']!r}"
        assert "steps" in content, "steps key missing from JSON"

    def test_finalize_records_failure_info(self, reporter: TestReporter, fixed_timestamp: None, tmp_path: Path) -> None:
        reporter.begin_phase("test_example")
        reporter.end_phase()
        path = reporter.finalize(
            status="FAILED",
            failure_message="assertion failed",
            stack_trace="traceback here",
        )
        assert path is not None, "finalize should return a path"
        content = json.loads(Path(path).read_text())
        assert content["testStatus"] == "FAILED", "testStatus should be FAILED"
        assert content["failureMessage"] == "assertion failed", "failureMessage mismatch"


# ---------------------------------------------------------------------------
# step context manager / decorator
# ---------------------------------------------------------------------------


class TestStep:
    def test_sync_context_manager_creates_step(self, reporter: TestReporter) -> None:
        token = _active_reporter.set(reporter)
        try:
            reporter.begin_phase("test")
            with step("my step"):
                pass
            reporter.end_phase()
        finally:
            _active_reporter.reset(token)

        step_names = [s.name for s in reporter.report.steps]
        assert any("my step" in n for n in step_names), f"Expected 'my step' in steps, got {step_names}"

    def test_sync_context_manager_marks_failed_on_exception(self, reporter: TestReporter) -> None:
        token = _active_reporter.set(reporter)
        try:
            reporter.begin_phase("test")
            with pytest.raises(ValueError):
                with step("failing step"):
                    raise ValueError("intentional error")
        finally:
            _active_reporter.reset(token)

        failed = [s for s in reporter.report.steps if s.status == "FAILED"]
        assert len(failed) == 1, f"Expected 1 failed step, got {failed}"
        assert "intentional error" in (
            failed[0].failureMessage or ""
        ), f"failureMessage should mention the error: {failed[0].failureMessage!r}"

    def test_sync_decorator_wraps_function(self, reporter: TestReporter) -> None:
        token = _active_reporter.set(reporter)
        try:
            reporter.begin_phase("test")

            @step("decorated step")
            def do_work() -> str:
                return "done"

            result = do_work()
        finally:
            _active_reporter.reset(token)

        assert result == "done", f"Decorator should return the function result, got {result!r}"
        step_names = [s.name for s in reporter.report.steps]
        assert any("decorated step" in n for n in step_names), f"Expected 'decorated step' in steps, got {step_names}"

    @pytest.mark.asyncio
    async def test_async_context_manager_creates_step(self, reporter: TestReporter) -> None:
        token = _active_reporter.set(reporter)
        try:
            reporter.begin_phase("test")
            async with step("async step"):
                pass
            reporter.end_phase()
        finally:
            _active_reporter.reset(token)

        step_names = [s.name for s in reporter.report.steps]
        assert any("async step" in n for n in step_names), f"Expected 'async step' in steps, got {step_names}"

    @pytest.mark.asyncio
    async def test_async_decorator_wraps_coroutine(self, reporter: TestReporter) -> None:
        token = _active_reporter.set(reporter)
        try:
            reporter.begin_phase("test")

            @step("async decorated")
            async def async_work() -> int:
                return 42

            result = await async_work()
        finally:
            _active_reporter.reset(token)

        assert result == 42, f"Async decorator should return coroutine result, got {result!r}"

    def test_step_without_active_reporter_does_not_raise(self) -> None:
        token = _active_reporter.set(None)
        try:
            with step("orphan step"):
                pass
        finally:
            _active_reporter.reset(token)
        # no exception means test passes

    def test_exit_returns_false_to_propagate_exceptions(self) -> None:
        s = step("test")
        result = s.__exit__(None, None, None)
        assert result is False, f"__exit__ should return False to propagate exceptions, got {result}"
