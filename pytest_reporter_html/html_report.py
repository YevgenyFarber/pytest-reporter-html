"""
Aggregated HTML report generator for pytest.

Reads all per-test JSON files produced by ``TestReporter`` and generates
a single-page HTML report with statistics, search/filter, collapsible
steps and events, JSON syntax highlighting, and CI-mode multi-run
index pages.

This is a direct port of the JUnit reporter's ``AggregatedReportGenerator.java``
so both reporters produce visually identical HTML output.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Timestamp format matching Logger.java / JUnit reporter
# ---------------------------------------------------------------------------
_TIMESTAMP_FMT = "%Y.%m.%d_%H.%M.%S"
_TIMESTAMP_FMT_MILLIS = "%Y.%m.%d_%H.%M.%S.%f"


def _format_ts(dt: datetime) -> str:
    """Format a datetime as ``yyyy.MM.dd_HH.mm.ss.SSS``."""
    base = dt.strftime(_TIMESTAMP_FMT)
    millis = f"{dt.microsecond // 1000:03d}"
    return f"{base}.{millis}"


# ---------------------------------------------------------------------------
# Data classes (mirrors Java inner classes)
# ---------------------------------------------------------------------------

@dataclass
class TestEvent:
    level: str = ""
    event: str = ""
    type: Optional[str] = None
    sourceFileName: Optional[str] = None
    sourceLineNumber: Optional[int] = None


@dataclass
class TestStep:
    name: str = ""
    startTime: int = 0
    endTime: int = 0
    status: str = "PASSED"
    failureMessage: Optional[str] = None
    stackTrace: Optional[str] = None
    events: list[TestEvent] = field(default_factory=list)


@dataclass
class TestResult:
    filename: str = ""
    className: str = "Tests"
    methodName: str = ""
    testName: str = ""
    startTime: int = 0
    status: str = "PASSED"
    failureMessage: Optional[str] = None
    stackTrace: Optional[str] = None
    duration: int = 0
    eventCount: int = 0
    httpRequestCount: int = 0
    tryNumber: int = 1
    steps: list[TestStep] = field(default_factory=list)


@dataclass
class RunInfo:
    timestamp: str = ""
    date: datetime = field(default_factory=datetime.now)
    fileName: str = ""
    file: Optional[Path] = None
    tryNumber: int = 1


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_report(report_directory: str, *, title: str = "Test Report") -> Optional[str]:
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

        json_files = sorted(json_dir.glob("*.json"))
        if not json_files:
            return None

        test_results: list[TestResult] = []
        for jf in json_files:
            try:
                with open(jf, "r", encoding="utf-8") as f:
                    root = json.load(f)
                test_results.append(_parse_test_result(jf.name, root))
            except Exception:
                print(f"  Failed to parse: {jf.name}")

        if not test_results:
            return None

        # Sort newest first
        test_results.sort(key=lambda r: r.startTime, reverse=True)

        # Group by class (preserving insertion order)
        grouped: dict[str, list[TestResult]] = {}
        for tr in test_results:
            grouped.setdefault(tr.className, []).append(tr)

        timestamp = _resolve_timestamp()
        is_ci = os.environ.get("REPORT_CI_RUN", "").lower() == "true"

        html = _generate_html(test_results, grouped, timestamp, is_ci, title=title)

        # Write a timestamped report
        ts_file = report_dir / f"TestReport_All_{timestamp}.html"
        ts_file.write_text(html, encoding="utf-8")

        if is_ci:
            _try_download_previous_runs(report_directory)

            all_runs = _find_all_runs(report_dir)
            _calculate_try_numbers(all_runs)
            _calculate_test_try_numbers(test_results, all_runs, timestamp)

            # Re-generate HTML with updated try numbers
            html = _generate_html(test_results, grouped, timestamp, is_ci, title=title)
            ts_file.write_text(html, encoding="utf-8")

            latest = report_dir / "TestReport_Latest.html"
            if len(all_runs) == 1:
                latest.write_text(html, encoding="utf-8")
            else:
                index_html = _generate_index_page(all_runs, timestamp)
                latest.write_text(index_html, encoding="utf-8")
        else:
            latest = report_dir / "TestReport_Latest.html"
            latest.write_text(html, encoding="utf-8")

        return str(latest)

    except Exception as exc:
        print(f"  Failed to generate aggregated report: {exc}")
        return None


# ---------------------------------------------------------------------------
# JSON parsing
# ---------------------------------------------------------------------------

def _parse_test_result(filename: str, root: dict) -> TestResult:
    result = TestResult(filename=filename)

    steps_data = root.get("steps", [])
    if not steps_data:
        return result

    first = steps_data[0]
    step_name = first.get("name", "")

    if " - " in step_name:
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

    for step_data in steps_data:
        ts = TestStep(
            name=step_data.get("name", ""),
            startTime=step_data.get("startTime", 0),
            endTime=step_data.get("endTime", 0),
            status=step_data.get("status", "PASSED"),
        )
        if ts.status == "FAILED":
            ts.failureMessage = step_data.get("failureMessage")
            ts.stackTrace = step_data.get("stackTrace")
            if result.status == "FAILED":
                if ts.failureMessage and not result.failureMessage:
                    result.failureMessage = ts.failureMessage
                if ts.stackTrace and not result.stackTrace:
                    result.stackTrace = ts.stackTrace

        for ev_data in step_data.get("events", []):
            te = TestEvent(
                level=ev_data.get("level", "INFO"),
                event=ev_data.get("event", ""),
                type=ev_data.get("type"),
                sourceFileName=ev_data.get("sourceFileName"),
                sourceLineNumber=ev_data.get("sourceLineNumber"),
            )
            ts.events.append(te)
            if "HTTP Request:" in te.event:
                result.httpRequestCount += 1
            result.eventCount += 1

        result.steps.append(ts)

    # Override failure info from the root level if present
    if root.get("failureMessage"):
        result.failureMessage = root["failureMessage"]
    if root.get("stackTrace"):
        result.stackTrace = root["stackTrace"]

    # Duration
    last = steps_data[-1]
    result.duration = last.get("endTime", 0) - result.startTime

    return result


# ---------------------------------------------------------------------------
# Timestamp helpers
# ---------------------------------------------------------------------------

def _resolve_timestamp() -> str:
    ts = os.environ.get("REPORT_TIMESTAMP")
    if ts and ts.strip():
        ts = ts.strip()
        try:
            millis = int(ts)
            dt = datetime.fromtimestamp(millis / 1000.0)
            return _format_ts(dt)
        except ValueError:
            return ts
    return _format_ts(datetime.now())


def _format_timestamp_hms(epoch_millis: int) -> str:
    dt = datetime.fromtimestamp(epoch_millis / 1000.0)
    return dt.strftime("%H:%M:%S.") + f"{dt.microsecond // 1000:03d}"


# ---------------------------------------------------------------------------
# Run discovery / try-number tracking
# ---------------------------------------------------------------------------

def _find_all_runs(report_dir: Path) -> list[RunInfo]:
    runs: list[RunInfo] = []
    for f in report_dir.glob("TestReport_All_*.html"):
        if not f.is_file():
            continue
        ts_str = f.name.replace("TestReport_All_", "").replace(".html", "")
        try:
            dt = datetime.strptime(ts_str, _TIMESTAMP_FMT_MILLIS)
        except ValueError:
            try:
                millis = int(ts_str)
                dt = datetime.fromtimestamp(millis / 1000.0)
                ts_str = _format_ts(dt)
            except ValueError:
                continue
        runs.append(RunInfo(timestamp=ts_str, date=dt, fileName=f.name, file=f))
    runs.sort(key=lambda r: r.date, reverse=True)
    return runs


def _calculate_try_numbers(runs: list[RunInfo]) -> None:
    for i, run in enumerate(runs):
        run.tryNumber = len(runs) - i


def _calculate_test_try_numbers(
    current: list[TestResult],
    all_runs: list[RunInfo],
    current_timestamp: str,
) -> None:
    counts: dict[str, int] = {}
    for run in all_runs:
        if run.timestamp == current_timestamp:
            continue
        for prev in _parse_previous_run_tests(run):
            key = f"{prev.className}.{prev.methodName}"
            counts[key] = counts.get(key, 0) + 1
    for tr in current:
        key = f"{tr.className}.{tr.methodName}"
        tr.tryNumber = counts.get(key, 0) + 1


def _parse_previous_run_tests(run: RunInfo) -> list[TestResult]:
    results: list[TestResult] = []
    if run.file is None:
        return results
    try:
        content = run.file.read_text(encoding="utf-8")
        for m in re.finditer(
            r"<span class='test-name'>(.*?)</span>.*?<span class='test-method-name'>(.*?)</span>",
            content,
            re.DOTALL,
        ):
            tr = TestResult()
            tr.methodName = m.group(2)
            start = m.start()
            before = content[max(0, start - 500): start]
            cm = re.search(r"<h2 class='class-name'>(.*?)</h2>", before, re.DOTALL)
            if cm:
                tr.className = cm.group(1).strip()
            else:
                test_name_html = m.group(1)
                if " - " in test_name_html:
                    tr.className = test_name_html.split(" - ")[0].strip()
                else:
                    tr.className = "Unknown"
            results.append(tr)
    except Exception:
        pass
    return results


def _try_download_previous_runs(report_directory: str) -> None:
    try:
        from .s3_utils import download_previous_runs_from_s3  # type: ignore[attr-defined]
        download_previous_runs_from_s3(report_directory)
    except (ImportError, AttributeError):
        pass
    except Exception as exc:
        print(f"  Failed to download previous runs from S3: {exc}")


# ---------------------------------------------------------------------------
# HTML helpers
# ---------------------------------------------------------------------------

def _escape_html(text: Optional[str]) -> str:
    if text is None:
        return ""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def _format_test_name(method_name: str) -> str:
    if not method_name:
        return method_name
    name = method_name
    if name.startswith("test_"):
        name = name[5:]
    name = name.replace("_", " ")
    name = re.sub(r"([a-z])([A-Z])", r"\1 \2", name)
    return name[0].upper() + name[1:] if name else name


def _format_class_name(class_name: str) -> str:
    if not class_name:
        return class_name
    name = class_name
    if name.startswith("Test") and (len(name) == 4 or name[4].isupper() or name[4] == "_"):
        name = name[4:]
    if name.startswith("test_"):
        name = name[5:]
    name = name.replace("_", " ")
    name = re.sub(r"([a-z])([A-Z])", r"\1 \2", name)
    name = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", name)
    name = " ".join(w if w.isupper() else (w[0].upper() + w[1:]) for w in name.split())
    return name.strip() if name.strip() else class_name


def _format_try_number(try_number: int) -> str:
    if try_number <= 0:
        return ""
    last = try_number % 10
    last_two = try_number % 100
    if 11 <= last_two <= 13:
        suffix = "th"
    elif last == 1:
        suffix = "st"
    elif last == 2:
        suffix = "nd"
    elif last == 3:
        suffix = "rd"
    else:
        suffix = "th"
    return f"{try_number}{suffix} try"


# ---------------------------------------------------------------------------
# JSON formatting / syntax highlighting for display
# ---------------------------------------------------------------------------

def _format_json_for_display(json_str: str) -> str:
    if not json_str or not json_str.strip():
        return ""
    escaped = _escape_html(json_str)

    # Highlight keys (quoted strings followed by colon)
    counter = 0
    placeholders: dict[str, str] = {}

    def _replace_key(m: re.Match) -> str:
        nonlocal counter
        placeholder = f"___JSON_KEY_PLACEHOLDER_{counter}___"
        counter += 1
        placeholders[placeholder] = f"<span class='json-key'>{m.group(1)}</span>"
        return placeholder

    escaped = re.sub(r'("(?:[^"\\]|\\.)+"\s*:)', _replace_key, escaped)

    # Highlight remaining string values
    escaped = re.sub(r'("(?:[^"\\]|\\.)+")', r"<span class='json-string'>\1</span>", escaped)

    # Restore key placeholders
    for ph, replacement in placeholders.items():
        escaped = escaped.replace(ph, replacement)

    # Highlight numbers
    escaped = re.sub(
        r'(?<!["\w])(\b\d+\.?\d*\b)(?!["\w])',
        r"<span class='json-number'>\1</span>",
        escaped,
    )

    # Highlight literals
    escaped = re.sub(
        r'(?<!["\w])\b(true|false|null)\b(?!["\w])',
        r"<span class='json-literal'>\1</span>",
        escaped,
    )

    return escaped


def _try_pretty_json(text: str) -> Optional[str]:
    """Return pretty-printed JSON if ``text`` is valid JSON, else None."""
    stripped = text.strip()
    if not ((stripped.startswith("{") and stripped.endswith("}"))
            or (stripped.startswith("[") and stripped.endswith("]"))):
        return None
    try:
        obj = json.loads(stripped)
        return json.dumps(obj, indent=2, ensure_ascii=False)
    except (json.JSONDecodeError, ValueError):
        return None


def _format_event_with_json(event_text: str) -> str:
    """Detect embedded JSON in event text and format it."""
    if not event_text or not event_text.strip():
        return f"<span class='event-text'>{_escape_html(event_text)}</span>"

    trimmed = event_text.strip()
    # Skip GraphQL
    if any(trimmed.startswith(kw) for kw in ("mutation", "query", "subscription")):
        return f"<span class='event-text'>{_escape_html(event_text)}</span>"
    if re.match(r"^\w+\s*\(", trimmed):
        return f"<span class='event-text'>{_escape_html(event_text)}</span>"
    if "[ ]" in event_text:
        return f"<span class='event-text'>{_escape_html(event_text)}</span>"

    # Try the whole message as JSON
    pretty = _try_pretty_json(event_text)
    if pretty is not None:
        fmt = _format_json_for_display(pretty)
        return (
            "<div class='json-container'>"
            "<div class='json-header'><span class='json-label'>JSON</span></div>"
            f"<pre class='event-json'>{fmt}</pre></div>"
        )

    # Search for embedded JSON
    result: list[str] = []
    i = 0
    last_processed = 0
    length = len(event_text)

    while i < length:
        c = event_text[i]
        if c in ("{", "["):
            json_start = i
            brace = 0
            bracket = 0
            in_str = False
            esc = False
            json_end = -1
            for j in range(i, length):
                ch = event_text[j]
                if esc:
                    esc = False
                    continue
                if ch == "\\":
                    esc = True
                    continue
                if ch == '"' and not esc:
                    in_str = not in_str
                    continue
                if in_str:
                    continue
                if ch == "{":
                    brace += 1
                elif ch == "}":
                    brace -= 1
                elif ch == "[":
                    bracket += 1
                elif ch == "]":
                    bracket -= 1
                if (c == "{" and brace == 0) or (c == "[" and bracket == 0):
                    json_end = j + 1
                    break

            if json_end > json_start:
                candidate = event_text[json_start:json_end]
                pretty_c = _try_pretty_json(candidate)
                if pretty_c is not None:
                    if json_start > last_processed:
                        result.append(
                            f"<span class='event-text'>{_escape_html(event_text[last_processed:json_start])}</span>"
                        )
                    fmt = _format_json_for_display(pretty_c)
                    result.append(
                        "<div class='json-container'>"
                        "<div class='json-header'><span class='json-label'>JSON</span></div>"
                        f"<pre class='event-json'>{fmt}</pre></div>"
                    )
                    i = json_end
                    last_processed = json_end
                    continue
        i += 1

    if result:
        if last_processed < length:
            result.append(
                f"<span class='event-text'>{_escape_html(event_text[last_processed:])}</span>"
            )
        return "".join(result)

    return f"<span class='event-text'>{_escape_html(event_text)}</span>"


def _extract_curl_from_json(json_str: str) -> Optional[str]:
    """Generate a curl command from an HTTP request JSON."""
    if not json_str or '"method"' not in json_str or '"url"' not in json_str:
        return None
    try:
        node = json.loads(json_str)
        if "method" not in node or "url" not in node:
            return None
        method = node["method"]
        url = node["url"]
        headers = node.get("headers", {})
        body = node.get("body")
        if body is not None and not isinstance(body, str):
            body = json.dumps(body, ensure_ascii=False)

        parts = [f"curl -X {method}"]
        for k, v in headers.items():
            parts.append(f"  -H '{k}: {v}'")
        if body:
            escaped_body = body.replace("'", "'\\''")
            parts.append(f"  -d '{escaped_body}'")
        parts.append(f"  '{url}'")
        return " \\\n".join(parts)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------

def _render_http_request_with_curl(
    json_str: str, curl_cmd: str, unique_id: str,
) -> str:
    h: list[str] = ["<div class='http-event http-request'>\n", "<div class='http-header'>\n", "<span class='http-icon'>&#127760;</span>\n", "<strong>HTTP Request</strong>\n", "</div>\n",
                    "<div class='json-container' style='margin-top: 12px;'>\n", "<div class='json-header'>\n", "<span class='json-label'>Request Details</span>\n",
                    f"<button class='copy-btn' onclick='copyToClipboard(\"{unique_id}\")' title='Copy JSON'>&#128203;</button>\n", "</div>\n"]
    # JSON
    data_orig = _escape_html(json_str).replace("'", "&#39;")
    h.append(f"<pre class='event-json' id='json-{unique_id}' data-original='{data_orig}'>{_format_json_for_display(json_str)}</pre>\n")
    h.append("</div>\n")
    # cURL
    curl_id = f"{unique_id}-curl"
    h.append("<div class='curl-container' style='margin-top: 12px;'>\n")
    h.append("<div class='curl-header'>\n")
    h.append("<span class='curl-label'>&#128203; cURL Command</span>\n")
    h.append(f"<button class='copy-btn' onclick='copyCurlCommand(\"{curl_id}\")' title='Copy cURL'>&#128203;</button>\n")
    h.append("</div>\n")
    curl_orig = _escape_html(curl_cmd).replace("'", "&#39;")
    h.append(f"<pre class='curl-command' id='curl-{curl_id}' data-original='{curl_orig}'>{_escape_html(curl_cmd)}</pre>\n")
    h.append("</div>\n")
    h.append("</div>\n")
    return "".join(h)


def _render_http_event(event_text: str) -> str:
    h: list[str] = []
    if "HTTP Request:" in event_text:
        h.append("<div class='http-event http-request'>\n")
        h.append("<div class='http-header'>\n")
        h.append("<span class='http-icon'>&#127760;</span>\n")
        h.append("<strong>HTTP Request</strong>\n")
        h.append("</div>\n")
        h.append(f"<div class='http-content'>{_escape_html(event_text)}</div>\n")
        h.append("</div>\n")
    elif "HTTP Response:" in event_text:
        icon = "&#9989;" if "✅" in event_text else "&#10060;"
        h.append("<div class='http-event http-response'>\n")
        h.append("<div class='http-header'>\n")
        h.append(f"<span class='http-icon'>{icon}</span>\n")
        h.append("<strong>HTTP Response</strong>\n")
        h.append("</div>\n")
        h.append(f"<div class='http-content'>{_escape_html(event_text)}</div>\n")
        h.append("</div>\n")
    else:
        h.append(f"<span class='event-text'>{_escape_html(event_text)}</span>\n")
    return "".join(h)


def _render_event_with_traceback(event_text: str, uid: str) -> str:
    """Render a log event that contains an embedded Python traceback."""
    tb_marker = "\nTraceback (most recent call last):"
    pos = event_text.index(tb_marker)
    message = event_text[:pos]
    traceback_text = event_text[pos + 1:]  # skip the leading newline

    h: list[str] = [f"<span class='event-text'>{_escape_html(message)}</span>\n", "<div class='event-stacktrace-section'>\n", f"<div class='event-stacktrace-toggle' onclick='toggleEventStackTrace(\"{uid}\")'>\n",
                    f"<span class='event-stacktrace-icon open' id='event-stacktrace-icon-{uid}'>&#9654;</span>\n", "<strong>Exception</strong>\n", "</div>\n",
                    f"<pre class='event-stacktrace-content' id='event-stacktrace-{uid}' style='display: block;'>{_escape_html(traceback_text)}</pre>\n", "</div>\n"]
    return "".join(h)


def _render_test(result: TestResult, index: int) -> str:
    status_class = "passed" if result.status == "PASSED" else "failed"
    status_icon = "<span class='status-dot pass'></span>" if result.status == "PASSED" else "<span class='status-dot fail'></span>"
    auto_open = result.status == "FAILED"

    total_events = sum(len(s.events) for s in result.steps)

    h: list[str] = [f"<div class='test-item {status_class}' data-status='{result.status}'>\n", f"<div class='test-header' onclick='toggleTest({index})'>\n", f"{status_icon}\n", "<div class='test-name-container'>\n",
                    f"<span class='test-name'>{_escape_html(result.testName)}</span>\n", f"<span class='test-method-name'>{_escape_html(result.methodName)}</span>\n", "</div>\n", "<span class='test-meta'>\n"]
    dur = result.duration / 1000
    h.append(f"<span class='meta-item meta-duration'>{dur:.2f}s</span>\n")
    if total_events > 0:
        h.append(f"<span class='meta-item meta-events'>{total_events} event{'s' if total_events != 1 else ''}</span>\n")
    if result.tryNumber > 1:
        h.append(f"<span class='meta-item try-badge'>{_format_try_number(result.tryNumber)}</span>\n")
    h.append("</span>\n")
    h.append(f"<span class='toggle-icon' id='icon-{index}'>&#9660;</span>\n")
    h.append("</div>\n")

    open_class = " open" if auto_open else ""
    display = "block" if auto_open else "none"
    h.append(f"<div class='test-details{open_class}' id='test-{index}' style='display: {display};'>\n")

    if result.failureMessage:
        h.append("<div class='failure-message'>\n")
        h.append("<div class='failure-label'>FAILURE</div>\n")
        h.append(f"<pre>{_escape_html(result.failureMessage)}</pre>\n")
        if result.stackTrace:
            h.append("<div class='stacktrace-section'>\n")
            h.append(f"<div class='stacktrace-toggle' onclick='toggleStackTrace({index})'>\n")
            h.append(f"<span class='stacktrace-icon' id='stacktrace-icon-{index}'>&#9654;</span>\n")
            h.append("<strong>Stack Trace</strong>\n")
            h.append("</div>\n")
            h.append(f"<pre class='stacktrace-content' id='stacktrace-{index}' style='display: none;'>{_escape_html(result.stackTrace)}</pre>\n")
            h.append("</div>\n")
        h.append("</div>\n")

    for si, step in enumerate(result.steps):
        has_events = len(step.events) > 0
        if not has_events and step.status == "PASSED":
            continue

        step_id = f"step-{index}-{si}"
        step_icon_id = f"step-icon-{index}-{si}"
        step_status = "step-passed" if step.status == "PASSED" else "step-failed"
        step_open = has_events and auto_open

        h.append(f"<div class='step {step_status}'>\n")
        h.append(f"<div class='step-header' onclick='toggleStep(\"{step_id}\", \"{step_icon_id}\")'>\n")
        open_cls = " open" if step_open else ""
        h.append(f"<span class='step-toggle-icon{open_cls}' id='{step_icon_id}'>&#9654;</span>\n")
        h.append(f"<span class='step-name'>{_escape_html(step.name)}</span>\n")
        if step.events:
            h.append(f"<span class='step-event-count'>{len(step.events)}</span>\n")
        dur_ms = step.endTime - step.startTime
        if dur_ms > 0:
            h.append(f"<span class='step-duration'>{dur_ms}ms</span>\n")
        h.append(f"<span class='step-time'>{_format_timestamp_hms(step.startTime)}</span>\n")
        h.append("</div>\n")

        step_display = "block" if step_open else "none"
        h.append(f"<div class='step-events' id='{step_id}' style='display: {step_display};'>\n")

        for ei, ev in enumerate(step.events):
            ev_class = f"event-{ev.level.lower()}"
            uid = f"{index}-{id(step)}-{ei}"
            h.append(f"<div class='event {ev_class}' data-event-level='{ev.level}'>\n")
            h.append(f"<span class='event-level'>{ev.level}</span>\n")

            if ev.sourceFileName or ev.sourceLineNumber is not None:
                loc_parts: list[str] = []
                if ev.sourceFileName:
                    loc_parts.append(_escape_html(ev.sourceFileName))
                if ev.sourceLineNumber is not None:
                    if ev.sourceFileName:
                        loc_parts.append(":")
                    loc_parts.append(str(ev.sourceLineNumber))
                h.append(f"<span class='event-source-location'>{''.join(loc_parts)}</span>\n")

            if ev.type == "json":
                curl = _extract_curl_from_json(ev.event)
                if curl:
                    h.append(_render_http_request_with_curl(ev.event, curl, uid))
                else:
                    h.append("<div class='json-container'>\n")
                    h.append("<div class='json-header'>\n")
                    h.append("<span class='json-label'>JSON</span>\n")
                    h.append(f"<button class='copy-btn' onclick='copyToClipboard(\"{uid}\")' title='Copy JSON'>&#128203;</button>\n")
                    h.append("</div>\n")
                    data_orig = _escape_html(ev.event).replace("'", "&#39;")
                    pretty = _try_pretty_json(ev.event)
                    display_j = _format_json_for_display(pretty if pretty else ev.event)
                    h.append(f"<pre class='event-json' id='json-{uid}' data-original='{data_orig}'>{display_j}</pre>\n")
                    h.append("</div>\n")
            elif "HTTP Request:" in ev.event or "HTTP Response:" in ev.event:
                h.append(_render_http_event(ev.event))
            elif ev.event.startswith("Stack Trace:"):
                st_content = ev.event[len("Stack Trace:"):].strip()
                h.append("<div class='event-stacktrace-section'>\n")
                h.append(f"<div class='event-stacktrace-toggle' onclick='toggleEventStackTrace(\"{uid}\")'>\n")
                h.append(f"<span class='event-stacktrace-icon open' id='event-stacktrace-icon-{uid}'>&#9654;</span>\n")
                h.append("<strong>Stack Trace</strong>\n")
                h.append("</div>\n")
                h.append(f"<pre class='event-stacktrace-content' id='event-stacktrace-{uid}' style='display: block;'>{_escape_html(st_content)}</pre>\n")
                h.append("</div>\n")
            elif "\nTraceback (most recent call last):" in ev.event:
                h.append(_render_event_with_traceback(ev.event, uid))
            else:
                h.append(_format_event_with_json(ev.event))
                h.append("\n")

            h.append("</div>\n")
        h.append("</div>\n")
        h.append("</div>\n")

    h.append("</div>\n")
    h.append("</div>\n")
    return "".join(h)


# ---------------------------------------------------------------------------
# Full-page HTML generation
# ---------------------------------------------------------------------------

def _generate_html(
    results: list[TestResult],
    grouped: dict[str, list[TestResult]],
    run_timestamp: str,
    is_ci: bool,
    title: str = "Test Report",
) -> str:
    total = len(results)
    passed = sum(1 for r in results if r.status == "PASSED")
    failed = total - passed
    total_duration = sum(r.duration for r in results)
    total_events = sum(r.eventCount for r in results)
    has_multiple_classes = len(grouped) > 1

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    h: list[str] = ["<!DOCTYPE html>\n<html lang='en'>\n<head>\n", "<meta charset='UTF-8'>\n", "<meta name='viewport' content='width=device-width, initial-scale=1.0'>\n", f"<title>{_escape_html(title)}</title>\n", "<style>\n",
                    _get_css(), "</style>\n", "</head>\n<body>\n", "<header class='header'>\n", "<div class='header-top'>\n"]

    # Header
    if is_ci:
        h.append("<a href='TestReport_Latest.html' class='back-link'>&larr; All Runs</a>\n")
    h.append(f"<h1>{_escape_html(title)}</h1>\n")
    h.append(f"<div class='header-meta'>{run_timestamp} &middot; {now_str}</div>\n")
    h.append("</div>\n")

    # Progress bar
    rate = (passed * 100.0 / total) if total > 0 else 0.0
    h.append("<div class='progress-bar-container'>\n")
    h.append(f"<div class='progress-bar pass-bar' style='width:{rate:.1f}%'></div>\n")
    if failed > 0:
        fail_pct = 100.0 - rate
        h.append(f"<div class='progress-bar fail-bar' style='width:{fail_pct:.1f}%'></div>\n")
    h.append("</div>\n")

    # Stats row
    h.append("<div class='stats-row'>\n")
    h.append(f"<span class='stat'><strong>{total}</strong> tests</span>\n")
    h.append(f"<span class='stat stat-pass'><strong>{passed}</strong> passed</span>\n")
    if failed > 0:
        h.append(f"<span class='stat stat-fail'><strong>{failed}</strong> failed</span>\n")
    h.append(f"<span class='stat'><strong>{rate:.0f}%</strong> pass rate</span>\n")
    h.append(f"<span class='stat'><strong>{total_duration / 1000:.2f}s</strong> duration</span>\n")
    if total_events > 0:
        h.append(f"<span class='stat'><strong>{total_events}</strong> events</span>\n")
    h.append("</div>\n")
    h.append("</header>\n")

    # Toolbar
    h.append("<div class='toolbar'>\n")
    h.append("<input type='text' id='searchInput' class='search-input' placeholder='Search tests...' onkeyup='filterTests()'>\n")
    h.append("<select id='statusFilter' class='status-filter' onchange='filterTests()'>\n")
    h.append("<option value='all'>All</option>\n")
    h.append("<option value='PASSED'>Passed</option>\n")
    h.append("<option value='FAILED'>Failed</option>\n")
    h.append("</select>\n")
    h.append("<select id='logLevelFilter' class='status-filter' onchange='filterLogLevel()' title='Minimum log level to display'>\n")
    h.append("<option value='TRACE'>TRACE</option>\n")
    h.append("<option value='DEBUG' selected>DEBUG</option>\n")
    h.append("<option value='INFO'>INFO</option>\n")
    h.append("<option value='WARN'>WARN</option>\n")
    h.append("<option value='ERROR'>ERROR</option>\n")
    h.append("</select>\n")
    h.append("<button class='toolbar-btn' onclick='expandAll()'>Expand All</button>\n")
    h.append("<button class='toolbar-btn' onclick='collapseAll()'>Collapse All</button>\n")
    h.append("</div>\n")

    # Tests
    h.append("<main class='tests-container'>\n")
    idx = 0
    if has_multiple_classes:
        cls_idx = 0
        for class_name, class_tests in grouped.items():
            cls_passed = sum(1 for t in class_tests if t.status == "PASSED")
            cls_total = len(class_tests)
            cls_failed = cls_total - cls_passed
            cls_status = "class-all-pass" if cls_failed == 0 else "class-has-fail"
            display_name = _format_class_name(class_name)
            h.append(f"<div class='test-class-group {cls_status}' data-class-status='{'FAILED' if cls_failed else 'PASSED'}'>\n")
            h.append(f"<h2 class='class-name' onclick='toggleClassGroup({cls_idx})'>\n")
            h.append(f"<span class='class-toggle-icon' id='class-icon-{cls_idx}'>&#9660;</span>\n")
            h.append(f"<span class='class-display-name'>{_escape_html(display_name)}</span>\n")
            h.append(f"<span class='class-count'>{cls_passed}/{cls_total}</span>\n")
            if cls_failed > 0:
                h.append(f"<span class='class-fail-badge'>{cls_failed} failed</span>\n")
            h.append("</h2>\n")
            h.append(f"<div class='class-tests' id='class-group-{cls_idx}'>\n")
            for tr in class_tests:
                h.append(_render_test(tr, idx))
                idx += 1
            h.append("</div>\n")
            h.append("</div>\n")
            cls_idx += 1
    else:
        for tr in results:
            h.append(_render_test(tr, idx))
            idx += 1
    h.append("</main>\n")

    h.append("<script>\n")
    h.append(_get_javascript())
    h.append("</script>\n")

    h.append("</body>\n</html>")
    return "".join(h)


# ---------------------------------------------------------------------------
# Index page (CI mode — lists all runs)
# ---------------------------------------------------------------------------

def _generate_index_page(runs: list[RunInfo], current_timestamp: str) -> str:
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    h: list[str] = ["<!DOCTYPE html>\n<html>\n<head>\n", "<meta charset='UTF-8'>\n", "<title>Test Reports - All Runs</title>\n", "<style>\n", _get_index_css(), "</style>\n", "</head>\n<body>\n", "<div class='header'>\n",
                    "<h1>&#128202; Test Execution Reports - All Runs</h1>\n", f"<div class='timestamp'>Generated: {now_str}</div>\n", "</div>\n", "<div class='runs-container'>\n", "<h2>Available Test Runs</h2>\n"]

    if not runs:
        h.append("<div class='no-runs'>No test runs found.</div>\n")
    else:
        h.append("<div class='runs-list'>\n")
        for run in runs:
            is_current = run.timestamp == current_timestamp
            badge = "<span class='current-badge'>Current Run</span>" if is_current else ""
            date_str = run.date.strftime("%Y-%m-%d %H:%M:%S")
            try_str = _format_try_number(run.tryNumber)

            cur_cls = " current" if is_current else ""
            h.append(f"<div class='run-item{cur_cls}'>\n")
            h.append("<div class='run-header'>\n")
            h.append(f"<a href='{run.fileName}' class='run-link'>\n")
            h.append("<span class='run-icon'>&#128203;</span>\n")
            h.append(f"<span class='run-date'>{date_str}</span>\n")
            h.append(f"<span class='try-number'>{try_str}</span>\n")
            h.append(badge)
            h.append("</a>\n")
            h.append("</div>\n")
            h.append("<div class='run-meta'>\n")
            h.append(f"<span class='run-file'>{run.fileName}</span>\n")
            h.append("</div>\n")
            h.append("</div>\n")
        h.append("</div>\n")

    h.append("</div>\n")
    h.append("</body>\n</html>")
    return "".join(h)


# ---------------------------------------------------------------------------
# CSS (identical to JUnit reporter's AggregatedReportGenerator.getCSS)
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
@media (prefers-reduced-motion: reduce) {{ * {{ transition: none !important; animation: none !important; }} }}

* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family: -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif; background:var(--bg); color:var(--text); line-height:1.5; }}

/* --- Header --- */
.header {{ background:#1a1d23; color:#fff; padding:24px 32px; }}
.header-top {{ display:flex; align-items:baseline; gap:16px; flex-wrap:wrap; }}
.header h1 {{ font-size:20px; font-weight:700; letter-spacing:-0.3px; }}
.header-meta {{ font-size:13px; color:#9ca3af; font-family:var(--mono); }}
.back-link {{ color:#93c5fd; text-decoration:none; font-size:13px; margin-right:auto; }}
.back-link:hover {{ color:#fff; }}

.progress-bar-container {{ display:flex; height:4px; margin-top:16px; border-radius:2px; overflow:hidden; background:#374151; }}
.progress-bar {{ transition:width 0.4s ease; }}
.pass-bar {{ background:var(--green); }}
.fail-bar {{ background:var(--red); }}

.stats-row {{ display:flex; gap:24px; margin-top:12px; flex-wrap:wrap; }}
.stat {{ font-size:13px; color:#d1d5db; }}
.stat strong {{ color:#fff; font-weight:600; }}
.stat-pass strong {{ color:var(--green); }}
.stat-fail strong {{ color:#f87171; }}

/* --- Toolbar --- */
.toolbar {{ display:flex; gap:8px; padding:12px 32px; background:var(--surface); border-bottom:1px solid var(--border); position:sticky; top:0; z-index:10; }}
.search-input {{ flex:1; padding:8px 12px; border:1px solid var(--border); border-radius:var(--radius); font-size:13px; font-family:inherit; background:var(--bg); }}
.search-input:focus {{ outline:none; border-color:var(--blue); box-shadow:0 0 0 3px rgba(37,99,235,.12); }}
.status-filter {{ padding:8px 12px; border:1px solid var(--border); border-radius:var(--radius); font-size:13px; cursor:pointer; background:var(--bg); font-family:inherit; }}
.status-filter:focus-visible {{ outline:2px solid var(--blue); outline-offset:1px; }}
.toolbar-btn {{ padding:8px 14px; border:1px solid var(--border); border-radius:var(--radius); font-size:12px; cursor:pointer; background:var(--surface); font-family:inherit; font-weight:500; color:var(--text-2); }}
.toolbar-btn:hover {{ background:var(--bg); color:var(--text); }}
.toolbar-btn:focus-visible {{ outline:2px solid var(--blue); outline-offset:1px; }}

/* --- Tests container --- */
.tests-container {{ padding:16px 32px 48px; }}
.test-class-group {{ margin-bottom:32px; }}
.class-name {{ font-size:14px; font-weight:700; color:var(--text-2); margin-bottom:0; padding:10px 12px; border-bottom:2px solid var(--border); cursor:pointer; display:flex; align-items:center; gap:10px; user-select:none; border-radius:var(--radius) var(--radius) 0 0; }}
.class-name:hover {{ background:rgba(0,0,0,.02); }}
.class-toggle-icon {{ font-size:10px; color:var(--text-3); transition:transform .2s; flex-shrink:0; }}
.class-toggle-icon.collapsed {{ transform:rotate(-90deg); }}
.class-display-name {{ flex:1; }}
.class-count {{ font-weight:400; color:var(--text-3); font-size:12px; }}
.class-fail-badge {{ font-size:10px; font-weight:600; background:var(--red-bg); color:var(--red); padding:1px 8px; border-radius:10px; border:1px solid var(--red-border); }}
.class-all-pass {{ border-left:3px solid var(--green); }}
.class-has-fail {{ border-left:3px solid var(--red); }}
.class-tests {{ padding:4px 0 4px 24px; }}

/* --- Test item --- */
.test-item {{ background:var(--surface); border:1px solid var(--border); border-radius:var(--radius); margin-bottom:6px; transition:box-shadow .15s; }}
.test-item:hover {{ box-shadow:0 1px 4px rgba(0,0,0,.06); }}
.test-item.passed {{ border-left:3px solid var(--green); }}
.test-item.failed {{ border-left:3px solid var(--red); background:var(--red-bg); }}
.test-item.hidden {{ display:none !important; }}
.test-item.highlight {{ background:#fef9c3 !important; border-color:var(--amber) !important; }}

.test-header {{ padding:10px 16px; cursor:pointer; display:flex; align-items:center; gap:12px; user-select:none; }}
.test-header:hover {{ background:rgba(0,0,0,.02); }}

.status-dot {{ width:8px; height:8px; border-radius:50%; flex-shrink:0; }}
.status-dot.pass {{ background:var(--green); }}
.status-dot.fail {{ background:var(--red); }}

.test-name-container {{ flex:1; min-width:0; }}
.test-name {{ font-weight:600; font-size:13px; color:var(--text); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
.test-method-name {{ font-size:11px; color:var(--text-3); font-family:var(--mono); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}

.test-meta {{ display:flex; gap:8px; font-size:11px; color:var(--text-3); flex-shrink:0; }}
.meta-item {{ font-family:var(--mono); }}
.meta-events {{ background:var(--blue-bg); color:var(--blue); padding:1px 6px; border-radius:10px; font-weight:600; }}
.meta-duration {{ }}
.try-badge {{ background:var(--blue); color:#fff; padding:1px 6px; border-radius:10px; font-weight:600; }}

.toggle-icon {{ color:var(--text-3); font-size:10px; transition:transform .2s; flex-shrink:0; }}
.toggle-icon.open {{ transform:rotate(180deg); }}

/* --- Test details --- */
.test-details {{ border-top:1px solid var(--border); background:var(--surface); overflow:hidden; padding-left:24px; }}
.test-details.open {{ overflow:visible; }}

.failure-message {{ padding:16px; background:var(--red-bg); border-left:3px solid var(--red); margin:12px 16px; border-radius:var(--radius); }}
.failure-label {{ font-size:11px; font-weight:700; text-transform:uppercase; letter-spacing:.05em; color:var(--red); margin-bottom:6px; }}
.failure-message pre {{ color:#991b1b; font-size:12px; line-height:1.6; white-space:pre-wrap; word-break:break-word; font-family:var(--mono); }}
.stacktrace-section {{ margin-top:12px; }}
.stacktrace-toggle {{ cursor:pointer; padding:4px 0; color:var(--red); font-size:12px; display:flex; align-items:center; gap:6px; user-select:none; }}
.stacktrace-toggle:hover {{ opacity:.8; }}
.stacktrace-icon {{ display:inline-block; transition:transform .2s; font-size:9px; }}
.stacktrace-icon.open {{ transform:rotate(90deg); }}
.stacktrace-content {{ margin-top:6px; color:#991b1b; font-size:11px; line-height:1.5; white-space:pre-wrap; word-break:break-word; font-family:var(--mono); max-height:400px; overflow-y:auto; background:#fff5f5; padding:10px; border-radius:var(--radius); border:1px solid var(--red-border); }}

/* --- Steps --- */
.step {{ padding:8px 16px; border-bottom:1px solid #f3f4f6; }}
.step:last-child {{ border-bottom:none; }}
.step-passed .step-header {{ background:#f8fafb; }}
.step-failed .step-header {{ background:var(--red-bg); }}
.step-header {{ display:flex; align-items:center; gap:8px; padding:6px 10px; border-radius:var(--radius); cursor:pointer; user-select:none; font-size:13px; }}
.step-header:hover {{ filter:brightness(.97); }}
.step-toggle-icon {{ font-size:9px; transition:transform .2s; color:var(--text-3); flex-shrink:0; }}
.step-toggle-icon.open {{ transform:rotate(90deg); }}
.step-name {{ font-weight:500; flex:1; color:var(--text); }}
.step-event-count {{ font-size:10px; font-weight:600; background:var(--blue-bg); color:var(--blue); padding:1px 6px; border-radius:10px; flex-shrink:0; }}
.step-duration {{ font-size:11px; color:var(--text-3); font-family:var(--mono); flex-shrink:0; }}
.step-time {{ font-size:11px; color:var(--text-3); font-family:var(--mono); flex-shrink:0; }}
.step-events {{ padding:4px 0 4px 18px; }}
.no-events {{ font-size:12px; color:var(--text-3); padding:6px 0; font-style:italic; }}

/* --- Events --- */
.event {{ padding:6px 10px; margin:3px 0; border-radius:4px; background:#f9fafb; border-left:3px solid #d1d5db; font-size:12px; line-height:1.6; }}
.event-info {{ border-left-color:var(--blue); background:var(--blue-bg); }}
.event-error {{ border-left-color:var(--red); background:var(--red-bg); }}
.event-warn {{ border-left-color:var(--amber); background:var(--amber-bg); }}
.event-debug {{ border-left-color:#9ca3af; background:#f9fafb; }}
.event-trace {{ border-left-color:#c4b5fd; background:#f5f3ff; }}
.event-level {{ font-weight:700; font-size:10px; text-transform:uppercase; color:var(--text-3); margin-right:6px; letter-spacing:.04em; }}
.event-source-location {{ font-size:10px; color:var(--text-3); font-family:var(--mono); margin-left:6px; padding:1px 5px; background:var(--border); border-radius:3px; }}
.event-text {{ color:var(--text); line-height:1.6; }}

/* --- JSON / HTTP / cURL --- */
.json-container {{ margin-top:6px; border:1px solid var(--blue-border); border-radius:var(--radius); overflow:hidden; }}
.json-header {{ display:flex; justify-content:space-between; align-items:center; background:#dbeafe; padding:5px 10px; border-bottom:1px solid var(--blue-border); }}
.json-label {{ font-size:10px; font-weight:700; text-transform:uppercase; color:#1e40af; letter-spacing:.05em; }}
.copy-btn {{ background:var(--blue); color:#fff; border:none; padding:3px 8px; border-radius:4px; cursor:pointer; font-size:11px; font-family:inherit; }}
.copy-btn:hover {{ background:#1d4ed8; }}
.event-json {{ font-family:var(--mono); font-size:12px; color:#1e3a5f; background:var(--blue-bg); padding:10px; overflow-x:auto; margin:0; line-height:1.6; }}
.json-key {{ color:var(--blue); font-weight:600; }}
.json-string {{ color:var(--blue); }}
.json-number {{ color:var(--blue); }}
.json-literal {{ color:#ea580c; font-weight:600; }}

.curl-container {{ margin-top:8px; border:1px solid var(--border); border-radius:var(--radius); overflow:hidden; }}
.curl-header {{ display:flex; justify-content:space-between; align-items:center; background:#f3f4f6; padding:5px 10px; border-bottom:1px solid var(--border); }}
.curl-label {{ font-size:10px; font-weight:700; text-transform:uppercase; color:#374151; letter-spacing:.05em; }}
.curl-command {{ font-family:var(--mono); font-size:11px; color:#1e293b; background:#f9fafb; padding:10px; overflow-x:auto; margin:0; line-height:1.6; white-space:pre-wrap; word-break:break-word; }}

.http-event {{ margin-top:6px; border-radius:var(--radius); overflow:hidden; border:1px solid; }}
.http-request {{ border-color:var(--blue-border); background:var(--blue-bg); }}
.http-response {{ border-color:var(--green-border); background:var(--green-bg); }}
.http-header {{ display:flex; align-items:center; gap:6px; padding:6px 10px; font-weight:600; font-size:12px; }}
.http-icon {{ font-size:14px; }}
.http-content {{ padding:10px; font-family:var(--mono); font-size:12px; color:#1e293b; white-space:pre-wrap; word-break:break-word; line-height:1.6; }}

.event-stacktrace-section {{ margin-top:8px; }}
.event-stacktrace-toggle {{ cursor:pointer; padding:4px 0; color:var(--red); font-weight:600; display:flex; align-items:center; gap:5px; user-select:none; font-size:11px; }}
.event-stacktrace-toggle:hover {{ opacity:.8; }}
.event-stacktrace-icon {{ display:inline-block; transition:transform .2s; font-size:9px; }}
.event-stacktrace-icon.open {{ transform:rotate(90deg); }}
.event-stacktrace-content {{ margin-top:4px; color:#7f1d1d; font-size:11px; line-height:1.5; white-space:pre; font-family:var(--mono); max-height:400px; overflow:auto; background:#fff5f5; padding:10px; border-radius:var(--radius); border:1px solid var(--red-border); }}

.no-results {{ text-align:center; padding:40px; color:var(--text-3); font-size:14px; }}
"""


