"""Password login and opaque Bearer-token lifecycle."""

from datetime import UTC, datetime, timedelta
import hashlib
import secrets
from uuid import uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.auth.contracts import AvatarKey, AuthPrincipal, LoginResult, RoleName
from app.auth.passwords import PasswordHasher
from app.auth.repository import AuthRepository
from app.auth.avatar_storage import (
    AvatarObjectStorage,
    AvatarStorageConfigurationError,
    AvatarStorageOperationError,
    UnavailableAvatarObjectStorage,
)


class InvalidCredentialsError(PermissionError):
    """Raised for both unknown usernames and incorrect passwords."""


class InvalidAccessTokenError(PermissionError):
    """Raised when a Bearer token is missing, expired, revoked or unknown."""


class UserAlreadyExistsError(RuntimeError):
    """Raised when username or employee identity is already registered."""


class InsufficientRoleError(PermissionError):
    """Raised when an authenticated principal lacks a required role."""


class CurrentPasswordInvalidError(PermissionError):
    """Raised when a password change does not prove the current password."""


class PasswordReuseError(RuntimeError):
    """Raised when the requested new password matches the current password."""


class AvatarNotFoundError(LookupError):
    """Raised when the current user has no uploaded avatar."""


class AvatarFileTooLargeError(ValueError):
    """Raised when an uploaded avatar exceeds the controlled size limit."""


class UnsupportedAvatarFileError(ValueError):
    """Raised when an avatar is not a supported, signature-matched image."""


