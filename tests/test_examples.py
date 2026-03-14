import os

from custom_python_logger import get_logger

from pytest_reporter_html import step

logger = get_logger(__name__)


def test_user_lifecycle():
    with step("Create user"):
        logger.info("Creating a new user with role 'user'")
        with step("Create sub_user 1"):
            logger.info("Creating a new sub user 1 with role 'user'")
            with step("Create sub_user 2"):
                logger.info("Creating a new sub user 2 with role 'user'")
                with step("Create sub_user 3"):
                    logger.info("Creating a new sub user 3 with role 'user'")
                    with step("Create sub_user 4"):
                        logger.info("Creating a new sub user 4 with role 'user'")

    with step("Update profile"):
        logger.info("Updating user profile to set role to 'admin'")

    with step("Verify changes"):
        logger.info("Verifying that the user's role has been updated to 'admin'")


@step("Fetch user data")
def get_user(user_id: str) -> dict:
    logger.info(f"Fetching user {user_id}")
    return {"id": user_id, "active": True}


@step("Send notification")
async def notify(user_id: str) -> None:
    logger.info(f"Sending notification to user {user_id}")


def test_flow():
    user = get_user("u-1")  # → Step 01: Fetch user data
    assert user["active"] is True


def test_order_checkout():
    with step("Create order"):
        logger.info("Creating order with 3 items")

    with step("Checkout"):
        logger.info("Submitting checkout request")
        if os.getenv("CI") != "true":
            assert False, "Checkout failed — payment declined"  # ← step is marked FAILED


def test_logging_levels():
    logger.info("Running test_simple")

    logger.trace("test_logging")
    logger.debug("This is a debug message")
    logger.info("This is an info message")
    logger.warning("This is a warning")
    logger.error("This is an error")

    assert True
