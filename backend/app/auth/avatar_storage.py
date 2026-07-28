"""Safe, server-side object storage adapter for custom user avatars."""

from __future__ import annotations

from typing import Protocol
from urllib.parse import quote, urlsplit


class AvatarStorageConfigurationError(RuntimeError):
    """Raised when avatar uploads are requested without complete OSS settings."""


class AvatarStorageOperationError(RuntimeError):
    """Raised when OSS cannot complete an avatar object operation."""


class AvatarObjectStorage(Protocol):
    """Small boundary that keeps provider details outside authentication logic."""

    def upload(self, *, object_key: str, content: bytes, content_type: str) -> None:
        """Store a validated avatar using the supplied opaque object key."""

    def delete(self, *, object_key: str) -> None:
        """Delete one previously stored avatar object."""

    def public_url(self, *, object_key: str) -> str:
        """Return the public-read URL for one opaque object key."""


class UnavailableAvatarObjectStorage:
    """Fail closed until all OSS configuration values are present."""

    def upload(self, *, object_key: str, content: bytes, content_type: str) -> None:
        raise AvatarStorageConfigurationError("avatar object storage is not configured")

    def delete(self, *, object_key: str) -> None:
        raise AvatarStorageConfigurationError("avatar object storage is not configured")

    def public_url(self, *, object_key: str) -> str:
        raise AvatarStorageConfigurationError("avatar object storage is not configured")


class OssAvatarObjectStorage:
    """Alibaba Cloud OSS implementation; credentials stay in the backend process."""

    def __init__(
        self,
        *,
        bucket_name: str,
        endpoint: str,
        access_key_id: str,
        access_key_secret: str,
    ) -> None:
        normalized_bucket = bucket_name.strip()
        normalized_endpoint = endpoint.strip().rstrip("/")
        if normalized_endpoint and "://" not in normalized_endpoint:
            normalized_endpoint = f"https://{normalized_endpoint}"
        if not all(
            (
                normalized_bucket,
                normalized_endpoint,
                access_key_id.strip(),
                access_key_secret.strip(),
            )
        ):
            raise AvatarStorageConfigurationError(
                "avatar object storage configuration is incomplete"
            )
        parsed = urlsplit(normalized_endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise AvatarStorageConfigurationError("OSS endpoint must be an HTTP(S) URL")

        try:
            import oss2
        except ImportError as exc:  # pragma: no cover - dependency baseline guards this
            raise AvatarStorageConfigurationError("oss2 dependency is unavailable") from exc

        self._bucket_name = normalized_bucket
        self._endpoint = normalized_endpoint
        self._bucket = oss2.Bucket(
            oss2.Auth(access_key_id, access_key_secret),
            normalized_endpoint,
            normalized_bucket,
        )

    def upload(self, *, object_key: str, content: bytes, content_type: str) -> None:
        try:
            self._bucket.put_object(
                object_key,
                content,
                headers={
                    "Content-Type": content_type,
                    "Cache-Control": "public, max-age=31536000, immutable",
                },
            )
        except Exception as exc:  # Provider exceptions must not leak through the API.
            raise AvatarStorageOperationError("avatar upload failed") from exc

    def delete(self, *, object_key: str) -> None:
        try:
            self._bucket.delete_object(object_key)
        except Exception as exc:  # Provider exceptions must not leak through the API.
            raise AvatarStorageOperationError("avatar deletion failed") from exc

    def public_url(self, *, object_key: str) -> str:
        parsed = urlsplit(self._endpoint)
        host = parsed.netloc
        bucket_prefix = f"{self._bucket_name}."
        if not host.startswith(bucket_prefix):
            host = f"{bucket_prefix}{host}"
        return f"{parsed.scheme}://{host}/{quote(object_key.lstrip('/'), safe='/')}"


def build_avatar_object_storage(settings) -> AvatarObjectStorage:
    """Build OSS only for complete settings; otherwise expose a safe 503 on upload."""
    access_key_id = (
        settings.oss_access_key_id.get_secret_value()
        if settings.oss_access_key_id
        else ""
    )
    access_key_secret = (
        settings.oss_access_key_secret.get_secret_value()
        if settings.oss_access_key_secret
        else ""
    )
    if not all(
        (
            settings.oss_bucket_name.strip(),
            settings.oss_endpoint.strip(),
            access_key_id.strip(),
            access_key_secret.strip(),
        )
    ):
        return UnavailableAvatarObjectStorage()
    return OssAvatarObjectStorage(
        bucket_name=settings.oss_bucket_name,
        endpoint=settings.oss_endpoint,
        access_key_id=access_key_id,
        access_key_secret=access_key_secret,
    )
