"""Domain-neutral workflow states and deterministic transition rules."""

from collections.abc import Mapping
from enum import Enum


class WorkflowState(str, Enum):
    """Lifecycle states shared by every BizOrch scenario package."""

    CREATED = "CREATED"
    RUNNING = "RUNNING"
    WAITING_USER = "WAITING_USER"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    WAITING_HUMAN = "WAITING_HUMAN"
    EXECUTING = "EXECUTING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


TERMINAL_WORKFLOW_STATES = frozenset(
    {
        WorkflowState.COMPLETED,
        WorkflowState.FAILED,
        WorkflowState.CANCELLED,
    }
)


_ALLOWED_TRANSITIONS: Mapping[WorkflowState, frozenset[WorkflowState]] = {
    WorkflowState.CREATED: frozenset(
        {WorkflowState.RUNNING, WorkflowState.CANCELLED}
    ),
    WorkflowState.RUNNING: frozenset(
        {
            WorkflowState.WAITING_USER,
            WorkflowState.WAITING_APPROVAL,
            WorkflowState.WAITING_HUMAN,
            WorkflowState.EXECUTING,
            WorkflowState.COMPLETED,
            WorkflowState.FAILED,
            WorkflowState.CANCELLED,
        }
    ),
    WorkflowState.WAITING_USER: frozenset(
        {WorkflowState.RUNNING, WorkflowState.CANCELLED}
    ),
    WorkflowState.WAITING_APPROVAL: frozenset(
        {
            WorkflowState.RUNNING,
            WorkflowState.WAITING_HUMAN,
            WorkflowState.COMPLETED,
            WorkflowState.FAILED,
            WorkflowState.CANCELLED,
        }
    ),
    WorkflowState.WAITING_HUMAN: frozenset(
        {
            WorkflowState.RUNNING,
            WorkflowState.COMPLETED,
            WorkflowState.FAILED,
            WorkflowState.CANCELLED,
        }
    ),
    WorkflowState.EXECUTING: frozenset(
        {
            WorkflowState.COMPLETED,
            WorkflowState.FAILED,
            WorkflowState.WAITING_HUMAN,
        }
    ),
    WorkflowState.COMPLETED: frozenset(),
    WorkflowState.FAILED: frozenset(),
    WorkflowState.CANCELLED: frozenset(),
}


class InvalidWorkflowTransition(ValueError):
    """Raised when code attempts an illegal workflow state change."""

    def __init__(self, current: WorkflowState, target: WorkflowState) -> None:
        self.current = current
        self.target = target
        super().__init__(f"workflow cannot transition from {current} to {target}")


class WorkflowStateMachine:
    """Validate lifecycle changes without knowing any scenario concepts."""

    @staticmethod
    def allowed_targets(current: WorkflowState) -> frozenset[WorkflowState]:
        """Return an immutable set of legal next states."""
        return _ALLOWED_TRANSITIONS[current]

    @classmethod
    def can_transition(
        cls,
        current: WorkflowState,
        target: WorkflowState,
    ) -> bool:
        """Return whether a transition is legal."""
        return target in cls.allowed_targets(current)

    @classmethod
    def require_transition(
        cls,
        current: WorkflowState,
        target: WorkflowState,
    ) -> WorkflowState:
        """Validate a transition and return the accepted target state."""
        if not cls.can_transition(current, target):
            raise InvalidWorkflowTransition(current, target)
        return target

    @staticmethod
    def is_terminal(state: WorkflowState) -> bool:
        """Return whether no further automatic transition is allowed."""
        return state in TERMINAL_WORKFLOW_STATES