class AuthService:
    """Authenticate users without storing plaintext passwords or access tokens."""

    MAX_AVATAR_BYTES = 1024 * 1024
    _AVATAR_SIGNATURES = {
        "image/png": lambda content: content.startswith(b"\x89PNG\r\n\x1a\n"),
        "image/jpeg": lambda content: content.startswith(b"\xff\xd8\xff"),
        "image/webp": lambda content: (
            len(content) >= 12
            and content.startswith(b"RIFF")
            and content[8:12] == b"WEBP"
        ),
    }

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        password_hasher: PasswordHasher | None = None,
        session_ttl: timedelta = timedelta(hours=8),
        avatar_storage: AvatarObjectStorage | None = None,
        avatar_prefix: str = "bizorch/avatars",
    ) -> None:
        self._session_factory = session_factory
        self._password_hasher = password_hasher or PasswordHasher()
        if session_ttl <= timedelta(0):
            raise ValueError("session_ttl must be positive")
        self._session_ttl = session_ttl
        self._avatar_storage = avatar_storage or UnavailableAvatarObjectStorage()
        self._avatar_prefix = self._normalize_avatar_prefix(avatar_prefix)
        self._dummy_hash = self._password_hasher.hash("invalid-password-value")

    def create_user(
        self,
        *,
        employee_id: str,
        username: str,
        password: str,
        roles: frozenset[RoleName] = frozenset({RoleName.EMPLOYEE}),
    ) -> AuthPrincipal:
        normalized_employee = employee_id.strip()
        normalized_username = username.strip().lower()
        if not normalized_employee or not normalized_username:
            raise ValueError("employee_id and username must not be blank")
        if not roles:
            raise ValueError("at least one role is required")
        password_hash = self._password_hasher.hash(password)
        try:
            with self._session_factory.begin() as session:
                user = AuthRepository(session).create_user(
                    employee_id=normalized_employee,
                    username=normalized_username,
                    password_hash=password_hash,
                    roles=frozenset(role.value for role in roles),
                )
                return self._principal(user)
        except IntegrityError as exc:
            raise UserAlreadyExistsError(normalized_username) from exc

    def ensure_user(
        self,
        *,
        employee_id: str,
        username: str,
        password: str,
        roles: frozenset[RoleName],
        reset_existing_credentials: bool = False,
    ) -> AuthPrincipal:
        """Create one seed user, or reconcile an existing demo identity."""
        normalized_employee = employee_id.strip()
        normalized_username = username.strip().lower()
        with self._session_factory.begin() as session:
            repository = AuthRepository(session)
            existing = repository.find_user_by_employee_id(normalized_employee)
            if existing is not None:
                principal = self._principal(existing)
                if principal.roles != roles:
                    raise UserAlreadyExistsError(normalized_username)
                if reset_existing_credentials:
                    username_owner = repository.find_user_by_username(
                        normalized_username
                    )
                    if (
                        username_owner is not None
                        and username_owner.id != existing.id
                    ):
                        raise UserAlreadyExistsError(normalized_username)
                    repository.update_login_credentials(
                        existing,
                        username=normalized_username,
                        password_hash=self._password_hasher.hash(password),
                    )
                    repository.revoke_all_sessions(
                        user_id=existing.id,
                        revoked_at=datetime.now(UTC),
                    )
                    return self._principal(existing)
                return principal
            username_owner = repository.find_user_by_username(normalized_username)
            if username_owner is not None:
                raise UserAlreadyExistsError(normalized_username)
        return self.create_user(
            employee_id=normalized_employee,
            username=normalized_username,
            password=password,
            roles=roles,
        )

    def login(self, *, username: str, password: str) -> LoginResult:
        normalized_username = username.strip().lower()
        with self._session_factory.begin() as session:
            repository = AuthRepository(session)
            user = repository.find_user_by_username(normalized_username)
            encoded_hash = user.password_hash if user is not None else self._dummy_hash
            password_valid = self._password_hasher.verify(password, encoded_hash)
            if user is None or not user.active or not password_valid:
                raise InvalidCredentialsError("invalid username or password")

            raw_token = secrets.token_urlsafe(32)
            now = datetime.now(UTC)
            expires_at = now + self._session_ttl
            repository.create_session(
                user_id=user.id,
                token_hash=self._token_hash(raw_token),
                expires_at=expires_at,
            )
            return LoginResult(
                access_token=raw_token,
                expires_in=int(self._session_ttl.total_seconds()),
                user=self._principal(user),
            )

    def authenticate(self, token: str) -> AuthPrincipal:
        if len(token) < 32 or len(token) > 512:
            raise InvalidAccessTokenError("invalid access token")
        with self._session_factory() as session:
            record = AuthRepository(session).find_active_session(
                self._token_hash(token),
                now=datetime.now(UTC),
            )
            if record is None:
                raise InvalidAccessTokenError("invalid access token")
            return self._principal(record.user)

    def get_profile(self, employee_id: str) -> AuthPrincipal:
        with self._session_factory() as session:
            user = AuthRepository(session).find_user_by_employee_id(employee_id)
            if user is None or not user.active:
                raise InvalidAccessTokenError("invalid user identity")
            return self._principal(user)

    def update_profile(
        self,
        *,
        employee_id: str,
        username: str,
        avatar_key: AvatarKey,
    ) -> AuthPrincipal:
        normalized_username = username.strip().lower()
        if not normalized_username:
            raise ValueError("username must not be blank")
        try:
            with self._session_factory.begin() as session:
                repository = AuthRepository(session)
                user = repository.find_user_by_employee_id(employee_id)
                if user is None or not user.active:
                    raise InvalidAccessTokenError("invalid user identity")
                repository.update_profile(
                    user,
                    username=normalized_username,
                    avatar_key=avatar_key.value,
                )
                return self._principal(user)
        except IntegrityError as exc:
            raise UserAlreadyExistsError(normalized_username) from exc

    def change_password(
        self,
        *,
        employee_id: str,
        current_password: str,
        new_password: str,
        current_token: str,
    ) -> None:
        with self._session_factory.begin() as session:
            repository = AuthRepository(session)
            user = repository.find_user_by_employee_id(employee_id)
            if user is None or not user.active:
                raise InvalidAccessTokenError("invalid user identity")
            if not self._password_hasher.verify(
                current_password,
                user.password_hash,
            ):
                raise CurrentPasswordInvalidError("current password is invalid")
            if self._password_hasher.verify(new_password, user.password_hash):
                raise PasswordReuseError("new password must be different")
            repository.update_password(
                user,
                password_hash=self._password_hasher.hash(new_password),
            )
            repository.revoke_other_sessions(
                user_id=user.id,
                current_token_hash=self._token_hash(current_token),
                revoked_at=datetime.now(UTC),
            )

    def update_avatar(
        self,
        *,
        employee_id: str,
        content: bytes,
        content_type: str | None,
    ) -> AuthPrincipal:
        normalized_type = (content_type or "").split(";", maxsplit=1)[0].lower()
        if len(content) > self.MAX_AVATAR_BYTES:
            raise AvatarFileTooLargeError("avatar exceeds the configured limit")
        signature_matches = self._AVATAR_SIGNATURES.get(normalized_type)
        if not content or signature_matches is None or not signature_matches(content):
            raise UnsupportedAvatarFileError("avatar image type is not supported")
        version = hashlib.sha256(content).hexdigest()
        object_key = self._build_avatar_object_key(normalized_type)
        self._avatar_storage.upload(
            object_key=object_key,
            content=content,
            content_type=normalized_type,
        )
        previous_object_key: str | None = None
        try:
            with self._session_factory.begin() as session:
                repository = AuthRepository(session)
                user = repository.find_user_by_employee_id(employee_id)
                if user is None or not user.active:
                    raise InvalidAccessTokenError("invalid user identity")
                previous_object_key = user.avatar_object_key
                repository.update_avatar_object(
                    user,
                    object_key=object_key,
                    version=version,
                )
                principal = self._principal(user)
        except Exception:
            try:
                self._avatar_storage.delete(object_key=object_key)
            except (AvatarStorageConfigurationError, AvatarStorageOperationError):
                pass
            raise
        if previous_object_key and previous_object_key != object_key:
            try:
                self._avatar_storage.delete(object_key=previous_object_key)
            except (AvatarStorageConfigurationError, AvatarStorageOperationError):
                pass
        return principal

    def get_avatar_url(self, employee_id: str) -> str | None:
        """Return the direct OSS URL; None means the account still has a legacy BLOB."""
        with self._session_factory() as session:
            user = AuthRepository(session).find_user_by_employee_id(employee_id)
            if user is None or not user.active:
                raise InvalidAccessTokenError("invalid user identity")
            if user.avatar_object_key:
                return self._avatar_storage.public_url(object_key=user.avatar_object_key)
            if user.avatar_content is not None and user.avatar_version is not None:
                return None
            raise AvatarNotFoundError("custom avatar was not found")

    def get_avatar(self, employee_id: str) -> tuple[bytes, str, str]:
        with self._session_factory() as session:
            user = AuthRepository(session).find_user_by_employee_id(employee_id)
            if (
                user is None
                or not user.active
                or user.avatar_content is None
                or user.avatar_content_type is None
                or user.avatar_version is None
            ):
                raise AvatarNotFoundError("custom avatar was not found")
            return (
                bytes(user.avatar_content),
                user.avatar_content_type,
                user.avatar_version,
            )

    def clear_avatar(self, employee_id: str) -> AuthPrincipal:
        previous_object_key: str | None = None
        with self._session_factory.begin() as session:
            repository = AuthRepository(session)
            user = repository.find_user_by_employee_id(employee_id)
            if user is None or not user.active:
                raise InvalidAccessTokenError("invalid user identity")
            previous_object_key = user.avatar_object_key
            repository.clear_avatar(user)
            principal = self._principal(user)
        if previous_object_key:
            try:
                self._avatar_storage.delete(object_key=previous_object_key)
            except (AvatarStorageConfigurationError, AvatarStorageOperationError):
                pass
        return principal

    def logout(self, token: str) -> None:
        if len(token) < 32 or len(token) > 512:
            return
        with self._session_factory.begin() as session:
            AuthRepository(session).revoke_session(
                self._token_hash(token),
                revoked_at=datetime.now(UTC),
            )

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def _principal(self, user) -> AuthPrincipal:
        avatar_url: str | None = None
        if user.avatar_object_key:
            try:
                avatar_url = self._avatar_storage.public_url(
                    object_key=user.avatar_object_key
                )
            except (AvatarStorageConfigurationError, AvatarStorageOperationError):
                avatar_url = None
        return AuthPrincipal(
            employee_id=user.employee_id,
            username=user.username,
            roles=frozenset(RoleName(role.role) for role in user.roles),
            avatar_key=AvatarKey(user.avatar_key) if user.avatar_key else None,
            has_custom_avatar=bool(user.avatar_object_key or user.avatar_version),
            avatar_version=user.avatar_version,
            avatar_url=avatar_url,
        )

    @staticmethod
    def _normalize_avatar_prefix(prefix: str) -> str:
        normalized = prefix.strip().strip("/")
        if not normalized or ".." in normalized.split("/"):
            raise ValueError("avatar_prefix must be a safe object-key prefix")
        return normalized

    def _build_avatar_object_key(self, content_type: str) -> str:
        extension = {
            "image/png": "png",
            "image/jpeg": "jpg",
            "image/webp": "webp",
        }[content_type]
        return f"{self._avatar_prefix}/{uuid4().hex}.{extension}"
