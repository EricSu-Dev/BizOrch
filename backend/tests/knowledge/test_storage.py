from pathlib import Path

import pytest

from app.knowledge.storage import KnowledgeFileStorage, KnowledgeFileStorageError


def test_storage_uses_server_generated_key_under_controlled_root(tmp_path) -> None:
    root = tmp_path / "uploads"
    storage = KnowledgeFileStorage(root)

    stored = storage.save(file_name="company-policy.md", content=b"# Policy")

    assert stored.absolute_path.read_bytes() == b"# Policy"
    assert stored.absolute_path.is_relative_to(root.resolve())
    assert stored.storage_key.endswith(".md")
    assert "company-policy" not in stored.storage_key
    assert storage.resolve(stored.storage_key) == stored.absolute_path


@pytest.mark.parametrize(
    "storage_key",
    ["../secret.md", "/absolute.md", "..\\secret.md"],
)
def test_storage_rejects_keys_that_can_escape_root(tmp_path, storage_key: str) -> None:
    storage = KnowledgeFileStorage(tmp_path / "uploads")

    with pytest.raises(KnowledgeFileStorageError):
        storage.resolve(storage_key)


def test_delete_does_not_fail_when_shard_directory_is_not_empty(tmp_path) -> None:
    storage = KnowledgeFileStorage(tmp_path / "uploads")
    stored = storage.save(file_name="policy.txt", content=b"policy")
    sibling = stored.absolute_path.parent / "another.txt"
    sibling.write_bytes(b"keep")

    storage.delete(stored.storage_key)

    assert not stored.absolute_path.exists()
    assert sibling.exists()
