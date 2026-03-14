"""
JSON report builder for a single test.

Schema dataclasses (ReportEvent, ReportStep, JsonReport) live here — no
separate types.py needed.
"""

from __future__ import annotations

import functools
import inspect
import json
import os
import traceback
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType

from custom_python_logger import get_logger

from .helpers import _now_millis

logger = get_logger(__name__)


@dataclass
class ReportEvent:
    """A single log event within a step."""

    startTime: int
    level: str
    event: str
    type: str | None = None
    sourceFileName: str | None = None
    sourceLineNumber: int | None = None

    def to_dict(self) -> dict:
        d: dict = {"startTime": self.startTime, "level": self.level, "event": self.event}
        if self.type is not None:
            d["type"] = self.type
        if self.sourceFileName is not None:
            d["sourceFileName"] = self.sourceFileName
        if self.sourceLineNumber is not None:
            d["sourceLineNumber"] = self.sourceLineNumber
        return d


@dataclass
class ReportStep:
    """A logical step in a test."""

    startTime: int
    endTime: int = 0
    name: str = ""
    status: str = "PASSED"
    events: list[ReportEvent] = field(default_factory=list)
    sub_steps: list[ReportStep] = field(default_factory=list)
    failureMessage: str | None = None
    stackTrace: str | None = None

    def to_dict(self) -> dict:
        d: dict = {
            "startTime": self.startTime,
            "endTime": self.endTime,
            "name": self.name,
            "status": self.status,
            "events": [e.to_dict() for e in self.events],
        }
        if self.sub_steps:
            d["subSteps"] = [s.to_dict() for s in self.sub_steps]
        if self.failureMessage is not None:
            d["failureMessage"] = self.failureMessage
        if self.stackTrace is not None:
            d["stackTrace"] = self.stackTrace
        return d


@dataclass
class JsonReport:
    """
    Top-level JSON report.

    Field names match the JUnit / Playwright reporters so both can be
    consumed identically by the HTML aggregator.
    """

    steps: list[ReportStep] = field(default_factory=list)
    testStatus: str = "PASSED"
    className: str | None = None
    failureMessage: str | None = None
    stackTrace: str | None = None

    def to_dict(self) -> dict:
        d: dict = {
            "steps": [s.to_dict() for s in self.steps],
            "testStatus": self.testStatus,
        }
        if self.className:
            d["className"] = self.className
        if self.failureMessage is not None:
            d["failureMessage"] = self.failureMessage
        if self.stackTrace is not None:
            d["stackTrace"] = self.stackTrace
        return d


