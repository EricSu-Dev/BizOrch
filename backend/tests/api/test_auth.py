from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.auth.contracts import RoleName
from app.auth.passwords import PasswordHasher
from app.auth.service import AuthService, InsufficientRoleError
from app.api.dependencies import CurrentActor, require_hr_operator
from app.core.config import Settings
from app.main import create_app
from app.persistence.base import Base


class AuthRuntimeHarness:
    def __init__(self, auth: AuthService) -> None:
        self.auth = auth
        self.closed = False

    def close(self) -> None:
        self.closed = True


class MemoryAvatarStorage:
    """Provider-free adapter used to verify the OSS-facing auth contract."""

    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}

    def upload(self, *, object_key: str, content: bytes, content_type: str) -> None:
        self.objects[object_key] = (content, content_type)

    def delete(self, *, object_key: str) -> None:
        self.objects.pop(object_key, None)

    def public_url(self, *, object_key: str) -> str:
        return f"https://avatars.test/{object_key}"


def build_app(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'auth-api.db'}")
    Base.metadata.create_all(engine)
    sessions: sessionmaker[Session] = sessionmaker(
        bind=engine,
        expire_on_commit=False,
    )
    avatar_storage = MemoryAvatarStorage()
    auth = AuthService(
        sessions,
        password_hasher=PasswordHasher(n=2**10),
        session_ttl=timedelta(hours=1),
        avatar_storage=avatar_storage,
    )
    auth.create_user(
        employee_id="EMP-1001",
        username="lin.employee",
        password="correct-password",
        roles=frozenset({RoleName.EMPLOYEE, RoleName.APPROVER}),
    )
    runtime = AuthRuntimeHarness(auth)
    application = create_app(
        settings=Settings(database_url="configured-for-test"),
        runtime_factory=lambda settings: runtime,
    )
    return application, runtime, avatar_storage


@pytest.mark.parametrize("role", ["hr", "admin"])
def test_hr_operator_gate_allows_only_hr_or_admin(role: str) -> None:
    actor = CurrentActor(user_id=f"EMP-{role.upper()}", roles=frozenset({role}))

    assert require_hr_operator(actor) is actor


@pytest.mark.parametrize("role", ["employee", "approver", "operator"])
def test_hr_operator_gate_rejects_unrelated_roles(role: str) -> None:
    actor = CurrentActor(user_id=f"EMP-{role.upper()}", roles=frozenset({role}))

    with pytest.raises(InsufficientRoleError):
        require_hr_operator(actor)


def test_login_me_and_logout_full_token_lifecycle(tmp_path) -> None:
    application, runtime, _ = build_app(tmp_path)

    with TestClient(application) as client:
        login = client.post(
            "/api/v1/auth/login",
            json={
                "username": "lin.employee",
                "password": "correct-password",
            },
        )
        assert login.status_code == 200
        body = login.json()
        assert body["token_type"] == "bearer"
        assert set(body["user"]["roles"]) == {"employee", "approver"}
        headers = {"Authorization": f"Bearer {body['access_token']}"}

        me = client.get("/api/v1/auth/me", headers=headers)
        assert me.status_code == 200
        assert me.json()["employee_id"] == "EMP-1001"

        logout = client.post("/api/v1/auth/logout", headers=headers)
        assert logout.status_code == 204
        assert logout.content == b""

        rejected = client.get("/api/v1/auth/me", headers=headers)
        assert rejected.status_code == 401
        assert rejected.json()["error"]["code"] == "AUTHENTICATION_FAILED"

    assert runtime.closed


def test_invalid_login_does_not_reveal_whether_username_exists(tmp_path) -> None:
    application, _, _ = build_app(tmp_path)

    with TestClient(application) as client:
        missing = client.post(
            "/api/v1/auth/login",
            json={"username": "missing", "password": "correct-password"},
        )
        wrong = client.post(
            "/api/v1/auth/login",
            json={"username": "lin.employee", "password": "wrong-password"},
        )

        assert missing.status_code == wrong.status_code == 401
        assert missing.json() == wrong.json()


def test_me_requires_bearer_token(tmp_path) -> None:
    application, _, _ = build_app(tmp_path)

    with TestClient(application) as client:
        response = client.get("/api/v1/auth/me")

        assert response.status_code == 401
        assert response.json()["error"]["code"] == "AUTHENTICATION_FAILED"


def test_current_user_can_update_login_name_and_avatar(tmp_path) -> None:
    application, _, _ = build_app(tmp_path)

    with TestClient(application) as client:
        login = client.post(
            "/api/v1/auth/login",
            json={
                "username": "lin.employee",
                "password": "correct-password",
            },
        )
        headers = {
            "Authorization": f"Bearer {login.json()['access_token']}",
        }

        updated = client.patch(
            "/api/v1/auth/me",
            headers=headers,
            json={
                "username": "lin.updated",
                "avatar_key": "operations",
            },
        )

        assert updated.status_code == 200
        assert updated.json()["username"] == "lin.updated"
        assert updated.json()["avatar_key"] == "operations"
        assert updated.json()["employee_id"] == "EMP-1001"
        assert set(updated.json()["roles"]) == {"employee", "approver"}

        me = client.get("/api/v1/auth/me", headers=headers)
        assert me.status_code == 200
        assert me.json()["username"] == "lin.updated"

        old_login = client.post(
            "/api/v1/auth/login",
            json={
                "username": "lin.employee",
                "password": "correct-password",
            },
        )
        new_login = client.post(
            "/api/v1/auth/login",
            json={
                "username": "lin.updated",
                "password": "correct-password",
            },
        )
        assert old_login.status_code == 401
        assert new_login.status_code == 200


