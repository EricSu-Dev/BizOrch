from app.auth.avatar_storage import OssAvatarObjectStorage


def test_oss_avatar_storage_builds_public_url_without_exposing_credentials() -> None:
    storage = OssAvatarObjectStorage(
        bucket_name="bizorch-public",
        endpoint="https://oss-cn-shanghai.aliyuncs.com",
        access_key_id="test-access-key",
        access_key_secret="test-access-secret",
    )

    assert storage.public_url(
        object_key="bizorch/avatars/opaque id.png"
    ) == (
        "https://bizorch-public.oss-cn-shanghai.aliyuncs.com/"
        "bizorch/avatars/opaque%20id.png"
    )


def test_oss_avatar_storage_accepts_console_endpoint_without_scheme() -> None:
    storage = OssAvatarObjectStorage(
        bucket_name="bizorch-public",
        endpoint="oss-cn-shanghai.aliyuncs.com",
        access_key_id="test-access-key",
        access_key_secret="test-access-secret",
    )

    assert storage.public_url(object_key="bizorch/avatars/avatar.png") == (
        "https://bizorch-public.oss-cn-shanghai.aliyuncs.com/"
        "bizorch/avatars/avatar.png"
    )
