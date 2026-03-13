"""pytest-reporter-html entry-point."""
from __future__ import annotations

import logging

import pytest
from custom_python_logger import get_logger

from .const import PluginConfig, TestStatus
from .helpers import _extract_failure, _module_label, _now_millis, _worse
from .reporter import ReportEvent, TestReporter, _active_reporter

logger = get_logger(__name__)

_cfg_key: pytest.StashKey[PluginConfig] = pytest.StashKey()
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
            self._reporter.add_event(ReportEvent(
                startTime=_now_millis(),
                level=record.levelname,
                event=self.format(record),
                sourceFileName=record.filename,
                sourceLineNumber=record.lineno,
            ))
        except Exception:
            self.handleError(record)


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--report-html",
        action="store_true",
        default=False,
        help="Generate an aggregated HTML report at session end."
    )


def pytest_configure(config: pytest.Config) -> None:
    config.stash[_cfg_key] = PluginConfig(
        generate_html=bool(config.getoption("--report-html", default=False)),
    )


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item: pytest.Item) -> None:
    cfg = item.config.stash[_cfg_key]
    reporter = TestReporter(
        test_name=item.name,
        class_name=_module_label(item),
        output_dir=cfg.output_dir,
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
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo):  # noqa: ARG001
    outcome = yield
    reporter = item.stash.get(_reporter_key, None)
    if reporter is None:
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

    elif report.when == "call":
        if report.failed:
            failure = _extract_failure(report)
            reporter.end_phase(*failure)
            new = _worse(item.stash[_status_key], TestStatus.FAILED.name)
            if new != item.stash[_status_key]:
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
            new = _worse(item.stash[_status_key], TestStatus.ERROR.name)
            if new != item.stash[_status_key]:
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

        handler = item.stash.get(_handler_key, None)
        if handler is not None:
            logging.getLogger().removeHandler(handler)

        _active_reporter.set(None)
        del item.stash[_reporter_key]


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_teardown(item: pytest.Item) -> None:
    reporter = item.stash.get(_reporter_key, None)
    if reporter is not None:
        reporter.begin_phase("Teardown")


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:  # noqa: ARG001
    cfg = session.config.stash[_cfg_key]
    if not cfg.generate_html:
        return
    from .html_report import generate_report
    report_path = generate_report(cfg.output_dir, title=cfg.title)
    if report_path:
        import os
        abs_path = os.path.abspath(report_path)
        logger.info(f"Report: file://{abs_path}")


@pytest.fixture
def report_test_name(request: pytest.FixtureRequest):
    """Override the test name used in the report at runtime."""
    def _set(name: str) -> None:
        reporter = request.node.stash.get(_reporter_key, None)
        if reporter is not None:
            reporter.test_name = name
    return _set
