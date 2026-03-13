"""
Shared helpers for pytest-reporter-html.

All pure utility functions live here so plugin.py, reporter.py and
html_report.py stay focused on their own responsibilities.
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime

import pytest

from .const import TestStatus

# ---------------------------------------------------------------------------
# Pytest helpers (used by plugin.py)
# ---------------------------------------------------------------------------


def _now_millis() -> int:
    """Return the current UTC time in milliseconds."""
    return int(time.time() * 1000)


def _worse(a: str, b: str) -> str:
    """Return whichever status is more severe."""
    return b if TestStatus[b] > TestStatus[a] else a


def _extract_failure(report: pytest.TestReport) -> tuple[str, str]:
    """Extract (failure_message, stack_trace) from a failed TestReport."""
    longrepr = report.longrepr
    msg = str(longrepr.reprcrash.message) if hasattr(longrepr, "reprcrash") else str(longrepr)
    return msg, str(longrepr)


def _module_label(item: pytest.Item) -> str | None:
    """Return a human-readable class/module label for the test item."""
    if item.cls is not None:  # type: ignore[attr-defined]
        return f"{item.module.__name__}.{item.cls.__name__}"  # type: ignore[attr-defined]
    return getattr(item.module, "__name__", None)  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Timestamp helpers (used by html_report.py)
# ---------------------------------------------------------------------------

_TIMESTAMP_FMT = "%Y.%m.%d_%H.%M.%S"


def _format_ts(dt: datetime) -> str:
    """Format a datetime as yyyy.MM.dd_HH.mm.ss.SSS."""
    base = dt.strftime(_TIMESTAMP_FMT)
    millis = f"{dt.microsecond // 1000:03d}"
    return f"{base}.{millis}"


def _format_timestamp_hms(epoch_millis: int) -> str:
    """Format an epoch-millisecond timestamp as HH:MM:SS.mmm."""
    dt = datetime.fromtimestamp(epoch_millis / 1000.0)
    return dt.strftime("%H:%M:%S.") + f"{dt.microsecond // 1000:03d}"


# ---------------------------------------------------------------------------
# HTML helpers (used by html_report.py)
# ---------------------------------------------------------------------------


def _escape_html(text: str | None) -> str:
    """Escape special HTML characters."""
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
    """Convert a snake_case or camelCase test method name to a readable title."""
    if not method_name:
        return method_name
    name = method_name
    if name.startswith("test_"):
        name = name[5:]
    name = name.replace("_", " ")
    name = re.sub(r"([a-z])([A-Z])", r"\1 \2", name)
    return name[0].upper() + name[1:] if name else name


def _format_class_name(class_name: str) -> str:
    """Convert a TestClassName to a readable display name."""
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


# ---------------------------------------------------------------------------
# JSON formatting helpers (used by html_report.py)
# ---------------------------------------------------------------------------


def _try_pretty_json(text: str) -> str | None:
    """Return pretty-printed JSON if text is valid JSON, else None."""
    stripped = text.strip()
    if not (
        (stripped.startswith("{") and stripped.endswith("}")) or (stripped.startswith("[") and stripped.endswith("]"))
    ):
        return None
    try:
        return json.dumps(json.loads(stripped), indent=2, ensure_ascii=False)
    except (json.JSONDecodeError, ValueError):  # pylint: disable=W0714
        return None


def _format_json_for_display(json_str: str) -> str:
    """Apply syntax-highlight spans to a JSON string for HTML display."""
    if not json_str or not json_str.strip():
        return ""
    escaped = _escape_html(json_str)

    counter = 0
    placeholders: dict[str, str] = {}

    def _replace_key(m: re.Match) -> str:
        nonlocal counter
        ph = f"___JSON_KEY_PLACEHOLDER_{counter}___"
        counter += 1
        placeholders[ph] = f"<span class='json-key'>{m.group(1)}</span>"
        return ph

    escaped = re.sub(r'("(?:[^"\\]|\\.)+"\s*:)', _replace_key, escaped)
    escaped = re.sub(r'("(?:[^"\\]|\\.)+\")', r"<span class='json-string'>\1</span>", escaped)
    for ph, replacement in placeholders.items():
        escaped = escaped.replace(ph, replacement)
    escaped = re.sub(
        r'(?<!["\w])(\b\d+\.?\d*\b)(?!["\w])',
        r"<span class='json-number'>\1</span>",
        escaped,
    )
    escaped = re.sub(
        r'(?<!["\w])\b(true|false|null)\b(?!["\w])',
        r"<span class='json-literal'>\1</span>",
        escaped,
    )
    return escaped


def _format_event_with_json(event_text: str) -> str:  # pylint: disable=R1260, R0914, R0912, R0915
    """Detect embedded JSON in event text and wrap it in a json-container block."""
    if not event_text or not event_text.strip():
        return f"<span class='event-text'>{_escape_html(event_text)}</span>"

    trimmed = event_text.strip()
    # Skip GraphQL and function-call-like strings
    if any(trimmed.startswith(kw) for kw in ("mutation", "query", "subscription")):
        return f"<span class='event-text'>{_escape_html(event_text)}</span>"
    if re.match(r"^\w+\s*\(", trimmed) or "[ ]" in event_text:
        return f"<span class='event-text'>{_escape_html(event_text)}</span>"

    # Whole message is JSON
    if (pretty := _try_pretty_json(event_text)) is not None:
        fmt = _format_json_for_display(pretty)
        return (
            "<div class='json-container'>"
            "<div class='json-header'><span class='json-label'>JSON</span></div>"
            f"<pre class='event-json'>{fmt}</pre></div>"
        )

    # Search for embedded JSON fragments
    result: list[str] = []
    i, last_processed, length = 0, 0, len(event_text)

    while i < length:  # pylint: disable=W0149
        if (c := event_text[i]) in {"{", "["}:
            json_start = i
            brace = bracket = 0
            in_str = esc = False
            json_end = -1
            for j in range(i, length):
                ch = event_text[j]
                if esc:
                    esc = False
                    continue
                if ch == "\\":
                    esc = True
                    continue
                if ch == '"':
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
                if (pretty_c := _try_pretty_json(candidate)) is not None:
                    if json_start > last_processed:
                        result.append(
                            f"<span class='event-text'>{_escape_html(event_text[last_processed:json_start])}</span>"
                        )
                    result.append(
                        "<div class='json-container'>"
                        "<div class='json-header'><span class='json-label'>JSON</span></div>"
                        f"<pre class='event-json'>{_format_json_for_display(pretty_c)}</pre></div>"
                    )
                    i = json_end
                    last_processed = json_end
                    continue
        i += 1

    if result:
        if last_processed < length:
            result.append(f"<span class='event-text'>{_escape_html(event_text[last_processed:])}</span>")
        return "".join(result)

    return f"<span class='event-text'>{_escape_html(event_text)}</span>"


def _render_event_with_traceback(event_text: str, uid: str) -> str:
    """Render a log event that contains an embedded Python traceback."""
    tb_marker = "\nTraceback (most recent call last):"
    pos = event_text.index(tb_marker)
    message = event_text[:pos]
    traceback_text = event_text[pos + 1 :]
    return (
        f"<span class='event-text'>{_escape_html(message)}</span>\n"
        "<div class='event-stacktrace-section'>\n"
        f"<div class='event-stacktrace-toggle' onclick='toggleEventStackTrace(\"{uid}\")'>\n"
        f"<span class='event-stacktrace-icon open' id='event-stacktrace-icon-{uid}'>&#9654;</span>\n"
        "<strong>Exception</strong>\n"
        "</div>\n"
        f"<pre class='event-stacktrace-content' id='event-stacktrace-{uid}' style='display: block;'>"
        f"{_escape_html(traceback_text)}</pre>\n"
        "</div>\n"
    )
