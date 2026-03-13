from custom_python_logger import get_logger

logger = get_logger(__name__)


def test_simple():
    logger.debug("This is a debug message")
    logger.info("This is an info message")
    logger.warning("This is a warning")
    logger.error("This is an error")

    assert True


def test_failed():
    logger.info("Running test_failed")
    assert False, "This test is designed to fail"
