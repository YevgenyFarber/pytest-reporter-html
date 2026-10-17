from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest

from pytest_reporter_html.reporter import TestReporter

pytest_plugins = ["pytester"]


@pytest.fixture
def reporter(tmp_path: Path) -> TestReporter:
    return TestReporter(
        test_name="test_example",
        class_name="tests.test_module",
        output_dir=str(tmp_path),
    )


@pytest.fixture
def fixed_timestamp() -> Iterator[None]:
    """Pin the timestamp to a known value for deterministic file names."""
    with patch.dict("os.environ", {"REPORT_TIMESTAMP": "1000000000000"}):
        yield
