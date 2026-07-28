"""Deterministic security boundary for every enterprise write operation."""

from __future__ import annotations

from typing import Protocol

from app.actions.contracts import (
    ActionGatewayOutcome,
    ActionGatewayResult,
    ActionProposal,
    ToolExecutionResult,
    ToolExecutionStatus,
    VerificationResult,
)
from app.actions.idempotency import (
    IdempotencyReservation,
    IdempotencyStatus,
    ReservationOutcome,
)


class AuthorizationPort(Protocol):
    def require_authorized(self, actor_id: str, proposal: ActionProposal) -> None: ...


class PolicyValidationPort(Protocol):
    def require_allowed(self, proposal: ActionProposal) -> None: ...


class ApprovalValidationPort(Protocol):
    def require_approved(self, approval_id: str, proposal: ActionProposal) -> object: ...


class ProposalVersionValidationPort(Protocol):
    def require_current(self, proposal: ActionProposal) -> None: ...


class LatestBusinessStatePort(Protocol):
    def require_current_and_executable(self, proposal: ActionProposal) -> None: ...


class WriteToolPort(Protocol):
    @property
    def name(self) -> str: ...

    def execute(
        self,
        proposal: ActionProposal,
        *,
        idempotency_key: str,
    ) -> ToolExecutionResult: ...


class ResultVerificationPort(Protocol):
    def verify(
        self,
        proposal: ActionProposal,
        tool_result: ToolExecutionResult,
    ) -> VerificationResult: ...


class ExecutionRecorderPort(Protocol):
    def start(
        self,
        proposal: ActionProposal,
        *,
        idempotency_key: str,
        tool_name: str,
    ) -> str: ...

    def finish(
        self,
        execution_id: str,
        *,
        status: str,
        tool_result: dict[str, object],
        verification_result: dict[str, object] | None,
    ) -> None: ...


class IdempotencyPort(Protocol):
    def reserve(
        self, key: str, proposal: ActionProposal
    ) -> IdempotencyReservation: ...

    def finalize(
        self,
        key: str,
        *,
        status: IdempotencyStatus,
        result_payload: dict[str, object],
    ) -> None: ...


class ActionGateway:
    """Run all mandatory gates before and after an external side effect."""

    def __init__(
        self,
        *,
        authorization: AuthorizationPort,
        policy: PolicyValidationPort,
        approvals: ApprovalValidationPort,
        proposals: ProposalVersionValidationPort,
        latest_state: LatestBusinessStatePort,
        idempotency: IdempotencyPort,
        tool: WriteToolPort,
        verifier: ResultVerificationPort,
        executions: ExecutionRecorderPort,
    ) -> None:
        self._authorization = authorization
        self._policy = policy
        self._approvals = approvals
        self._proposals = proposals
        self._latest_state = latest_state
        self._idempotency = idempotency
        self._tool = tool
        self._verifier = verifier
        self._executions = executions

    def execute(
        self,
        proposal: ActionProposal,
        *,
        actor_id: str,
        approval_id: str,
        idempotency_key: str,
    ) -> ActionGatewayResult:
        """Execute once after deterministic checks, then verify by reading state."""
        self._authorization.require_authorized(actor_id, proposal)
        self._policy.require_allowed(proposal)
        self._approvals.require_approved(approval_id, proposal)
        self._proposals.require_current(proposal)
        self._latest_state.require_current_and_executable(proposal)

        reservation = self._idempotency.reserve(idempotency_key, proposal)
        if reservation.outcome is ReservationOutcome.REPLAY:
            return ActionGatewayResult(
                outcome=ActionGatewayOutcome.REPLAYED,
                action_id=proposal.action_id,
                action_version=proposal.version,
                idempotency_key=idempotency_key,
                replay_payload=reservation.result_payload,
            )

        execution_id = self._executions.start(
            proposal,
            idempotency_key=idempotency_key,
            tool_name=self._tool.name,
        )
        try:
            tool_result = self._tool.execute(
                proposal, idempotency_key=idempotency_key
            )
        except Exception as exc:  # External exceptions cannot prove no side effect.
            tool_result = ToolExecutionResult(
                status=ToolExecutionStatus.UNKNOWN,
                details={"error_type": type(exc).__name__},
            )

        if tool_result.status is ToolExecutionStatus.FAILED:
            return self._finalize_without_verification(
                proposal,
                idempotency_key,
                execution_id,
                tool_result,
                gateway_outcome=ActionGatewayOutcome.TOOL_FAILED,
                idempotency_status=IdempotencyStatus.FAILED,
            )
        if tool_result.status is ToolExecutionStatus.UNKNOWN:
            return self._finalize_without_verification(
                proposal,
                idempotency_key,
                execution_id,
                tool_result,
                gateway_outcome=ActionGatewayOutcome.RESULT_UNKNOWN,
                idempotency_status=IdempotencyStatus.UNKNOWN,
            )

        try:
            verification = self._verifier.verify(proposal, tool_result)
        except Exception as exc:
            verification = VerificationResult(
                confirmed=False,
                details={"error_type": type(exc).__name__},
            )
        outcome = (
            ActionGatewayOutcome.SUCCEEDED
            if verification.confirmed
            else ActionGatewayOutcome.VERIFICATION_FAILED
        )
        idempotency_status = (
            IdempotencyStatus.SUCCEEDED
            if verification.confirmed
            else IdempotencyStatus.VERIFICATION_FAILED
        )
        payload = {
            "outcome": outcome.value,
            "tool_result": tool_result.model_dump(mode="json"),
            "verification": verification.model_dump(mode="json"),
        }
        self._idempotency.finalize(
            idempotency_key, status=idempotency_status, result_payload=payload
        )
        self._executions.finish(
            execution_id,
            status=outcome.value,
            tool_result=tool_result.model_dump(mode="json"),
            verification_result=verification.model_dump(mode="json"),
        )
        return ActionGatewayResult(
            outcome=outcome,
            action_id=proposal.action_id,
            action_version=proposal.version,
            idempotency_key=idempotency_key,
            tool_result=tool_result,
            verification=verification,
        )

    def _finalize_without_verification(
        self,
        proposal: ActionProposal,
        idempotency_key: str,
        execution_id: str,
        tool_result: ToolExecutionResult,
        *,
        gateway_outcome: ActionGatewayOutcome,
        idempotency_status: IdempotencyStatus,
    ) -> ActionGatewayResult:
        payload = {
            "outcome": gateway_outcome.value,
            "tool_result": tool_result.model_dump(mode="json"),
        }
        self._idempotency.finalize(
            idempotency_key,
            status=idempotency_status,
            result_payload=payload,
        )
        self._executions.finish(
            execution_id,
            status=gateway_outcome.value,
            tool_result=tool_result.model_dump(mode="json"),
            verification_result=None,
        )
        return ActionGatewayResult(
            outcome=gateway_outcome,
            action_id=proposal.action_id,
            action_version=proposal.version,
            idempotency_key=idempotency_key,
            tool_result=tool_result,
        )
