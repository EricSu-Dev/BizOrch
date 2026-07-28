from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select, update
from sqlalchemy.orm import Session, sessionmaker

from app.auth.contracts import RoleName
from app.auth.models import AuthSession, User
from app.auth.passwords import PasswordHasher
from app.auth.service import (
    AuthService,
    InvalidAccessTokenError,
    InvalidCredentialsError,
    UserAlreadyExistsError,
)
from app.persistence.base import Base


@pytest.fixture
def sessions(tmp_path) -> sessionmaker[Session]:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'auth.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture
def auth(sessions) -> AuthService:
    return AuthService(
        sessions,
        password_hasher=PasswordHasher(n=2**10),
        session_ttl=timedelta(hours=2),
    )


def create_user(auth: AuthService) -> None:
    auth.create_user(
        employee_id="EMP-1001",
        username="lin.employee",
        password="correct-password",
        roles=frozenset({RoleName.EMPLOYEE, RoleName.APPROVER}),
    )


def test_login_stores_only_hash_and_authenticates_roles(
    auth: AuthService,
    sessions: sessionmaker[Session],
) -> None:
    create_user(auth)

    result = auth.login(username="LIN.EMPLOYEE", password="correct-password")

    assert result.access_token
    assert result.expires_in == 7200
    assert result.user.employee_id == "EMP-1001"
    assert result.user.roles == frozenset(
        {RoleName.EMPLOYEE, RoleName.APPROVER}
    )
    principal = auth.authenticate(result.access_token)
    assert principal == result.user
    with sessions() as session:
        user = session.scalar(select(User))
        token = session.scalar(select(AuthSession))
        assert user is not None
        assert "correct-password" not in user.password_hash
        assert token is not None
        assert token.token_hash != result.access_token
        assert len(token.token_hash) == 64


@pytest.mark.parametrize(
    ("username", "password"),
    [
        ("missing-user", "correct-password"),
        ("lin.employee", "wrong-password"),
    ],
)
def test_unknown_user_and_wrong_password_share_one_error(
    auth: AuthService,
    username: str,
    password: str,
) -> None:
    create_user(auth)

    with pytest.raises(InvalidCredentialsError, match="invalid username or password"):
        auth.login(username=username, password=password)


def test_logout_revokes_token(auth: AuthService) -> None:
    create_user(auth)
    token = auth.login(
        username="lin.employee", password="correct-password"
    ).access_token

    auth.logout(token)

    with pytest.raises(InvalidAccessTokenError):
        auth.authenticate(token)
    auth.logout(token)


def test_expired_session_is_rejected(
    auth: AuthService,
    sessions: sessionmaker[Session],
) -> None:
    create_user(auth)
    token = auth.login(
        username="lin.employee", password="correct-password"
    ).access_token
    with sessions.begin() as session:
        session.execute(
            update(AuthSession).values(
                expires_at=datetime.now(UTC) - timedelta(seconds=1)
            )
        )

    with pytest.raises(InvalidAccessTokenError):
        auth.authenticate(token)


def test_inactive_user_cannot_login_or_reuse_existing_session(
    auth: AuthService,
    sessions: sessionmaker[Session],
) -> None:
    create_user(auth)
    token = auth.login(
        username="lin.employee", password="correct-password"
    ).access_token
    with sessions.begin() as session:
        session.execute(update(User).values(active=False))

    with pytest.raises(InvalidAccessTokenError):
        auth.authenticate(token)
    with pytest.raises(InvalidCredentialsError):
        auth.login(username="lin.employee", password="correct-password")


def test_duplicate_identity_is_rejected(auth: AuthService) -> None:
    create_user(auth)

    with pytest.raises(UserAlreadyExistsError):
        auth.create_user(
            employee_id="EMP-1002",
            username="lin.employee",
            password="another-password",
        )
