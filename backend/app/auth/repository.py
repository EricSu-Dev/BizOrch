"""Persistence operations for users, roles and opaque sessions."""

from datetime import datetime
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import Session, selectinload

from app.auth.models import AuthSession, User, UserRole


class AuthRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create_user(
        self,
        *,
        employee_id: str,
        username: str,
        password_hash: str,
        roles: frozenset[str],
        user_id: str | None = None,
    ) -> User:
        user = User(
            id=user_id or str(uuid4()),
            employee_id=employee_id,
            username=username,
            password_hash=password_hash,
            active=True,
        )
        user.roles.extend(UserRole(role=role) for role in sorted(roles))
        self._session.add(user)
        self._session.flush()
        return user

    def find_user_by_username(self, username: str) -> User | None:
        return self._session.scalar(
            select(User)
            .options(selectinload(User.roles))
            .where(User.username == username)
        )

    def find_user_by_employee_id(self, employee_id: str) -> User | None:
        return self._session.scalar(
            select(User)
            .options(selectinload(User.roles))
            .where(User.employee_id == employee_id)
        )

    def update_profile(
        self,
        user: User,
        *,
        username: str,
        avatar_key: str,
    ) -> User:
        user.username = username
        user.avatar_key = avatar_key
        self._session.flush()
        return user

    def update_password(self, user: User, *, password_hash: str) -> None:
        user.password_hash = password_hash
        self._session.flush()

    def update_login_credentials(
        self,
        user: User,
        *,
        username: str,
        password_hash: str,
    ) -> None:
        user.username = username
        user.password_hash = password_hash
        self._session.flush()

    def update_avatar_object(
        self,
        user: User,
        *,
        object_key: str,
        version: str,
    ) -> User:
        user.avatar_object_key = object_key
        # The previous BLOB fields are retained temporarily only to serve users
        # who uploaded before the OSS migration.
        user.avatar_content = None
        user.avatar_content_type = None
        user.avatar_version = version
        self._session.flush()
        return user

    def clear_avatar(self, user: User) -> User:
        user.avatar_object_key = None
        user.avatar_content = None
        user.avatar_content_type = None
        user.avatar_version = None
        self._session.flush()
        return user

    def create_session(
        self,
        *,
        user_id: str,
        token_hash: str,
        expires_at: datetime,
        session_id: str | None = None,
    ) -> AuthSession:
        record = AuthSession(
            id=session_id or str(uuid4()),
            user_id=user_id,
            token_hash=token_hash,
            expires_at=expires_at,
        )
        self._session.add(record)
        self._session.flush()
        return record

    def find_active_session(
        self,
        token_hash: str,
        *,
        now: datetime,
    ) -> AuthSession | None:
        return self._session.scalar(
            select(AuthSession)
            .options(selectinload(AuthSession.user).selectinload(User.roles))
            .where(
                AuthSession.token_hash == token_hash,
                AuthSession.revoked_at.is_(None),
                AuthSession.expires_at > now,
                AuthSession.user.has(User.active.is_(True)),
            )
        )

    def revoke_session(self, token_hash: str, *, revoked_at: datetime) -> bool:
        result = self._session.execute(
            update(AuthSession)
            .where(
                AuthSession.token_hash == token_hash,
                AuthSession.revoked_at.is_(None),
            )
            .values(revoked_at=revoked_at)
        )
        return result.rowcount == 1

    def revoke_other_sessions(
        self,
        *,
        user_id: str,
        current_token_hash: str,
        revoked_at: datetime,
    ) -> int:
        result = self._session.execute(
            update(AuthSession)
            .where(
                AuthSession.user_id == user_id,
                AuthSession.token_hash != current_token_hash,
                AuthSession.revoked_at.is_(None),
            )
            .values(revoked_at=revoked_at)
        )
        return result.rowcount

    def revoke_all_sessions(
        self,
        *,
        user_id: str,
        revoked_at: datetime,
    ) -> int:
        result = self._session.execute(
            update(AuthSession)
            .where(
                AuthSession.user_id == user_id,
                AuthSession.revoked_at.is_(None),
            )
            .values(revoked_at=revoked_at)
        )
        return result.rowcount