def test_password_change_keeps_current_session_and_revokes_other_sessions(
    tmp_path,
) -> None:
    application, _, _ = build_app(tmp_path)

    with TestClient(application) as client:
        first = client.post(
            "/api/v1/auth/login",
            json={
                "username": "lin.employee",
                "password": "correct-password",
            },
        ).json()
        second = client.post(
            "/api/v1/auth/login",
            json={
                "username": "lin.employee",
                "password": "correct-password",
            },
        ).json()
        first_headers = {
            "Authorization": f"Bearer {first['access_token']}",
        }
        second_headers = {
            "Authorization": f"Bearer {second['access_token']}",
        }

        wrong = client.post(
            "/api/v1/auth/me/password",
            headers=first_headers,
            json={
                "current_password": "incorrect-password",
                "new_password": "new-secure-password",
            },
        )
        assert wrong.status_code == 400
        assert wrong.json()["error"]["code"] == "CURRENT_PASSWORD_INVALID"

        changed = client.post(
            "/api/v1/auth/me/password",
            headers=first_headers,
            json={
                "current_password": "correct-password",
                "new_password": "new-secure-password",
            },
        )
        assert changed.status_code == 204

        assert client.get("/api/v1/auth/me", headers=first_headers).status_code == 200
        rejected_other_session = client.get(
            "/api/v1/auth/me",
            headers=second_headers,
        )
        assert rejected_other_session.status_code == 401

        old_password = client.post(
            "/api/v1/auth/login",
            json={
                "username": "lin.employee",
                "password": "correct-password",
            },
        )
        new_password = client.post(
            "/api/v1/auth/login",
            json={
                "username": "lin.employee",
                "password": "new-secure-password",
            },
        )
        assert old_password.status_code == 401
        assert new_password.status_code == 200


def test_current_user_can_upload_read_and_remove_custom_avatar(tmp_path) -> None:
    application, _, _ = build_app(tmp_path)
    avatar = b"\x89PNG\r\n\x1a\n" + b"safe-avatar-content"

    with TestClient(application) as client:
        login = client.post(
            "/api/v1/auth/login",
            json={
                "username": "lin.employee",
                "password": "correct-password",
            },
        ).json()
        headers = {
            "Authorization": f"Bearer {login['access_token']}",
        }

        uploaded = client.post(
            "/api/v1/auth/me/avatar",
            headers=headers,
            files={"file": ("avatar.png", avatar, "image/png")},
        )
        assert uploaded.status_code == 200
        assert uploaded.json()["has_custom_avatar"] is True
        assert len(uploaded.json()["avatar_version"]) == 64
        assert uploaded.json()["avatar_url"].startswith("https://avatars.test/")

        fetched = client.get(
            "/api/v1/auth/me/avatar",
            headers=headers,
            follow_redirects=False,
        )
        assert fetched.status_code == 307
        assert fetched.headers["location"] == uploaded.json()["avatar_url"]
        assert fetched.headers["x-content-type-options"] == "nosniff"

        removed = client.delete("/api/v1/auth/me/avatar", headers=headers)
        assert removed.status_code == 200
        assert removed.json()["has_custom_avatar"] is False
        assert removed.json()["avatar_version"] is None

        missing = client.get("/api/v1/auth/me/avatar", headers=headers)
        assert missing.status_code == 404
        assert missing.json()["error"]["code"] == "AVATAR_NOT_FOUND"


def test_avatar_upload_rejects_unsupported_or_oversized_content(tmp_path) -> None:
    application, _, _ = build_app(tmp_path)

    with TestClient(application) as client:
        login = client.post(
            "/api/v1/auth/login",
            json={
                "username": "lin.employee",
                "password": "correct-password",
            },
        ).json()
        headers = {
            "Authorization": f"Bearer {login['access_token']}",
        }

        disguised_text = client.post(
            "/api/v1/auth/me/avatar",
            headers=headers,
            files={"file": ("avatar.png", b"not-an-image", "image/png")},
        )
        assert disguised_text.status_code == 415
        assert disguised_text.json()["error"]["code"] == "AVATAR_FILE_UNSUPPORTED"

        oversized = client.post(
            "/api/v1/auth/me/avatar",
            headers=headers,
            files={
                "file": (
                    "large.png",
                    b"\x89PNG\r\n\x1a\n"
                    + b"x" * AuthService.MAX_AVATAR_BYTES,
                    "image/png",
                )
            },
        )
        assert oversized.status_code == 413
        assert oversized.json()["error"]["code"] == "AVATAR_FILE_TOO_LARGE"
