![PyPI version](https://img.shields.io/pypi/v/pytest-reporter-html)
![Python](https://img.shields.io/badge/python->=3.11-blue)
![Development Status](https://img.shields.io/badge/status-stable-green)
![Maintenance](https://img.shields.io/maintenance/yes/2026)
![PyPI](https://img.shields.io/pypi/dm/pytest-reporter-html)
![License](https://img.shields.io/pypi/l/pytest-reporter-html)

---

# 💡 pytest-reporter-html

A pytest plugin that automatically generates rich, interactive HTML test reports with **zero-config log capture**, **named step tracking**, **exception rendering**, and **real-time filtering** — open the file in any browser and start debugging.

---

## 📦 Installation

```bash
uv add pytest-reporter-html
```

---

## 🚀 Features

- ✅ **Zero-Config Log Capture** — attaches to Python's root logger automatically; every `logging.*()` call is captured as a report event without any code changes
- ✅ **Named Steps** — group events into collapsible, timed steps using the `step` context manager or decorator (sync and async)
- ✅ **Automatic Phase Steps** — Setup, test body, and Teardown phases are created automatically for every test
- ✅ **Interactive HTML Report** — real-time search, status filter (Passed/Failed), log-level filter (TRACE→ERROR), expand/collapse all, progress bar
- ✅ **Exception Rendering** — full tracebacks captured and rendered as collapsible blocks; failed tests auto-expand
- ✅ **JSON + HTTP Visualisation** — embedded JSON is syntax-highlighted; HTTP requests are shown with a generated cURL command and copy button
- ✅ **Per-Test JSON Files** — one structured JSON file per test; usable by other tools independently of the HTML
- ✅ **`report_test_name` Fixture** — override the displayed test name at runtime (useful for parameterised tests)
- ✅ **Async Support** — `step` works as both `async with` and an `async def` decorator

---

## ⚙️ Configuration

Enable the HTML report by adding `--report-html` to your pytest options:

```ini
# pytest.ini
[pytest]
addopts =
    --report-html
    --output-dir=logs
```

Or pass it directly on the command line:

```bash
pytest --report-html
```

### Output directory

The default output directory is `logs/test-reports`. Override it with `--output-dir`:

```bash
pytest --report-html --output-dir=build/reports
```

---

## 🛠️ How to Use

1. **Install** the plugin: `uv add pytest-reporter-html`
2. **Enable** HTML report generation by adding `--report-html` to your pytest options
3. **Run** your tests normally with `pytest`
4. **Open** `logs/test-reports/TestReport_Latest.html` in any browser
5. *(Optional)* Use `step` to group log events into named, collapsible blocks

---

## 🚀 Quick Start

```toml
# pyproject.toml
[tool.pytest.ini_options]
addopts = "--report-html"
```

```bash
pytest
```

After the run, open the report:

```
logs/test-reports/TestReport_Latest.html
```

---

## ▶️ Usage Examples

### Example 1: Named steps with logging

```python
from custom_python_logger import get_logger
from pytest_reporter_html import step

logger = get_logger(__name__)

def test_user_lifecycle():
    with step("Create user"):
        logger.info("Creating a new user with role 'user'")

    with step("Update profile"):
        logger.info("Updating user profile to set role to 'admin'")

    with step("Verify changes"):
        logger.info("Verifying that the user's role has been updated to 'admin'")
```

Report output:

```
Test: test_user_lifecycle                                            PASSED
 ├── Step 01: Create user                               PASSED    120ms
 ├── Step 02: Update profile                            PASSED     45ms
 └── Step 03: Verify changes                            PASSED     30ms
```

---

### Example 2: `step` as a decorator

```python
from custom_python_logger import get_logger
from pytest_reporter_html import step

logger = get_logger(__name__)

@step("Fetch user data")
def get_user(user_id: str) -> dict:
    logger.info(f"Fetching user {user_id}")
    return {"id": user_id, "active": True}

@step("Send notification")
async def notify(user_id: str) -> None:
    logger.info(f"Sending notification to user {user_id}")

def test_flow():
    user = get_user("u-1")   # → Step 01: Fetch user data
    assert user["active"] is True
```

---

### Example 3: Failure output

When a test fails it auto-expands in the report, showing the failure message, stack trace, and all log events up to the point of failure:

```python
from custom_python_logger import get_logger
from pytest_reporter_html import step

logger = get_logger(__name__)

def test_order_checkout():
    with step("Create order"):
        logger.info("Creating order with 3 items")

    with step("Checkout"):
        logger.info("Submitting checkout request")
        assert False, "Checkout failed — payment declined"  # ← step is marked FAILED
```

---

## 🤝 Contributing

If you have a helpful pattern or improvement to suggest:

1. Fork the repo
2. Create a new branch
3. Submit a pull request

Contributions that improve report quality, add new rendering formats, or extend CI integrations are welcome.

---

## 📄 License

MIT License — see [LICENSE](LICENSE) for details.

---

## 🙏 Thanks

Thanks for exploring this repository! <br>
Happy coding!

[![GitHub](https://img.shields.io/badge/GitHub-YevgenyFarber-181717?logo=github)](https://github.com/YevgenyFarber)
&nbsp; [![PyPI](https://img.shields.io/badge/PyPI-yfarber-3775A9?logo=pypi)](https://pypi.org/user/yfarber/)
&nbsp; [![LinkedIn](https://img.shields.io/badge/LinkedIn-yevgeny--farber-0A66C2?logo=linkedin)](https://www.linkedin.com/in/yevgeny-farber-34146a64/)

[![GitHub](https://img.shields.io/badge/GitHub-aviz92-181717?logo=github)](https://github.com/aviz92)
&nbsp; [![PyPI](https://img.shields.io/badge/PyPI-aviz-3775A9?logo=pypi)](https://pypi.org/user/aviz/)
&nbsp; [![Blog](https://img.shields.io/badge/Blog-aviz92.github.io-0066CC?logo=googlechrome)](https://aviz92.github.io/)
&nbsp; [![LinkedIn](https://img.shields.io/badge/LinkedIn-avi--zaguri-0A66C2?logo=linkedin)](https://www.linkedin.com/in/avi-zaguri-41869b11b)