def _get_index_css() -> str:
    return (
        "* { margin: 0; padding: 0; box-sizing: border-box; }\n"
        "body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Roboto', 'Helvetica Neue', Arial, sans-serif; background: #f0f2f5; padding: 20px; }\n"
        ".header { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 30px; border-radius: 12px; margin-bottom: 20px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); }\n"
        ".header h1 { font-size: 32px; margin-bottom: 10px; font-weight: 600; letter-spacing: -0.5px; }\n"
        ".timestamp { opacity: 0.9; font-size: 14px; }\n"
        ".runs-container { background: white; padding: 25px; border-radius: 12px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }\n"
        ".runs-container h2 { color: #667eea; font-size: 24px; margin-bottom: 20px; }\n"
        ".runs-list { display: flex; flex-direction: column; gap: 12px; }\n"
        ".run-item { border: 1px solid #e5e7eb; border-radius: 8px; padding: 15px; transition: all 0.2s; }\n"
        ".run-item:hover { box-shadow: 0 2px 8px rgba(0,0,0,0.1); border-color: #667eea; }\n"
        ".run-item.current { border-left: 4px solid #10b981; background: #f0fdf4; }\n"
        ".run-header { margin-bottom: 8px; }\n"
        ".run-link { display: flex; align-items: center; gap: 12px; text-decoration: none; color: #333; }\n"
        ".run-link:hover { color: #667eea; }\n"
        ".run-icon { font-size: 20px; }\n"
        ".run-date { font-weight: 600; font-size: 16px; flex: 1; }\n"
        ".try-number { background: #667eea; color: white; padding: 4px 10px; border-radius: 12px; font-size: 12px; font-weight: 600; margin-left: 8px; }\n"
        ".current-badge { background: #10b981; color: white; padding: 4px 12px; border-radius: 12px; font-size: 12px; font-weight: 600; }\n"
        ".run-meta { font-size: 12px; color: #6b7280; }\n"
        ".run-file { font-family: 'SF Mono', 'Monaco', 'Inconsolata', 'Roboto Mono', 'Courier New', monospace; }\n"
        ".no-runs { text-align: center; padding: 40px; color: #6b7280; font-size: 16px; }\n"
    )


