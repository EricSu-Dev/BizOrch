"""Process-local ownership of an in-flight enterprise write.

The deployment runs one API worker. A marker is held from before the first
EXECUTING commit until the final workflow result is durable. A new process has
no marker, so a persisted EXECUTING state can be reconciled after a restart.
"""

from contextlib import contextmanager
from threading import Lock
from collections.abc import Iterator


class ExecutionStillActiveError(RuntimeError):
    """A write is running in this process and must not be treated as crashed."""


class ExecutionActivityRegistry:
    def __init__(self) -> None:
        self._lock = Lock()
        self._active: set[str] = set()

    def is_active(self, workflow_run_id: str) -> bool:
        with self._lock:
            return workflow_run_id in self._active

    @contextmanager
    def claim(self, workflow_run_id: str) -> Iterator[None]:
        with self._lock:
            if workflow_run_id in self._active:
                raise ExecutionStillActiveError(workflow_run_id)
            self._active.add(workflow_run_id)
        try:
            yield
        finally:
            with self._lock:
                self._active.remove(workflow_run_id)

    def require_inactive(self, workflow_run_id: str) -> None:
        if self.is_active(workflow_run_id):
            raise ExecutionStillActiveError(workflow_run_id)
