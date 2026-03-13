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