class TestReporter:
    """
    Builds a :class:`JsonReport` for a single test.

    Lifecycle (managed by the plugin)::

        __init__        # created at test setup
        begin_phase()   # "Setup" / test-name / "Teardown"
        end_phase()     # symmetric with begin_phase
        begin_step()    # called by step() context manager
        end_step()      # symmetric with begin_step
        finalize()      # writes JSON, returns file path
    """

    def __init__(
        self,
        test_name: str,
        class_name: str | None,
        output_dir: str,
    ) -> None:
        self.test_name = test_name
        self.output_dir = output_dir
        self._timestamp = os.environ.get("REPORT_TIMESTAMP", "").strip() or str(_now_millis())
        self._report = JsonReport(className=class_name)
        self._current_step: ReportStep | None = None
        self._step_stack: list[ReportStep] = []
        self._number_parts: list[int] = [0]
        self._is_phase = False

    @property
    def report(self) -> JsonReport:
        return self._report

    @property
    def current_step(self) -> ReportStep | None:
        return self._current_step

    @current_step.setter
    def current_step(self, value: ReportStep | None) -> None:
        self._current_step = value

    @property
    def step_counter(self) -> int:
        return self._number_parts[0] if self._number_parts else 0

    def begin_step(self, name: str) -> None:
        if self._is_phase:
            self._close_step()
        if self._current_step is not None:
            self._step_stack.append(self._current_step)
        depth = len(self._step_stack)
        while len(self._number_parts) <= depth:  # pylint: disable=W0149
            self._number_parts.append(0)
        for i in range(depth + 1, len(self._number_parts)):
            self._number_parts[i] = 0
        self._number_parts[depth] += 1
        number = ".".join(str(self._number_parts[i]) for i in range(depth + 1))
        self._current_step = ReportStep(
            startTime=_now_millis(),
            name=f"Step {number}: {name}",
        )
        self._is_phase = False

    def end_step(
        self,
        failure_message: str | None = None,
        stack_trace: str | None = None,
    ) -> None:
        self._close_step(failure_message, stack_trace)

    def begin_phase(self, name: str) -> None:
        self._close_step()
        self._current_step = ReportStep(startTime=_now_millis(), name=name)
        self._is_phase = True

    def end_phase(
        self,
        failure_message: str | None = None,
        stack_trace: str | None = None,
    ) -> None:
        if self._current_step is None:
            return
        is_phase = self._is_phase
        self._close_step(failure_message, stack_trace)
        # Drop empty, passing Setup/Teardown phases to keep reports clean.
        if is_phase and self._report.steps:
            last = self._report.steps[-1]
            if last.name in {"Setup", "Teardown"} and not last.events and last.status == "PASSED":
                self._report.steps.pop()

    def add_event(self, event: ReportEvent) -> None:
        if self._current_step is not None:
            self._current_step.events.append(event)
        else:
            self._report.steps.append(
                ReportStep(
                    startTime=event.startTime,
                    endTime=event.startTime,
                    name=self.test_name,
                    events=[event],
                )
            )

    def finalize(
        self,
        status: str,
        failure_message: str | None = None,
        stack_trace: str | None = None,
    ) -> str | None:
        """Close any open step, set final status, write JSON. Returns the file path."""
        self._close_step()
        self._report.testStatus = status
        self._report.failureMessage = failure_message
        self._report.stackTrace = stack_trace
        return self._write()

    def _close_step(
        self,
        failure_message: str | None = None,
        stack_trace: str | None = None,
    ) -> None:
        if self._current_step is None:
            return
        self._current_step.endTime = _now_millis()
        if failure_message is not None:
            self._current_step.status = "FAILED"
            self._current_step.failureMessage = failure_message
            self._current_step.stackTrace = stack_trace
        if self._step_stack:
            parent = self._step_stack.pop()
            parent.sub_steps.append(self._current_step)
            self._current_step = parent
        else:
            self._report.steps.append(self._current_step)
            self._current_step = None
            self._is_phase = False

    def _write(self) -> str | None:
        try:
            out = Path(self.output_dir) / "json"
            out.mkdir(parents=True, exist_ok=True)
            path = out / f"{self.test_name}_{self._timestamp}.json"
            path.write_text(
                json.dumps(self._report.to_dict(), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            logger.info(f"  JSON report: {path}")
            return str(path)
        except Exception as exc:
            logger.error(f"  Failed to write report: {exc}")
            return None


_active_reporter: ContextVar[TestReporter | None] = ContextVar("_active_reporter", default=None)


class step:
    """
    Context manager **and** decorator for creating a named report step.

    Usage::

        with step("Login"):
            ...

        @step("Create user")
        async def create_user(client):
            ...

    If the block/function raises, the step is marked FAILED with the
    exception message and full traceback.
    """

    def __init__(self, name: str) -> None:
        self.name = name

    # sync context manager
    def __enter__(self) -> step:
        if (r := _active_reporter.get()) is not None:
            r.begin_step(self.name)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> bool:
        if (r := _active_reporter.get()) is not None:
            if exc_val is not None:
                trace = "".join(traceback.format_exception(exc_type, exc_val, exc_tb))
                r.end_step(str(exc_val), trace)
            else:
                r.end_step()
        return False

    # async context manager
    async def __aenter__(self) -> step:
        if (r := _active_reporter.get()) is not None:
            r.begin_step(self.name)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> bool:
        if (r := _active_reporter.get()) is not None:
            if exc_val is not None:
                trace = "".join(traceback.format_exception(exc_type, exc_val, exc_tb))
                r.end_step(str(exc_val), trace)
            else:
                r.end_step()
        return False

    # decorator
    def __call__(self, func: Callable) -> Callable:
        if inspect.iscoroutinefunction(func):

            @functools.wraps(func)
            async def _async(*args: object, **kwargs: object) -> object:
                async with self:
                    return await func(*args, **kwargs)

            return _async

        @functools.wraps(func)
        def _sync(*args: object, **kwargs: object) -> object:
            with self:
                return func(*args, **kwargs)

        return _sync
