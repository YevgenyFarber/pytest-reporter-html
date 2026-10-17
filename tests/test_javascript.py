"""Tests for the search/filter JavaScript embedded in the HTML report (needs ``node``)."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from pytest_reporter_html.html_report import _get_javascript

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")

_PRELUDE = """
const src = %s;
const start = src.indexOf('function globToRegex');
const end = src.indexOf('function filterTests');
eval(src.slice(start, end) + ';globalThis.matches = (text, pat) => matchesQuery(text, globToRegex(pat));');
const levels = eval('(' + src.match(/var _LEVEL_ORDER = (\\{[^}]*\\})/)[1] + ')');
"""


def _node(script: str, tmp_path: Path) -> object:
    file = tmp_path / "check.js"
    file.write_text(_PRELUDE % json.dumps(_get_javascript()) + script, encoding="utf-8")
    out = subprocess.run(["node", str(file)], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def test_javascript_is_syntactically_valid(tmp_path: Path) -> None:
    file = tmp_path / "page.js"
    file.write_text(_get_javascript(), encoding="utf-8")
    subprocess.run(["node", "--check", str(file)], check=True)


@pytest.mark.parametrize(
    ("text", "pattern", "expected"),
    [
        ("Multi test_multi", "multi", True),
        ("Multi test_multi", "test_*", True),  # wildcard must not be anchored to the display name
        ("Multi test_multi", "mul?i", True),
        ("Multi test_multi", "zzz", False),
        ("Checkout test_checkout", "order", False),  # "or" prefix must not be parsed as an operator
        ("Organization test_org", "organization", True),
        ("Order test_order", "order", True),
        ("Login test_login", "login OR checkout", True),
        ("Checkout test_checkout", "login OR checkout", True),
        ("Logout test_logout", "login OR checkout", False),
        ("Login test_login", "login checkout", False),  # space = AND
        ("Fail In Step", '"fail in"', True),
        ("Fail Step", '"fail in"', False),
        ("a*b", '"a*b"', True),  # quoted phrases are literal
        ("axxb", '"a*b"', False),
    ],
)
def test_search_matching(text: str, pattern: str, expected: bool, tmp_path: Path) -> None:
    got = _node(f"console.log(JSON.stringify(matches({json.dumps(text)}, {json.dumps(pattern)})));", tmp_path)
    assert got is expected, f"{pattern!r} vs {text!r}"


def test_log_level_order_covers_standard_levels(tmp_path: Path) -> None:
    levels = _node("console.log(JSON.stringify(levels));", tmp_path)
    assert isinstance(levels, dict)
    for name in ("TRACE", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
        assert name in levels, f"{name} missing from _LEVEL_ORDER"
    assert levels["CRITICAL"] > levels["ERROR"] > levels["WARNING"] > levels["INFO"] > levels["DEBUG"] > levels["TRACE"]
