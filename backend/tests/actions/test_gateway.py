import pytest

from app.actions.contracts import (
    ActionGatewayOutcome,
    ActionProposal,
    ToolExecutionResult,
    ToolExecutionStatus,
    VerificationResult,
)
from app.actions.gateway import ActionGateway
from app.actions.idempotency import (
    IdempotencyReservation,
    IdempotencyStatus,
    ReservationOutcome,
)


def proposal() -> ActionProposal:
    return ActionProposal(
        action_id="action-1",
        action_type="grant_access",
        target_resource="application/CRM/user/EMP-1001",
        parameters={"role_code": "read_only", "duration_days": 30},
        version=1,
        content_summary="Grant CRM read-only access for 30 days",
    )


class Guard:
    def __init__(self, calls: list[str], label: str) -> None:
        self.calls = calls
        self.label = label

    def require_authorized(self, actor_id: str, action: ActionProposal) -> None:
        self.calls.append(self.label)

    def require_allowed(self, action: ActionProposal) -> None:
        self.calls.append(self.label)

    def require_approved(self, approval_id: str, action: ActionProposal) -> object:
        self.calls.append(self.label)
        return object()

    def require_current(self, action: ActionProposal) -> None:
        self.calls.append(self.label)

    def require_current_and_executable(self, action: ActionProposal) -> None:
        self.calls.append(self.label)


class FakeIdempotency:
    def __init__(
        self,
        calls: list[str],
        reservation: IdempotencyReservation | None = None,
    ) -> None:
        self.calls = calls
        self.reservation = reservation or IdempotencyReservation(
            outcome=ReservationOutcome.ACQUIRED
        )
        self.final_status: IdempotencyStatus | None = None

    def reserve(
        self, key: str, action: ActionProposal
    ) -> IdempotencyReservation:
        self.calls.append("idempotency")
        return self.reservation

    def finalize(
        self,
        key: str,
        *,
        status: IdempotencyStatus,
        result_payload: dict[str, object],
    ) -> None:
        self.calls.append("idempotency_finalized")
        self.final_status = status


class FakeTool:
    name = "enterprise.write"

    def __init__(
        self,
        calls: list[str],
        result: ToolExecutionResult | Exception,
    ) -> None:
        self.calls = calls
        self.result = result

    def execute(
        self, action: ActionProposal, *, idempotency_key: str
    ) -> ToolExecutionResult:
        self.calls.append("tool")
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class FakeVerifier:
    def __init__(self, calls: list[str], result: VerificationResult) -> None:
        self.calls = calls
        self.result = result

    def verify(
        self, action: ActionProposal, tool_result: ToolExecutionResult
    ) -> VerificationResult:
        self.calls.append("verify")
        return self.result


class FakeExecutions:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls
        self.finished_status: str | None = None

    def start(
        self,
        action: ActionProposal,
        *,
        idempotency_key: str,
        tool_name: str,
    ) -> str:
        self.calls.append("execution_started")
        return "execution-1"

    def finish(
        self,
        execution_id: str,
        *,
        status: str,
        tool_result: dict[str, object],
        verification_result: dict[str, object] | None,
    ) -> None:
        self.calls.append("execution_finished")
        self.finished_status = status


def build_gateway(
    calls: list[str],
    *,
    tool_result: ToolExecutionResult | Exception,
    verification: VerificationResult | None = None,
    reservation: IdempotencyReservation | None = None,
) -> tuple[ActionGateway, FakeIdempotency, FakeExecutions]:
    idempotency = FakeIdempotency(calls, reservation)
    executions = FakeExecutions(calls)
    gateway = ActionGateway(
        authorization=Guard(calls, "authorization"),
        policy=Guard(calls, "policy"),
        approvals=Guard(calls, "approval"),
        proposals=Guard(calls, "proposal_version"),
        latest_state=Guard(calls, "latest_state"),
        idempotency=idempotency,
        tool=FakeTool(calls, tool_result),
        verifier=FakeVerifier(
            calls, verification or VerificationResult(confirmed=True)
        ),
        executions=executions,
    )
    return gateway, idempotency, executions


def execute(gateway: ActionGateway):
    return gateway.execute(
        proposal(),
        actor_id="system-action-executor",
        approval_id="approval-1",
        idempotency_key="idem-1",
    )