# ---------------------------------------------------------------------------
# JavaScript (identical to JUnit reporter's AggregatedReportGenerator.getJavaScript)
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
  btn.innerHTML = '\\u2713'; btn.style.background = '#059669';
  setTimeout(function() { btn.innerHTML = orig; btn.style.background = ''; }, 1500);
}
function copyToClipboard(id) {
  var el = document.getElementById('json-' + id);
  if (el) _copyText(el.getAttribute('data-original') || el.textContent, event.target);
}
function copyCurlCommand(id) {
  var el = document.getElementById('curl-' + id);
  if (el) _copyText(el.getAttribute('data-original') || el.textContent, event.target);
}
function expandAll() {
  document.querySelectorAll('.class-tests').forEach(function(d) {
    d.style.display = 'block';
  });
  document.querySelectorAll('.class-toggle-icon').forEach(function(i) { i.classList.remove('collapsed'); });
  document.querySelectorAll('.test-item:not(.hidden)').forEach(function(item) {
    var idx = item.querySelector('.test-details')?.id.replace('test-','');
    if (idx !== undefined) {
      var d = document.getElementById('test-' + idx);
      var icon = document.getElementById('icon-' + idx);
      if (d) { d.style.display = 'block'; d.classList.add('open'); }
      if (icon) icon.classList.add('open');
    }
  });
}
function collapseAll() {
  document.querySelectorAll('.class-tests').forEach(function(d) {
    d.style.display = 'none';
  });
  document.querySelectorAll('.class-toggle-icon').forEach(function(i) { i.classList.add('collapsed'); });
  document.querySelectorAll('.test-details').forEach(function(d) {
    d.style.display = 'none'; d.classList.remove('open');
  });
  document.querySelectorAll('.toggle-icon').forEach(function(i) { i.classList.remove('open'); });
}
var _LEVEL_ORDER = {TRACE:0, DEBUG:1, INFO:2, WARN:3, ERROR:4};
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
    var events = step.querySelectorAll('.event[data-event-level]');
    var vis = 0;
    events.forEach(function(ev) { if (ev.style.display !== 'none') vis++; });
    badge.textContent = vis;
  });
}
function filterTests() {
  var term = (document.getElementById('searchInput').value || '').toLowerCase();
  var status = document.getElementById('statusFilter').value;
  var items = document.querySelectorAll('.test-item');
  var visible = 0;
  items.forEach(function(item) {
    var name = (item.querySelector('.test-name')?.textContent || '').toLowerCase();
    var method = (item.querySelector('.test-method-name')?.textContent || '').toLowerCase();
    var st = item.dataset.status;
    var matchSearch = !term || name.includes(term) || method.includes(term) || item.textContent.toLowerCase().includes(term);
    var matchStatus = status === 'all' || st === status;
    if (matchSearch && matchStatus) {
      item.classList.remove('hidden'); visible++;
      item.classList.toggle('highlight', !!term && (name.includes(term) || method.includes(term)));
    } else {
      item.classList.add('hidden'); item.classList.remove('highlight');
    }
  });
  var nr = document.getElementById('no-results');
  if (visible === 0 && (term || status !== 'all')) {
    if (!nr) {
      var m = document.createElement('div'); m.id = 'no-results'; m.className = 'no-results';
      m.textContent = 'No tests match your criteria.';
      document.querySelector('.tests-container').appendChild(m);
    }
  } else if (nr) nr.remove();
}"""
