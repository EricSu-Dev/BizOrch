"""Persistence for immutable action proposal versions."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.actions.contracts import ActionProposal
from app.actions.models import ActionProposalRecord


class ActionProposalNotFoundError(LookupError):
    """Raised when a requested action version is absent."""


class ActionProposalVersionError(RuntimeError):
    """Raised when execution does not use the latest persisted proposal version."""


class ActionProposalRepository:
    """Store and reload append-only proposal versions without committing."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, workflow_run_id: str, proposal: ActionProposal) -> None:
        self._session.add(
            ActionProposalRecord(
                action_id=proposal.action_id,
                workflow_run_id=workflow_run_id,
                action_type=proposal.action_type,
                target_resource=proposal.target_resource,
                parameters=proposal.parameters,
                version=proposal.version,
                content_summary=proposal.content_summary,
                content_digest=proposal.content_digest,
            )
        )
        self._session.flush()

    def get(self, action_id: str, version: int) -> ActionProposal:
        record = self._session.scalar(
            select(ActionProposalRecord).where(
                ActionProposalRecord.action_id == action_id,
                ActionProposalRecord.version == version,
            )
        )
        if record is None:
            raise ActionProposalNotFoundError(f"{action_id}:{version}")
        proposal = ActionProposal(
            action_id=record.action_id,
            action_type=record.action_type,
            target_resource=record.target_resource,
            parameters=record.parameters,
            version=record.version,
            content_summary=record.content_summary,
        )
        if proposal.content_digest != record.content_digest:
            raise ValueError("persisted action proposal digest does not match content")
        return proposal

    def require_current(self, proposal: ActionProposal) -> None:
        """Reject stale versions or content not matching authoritative persistence."""
        latest_version = self._session.scalar(
            select(func.max(ActionProposalRecord.version)).where(
                ActionProposalRecord.action_id == proposal.action_id
            )
        )
        if latest_version is None:
            raise ActionProposalNotFoundError(proposal.action_id)
        if latest_version != proposal.version:
            raise ActionProposalVersionError("action proposal version is stale")
        persisted = self.get(proposal.action_id, proposal.version)
        if persisted.content_digest != proposal.content_digest:
            raise ActionProposalVersionError("action proposal content is not authoritative")
