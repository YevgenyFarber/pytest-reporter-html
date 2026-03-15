"""pytest-reporter-html entry-point."""

from __future__ import annotations

import logging
import os
import shutil
from collections.abc import Callable, Generator
from typing import Any

import pytest
from _pytest.python import Function
from custom_python_logger import get_logger

from .const import DEFAULT_OUTPUT_DIR, DEFAULT_TITLE, TestStatus
from .helpers import _extract_failure, _module_label, _now_millis, _worse
from .html_report import generate_report
from .reporter import ReportEvent, TestReporter, _active_reporter

logger = get_logger(__name__)

_reporter_key: pytest.StashKey[TestReporter] = pytest.StashKey()
_status_key: pytest.StashKey[str] = pytest.StashKey()
_failure_key: pytest.StashKey[tuple[str, str]] = pytest.StashKey()
_handler_key: pytest.StashKey[logging.Handler] = pytest.StashKey()


class _ReportLogHandler(logging.Handler):
    """Forwards every log record into the active TestReporter as a ReportEvent."""

    def __init__(self, reporter: TestReporter) -> None:
        super().__init__()
        self._reporter = reporter

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._reporter.add_event(
                ReportEvent(
                    startTime=_now_millis(),
                    level=record.levelname,
                    event=self.format(record),
                    sourceFileName=record.filename,
                    sourceLineNumber=record.lineno,
                )
            )
        except Exception:
            self.handleError(record)


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--report-html", action="store_true", default=False, help="Generate an aggregated HTML report at session end."
    )
    parser.addoption(
        "--keep-json",
        action="store_true",
        default=False,
        help="Keep intermediate JSON files after HTML report generation.",
    )


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item: pytest.Item) -> None:
    reporter = TestReporter(
        test_name=item.name,
        class_name=_module_label(item),
        output_dir=DEFAULT_OUTPUT_DIR,
    )
    item.stash[_reporter_key] = reporter
    item.stash[_status_key] = TestStatus.PASSED.name
    _active_reporter.set(reporter)
    reporter.begin_phase("Setup")

    handler = _ReportLogHandler(reporter)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logging.getLogger().addHandler(handler)
    item.stash[_handler_key] = handler


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: Function) -> Generator[None, Any, None]:  # pylint: disable=R1260, R0912
    outcome = yield
    if (reporter := item.stash.get(_reporter_key, None)) is None:
        return

    report: pytest.TestReport = outcome.get_result()

    if report.when == "setup":
        if report.passed:
            reporter.end_phase()
            reporter.begin_phase(item.name)
        elif report.failed:
            failure = _extract_failure(report)
            reporter.end_phase(*failure)
            item.stash[_status_key] = TestStatus.ERROR.name
            item.stash[_failure_key] = failure
        else:  # skipped
            reporter.end_phase()
            item.stash[_status_key] = TestStatus.SKIPPED.name

    elif report.when == "call" and report.failed:
        failure = _extract_failure(report)
        reporter.end_phase(*failure)
        if (new := _worse(item.stash[_status_key], TestStatus.FAILED.name)) != item.stash[_status_key]:
            item.stash[_status_key] = new
            item.stash[_failure_key] = failure
        elif report.skipped:
            reporter.end_phase()
            item.stash[_status_key] = _worse(item.stash[_status_key], TestStatus.SKIPPED.name)
        else:
            reporter.end_phase()

    elif report.when == "teardown":
        if report.failed:
            failure = _extract_failure(report)
            reporter.end_phase(*failure)
            if (new := _worse(item.stash[_status_key], TestStatus.ERROR.name)) != item.stash[_status_key]:
                item.stash[_status_key] = new
                item.stash[_failure_key] = failure
        else:
            reporter.end_phase()

        failure = item.stash.get(_failure_key, None)
        reporter.finalize(
            status=item.stash[_status_key],
            failure_message=failure[0] if failure else None,
            stack_trace=failure[1] if failure else None,
        )

        if (handler := item.stash.get(_handler_key, None)) is not None:
            logging.getLogger().removeHandler(handler)

        _active_reporter.set(None)
        del item.stash[_reporter_key]


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_teardown(item: pytest.Item) -> None:
    if (reporter := item.stash.get(_reporter_key, None)) is not None:
        reporter.begin_phase("Teardown")


def pytest_sessionfinish(session: pytest.Session) -> None:
    config = session.config
    if not config.getoption("--report-html", default=False):
        return

    if report_path := generate_report(DEFAULT_OUTPUT_DIR, title=DEFAULT_TITLE):
        logger.info(f"Report: file://{os.path.abspath(report_path)}")

    if not config.getoption("--keep-json", default=False):
        json_dir = os.path.join(DEFAULT_OUTPUT_DIR, "json")
        if os.path.isdir(json_dir):
            shutil.rmtree(json_dir)
            logger.debug(f"Removed intermediate JSON directory: {json_dir}")


@pytest.fixture
def report_test_name(request: pytest.FixtureRequest) -> Callable[[str], None]:
    """Override the test name used in the report at runtime."""

    def _set(name: str) -> None:
        if (reporter := request.node.stash.get(_reporter_key, None)) is not None:
            reporter.test_name = name

    return _set
