"""Controlled local persistence for original enterprise knowledge files."""

import os
from pathlib import Path, PurePosixPath
from uuid import uuid4

from pydantic import BaseModel, ConfigDict


class KnowledgeFileStorageError(RuntimeError):
    """Raised when an original knowledge file cannot be safely persisted."""


class KnowledgeStorageConfigurationError(KnowledgeFileStorageError):
    """Raised when the controlled upload root is unavailable."""


class StoredKnowledgeFile(BaseModel):
    model_config = ConfigDict(frozen=True)

    storage_key: str
    absolute_path: Path


class KnowledgeFileStorage:
    """Persist bytes under server-generated keys within one configured root."""

    _ALLOWED_SUFFIXES = frozenset({".md", ".markdown", ".txt", ".pdf"})

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def save(self, *, file_name: str, content: bytes) -> StoredKnowledgeFile:
        suffix = Path(file_name).suffix.lower()
        if suffix not in self._ALLOWED_SUFFIXES:
            raise KnowledgeFileStorageError("knowledge file suffix is not supported")
        try:
            self.root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise KnowledgeStorageConfigurationError(
                "knowledge upload directory is unavailable"
            ) from exc

        file_id = uuid4().hex
        storage_key = f"{file_id[:2]}/{file_id}{suffix}"
        target = self._resolve_key(storage_key)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
        except OSError as exc:
            try:
                target.unlink(missing_ok=True)
            except OSError:
                pass
            raise KnowledgeFileStorageError(
                "knowledge file could not be persisted"
            ) from exc
        return StoredKnowledgeFile(storage_key=storage_key, absolute_path=target)

    def delete(self, storage_key: str) -> None:
        target = self._resolve_key(storage_key)
        try:
            target.unlink(missing_ok=True)
        except FileNotFoundError:
            return
        except OSError as exc:
            raise KnowledgeFileStorageError(
                "knowledge file compensation failed"
            ) from exc
        if target.parent != self.root:
            try:
                target.parent.rmdir()
            except OSError:
                # A shared shard directory can legitimately still contain files.
                pass

    def resolve(self, storage_key: str) -> Path:
        return self._resolve_key(storage_key)

    def _resolve_key(self, storage_key: str) -> Path:
        if "\\" in storage_key:
            raise KnowledgeFileStorageError("invalid knowledge storage key")
        key = PurePosixPath(storage_key)
        if key.is_absolute() or not key.parts or ".." in key.parts:
            raise KnowledgeFileStorageError("invalid knowledge storage key")
        target = (self.root / Path(*key.parts)).resolve()
        try:
            target.relative_to(self.root)
        except ValueError as exc:
            raise KnowledgeFileStorageError(
                "knowledge storage key escapes the configured root"
            ) from exc
        return target
