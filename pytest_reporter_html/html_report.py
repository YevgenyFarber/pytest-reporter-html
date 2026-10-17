# pylint: disable=too-many-lines, too-many-instance-attributes, too-many-statements, too-many-branches, too-complex,
# pylint: disable=too-many-locals, too-many-arguments, too-many-public-methods, too-many-nested-blocks, too-many-return-statements

"""
Aggregated HTML report generator for pytest.

Reads all per-test JSON files produced by ``TestReporter`` and generates
a single-page HTML report with statistics, search/filter, collapsible
steps and events, and JSON syntax highlighting.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from custom_python_logger import get_logger

from .const import SESSION_SETUP_KIND, SESSION_TEARDOWN_KIND
from .helpers import (
    _escape_html,
    _format_class_name,
    _format_event_with_json,
    _format_json_for_display,
    _format_test_name,
    _format_timestamp_hms,
    _format_ts,
    _render_event_with_traceback,
    _try_pretty_json,
)

logger = get_logger(__name__)

PASSED, FAILED, SKIPPED, ERROR = "PASSED", "FAILED", "SKIPPED", "ERROR"
_FAILURE_LABELS = {FAILED: "FAILURE", ERROR: "ERROR", SKIPPED: "SKIPPED"}
_LOG_LEVEL_RE = re.compile(r"[^a-z0-9_-]")

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class TestEvent:
    startTime: int = 0
    level: str = ""
    event: str = ""
    type: str | None = None
    sourceFileName: str | None = None
    sourceLineNumber: int | None = None


@dataclass
class TestStep:
    name: str = ""
    startTime: int = 0
    endTime: int = 0
    status: str = "PASSED"
    failureMessage: str | None = None
    stackTrace: str | None = None
    events: list[TestEvent] = field(default_factory=list)
    sub_steps: list[TestStep] = field(default_factory=list)


@dataclass
class TestResult:
    filename: str = ""
    className: str = "Tests"
    methodName: str = ""
    testName: str = ""
    startTime: int = 0
    status: str = "PASSED"
    failureMessage: str | None = None
    stackTrace: str | None = None
    duration: int = 0
    eventCount: int = 0
    steps: list[TestStep] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_report(report_directory: str, *, title: str = "Test Report") -> str | None:
    """
    Generate an aggregated HTML report from all JSON files in
    ``report_directory/json/``.

    Returns the path to ``TestReport_Latest.html`` on success, ``None`` on
    error or if there are no JSON files.
    """
    try:
        report_dir = Path(report_directory)
        if not report_dir.exists():
            return None

        json_dir = report_dir / "json"
        if not json_dir.is_dir():
            return None

        if not (json_files := sorted(json_dir.glob("*.json"))):
            return None

        test_results: list[TestResult] = []
        session_results: dict[str, list[TestResult]] = {SESSION_SETUP_KIND: [], SESSION_TEARDOWN_KIND: []}
        for jf in json_files:
            try:
                with open(jf, encoding="utf-8") as f:
                    root = json.load(f)
                result = _parse_test_result(jf.name, root)
                if (kind := root.get("kind")) in session_results:  # session fixtures, not a test
                    session_results[kind].append(result)
                else:
                    test_results.append(result)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.warning(f"  Failed to parse: {jf.name}: {exc}")

        if not test_results:
            return None

        test_results.sort(key=lambda r: r.startTime)

        grouped: dict[str, list[TestResult]] = {}
        for tr in test_results:
            grouped.setdefault(tr.className, []).append(tr)

        timestamp = _format_ts(datetime.now())
        html = _generate_html(test_results, grouped, timestamp, title=title, session_results=session_results)

        ts_file = report_dir / f"TestReport_All_{timestamp}.html"
        ts_file.write_text(html, encoding="utf-8")

        latest = report_dir / "TestReport_Latest.html"
        latest.write_text(html, encoding="utf-8")

        return str(latest)

    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.error(f"  Failed to generate aggregated report: {exc}")
        return None


# ---------------------------------------------------------------------------
# JSON parsing
# ---------------------------------------------------------------------------


def _parse_test_result(filename: str, root: dict) -> TestResult:
    result = TestResult(filename=filename)

    if not (steps_data := root.get("steps", [])):
        # e.g. skipped during setup: no steps, but status/reason are still recorded at the top level
        result.status = root.get("testStatus", PASSED)
        result.failureMessage = root.get("failureMessage")
        result.stackTrace = root.get("stackTrace")
        if test_name := root.get("testName"):
            result.className = root.get("className") or "Tests"
            result.methodName = test_name
            result.testName = _format_test_name(test_name)
        return result

    first = steps_data[0]
    step_name = first.get("name", "")

    if test_name := root.get("testName"):
        result.className = root.get("className") or "Tests"
        result.methodName = test_name
        result.testName = _format_test_name(test_name)
    elif " - " in step_name:
        parts = step_name.split(" - ", 1)
        result.className = parts[0].strip()
        method_part = parts[1].strip()
        if " @" in method_part:
            method_part = method_part[: method_part.index(" @")]
        method_part = re.sub(r"[|\s]+$", "", method_part).strip()
        result.methodName = method_part
        result.testName = _format_test_name(method_part)
    else:
        raw = filename.replace(".json", "")
        raw = re.sub(r"_\d+$", "", raw)
        result.className = root.get("className") or "Tests"
        result.methodName = raw
        result.testName = _format_test_name(raw)

    result.startTime = first.get("startTime", 0)
    result.status = root.get("testStatus", first.get("status", "PASSED"))

    def _load_step(step_data: dict) -> TestStep:
        ts = TestStep(
            name=step_data.get("name", ""),
            startTime=step_data.get("startTime", 0),
            endTime=step_data.get("endTime", 0),
            status=step_data.get("status", "PASSED"),
        )
        if ts.status == FAILED:
            ts.failureMessage = step_data.get("failureMessage")
            ts.stackTrace = step_data.get("stackTrace")
            if result.status in {FAILED, ERROR}:
                if ts.failureMessage and not result.failureMessage:
                    result.failureMessage = ts.failureMessage
                if ts.stackTrace and not result.stackTrace:
                    result.stackTrace = ts.stackTrace
        for ev_data in step_data.get("events", []):
            ts.events.append(
                TestEvent(
                    startTime=ev_data.get("startTime", 0),
                    level=ev_data.get("level", "INFO"),
                    event=ev_data.get("event", ""),
                    type=ev_data.get("type"),
                    sourceFileName=ev_data.get("sourceFileName"),
                    sourceLineNumber=ev_data.get("sourceLineNumber"),
                )
            )
            result.eventCount += 1
        for sub_data in step_data.get("subSteps", []):
            ts.sub_steps.append(_load_step(sub_data))
        return ts

    for step_data in steps_data:
        result.steps.append(_load_step(step_data))

    if root.get("failureMessage"):
        result.failureMessage = root["failureMessage"]
    if root.get("stackTrace"):
        result.stackTrace = root["stackTrace"]

    result.duration = max(s.get("endTime", 0) for s in steps_data) - result.startTime

    return result


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _merge_children(step: TestStep) -> list[tuple[str, int, TestEvent | TestStep]]:
    """Interleave a step's events and sub-steps chronologically.

    The sort is stable and events come first, so same-millisecond ties keep
    a log line ahead of the step that started right after it.
    """
    items: list[tuple[str, int, TestEvent | TestStep]] = [("event", i, ev) for i, ev in enumerate(step.events)]
    items += [("step", i, sub) for i, sub in enumerate(step.sub_steps)]
    return sorted(items, key=lambda item: item[2].startTime)


def _render_event(ev: TestEvent, uid: str) -> list[str]:
    h: list[str] = []
    ev_class = f"event-{_LOG_LEVEL_RE.sub('', ev.level.lower())}"
    level = _escape_html(ev.level)
    h.append(f"<div class='event {ev_class}' data-event-level='{level}'>\n")
    h.append(f"<span class='event-level'>{level}</span>\n")

    if ev.sourceFileName or ev.sourceLineNumber is not None:
        loc_parts = []
        if ev.sourceFileName:
            loc_parts.append(_escape_html(ev.sourceFileName))
        if ev.sourceLineNumber is not None:
            if ev.sourceFileName:
                loc_parts.append(":")
            loc_parts.append(str(ev.sourceLineNumber))
        h.append(f"<span class='event-source-location'>{''.join(loc_parts)}</span>\n")

    if ev.type == "json":
        pretty = _try_pretty_json(ev.event)
        display_j = _format_json_for_display(pretty if pretty else ev.event)
        data_orig = _escape_html(ev.event).replace("'", "&#39;")
        h += [
            "<div class='json-container'>\n",
            "<div class='json-header'>\n",
            "<span class='json-label'>JSON</span>\n",
            f"<button class='copy-btn' onclick='copyToClipboard(\"{uid}\", this)'>&#128203;</button>\n",
            "</div>\n",
            f"<pre class='event-json' id='json-{uid}' data-original='{data_orig}'>{display_j}</pre>\n",
            "</div>\n",
        ]
    elif ev.event.startswith("Stack Trace:"):
        st_content = ev.event[len("Stack Trace:") :].strip()
        h += [
            "<div class='event-stacktrace-section'>\n",
            f"<div class='event-stacktrace-toggle' onclick='toggleEventStackTrace(\"{uid}\")'>\n",
            f"<span class='event-stacktrace-icon open' id='event-stacktrace-icon-{uid}'>&#9654;</span>\n",
            "<strong>Stack Trace</strong>\n",
            "</div>\n",
            f"<pre class='event-stacktrace-content' id='event-stacktrace-{uid}' style='display: block;'>"
            f"{_escape_html(st_content)}</pre>\n",
            "</div>\n",
        ]
    elif "\nTraceback (most recent call last):" in ev.event:
        h.append(_render_event_with_traceback(ev.event, uid))
    else:
        h.append(_format_event_with_json(ev.event))
        h.append("\n")

    h.append("</div>\n")
    return h


def _render_step_block(
    step: TestStep,
    step_id: str,
    step_icon_id: str,
    auto_open: bool,
) -> list[str]:
    has_events = bool(step.events)
    has_sub = bool(step.sub_steps)
    has_content = has_events or has_sub

    step_status = "step-passed" if step.status == PASSED else "step-failed"
    step_open = has_content and auto_open
    open_cls = " open" if step_open else ""

    if has_content:
        onclick = f' onclick=\'toggleStep("{step_id}", "{step_icon_id}")\''
        icon = f"<span class='step-toggle-icon{open_cls}' id='{step_icon_id}'>&#9654;</span>\n"
    else:  # nothing to expand: still show the step, but not as a toggle
        onclick = ""
        icon = "<span class='step-toggle-icon placeholder'>&#9654;</span>\n"

    h: list[str] = [
        f"<div class='step {step_status}'>\n",
        f"<div class='step-header{'' if has_content else ' no-toggle'}'{onclick}>\n",
        icon,
        f"<span class='step-name'>{_escape_html(step.name)}</span>\n",
    ]
    if step.events:
        h.append(f"<span class='step-event-count'>{len(step.events)}</span>\n")
    if (dur_ms := step.endTime - step.startTime) > 0:
        h.append(f"<span class='step-duration'>{dur_ms}ms</span>\n")
    h += [
        f"<span class='step-time'>{_format_timestamp_hms(step.startTime)}</span>\n",
        "</div>\n",
    ]

    step_display = "block" if step_open else "none"
    h.append(f"<div class='step-events' id='{step_id}' style='display: {step_display};'>\n")

    for kind, idx, child in _merge_children(step):
        if kind == "event":
            h += _render_event(child, f"{step_id}-{idx}")
        else:
            h += _render_step_block(child, f"{step_id}-{idx}", f"{step_icon_id}-{idx}", auto_open)

    h += ["</div>\n", "</div>\n"]
    return h


def _render_session_block(label: str, kind: str, results: list[TestResult]) -> list[str]:
    """Render session-scoped fixture steps (all xdist workers merged) as one collapsible block."""
    if not (steps := sorted((s for r in results for s in r.steps), key=lambda s: s.startTime)):
        return []
    failed = any(r.status in {FAILED, ERROR} for r in results)
    h = [
        f"<details class='session-block{' session-failed' if failed else ''}'{' open' if failed else ''}>\n",
        f"<summary class='session-title'>{_escape_html(label)}"
        f"<span class='session-count'>{len(steps)} fixture{'s' if len(steps) != 1 else ''}</span></summary>\n",
    ]
    for si, step in enumerate(steps):
        h += _render_step_block(step, f"{kind}-{si}", f"{kind}-icon-{si}", failed)
    h.append("</details>\n")
    return h


_STATUS_CLASS = {PASSED: "passed", SKIPPED: "skipped", FAILED: "failed", ERROR: "failed"}
_STATUS_DOT = {PASSED: "pass", SKIPPED: "skip", FAILED: "fail", ERROR: "fail"}


def _render_test(result: TestResult, index: int) -> str:
    status_class = _STATUS_CLASS.get(result.status, "failed")
    dot_class = _STATUS_DOT.get(result.status, "fail")
    status_icon = f"<span class='status-dot {dot_class}' title='{_escape_html(result.status)}'></span>"
    auto_open = result.status in {FAILED, ERROR}
    total_events = result.eventCount

    h: list[str] = [
        f"<div class='test-item {status_class}' data-status='{result.status}'>\n",
        f"<div class='test-header' onclick='toggleTest({index})'>\n",
        f"{status_icon}\n",
        "<div class='test-name-container'>\n",
        f"<span class='test-name'>{_escape_html(result.testName)}</span>\n",
        f"<span class='test-method-name'>{_escape_html(result.methodName)}</span>\n",
        "</div>\n",
        "<span class='test-meta'>\n",
        f"<span class='meta-item meta-duration'>{result.duration / 1000:.2f}s</span>\n",
    ]
    if total_events > 0:
        h.append(
            f"<span class='meta-item meta-events'>" f"{total_events} event{'s' if total_events != 1 else ''}</span>\n"
        )
    h += [
        "</span>\n",
        f"<span class='toggle-icon' id='icon-{index}'>&#9660;</span>\n",
        "</div>\n",
    ]

    display = "block" if auto_open else "none"
    open_class = " open" if auto_open else ""
    h.append(f"<div class='test-details{open_class}' id='test-{index}' style='display: {display};'>\n")

    if result.failureMessage:
        h += [
            "<div class='failure-message'>\n",
            f"<div class='failure-label'>{_FAILURE_LABELS.get(result.status, 'FAILURE')}</div>\n",
            f"<pre>{_escape_html(result.failureMessage)}</pre>\n",
        ]
        if result.stackTrace:
            h += [
                "<div class='stacktrace-section'>\n",
                f"<div class='stacktrace-toggle' onclick='toggleStackTrace({index})'>\n",
                f"<span class='stacktrace-icon' id='stacktrace-icon-{index}'>&#9654;</span>\n",
                "<strong>Stack Trace</strong>\n",
                "</div>\n",
                f"<pre class='stacktrace-content' id='stacktrace-{index}' style='display: none;'>"
                f"{_escape_html(result.stackTrace)}</pre>\n",
                "</div>\n",
            ]
        h.append("</div>\n")

    for si, step in enumerate(result.steps):
        step_id = f"step-{index}-{si}"
        step_icon_id = f"step-icon-{index}-{si}"
        h += _render_step_block(step, step_id, step_icon_id, auto_open)

    h += ["</div>\n", "</div>\n"]
    return "".join(h)


# ---------------------------------------------------------------------------
# Full-page HTML generation
# ---------------------------------------------------------------------------


def _generate_html(
    results: list[TestResult],
    grouped: dict[str, list[TestResult]],
    run_timestamp: str,
    title: str = "Test Report",
    session_results: dict[str, list[TestResult]] | None = None,
) -> str:
    session_results = session_results or {}
    total = len(results)
    passed = sum(1 for r in results if r.status == PASSED)
    skipped = sum(1 for r in results if r.status == SKIPPED)
    failed = total - passed - skipped  # FAILED + ERROR (+ any unknown status)
    total_duration = sum(r.duration for r in results)
    total_events = sum(r.eventCount for r in results)
    has_multiple_classes = len(grouped) > 1
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    h: list[str] = [
        "<!DOCTYPE html>\n<html lang='en'>\n<head>\n",
        "<meta charset='UTF-8'>\n",
        "<meta name='viewport' content='width=device-width, initial-scale=1.0'>\n",
        f"<title>{_escape_html(title)}</title>\n",
        "<style>\n",
        _get_css(),
        "</style>\n",
        "</head>\n<body>\n",
        "<header class='header'>\n",
        "<div class='header-top'>\n",
        f"<h1>{_escape_html(title)}</h1>\n",
        f"<div class='header-meta'>{run_timestamp} &middot; {now_str}</div>\n",
        "</div>\n",
    ]

    rate = (passed * 100.0 / total) if total > 0 else 0.0
    skip_rate = (skipped * 100.0 / total) if total > 0 else 0.0
    fail_rate = (failed * 100.0 / total) if total > 0 else 0.0
    h.append("<div class='progress-bar-container'>\n")
    h.append(f"<div class='progress-bar pass-bar' style='width:{rate:.1f}%'></div>\n")
    if skipped > 0:
        h.append(f"<div class='progress-bar skip-bar' style='width:{skip_rate:.1f}%'></div>\n")
    if failed > 0:
        h.append(f"<div class='progress-bar fail-bar' style='width:{fail_rate:.1f}%'></div>\n")
    h.append("</div>\n")

    h.append("<div class='stats-row'>\n")
    h.append(f"<span class='stat'><strong>{total}</strong> tests</span>\n")
    h.append(f"<span class='stat stat-pass'><strong>{passed}</strong> passed</span>\n")
    if failed > 0:
        h.append(f"<span class='stat stat-fail'><strong>{failed}</strong> failed</span>\n")
    if skipped > 0:
        h.append(f"<span class='stat stat-skip'><strong>{skipped}</strong> skipped</span>\n")
    h.append(f"<span class='stat'><strong>{rate:.0f}%</strong> pass rate</span>\n")
    h.append(f"<span class='stat'><strong>{total_duration / 1000:.2f}s</strong> duration</span>\n")
    if total_events > 0:
        h.append(f"<span class='stat'><strong>{total_events}</strong> events</span>\n")
    h += ["</div>\n", "</header>\n"]

    h += [
        "<div class='toolbar'>\n",
        "<input type='text' id='searchInput' class='search-input'"
        " placeholder='Search tests... (* wildcard, ? single char, OR, &quot;phrase&quot;)' oninput='filterTests()'>\n",
        "<select id='statusFilter' class='status-filter' onchange='filterTests()'>\n",
        "<option value='all'>All</option>\n",
        "<option value='PASSED'>Passed</option>\n",
        "<option value='FAILED'>Failed</option>\n",
        "<option value='ERROR'>Error</option>\n",
        "<option value='SKIPPED'>Skipped</option>\n",
        "</select>\n",
        "<select id='logLevelFilter' class='status-filter' onchange='filterLogLevel()'"
        " title='Minimum log level to display'>\n",
        "<option value='TRACE'>TRACE</option>\n",
        "<option value='DEBUG' selected>DEBUG</option>\n",
        "<option value='INFO'>INFO</option>\n",
        "<option value='WARNING'>WARNING</option>\n",
        "<option value='ERROR'>ERROR</option>\n",
        "<option value='CRITICAL'>CRITICAL</option>\n",
        "</select>\n",
        "<button class='toolbar-btn' onclick='expandAll()'>Expand All</button>\n",
        "<button class='toolbar-btn' onclick='collapseAll()'>Collapse All</button>\n",
        "</div>\n",
    ]

    h.append("<main class='tests-container'>\n")
    h += _render_session_block("Session Setup", SESSION_SETUP_KIND, session_results.get(SESSION_SETUP_KIND, []))
    idx = 0
    if has_multiple_classes:
        for cls_idx, (class_name, class_tests) in enumerate(grouped.items()):
            cls_passed = sum(1 for t in class_tests if t.status == PASSED)
            cls_total = len(class_tests)
            cls_failed = sum(1 for t in class_tests if t.status not in {PASSED, SKIPPED})
            cls_status = "class-all-pass" if cls_failed == 0 else "class-has-fail"
            status_value = FAILED if cls_failed else PASSED
            display_name = _format_class_name(class_name)
            h += [
                f"<div class='test-class-group {cls_status}' data-class-status='{status_value}'>\n",
                f"<h2 class='class-name' onclick='toggleClassGroup({cls_idx})'>\n",
                f"<span class='class-toggle-icon' id='class-icon-{cls_idx}'>&#9660;</span>\n",
                f"<span class='class-display-name'>{_escape_html(display_name)}</span>\n",
                f"<span class='class-count'>{cls_passed}/{cls_total}</span>\n",
            ]
            if cls_failed > 0:
                h.append(f"<span class='class-fail-badge'>{cls_failed} failed</span>\n")
            h += ["</h2>\n", f"<div class='class-tests' id='class-group-{cls_idx}'>\n"]
            for tr in class_tests:
                h.append(_render_test(tr, idx))
                idx += 1
            h += ["</div>\n", "</div>\n"]
    else:
        for tr in results:
            h.append(_render_test(tr, idx))
            idx += 1
    h += _render_session_block(
        "Session Teardown", SESSION_TEARDOWN_KIND, session_results.get(SESSION_TEARDOWN_KIND, [])
    )
    h.append("</main>\n")

    h += ["<script>\n", _get_javascript(), "</script>\n", "</body>\n</html>"]
    return "".join(h)


# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------


def _get_css() -> str:
    mono = "'SF Mono','Monaco','Inconsolata','Roboto Mono','Courier New',monospace"
    return f"""\
