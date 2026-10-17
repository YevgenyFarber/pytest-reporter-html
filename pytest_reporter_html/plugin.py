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
from .helpers import _extract_failure, _extract_skip_reason, _module_label, _now_millis, _worse
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
    parser.addoption(
        "--report-html-dir",
        action="store",
        default=DEFAULT_OUTPUT_DIR,
        help=f"Directory for the JSON/HTML report output (default: {DEFAULT_OUTPUT_DIR}).",
    )
    parser.addoption(
        "--report-html-title",
        action="store",
        default=DEFAULT_TITLE,
        help=f"Title of the HTML report (default: {DEFAULT_TITLE!r}).",
    )


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item: pytest.Item) -> None:
    reporter = TestReporter(
        test_name=item.name,
        class_name=_module_label(item),
        output_dir=item.config.getoption("--report-html-dir", default=DEFAULT_OUTPUT_DIR),
    )
    item.stash[_reporter_key] = reporter
    if _failure_key in item.stash:  # left over from a previous attempt (e.g. pytest-rerunfailures)
        del item.stash[_failure_key]
    item.stash[_status_key] = TestStatus.PASSED.name
    _active_reporter.set(reporter)
    reporter.begin_phase("Setup")

    handler = _ReportLogHandler(reporter)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logging.getLogger().addHandler(handler)
    item.stash[_handler_key] = handler


def _record_setup(item: Function, reporter: TestReporter, report: pytest.TestReport) -> None:
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
        item.stash[_failure_key] = (_extract_skip_reason(report), "")


def _record_call(item: Function, reporter: TestReporter, report: pytest.TestReport) -> None:
    if report.failed:
        failure = _extract_failure(report)
        reporter.end_phase(*failure)
        new_status, new_failure = TestStatus.FAILED.name, failure
    elif report.skipped:  # pytest.skip() inside the test body, or xfail
        reporter.end_phase()
        new_status, new_failure = TestStatus.SKIPPED.name, (_extract_skip_reason(report), "")
    else:
        return  # passed: the test phase is closed when the teardown phase begins
    if (new := _worse(item.stash[_status_key], new_status)) != item.stash[_status_key]:
        item.stash[_status_key] = new
        item.stash[_failure_key] = new_failure


def _record_teardown(item: Function, reporter: TestReporter, report: pytest.TestReport) -> None:
    if report.failed:
        failure = _extract_failure(report)
        reporter.end_phase(*failure)
        if (new := _worse(item.stash[_status_key], TestStatus.ERROR.name)) != item.stash[_status_key]:
            item.stash[_status_key] = new
            item.stash[_failure_key] = failure
    else:
        reporter.end_phase()

    if (handler := item.stash.get(_handler_key, None)) is not None:
        logging.getLogger().removeHandler(handler)

    failure = item.stash.get(_failure_key, None)
    reporter.finalize(
        status=item.stash[_status_key],
        failure_message=failure[0] if failure else None,
        stack_trace=(failure[1] or None) if failure else None,
    )

    _active_reporter.set(None)
    del item.stash[_reporter_key]


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: Function) -> Generator[None, Any, None]:
    outcome = yield
    if (reporter := item.stash.get(_reporter_key, None)) is None:
        return

    report: pytest.TestReport = outcome.get_result()
    handlers = {"setup": _record_setup, "call": _record_call, "teardown": _record_teardown}
    if (handler := handlers.get(report.when)) is not None:
        handler(item, reporter, report)


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_teardown(item: pytest.Item) -> None:
    if (reporter := item.stash.get(_reporter_key, None)) is not None:
        reporter.begin_phase("Teardown")


def pytest_sessionfinish(session: pytest.Session) -> None:
    config = session.config
    if not config.getoption("--report-html", default=False):
        return
    if hasattr(config, "workerinput"):  # xdist worker: only the controller aggregates and cleans up
        return

    output_dir = config.getoption("--report-html-dir", default=DEFAULT_OUTPUT_DIR)
    title = config.getoption("--report-html-title", default=DEFAULT_TITLE)
    if not (report_path := generate_report(output_dir, title=title)):
        logger.warning("HTML report was not generated; keeping intermediate JSON files")
        return
    logger.info(f"Report: file://{os.path.abspath(report_path)}")

    if not config.getoption("--keep-json", default=False):
        json_dir = os.path.join(output_dir, "json")
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
