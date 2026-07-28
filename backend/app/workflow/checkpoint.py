"""Lifecycle wrapper for the LangGraph SQLite checkpoint saver."""

from pathlib import Path
import sqlite3

from langgraph.checkpoint.sqlite import SqliteSaver


class SqliteCheckpointStore:
    """Own one process-local SQLite connection used by LangGraph checkpoints."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(
            self.path,
            check_same_thread=False,
        )
        self._saver = SqliteSaver(self._connection)
        self._saver.setup()
        self._closed = False

    @property
    def saver(self) -> SqliteSaver:
        """Return the initialized saver while this store is open."""
        if self._closed:
            raise RuntimeError("checkpoint store is closed")
        return self._saver

    def close(self) -> None:
        """Close the owned SQLite connection; repeated calls are harmless."""
        if not self._closed:
            self._connection.close()
            self._closed = True

    def __enter__(self) -> "SqliteCheckpointStore":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
