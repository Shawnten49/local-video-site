from fastapi.testclient import TestClient

from local_video_site.identity import accounts
from local_video_site import database
from local_video_site.app import app
from local_video_site.identity.permissions.account import ROLE_ADMIN, ROLE_USER


TEST_PASSWORD_HASH = "test-session-only"


def account_client(
    role: str = ROLE_ADMIN,
    username: str = "",
    client=("testclient", 50000),
):
    try:
        database.user_count()
    except Exception:
        database.init_db()
    username = username or ("test-admin" if role == ROLE_ADMIN else "test-user")
    normalized = username.casefold()
    user = database.get_user_by_normalized_username(normalized)
    if user is None:
        user_id = database.create_user(
            username=username,
            normalized_username=normalized,
            password_hash=TEST_PASSWORD_HASH,
            role=role,
        )
        user = database.get_user(user_id)
    _, token, _ = accounts.create_session(
        int(user["id"]),
        remember=False,
        client_ip="testclient",
        user_agent="test-suite",
    )
    test_client = TestClient(app, client=client)
    test_client.cookies.set(accounts.SESSION_COOKIE_NAME, token)
    return test_client


def admin_client(client=("testclient", 50000)):
    return account_client(ROLE_ADMIN, "test-admin", client=client)


def user_client(username: str = "test-user", client=("testclient", 50000)):
    return account_client(ROLE_USER, username, client=client)
