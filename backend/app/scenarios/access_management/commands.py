"""Application facade used by HTTP routes for the access-request use case."""

from typing import Protocol

from app.approval.contracts import ApprovalDecisionType
from app.scenarios.access_management.contracts import (
    AccessRequestContext,
    AccessRequestDraft,
)
from app.scenarios.access_management.workflow import (
    AccessRequestWorkflow,
    AccessWorkflowSnapshot,
)
from app.tickets.service import TicketProjectionService
from app.workflow.repository import WorkflowNotFoundError


class AccessContextResolverPort(Protocol):
    def resolve(self, draft: AccessRequestDraft): ...


class AccessRequestActorMismatchError(PermissionError):
    """Raised when an actor tries to create or modify another employee's request."""


class AccessRequestCommandService:
    """Resolve trusted facts before delegating lifecycle changes to the graph."""

    TICKET_TITLE = "企业系统权限申请"

    def __init__(
        self,
        workflow: AccessRequestWorkflow,
        context_resolver: AccessContextResolverPort,
        ticket_projection: TicketProjectionService,
    ) -> None:
        self._workflow = workflow
        self._context_resolver = context_resolver
        self._ticket_projection = ticket_projection

    def create(
        self,
        draft: AccessRequestDraft,
        *,
        actor_id: str,
    ) -> AccessWorkflowSnapshot:
        if draft.employee_id and draft.employee_id != actor_id:
            raise AccessRequestActorMismatchError(actor_id)
        owned_draft = draft.model_copy(update={"employee_id": actor_id})
        context = self._context_resolver.resolve(owned_draft)
        return self.create_with_context(
            owned_draft,
            context=context,
            actor_id=actor_id,
        )

    def create_with_context(
        self,
        draft: AccessRequestDraft,
        *,
        context: AccessRequestContext,
        actor_id: str,
        run_id: str | None = None,
    ) -> AccessWorkflowSnapshot:
        """Start a request with context already resolved by a read-only Domain Agent."""
        if draft.employee_id and draft.employee_id != actor_id:
            raise AccessRequestActorMismatchError(actor_id)
        owned_draft = draft.model_copy(update={"employee_id": actor_id})
        if run_id:
            try:
                existing = self._workflow.request_draft(run_id)
            except WorkflowNotFoundError:
                pass
            else:
                if existing.employee_id != actor_id:
                    raise AccessRequestActorMismatchError(actor_id)
                return self._attach_ticket(
                    self._workflow.snapshot(run_id),
                    requester_id=actor_id,
                    title=self.TICKET_TITLE,
                )
        snapshot = self._workflow.start(owned_draft, context, run_id=run_id)
        return self._attach_ticket(
            snapshot,
            requester_id=actor_id,
            title=self.TICKET_TITLE,
        )

    def provide_information(
        self,
        *,
        workflow_run_id: str,
        expected_workflow_version: int,
        updates: AccessRequestDraft,
        actor_id: str,
    ) -> AccessWorkflowSnapshot:
        current = self._workflow.request_draft(workflow_run_id)
        if current.employee_id != actor_id:
            raise AccessRequestActorMismatchError(actor_id)
        merged = current.model_dump(mode="json")
        merged.update(updates.model_dump(exclude_unset=True, mode="json"))
        resolved_draft = AccessRequestDraft.model_validate(merged)
        context = self._context_resolver.resolve(resolved_draft)
        return self.provide_information_with_context(
            workflow_run_id=workflow_run_id,
            expected_workflow_version=expected_workflow_version,
            updates=updates,
            context=context,
            actor_id=actor_id,
        )

    def provide_information_with_context(
        self,
        *,
        workflow_run_id: str,
        expected_workflow_version: int,
        updates: AccessRequestDraft,
        context: AccessRequestContext,
        actor_id: str,
    ) -> AccessWorkflowSnapshot:
        """Resume with facts already resolved by the read-only Domain Agent."""
        current = self.current_draft(workflow_run_id, actor_id=actor_id)
        update_values = updates.model_dump(exclude_unset=True, mode="json")
        if set(update_values) - set(current.missing_fields()):
            raise ValueError("only missing request fields can be supplemented")
        snapshot = self._workflow.provide_information_and_resume(
            workflow_run_id=workflow_run_id,
            expected_workflow_version=expected_workflow_version,
            updates=updates,
            context=context,
        )
        return self._attach_ticket(
            snapshot,
            requester_id=actor_id,
            title=self.TICKET_TITLE,
        )

    def current_draft(
        self,
        workflow_run_id: str,
        *,
        actor_id: str,
    ) -> AccessRequestDraft:
        current = self._workflow.request_draft(workflow_run_id)
        if current.employee_id != actor_id:
            raise AccessRequestActorMismatchError(actor_id)
        return current

    def decide(
        self,
        *,
        workflow_run_id: str,
        approval_id: str,
        expected_workflow_version: int,
        actor_id: str,
        decision: ApprovalDecisionType,
        comment: str | None = None,
    ) -> AccessWorkflowSnapshot:
        requester_id = self._workflow.request_draft(workflow_run_id).employee_id
        snapshot = self._workflow.decide_and_resume(
            workflow_run_id=workflow_run_id,
            approval_id=approval_id,
            expected_workflow_version=expected_workflow_version,
            actor_id=actor_id,
            decision=decision,
            comment=comment,
        )
        return self._attach_ticket(
            snapshot,
            requester_id=requester_id,
            title=self.TICKET_TITLE,
        )

    def get(
        self,
        workflow_run_id: str,
        *,
        actor_id: str,
    ) -> AccessWorkflowSnapshot:
        current = self._workflow.request_draft(workflow_run_id)
        if current.employee_id != actor_id:
            raise AccessRequestActorMismatchError(actor_id)
        return self._attach_ticket(
            self._workflow.snapshot(workflow_run_id),
            requester_id=actor_id,
            title=self.TICKET_TITLE,
        )

    def _attach_ticket(
        self,
        snapshot: AccessWorkflowSnapshot,
        *,
        requester_id: str | None = None,
        title: str | None = None,
    ) -> AccessWorkflowSnapshot:
        link = self._ticket_projection.sync(
            snapshot.workflow_run_id,
            requester_id=requester_id,
            title=title,
        )
        return snapshot.model_copy(
            update={
                "service_request_id": link.service_request_id,
                "ticket_id": link.ticket_id,
            }
        )
