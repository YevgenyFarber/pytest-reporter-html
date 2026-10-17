"""End-to-end tests: run real pytest sessions with the plugin and inspect the generated reports."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

TEST_FILE = '''
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
'''


@pytest.fixture
def run(pytester: pytest.Pytester):
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
    def test_every_test_has_a_json_report(self, run) -> None:
        _, reports, _ = run
        assert len(reports) == 7, sorted(reports)  # incl. both parametrized ids (one has a '/')
        assert "test_param[a/b]" in reports

    def test_statuses(self, run) -> None:
        _, reports, _ = run
        got = {k: v["testStatus"] for k, v in reports.items()}
        assert got["test_multi"] == "PASSED"
        assert got["test_fail_in_step"] == "FAILED"
        assert got["test_skip_in_body"] == "SKIPPED"
        assert got["test_skip_marker"] == "SKIPPED"
        assert got["test_setup_error"] == "ERROR"
        assert "not today" in reports["test_skip_in_body"]["failureMessage"]
        assert "marked" in reports["test_skip_marker"]["failureMessage"]

    def test_all_steps_and_trailing_logs_recorded(self, run) -> None:
        _, reports, _ = run
        steps = reports["test_multi"]["steps"]
        assert _names(steps) == ["test_multi", "Step 1: one", "Step 2: silent", "Step 3: three", "test_multi"]
        assert steps[-1]["events"][0]["event"] == "after"

    def test_failed_step_is_marked(self, run) -> None:
        _, reports, _ = run
        failed = [s for s in reports["test_fail_in_step"]["steps"] if s["status"] == "FAILED"]
        assert _names(failed) == ["Step 2: b"]

    def test_html_report_contents(self, run) -> None:
        _, _, out = run
        html = (out / "TestReport_Latest.html").read_text(encoding="utf-8")
        assert "<title>T1</title>" in html
        assert "<span class='step-name'>Step 2: silent</span>" in html
        assert "skipped</span>" in html

    def test_json_kept_with_flag(self, run) -> None:
        _, _, out = run
        assert (out / "json").is_dir()

    def test_json_removed_without_flag(self, pytester: pytest.Pytester) -> None:
        pytester.makepyfile(test_demo="def test_a():\n    pass\n")
        out = pytester.path / "out"
        pytester.runpytest_subprocess("-p", "no:cacheprovider", "--report-html", f"--report-html-dir={out}")
        assert (out / "TestReport_Latest.html").exists()
        assert not (out / "json").exists()

    def test_json_kept_when_report_generation_fails(self, pytester: pytest.Pytester) -> None:
        pytester.makepyfile(
            conftest="import pytest_reporter_html.plugin as p\n"
            "p.generate_report = lambda *a, **k: None\n",
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
