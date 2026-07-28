import pytest

from app.workflow.state import (
    InvalidWorkflowTransition,
    WorkflowState,
    WorkflowStateMachine,
)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (WorkflowState.CREATED, WorkflowState.RUNNING),
        (WorkflowState.RUNNING, WorkflowState.WAITING_USER),
        (WorkflowState.WAITING_USER, WorkflowState.RUNNING),
        (WorkflowState.RUNNING, WorkflowState.WAITING_APPROVAL),
        (WorkflowState.WAITING_APPROVAL, WorkflowState.RUNNING),
        (WorkflowState.WAITING_APPROVAL, WorkflowState.COMPLETED),
        (WorkflowState.RUNNING, WorkflowState.EXECUTING),
        (WorkflowState.EXECUTING, WorkflowState.COMPLETED),
        (WorkflowState.EXECUTING, WorkflowState.WAITING_HUMAN),
    ],
)
def test_allows_expected_lifecycle_transitions(
    current: WorkflowState,
    target: WorkflowState,
) -> None:
    assert WorkflowStateMachine.can_transition(current, target)
    assert WorkflowStateMachine.require_transition(current, target) is target


@pytest.mark.parametrize(
    "terminal",
    [WorkflowState.COMPLETED, WorkflowState.FAILED, WorkflowState.CANCELLED],
)
def test_terminal_states_cannot_transition(terminal: WorkflowState) -> None:
    assert WorkflowStateMachine.is_terminal(terminal)
    assert WorkflowStateMachine.allowed_targets(terminal) == frozenset()


def test_rejects_skipping_directly_from_created_to_executing() -> None:
    with pytest.raises(InvalidWorkflowTransition) as error:
        WorkflowStateMachine.require_transition(
            WorkflowState.CREATED,
            WorkflowState.EXECUTING,
        )

    assert error.value.current is WorkflowState.CREATED
    assert error.value.target is WorkflowState.EXECUTING


def test_rejects_same_state_transition() -> None:
    assert not WorkflowStateMachine.can_transition(
        WorkflowState.RUNNING,
        WorkflowState.RUNNING,
    )


def test_waiting_states_are_not_interchangeable() -> None:
    assert not WorkflowStateMachine.can_transition(
        WorkflowState.WAITING_USER,
        WorkflowState.WAITING_APPROVAL,
    )
    assert not WorkflowStateMachine.can_transition(
        WorkflowState.WAITING_APPROVAL,
        WorkflowState.WAITING_USER,
    )
