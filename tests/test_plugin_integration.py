"""End-to-end tests: run real pytest sessions with the plugin and inspect the generated reports."""

from __future__ import annotations

import json

import pytest

TEST_FILE = """
import logging
import pytest
from pytest_reporter_html import step

log = logging.getLogger("demo")
log.setLevel(logging.DEBUG)

def test_multi():
    log.info("before")
    with step("one"):
        log.info("in one")
    with step("silent"):
        pass
    with step("three"):
        log.info("in three")
    log.info("after")

def test_fail_in_step():
    with step("a"):
        log.info("x")
    with step("b"):
        assert 1 == 2

def test_skip_in_body():
    pytest.skip("not today")

@pytest.mark.skip(reason="marked")
def test_skip_marker():
    pass

@pytest.fixture
def broken():
    raise RuntimeError("fixture boom")

def test_setup_error(broken):
    pass

@pytest.mark.parametrize("val", ["a/b", "c d"])
def test_param(val):
    pass
"""


@pytest.fixture(name="session_run")
def _session_run(pytester: pytest.Pytester):
    pytester.makepyfile(test_demo=TEST_FILE)
    out = pytester.path / "out"
    result = pytester.runpytest_subprocess(
        "-p", "no:cacheprovider", "--report-html", "--keep-json", f"--report-html-dir={out}", "--report-html-title=T1"
    )
    reports = {}
    for f in (out / "json").glob("*.json"):
        data = json.loads(f.read_text(encoding="utf-8"))
        reports[data["testName"]] = data
    return result, reports, out


def _names(steps: list[dict]) -> list[str]:
    return [s["name"] for s in steps]


class TestPluginEndToEnd:
    def test_every_test_has_a_json_report(self, session_run) -> None:
        _, reports, _ = session_run
        assert len(reports) == 7, sorted(reports)  # incl. both parametrized ids (one has a '/')
        assert "test_param[a/b]" in reports

    def test_statuses(self, session_run) -> None:
        _, reports, _ = session_run
        got = {k: v["testStatus"] for k, v in reports.items()}
        assert got["test_multi"] == "PASSED"
        assert got["test_fail_in_step"] == "FAILED"
        assert got["test_skip_in_body"] == "SKIPPED"
        assert got["test_skip_marker"] == "SKIPPED"
        assert got["test_setup_error"] == "ERROR"
        assert "not today" in reports["test_skip_in_body"]["failureMessage"]
        assert "marked" in reports["test_skip_marker"]["failureMessage"]

    def test_all_steps_and_trailing_logs_recorded(self, session_run) -> None:
        _, reports, _ = session_run
        (phase,) = reports["test_multi"]["steps"]
        assert phase["name"] == "test_multi"
        assert _names(phase["subSteps"]) == ["Step 1: one", "Step 2: silent", "Step 3: three"]
        assert phase["events"][-1]["event"] == "after"

    def test_failed_step_is_marked(self, session_run) -> None:
        _, reports, _ = session_run
        (phase,) = reports["test_fail_in_step"]["steps"]
        failed = [s for s in phase["subSteps"] if s["status"] == "FAILED"]
        assert _names(failed) == ["Step 2: b"]

    def test_html_report_contents(self, session_run) -> None:
        _, _, out = session_run
        html = (out / "TestReport_Latest.html").read_text(encoding="utf-8")
        assert "<title>T1</title>" in html
        assert "<span class='step-name'>Step 2: silent</span>" in html
        assert "skipped</span>" in html

    def test_json_kept_with_flag(self, session_run) -> None:
        _, _, out = session_run
        assert (out / "json").is_dir()

    def test_json_removed_without_flag(self, pytester: pytest.Pytester) -> None:
        pytester.makepyfile(test_demo="def test_a():\n    pass\n")
        out = pytester.path / "out"
        pytester.runpytest_subprocess("-p", "no:cacheprovider", "--report-html", f"--report-html-dir={out}")
        assert (out / "TestReport_Latest.html").exists()
        assert not (out / "json").exists()

    def test_json_kept_when_report_generation_fails(self, pytester: pytest.Pytester) -> None:
        pytester.makepyfile(
            conftest="import pytest_reporter_html.plugin as p\n" "p.generate_report = lambda *a, **k: None\n",
            test_demo="def test_a():\n    pass\n",
        )
        out = pytester.path / "out"
        pytester.runpytest_subprocess("-p", "no:cacheprovider", "--report-html", f"--report-html-dir={out}")
        assert list((out / "json").glob("*.json")), "JSON must not be deleted when no HTML was produced"

    def test_rerun_does_not_leak_previous_failure(self, pytester: pytest.Pytester) -> None:
        pytest.importorskip("pytest_rerunfailures")
        pytester.makepyfile(
            test_flaky="import itertools\n_c = itertools.count()\n"
            "def test_flaky():\n    assert next(_c) > 0, 'first attempt fails'\n"
        )
        out = pytester.path / "out"
        pytester.runpytest_subprocess(
            "-p", "no:cacheprovider", "--reruns", "1", "--report-html", "--keep-json", f"--report-html-dir={out}"
        )
        finals = [json.loads(f.read_text()) for f in (out / "json").glob("*.json")]
        passed = [d for d in finals if d["testStatus"] == "PASSED"]
        assert passed, finals
        assert all("failureMessage" not in d for d in passed), passed


