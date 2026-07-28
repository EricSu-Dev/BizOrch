import pytest

from app.workflow.checkpoint import SqliteCheckpointStore


def test_checkpoint_store_creates_parent_directory_and_database(tmp_path) -> None:
    path = tmp_path / "nested" / "checkpoints.db"

    with SqliteCheckpointStore(path) as store:
        assert store.saver is not None
        assert path.exists()

    with pytest.raises(RuntimeError, match="closed"):
        _ = store.saver


def test_checkpoint_store_close_is_idempotent(tmp_path) -> None:
    store = SqliteCheckpointStore(tmp_path / "checkpoints.db")

    store.close()
    store.close()