def test_gateway_runs_mandatory_checks_and_verification_in_order() -> None:
    calls: list[str] = []
    gateway, idempotency, executions = build_gateway(
        calls,
        tool_result=ToolExecutionResult(
            status=ToolExecutionStatus.SUCCEEDED,
            external_reference="grant-1",
        ),
        verification=VerificationResult(confirmed=True, details={"active": True}),
    )

    result = execute(gateway)

    assert result.outcome is ActionGatewayOutcome.SUCCEEDED
    assert calls == [
        "authorization",
        "policy",
        "approval",
        "proposal_version",
        "latest_state",
        "idempotency",
        "execution_started",
        "tool",
        "verify",
        "idempotency_finalized",
        "execution_finished",
    ]
    assert idempotency.final_status is IdempotencyStatus.SUCCEEDED
    assert executions.finished_status == ActionGatewayOutcome.SUCCEEDED.value


def test_successful_replay_never_calls_write_tool() -> None:
    calls: list[str] = []
    gateway, _, _ = build_gateway(
        calls,
        tool_result=AssertionError("write tool must not run"),
        reservation=IdempotencyReservation(
            outcome=ReservationOutcome.REPLAY,
            result_payload={"external_reference": "grant-1"},
        ),
    )

    result = execute(gateway)

    assert result.outcome is ActionGatewayOutcome.REPLAYED
    assert calls[-1] == "idempotency"
    assert "tool" not in calls


@pytest.mark.parametrize(
    ("tool_result", "expected_outcome", "expected_status"),
    [
        (
            ToolExecutionResult(status=ToolExecutionStatus.FAILED),
            ActionGatewayOutcome.TOOL_FAILED,
            IdempotencyStatus.FAILED,
        ),
        (
            ToolExecutionResult(status=ToolExecutionStatus.UNKNOWN),
            ActionGatewayOutcome.RESULT_UNKNOWN,
            IdempotencyStatus.UNKNOWN,
        ),
        (
            TimeoutError("network timeout"),
            ActionGatewayOutcome.RESULT_UNKNOWN,
            IdempotencyStatus.UNKNOWN,
        ),
    ],
)
def test_failed_or_uncertain_write_skips_verification(
    tool_result: ToolExecutionResult | Exception,
    expected_outcome: ActionGatewayOutcome,
    expected_status: IdempotencyStatus,
) -> None:
    calls: list[str] = []
    gateway, idempotency, _ = build_gateway(calls, tool_result=tool_result)

    result = execute(gateway)

    assert result.outcome is expected_outcome
    assert idempotency.final_status is expected_status
    assert "verify" not in calls


def test_unconfirmed_read_after_write_is_not_reported_as_success() -> None:
    calls: list[str] = []
    gateway, idempotency, _ = build_gateway(
        calls,
        tool_result=ToolExecutionResult(status=ToolExecutionStatus.SUCCEEDED),
        verification=VerificationResult(
            confirmed=False, details={"active": False}
        ),
    )

    result = execute(gateway)

    assert result.outcome is ActionGatewayOutcome.VERIFICATION_FAILED
    assert idempotency.final_status is IdempotencyStatus.VERIFICATION_FAILED


def test_verifier_exception_is_conservatively_recorded_as_verification_failure() -> None:
    calls: list[str] = []

    class FailingVerifier(FakeVerifier):
        def verify(self, action, tool_result):
            self.calls.append("verify")
            raise TimeoutError("verification endpoint timed out")

    gateway, idempotency, _ = build_gateway(
        calls,
        tool_result=ToolExecutionResult(status=ToolExecutionStatus.SUCCEEDED),
    )
    gateway._verifier = FailingVerifier(calls, VerificationResult(confirmed=True))

    result = execute(gateway)

    assert result.outcome is ActionGatewayOutcome.VERIFICATION_FAILED
    assert result.verification is not None
    assert result.verification.details == {"error_type": "TimeoutError"}
    assert idempotency.final_status is IdempotencyStatus.VERIFICATION_FAILED


def test_guard_failure_stops_before_idempotency_and_write() -> None:
    calls: list[str] = []

    class DeniedAuthorization(Guard):
        def require_authorized(
            self, actor_id: str, action: ActionProposal
        ) -> None:
            self.calls.append(self.label)
            raise PermissionError("actor is not an action executor")

    gateway, _, _ = build_gateway(
        calls,
        tool_result=ToolExecutionResult(status=ToolExecutionStatus.SUCCEEDED),
    )
    gateway._authorization = DeniedAuthorization(calls, "authorization")

    with pytest.raises(PermissionError):
        execute(gateway)

    assert calls == ["authorization"]