SESSION_FIXTURE_FILE = """
import logging
import pytest
from pytest_reporter_html import step

log = logging.getLogger("demo")
log.setLevel(logging.DEBUG)

@pytest.fixture(scope="session")
def db():
    with step("connect"):
        log.info("db up")
    yield
    log.info("db down")

@pytest.fixture(scope="session")
def client(db):
    log.info("client up")
    yield
    log.info("client down")

def test_a(client):
    log.info("in a")

def test_b(client):
    pass
"""

FAILING_SESSION_FIXTURE_FILE = """
import pytest

@pytest.fixture(scope="session")
def broken():
    raise RuntimeError("session boom")

def test_a(broken):
    pass
"""


def _run_with_report(pytester: pytest.Pytester, source: str):
    pytester.makepyfile(test_demo=source)
    out = pytester.path / "out"
    pytester.runpytest_subprocess(
        "-p", "no:cacheprovider", "--report-html", "--keep-json", f"--report-html-dir={out}", "--report-html-title=T1"
    )
    reports = {
        json.loads(f.read_text(encoding="utf-8"))["testName"]: json.loads(f.read_text(encoding="utf-8"))
        for f in (out / "json").glob("*.json")
    }
    return reports, (out / "TestReport_Latest.html").read_text(encoding="utf-8")


def _all_events(steps: list[dict]) -> list[str]:
    found = []
    for s in steps:
        found += [e["event"] for e in s["events"]]
        found += _all_events(s.get("subSteps", []))
    return found


class TestSessionBlock:
    def test_session_fixture_logs_go_to_session_reports(self, pytester: pytest.Pytester) -> None:
        reports, _ = _run_with_report(pytester, SESSION_FIXTURE_FILE)
        setup, teardown = reports["session_setup"], reports["session_teardown"]
        assert setup["kind"] == "session_setup"
        assert teardown["kind"] == "session_teardown"
        assert _names(setup["steps"]) == ["Fixture: db", "Fixture: client"]
        assert _names(setup["steps"][0]["subSteps"]) == ["Step 1: connect"]
        assert _all_events(setup["steps"]) == ["db up", "client up"]
        # teardown order is the reverse of setup
        assert _names(teardown["steps"]) == ["Fixture: client", "Fixture: db"]
        assert _all_events(teardown["steps"]) == ["client down", "db down"]

    def test_session_logs_are_not_attributed_to_tests(self, pytester: pytest.Pytester) -> None:
        reports, _ = _run_with_report(pytester, SESSION_FIXTURE_FILE)
        assert _all_events(reports["test_a"]["steps"]) == ["in a"]
        assert _all_events(reports["test_b"]["steps"]) == []

    def test_html_has_blocks_and_counts_only_tests(self, pytester: pytest.Pytester) -> None:
        _, html = _run_with_report(pytester, SESSION_FIXTURE_FILE)
        assert html.index("Session Setup") < html.index("test_a") < html.index("Session Teardown")
        assert "<strong>2</strong> tests" in html
        assert "db up" in html
        assert "db down" in html

    def test_no_session_fixtures_means_no_block(self, session_run) -> None:
        _, reports, out = session_run
        html = (out / "TestReport_Latest.html").read_text(encoding="utf-8")
        assert "session_setup" not in reports
        assert "Session Setup" not in html

    def test_failing_session_fixture_is_marked(self, pytester: pytest.Pytester) -> None:
        reports, html = _run_with_report(pytester, FAILING_SESSION_FIXTURE_FILE)
        assert reports["session_setup"]["testStatus"] == "FAILED"
        assert reports["session_setup"]["steps"][0]["status"] == "FAILED"
        assert "session boom" in reports["session_setup"]["steps"][0]["failureMessage"]
        assert reports["test_a"]["testStatus"] == "ERROR"
        assert "session-failed" in html
