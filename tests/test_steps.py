from custom_python_logger import get_logger

from pytest_reporter_html import step

logger = get_logger(__name__)


class TestUserLifecycle:
    def test_user_lifecycle(self):
        with step("Create user"):
            logger.debug("Creating a new user with role 'user'")

        with step("Update profile"):
            logger.info("Updating user profile to set role to 'admin'")

        with step("Verify changes"):
            logger.warning("Verifying that the user's role has been updated to 'admin'")

        with step("Final verification"):
            logger.error("Final verification step failed due to unexpected error")
