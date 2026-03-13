"""
pytest-reporter-html — Pytest plugin for JSON + HTML test reports.

Public API::

    from pytest_reporter_html import step

    with step("Login"):
        ...

    @step("Create user")
    async def create_user(client):
        ...
"""
from .reporter import JsonReport, ReportEvent, ReportStep
from .reporter import step

__all__ = [
    "step",
    "JsonReport",
    "ReportStep",
    "ReportEvent",
]