:root {{
  --bg: #f5f6f8; --surface: #fff; --border: #e2e4e9;
  --text: #1a1d23; --text-2: #6b7280; --text-3: #9ca3af;
  --green: #059669; --green-bg: #ecfdf5; --green-border: #a7f3d0;
  --red: #dc2626; --red-bg: #fef2f2; --red-border: #fecaca;
  --blue: #2563eb; --blue-bg: #eff6ff; --blue-border: #bfdbfe;
  --amber: #d97706; --amber-bg: #fffbeb;
  --radius: 6px; --mono: {mono};
}}
@media (prefers-reduced-motion: reduce) {{
  * {{ transition: none !important; animation: none !important; }}
}}
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{
  font-family: -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
  background:var(--bg); color:var(--text); line-height:1.5;
}}
.header {{ background:#1a1d23; color:#fff; padding:24px 32px; }}
.header-top {{ display:flex; align-items:baseline; gap:16px; flex-wrap:wrap; }}
.header h1 {{ font-size:20px; font-weight:700; letter-spacing:-0.3px; }}
.header-meta {{ font-size:13px; color:#9ca3af; font-family:var(--mono); }}
.progress-bar-container {{
  display:flex; height:4px; margin-top:16px;
  border-radius:2px; overflow:hidden; background:#374151;
}}
.progress-bar {{ transition:width 0.4s ease; }}
.pass-bar {{ background:var(--green); }}
.skip-bar {{ background:var(--amber); }}
.fail-bar {{ background:var(--red); }}
.stats-row {{ display:flex; gap:24px; margin-top:12px; flex-wrap:wrap; }}
.stat {{ font-size:13px; color:#d1d5db; }}
.stat strong {{ color:#fff; font-weight:600; }}
.stat-pass strong {{ color:var(--green); }}
.stat-fail strong {{ color:#f87171; }}
.stat-skip strong {{ color:#fbbf24; }}
.toolbar {{
  display:flex; gap:8px; padding:12px 32px;
  background:var(--surface); border-bottom:1px solid var(--border);
  position:sticky; top:0; z-index:10;
}}
.search-input {{
  flex:1; padding:8px 12px; border:1px solid var(--border);
  border-radius:var(--radius); font-size:13px;
  font-family:inherit; background:var(--bg);
}}
.search-input:focus {{
  outline:none; border-color:var(--blue);
  box-shadow:0 0 0 3px rgba(37,99,235,.12);
}}
.status-filter {{
  padding:8px 12px; border:1px solid var(--border);
  border-radius:var(--radius); font-size:13px;
  cursor:pointer; background:var(--bg); font-family:inherit;
}}
.toolbar-btn {{
  padding:8px 14px; border:1px solid var(--border);
  border-radius:var(--radius); font-size:12px; cursor:pointer;
  background:var(--surface); font-family:inherit;
  font-weight:500; color:var(--text-2);
}}
.toolbar-btn:hover {{ background:var(--bg); color:var(--text); }}
.tests-container {{ padding:16px 32px 48px; }}
.test-class-group {{ margin-bottom:32px; }}
.session-block {{
  margin-bottom:24px; background:var(--surface); border:1px solid var(--border);
  border-left:3px solid var(--blue); border-radius:var(--radius); padding:0 12px 8px;
}}
.session-block.session-failed {{ border-left-color:var(--red); }}
.session-title {{
  font-size:14px; font-weight:700; color:var(--text-2); padding:10px 0; cursor:pointer;
  display:flex; align-items:center; gap:10px; user-select:none;
}}
.session-count {{ font-weight:400; color:var(--text-3); font-size:12px; }}
.class-name {{
  font-size:14px; font-weight:700; color:var(--text-2);
  padding:10px 12px; border-bottom:2px solid var(--border);
  cursor:pointer; display:flex; align-items:center; gap:10px;
  user-select:none; border-radius:var(--radius) var(--radius) 0 0;
}}
.class-name:hover {{ background:rgba(0,0,0,.02); }}
.class-toggle-icon {{ font-size:10px; color:var(--text-3); transition:transform .2s; flex-shrink:0; }}
.class-toggle-icon.collapsed {{ transform:rotate(-90deg); }}
.class-display-name {{ flex:1; }}
.class-count {{ font-weight:400; color:var(--text-3); font-size:12px; }}
.class-fail-badge {{
  font-size:10px; font-weight:600; background:var(--red-bg);
  color:var(--red); padding:1px 8px;
  border-radius:10px; border:1px solid var(--red-border);
}}
.class-all-pass {{ border-left:3px solid var(--green); }}
.class-has-fail {{ border-left:3px solid var(--red); }}
.class-tests {{ padding:4px 0 4px 24px; }}
.test-item {{
  background:var(--surface); border:1px solid var(--border);
  border-radius:var(--radius); margin-bottom:6px;
  transition:box-shadow .15s;
}}
.test-item:hover {{ box-shadow:0 1px 4px rgba(0,0,0,.06); }}
.test-item.passed {{ border-left:3px solid var(--green); }}
.test-item.skipped {{ border-left:3px solid var(--amber); }}
.test-item.failed {{ border-left:3px solid var(--red); background:var(--red-bg); }}
.test-item.hidden {{ display:none !important; }}
.test-item.highlight {{ background:#fef9c3 !important; border-color:var(--amber) !important; }}
.test-header {{
  padding:10px 16px; cursor:pointer; display:flex;
  align-items:center; gap:12px; user-select:none;
}}
.test-header:hover {{ background:rgba(0,0,0,.02); }}
.status-dot {{ width:8px; height:8px; border-radius:50%; flex-shrink:0; }}
.status-dot.pass {{ background:var(--green); }}
.status-dot.fail {{ background:var(--red); }}
.status-dot.skip {{ background:var(--amber); }}
.test-name-container {{ flex:1; min-width:0; }}
.test-name {{
  font-weight:600; font-size:13px; color:var(--text);
  white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
}}
.test-method-name {{
  font-size:11px; color:var(--text-3); font-family:var(--mono);
  white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
}}
.test-meta {{ display:flex; gap:8px; font-size:11px; color:var(--text-3); flex-shrink:0; }}
.meta-item {{ font-family:var(--mono); }}
.meta-events {{
  background:var(--blue-bg); color:var(--blue);
  padding:1px 6px; border-radius:10px; font-weight:600;
}}
.toggle-icon {{ color:var(--text-3); font-size:10px; transition:transform .2s; flex-shrink:0; }}
.toggle-icon.open {{ transform:rotate(180deg); }}
.test-details {{
  border-top:1px solid var(--border); background:var(--surface);
  overflow:hidden; padding-left:24px;
}}
.test-details.open {{ overflow:visible; }}
.failure-message {{
  padding:16px; background:var(--red-bg);
  border-left:3px solid var(--red); margin:12px 16px;
  border-radius:var(--radius);
}}
.failure-label {{
  font-size:11px; font-weight:700; text-transform:uppercase;
  letter-spacing:.05em; color:var(--red); margin-bottom:6px;
}}
.failure-message pre {{
  color:#991b1b; font-size:12px; line-height:1.6;
  white-space:pre-wrap; word-break:break-word; font-family:var(--mono);
}}
.stacktrace-section {{ margin-top:12px; }}
.stacktrace-toggle {{
  cursor:pointer; padding:4px 0; color:var(--red); font-size:12px;
  display:flex; align-items:center; gap:6px; user-select:none;
}}
.stacktrace-toggle:hover {{ opacity:.8; }}
.stacktrace-icon {{ display:inline-block; transition:transform .2s; font-size:9px; }}
.stacktrace-icon.open {{ transform:rotate(90deg); }}
.stacktrace-content {{
  margin-top:6px; color:#991b1b; font-size:11px; line-height:1.5;
  white-space:pre-wrap; word-break:break-word; font-family:var(--mono);
  max-height:400px; overflow-y:auto; background:#fff5f5;
  padding:10px; border-radius:var(--radius); border:1px solid var(--red-border);
}}
.step {{ padding:8px 16px; border-bottom:1px solid #f3f4f6; }}
.step:last-child {{ border-bottom:none; }}
.step-passed .step-header {{ background:#f8fafb; }}
.step-failed .step-header {{ background:var(--red-bg); }}
.step-header {{
  display:flex; align-items:center; gap:8px; padding:6px 10px;
  border-radius:var(--radius); cursor:pointer; user-select:none; font-size:13px;
}}
.step-header:hover {{ filter:brightness(.97); }}
.step-toggle-icon {{ font-size:9px; transition:transform .2s; color:var(--text-3); flex-shrink:0; }}
.step-toggle-icon.open {{ transform:rotate(90deg); }}
.step-toggle-icon.placeholder {{ visibility:hidden; }}
.step-header.no-toggle {{ cursor:default; }}
.step-name {{ font-weight:500; flex:1; color:var(--text); }}
.step-event-count {{
  font-size:10px; font-weight:600; background:var(--blue-bg);
  color:var(--blue); padding:1px 6px; border-radius:10px; flex-shrink:0;
}}
.step-duration {{ font-size:11px; color:var(--text-3); font-family:var(--mono); flex-shrink:0; }}
.step-time {{ font-size:11px; color:var(--text-3); font-family:var(--mono); flex-shrink:0; }}
.step-events {{ padding:4px 0 4px 18px; }}
.step-events > .step {{ border-left:2px solid var(--border); border-bottom:none; margin:4px 0;
border-radius:var(--radius); }}
.event {{
  padding:6px 10px; margin:3px 0; border-radius:4px;
  background:#f9fafb; border-left:3px solid #d1d5db;
  font-size:12px; line-height:1.6;
}}
.event-info {{ border-left-color:var(--blue); background:var(--blue-bg); }}
.event-error {{ border-left-color:var(--red); background:var(--red-bg); }}
.event-warn, .event-warning {{ border-left-color:var(--amber); background:var(--amber-bg); }}
.event-critical {{ border-left-color:#991b1b; background:var(--red-bg); }}
.event-debug {{ border-left-color:#9ca3af; background:#f9fafb; }}
.event-trace {{ border-left-color:#c4b5fd; background:#f5f3ff; }}
.event-level {{
  font-weight:700; font-size:10px; text-transform:uppercase;
  color:var(--text-3); margin-right:6px; letter-spacing:.04em;
}}
.event-source-location {{
  font-size:10px; color:var(--text-3); font-family:var(--mono);
  margin-left:6px; padding:1px 5px;
  background:var(--border); border-radius:3px;
}}
.event-text {{ color:var(--text); line-height:1.6; }}
.json-container {{
  margin-top:6px; border:1px solid var(--blue-border);
  border-radius:var(--radius); overflow:hidden;
}}
.json-header {{
  display:flex; justify-content:space-between; align-items:center;
  background:#dbeafe; padding:5px 10px;
  border-bottom:1px solid var(--blue-border);
}}
.json-label {{
  font-size:10px; font-weight:700; text-transform:uppercase;
  color:#1e40af; letter-spacing:.05em;
}}
.copy-btn {{
  background:var(--blue); color:#fff; border:none;
  padding:3px 8px; border-radius:4px;
  cursor:pointer; font-size:11px; font-family:inherit;
}}
.copy-btn:hover {{ background:#1d4ed8; }}
.event-json {{
  font-family:var(--mono); font-size:12px; color:#1e3a5f;
  background:var(--blue-bg); padding:10px;
  overflow-x:auto; margin:0; line-height:1.6;
}}
.json-key {{ color:var(--blue); font-weight:600; }}
.json-string {{ color:var(--blue); }}
.json-number {{ color:var(--blue); }}
.json-literal {{ color:#ea580c; font-weight:600; }}
.event-stacktrace-section {{ margin-top:8px; }}
.event-stacktrace-toggle {{
  cursor:pointer; padding:4px 0; color:var(--red); font-weight:600;
  display:flex; align-items:center; gap:5px;
  user-select:none; font-size:11px;
}}
.event-stacktrace-toggle:hover {{ opacity:.8; }}
.event-stacktrace-icon {{ display:inline-block; transition:transform .2s; font-size:9px; }}
.event-stacktrace-icon.open {{ transform:rotate(90deg); }}
.event-stacktrace-content {{
  margin-top:4px; color:#7f1d1d; font-size:11px; line-height:1.5;
  white-space:pre; font-family:var(--mono); max-height:400px;
  overflow:auto; background:#fff5f5; padding:10px;
  border-radius:var(--radius); border:1px solid var(--red-border);
}}
.no-results {{ text-align:center; padding:40px; color:var(--text-3); font-size:14px; }}
"""


# ---------------------------------------------------------------------------
# JavaScript
# ---------------------------------------------------------------------------


def _get_javascript() -> str:
    return """\
function toggleClassGroup(index) {
  var d = document.getElementById('class-group-' + index);
  var icon = document.getElementById('class-icon-' + index);
  if (d.style.display === 'none') {
    d.style.display = 'block'; icon.classList.remove('collapsed');
  } else {
    d.style.display = 'none'; icon.classList.add('collapsed');
  }
}
function toggleTest(index) {
  var d = document.getElementById('test-' + index);
  var icon = document.getElementById('icon-' + index);
  if (d.style.display === 'none') {
    d.style.display = 'block'; d.classList.add('open'); icon.classList.add('open');
  } else {
    d.style.display = 'none'; d.classList.remove('open'); icon.classList.remove('open');
  }
}
function toggleStep(stepId, iconId) {
  var el = document.getElementById(stepId);
  var icon = document.getElementById(iconId);
  if (el.style.display === 'none') {
    el.style.display = 'block'; icon.classList.add('open');
  } else {
    el.style.display = 'none'; icon.classList.remove('open');
  }
}
function toggleStackTrace(index) {
  var el = document.getElementById('stacktrace-' + index);
  var icon = document.getElementById('stacktrace-icon-' + index);
  if (el.style.display === 'none') {
    el.style.display = 'block'; icon.classList.add('open');
  } else {
    el.style.display = 'none'; icon.classList.remove('open');
  }
}
function toggleEventStackTrace(id) {
  var el = document.getElementById('event-stacktrace-' + id);
  var icon = document.getElementById('event-stacktrace-icon-' + id);
  if (el.style.display === 'none') {
    el.style.display = 'block'; icon.classList.add('open');
  } else {
    el.style.display = 'none'; icon.classList.remove('open');
  }
}
function _copyText(text, btn) {
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(function() { _flashBtn(btn); });
  } else {
    var ta = document.createElement('textarea');
    ta.value = text; ta.style.cssText = 'position:fixed;opacity:0';
    document.body.appendChild(ta); ta.select();
    try { document.execCommand('copy'); _flashBtn(btn); } catch(e) {}
    document.body.removeChild(ta);
  }
}
function _flashBtn(btn) {
  var orig = btn.innerHTML;
  btn.innerHTML = '\u2713'; btn.style.background = '#059669';
  setTimeout(function() { btn.innerHTML = orig; btn.style.background = ''; }, 1500);
}
function copyToClipboard(id, btn) {
  var el = document.getElementById('json-' + id);
  if (el) _copyText(el.getAttribute('data-original') || el.textContent, btn);
}
function expandAll() {
  document.querySelectorAll('.class-tests').forEach(function(d) { d.style.display = 'block'; });
  document.querySelectorAll('.class-toggle-icon').forEach(function(i) { i.classList.remove('collapsed'); });
  document.querySelectorAll('.test-item:not(.hidden)').forEach(function(item) {
    var details = item.querySelector('.test-details');
    var idx = details && details.id.replace('test-', '');
    if (idx !== undefined) {
      var d = document.getElementById('test-' + idx);
      var icon = document.getElementById('icon-' + idx);
      if (d) { d.style.display = 'block'; d.classList.add('open'); }
      if (icon) icon.classList.add('open');
    }
  });
}
function collapseAll() {
  document.querySelectorAll('.class-tests').forEach(function(d) { d.style.display = 'none'; });
  document.querySelectorAll('.class-toggle-icon').forEach(function(i) { i.classList.add('collapsed'); });
  document.querySelectorAll('.test-details').forEach(function(d) {
    d.style.display = 'none'; d.classList.remove('open');
  });
  document.querySelectorAll('.toggle-icon').forEach(function(i) { i.classList.remove('open'); });
}
var _LEVEL_ORDER = {TRACE:0, DEBUG:1, INFO:2, WARN:3, WARNING:3, ERROR:4, CRITICAL:5, FATAL:5};
function filterLogLevel() {
  var sel = document.getElementById('logLevelFilter').value;
  var minLevel = _LEVEL_ORDER[sel] || 0;
  document.querySelectorAll('.event[data-event-level]').forEach(function(ev) {
    var lvl = _LEVEL_ORDER[ev.dataset.eventLevel] !== undefined ? _LEVEL_ORDER[ev.dataset.eventLevel] : 0;
    ev.style.display = lvl >= minLevel ? '' : 'none';
  });
  document.querySelectorAll('.step-event-count').forEach(function(badge) {
    var step = badge.closest('.step');
    if (!step) return;
    var vis = 0;
    // only this step's own events: nested sub-steps have their own badge
    step.querySelectorAll(':scope > .step-events > .event[data-event-level]').forEach(function(ev) {
      if (ev.style.display !== 'none') vis++;
    });
    badge.textContent = vis;
  });
}
function globToRegex(pattern) {
  /* Convert a glob/wildcard pattern to a RegExp.
     Supports: * (any chars), ? (single char), "phrase" (literal quoted phrase).
     Multiple space-separated tokens are ANDed together.
     Tokens separated by OR (case-insensitive) are ORed. */
  var raw = pattern.trim();
  if (!raw) return null;
  // Tokenise: respect double-quoted strings
  var tokens = [];
  var re = /"([^"]*)"|(OR)(?=\\s|$)|[^\\s]+/gi;
  var m;
  while ((m = re.exec(raw)) !== null) {
    if (m[1] !== undefined) tokens.push({type:'literal', val:m[1].toLowerCase()});
    else if (m[2]) tokens.push({type:'OR'});
    else tokens.push({type:'glob', val:m[0].toLowerCase()});
  }
  // Build groups of AND-terms separated by OR
  var groups = [[]];
  tokens.forEach(function(t) {
    if (t.type === 'OR') groups.push([]);
    else groups[groups.length-1].push(t);
  });
  function escLiteral(s) { return s.replace(/[.*?+^${}()|[\\]\\\\]/g,'\\\\$&'); }
  function termToRegex(t) {
    if (t.type === 'literal') return new RegExp(escLiteral(t.val), 'i');
    // glob: * → .*, ? → .  (unanchored, so every term is a substring match)
    var p = t.val.split('').map(function(c){
      if (c==='*') return '.*';
      if (c==='?') return '.';
      return escLiteral(c);
    }).join('');
    return new RegExp(p, 'i');
  }
  return {groups: groups.map(function(g){ return g.map(termToRegex); })};
}
function matchesQuery(text, parsed) {
  if (!parsed) return true;
  return parsed.groups.some(function(andTerms) {
    return andTerms.every(function(rx) { return rx.test(text); });
  });
}
function filterTests() {
  var raw = (document.getElementById('searchInput').value || '');
  var parsed = globToRegex(raw);
  var status = document.getElementById('statusFilter').value;
  var visible = 0;
  document.querySelectorAll('.test-item').forEach(function(item) {
    var name = (item.querySelector('.test-name') && item.querySelector('.test-name').textContent || '');
    var method = (item.querySelector('.test-method-name') && item.querySelector('.test-method-name').textContent || '');
    var haystack = name + ' ' + method;
    var matchSearch = !parsed || matchesQuery(haystack, parsed);
    var matchStatus = status === 'all' || item.dataset.status === status;
    if (matchSearch && matchStatus) {
      item.classList.remove('hidden'); visible++;
      item.classList.toggle('highlight', !!parsed);
    } else {
      item.classList.add('hidden'); item.classList.remove('highlight');
    }
  });
  document.querySelectorAll('.test-class-group').forEach(function(g) {
    g.style.display = g.querySelector('.test-item:not(.hidden)') ? '' : 'none';
  });
  var nr = document.getElementById('no-results');
  if (visible === 0 && (parsed || status !== 'all')) {
    if (!nr) {
      var m = document.createElement('div'); m.id = 'no-results'; m.className = 'no-results';
      m.textContent = 'No tests match your criteria.';
      document.querySelector('.tests-container').appendChild(m);
    }
  } else if (nr) nr.remove();
}
filterLogLevel();
"""
