"""Recoverable five-step onboarding execution through Action Gateway."""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ConfigDict, JsonValue
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.actions.activity import ExecutionActivityRegistry
from app.actions.contracts import (
    ActionGatewayOutcome,
    ActionGatewayResult,
    ActionPlanStepStatus,
    ActionProposal,
    ToolExecutionResult,
    ToolExecutionStatus,
    VerificationResult,
)
from app.actions.models import ActionPlanRecord, ActionPlanStepRecord
from app.actions.plans import ActionPlanRepository, ActionPlanVersionError
from app.approval.repository import ApprovalRepository
from app.integrations.enterprise_ops import (
    EnterpriseOpsAccessPackage,
    EnterpriseOpsEmployeeLifecycleProfile,
    EnterpriseOpsEmployeeOnboardingWriteResult,
    EnterpriseOpsRejectedError,
)
from app.workflow.repository import WorkflowRepository
from app.workflow.state import WorkflowState


_ACTIONS_BY_REQUEST_TYPE = {
    "ONBOARDING": frozenset(
        {
        "create_pending_employee",
        "create_disabled_corporate_account",
        "assign_baseline_access_package",
        "create_asset_assignment_task",
        "activate_employee_and_account",
        }
    ),
    "TRANSFER": frozenset(
        {
            "update_employee_assignment",
            "revoke_obsolete_baseline_access",
            "grant_target_baseline_access",
            "create_asset_adjustment_task",
            "verify_employee_transfer_consistency",
        }
    ),
    "OFFBOARDING": frozenset(
        {
            "disable_corporate_account",
            "revoke_all_employee_access",
            "create_asset_return_task",
            "mark_employee_inactive",
            "verify_employee_offboarding_consistency",
        }
    ),
}
_LIFECYCLE_ACTIONS = frozenset().union(*_ACTIONS_BY_REQUEST_TYPE.values())


class EmployeeLifecycleWritePort(Protocol):
    def query_employee_lifecycle_profile(
        self, employee_id: str
    ) -> EnterpriseOpsEmployeeLifecycleProfile | None: ...

    def query_organization_unit(self, department_code: str): ...

    def query_job_profile(self, job_code: str): ...

    def query_work_location(self, location_code: str): ...

    def query_access_package(
        self, package_code: str
    ) -> EnterpriseOpsAccessPackage | None: ...

    def create_pending_employee(
        self, payload: dict[str, JsonValue], *, idempotency_key: str
    ) -> EnterpriseOpsEmployeeOnboardingWriteResult: ...

    def create_disabled_corporate_account(
        self, payload: dict[str, JsonValue], *, idempotency_key: str
    ) -> EnterpriseOpsEmployeeOnboardingWriteResult: ...

    def assign_baseline_access_package(
        self, payload: dict[str, JsonValue], *, idempotency_key: str
    ) -> EnterpriseOpsEmployeeOnboardingWriteResult: ...

    def create_asset_assignment_task(
        self, payload: dict[str, JsonValue], *, idempotency_key: str
    ) -> EnterpriseOpsEmployeeOnboardingWriteResult: ...

    def activate_employee_and_account(
        self, payload: dict[str, JsonValue], *, idempotency_key: str
    ) -> EnterpriseOpsEmployeeOnboardingWriteResult: ...

    def update_employee_assignment(
        self, payload: dict[str, JsonValue], *, idempotency_key: str
    ) -> EnterpriseOpsEmployeeOnboardingWriteResult: ...

    def revoke_obsolete_baseline_access(
        self, payload: dict[str, JsonValue], *, idempotency_key: str
    ) -> EnterpriseOpsEmployeeOnboardingWriteResult: ...

    def grant_target_baseline_access(
        self, payload: dict[str, JsonValue], *, idempotency_key: str
    ) -> EnterpriseOpsEmployeeOnboardingWriteResult: ...

    def create_asset_adjustment_task(
        self, payload: dict[str, JsonValue], *, idempotency_key: str
    ) -> EnterpriseOpsEmployeeOnboardingWriteResult: ...

    def verify_employee_transfer_consistency(
        self, payload: dict[str, JsonValue], *, idempotency_key: str
    ) -> EnterpriseOpsEmployeeOnboardingWriteResult: ...

    def disable_corporate_account(
        self, payload: dict[str, JsonValue], *, idempotency_key: str
    ) -> EnterpriseOpsEmployeeOnboardingWriteResult: ...

    def revoke_all_employee_access(
        self, payload: dict[str, JsonValue], *, idempotency_key: str
    ) -> EnterpriseOpsEmployeeOnboardingWriteResult: ...

    def create_asset_return_task(
        self, payload: dict[str, JsonValue], *, idempotency_key: str
    ) -> EnterpriseOpsEmployeeOnboardingWriteResult: ...

    def mark_employee_inactive(
        self, payload: dict[str, JsonValue], *, idempotency_key: str
    ) -> EnterpriseOpsEmployeeOnboardingWriteResult: ...

    def verify_employee_offboarding_consistency(
        self, payload: dict[str, JsonValue], *, idempotency_key: str
    ) -> EnterpriseOpsEmployeeOnboardingWriteResult: ...


class ActionGatewayPort(Protocol):
    def execute(
        self,
        proposal: ActionProposal,
        *,
        actor_id: str,
        approval_id: str,
        idempotency_key: str,
    ) -> ActionGatewayResult: ...


class EmployeeOnboardingExecutionError(RuntimeError):
    """Known deterministic condition that requires human reconciliation."""


class EmployeeLifecycleActionAuthorization:
    def __init__(self, allowed_actor_ids: frozenset[str]) -> None:
        self._allowed_actor_ids = allowed_actor_ids

    def require_authorized(self, actor_id: str, proposal: ActionProposal) -> None:
        if actor_id not in self._allowed_actor_ids:
            raise PermissionError("actor cannot execute employee lifecycle actions")


class EmployeeLifecycleExecutionPolicy:
    """Allow only fixed actions belonging to the proposal request type."""

    def require_allowed(self, proposal: ActionProposal) -> None:
        request_type = str(proposal.parameters.get("request_type", ""))
        if (
            proposal.action_type not in _LIFECYCLE_ACTIONS
            or proposal.action_type
            not in _ACTIONS_BY_REQUEST_TYPE.get(request_type, frozenset())
        ):
            raise PermissionError("action does not belong to lifecycle request type")
        employee_id = str(proposal.parameters.get("subject_employee_id", "")).strip()
        if not employee_id:
            raise PermissionError("onboarding step has no subject employee")
        if not proposal.target_resource.startswith(f"employees/{employee_id}/") and (
            proposal.target_resource != f"employees/{employee_id}"
        ):
            raise PermissionError("lifecycle target does not match employee")


class EmployeeLifecyclePlanApprovalValidator:
    """Validate exact proposal membership in the approved current plan."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def require_approved(self, approval_id: str, proposal: ActionProposal) -> object:
        with self._session_factory() as session:
            step = session.scalar(
                select(ActionPlanStepRecord).where(
                    ActionPlanStepRecord.action_id == proposal.action_id,
                    ActionPlanStepRecord.action_version == proposal.version,
                )
            )
            if step is None:
                raise EmployeeOnboardingExecutionError(
                    "proposal is not attached to an action plan"
                )
            record = session.get(ActionPlanRecord, step.plan_record_id)
            if record is None:
                raise EmployeeOnboardingExecutionError("action plan does not exist")
            plan = ActionPlanRepository(session).get(record.plan_id, record.version)
            return ApprovalRepository(session).require_approved_plan_step(
                approval_id,
                plan,
                proposal,
            )


class EmployeeLifecycleLatestStateChecker:
    """Re-read authoritative facts immediately before every write."""

    def __init__(self, client: EmployeeLifecycleWritePort) -> None:
        self._client = client

    def require_current_and_executable(self, proposal: ActionProposal) -> None:
        parameters = proposal.parameters
        employee_id = str(parameters["subject_employee_id"])
        action_type = proposal.action_type
        profile = self._client.query_employee_lifecycle_profile(employee_id)
        request_type = str(parameters["request_type"])
        if action_type == "create_pending_employee":
            if profile is not None:
                raise EmployeeOnboardingExecutionError("employee already exists")
            department = self._client.query_organization_unit(
                str(parameters["department_code"])
            )
            job = self._client.query_job_profile(str(parameters["job_code"]))
            location = self._client.query_work_location(
                str(parameters["work_location_code"])
            )
            manager = self._client.query_employee_lifecycle_profile(
                str(parameters["manager_id"])
            )
            if (
                department is None
                or job is None
                or location is None
                or manager is None
                or not department.active
                or not job.active
                or not location.active
                or department.version
                != int(parameters["expected_department_version"])
                or job.version != int(parameters["expected_job_version"])
                or location.version
                != int(parameters["expected_work_location_version"])
                or job.department_code != department.department_code
                or manager.employee.employment_status != "ACTIVE"
                or manager.employee.department_code != department.department_code
            ):
                raise EmployeeOnboardingExecutionError(
                    "onboarding master data changed after approval"
                )
            return
        if profile is None:
            raise EmployeeOnboardingExecutionError("employee does not exist")
        if request_type == "TRANSFER":
            self._require_transfer_state(profile, action_type, parameters)
            return
        if request_type == "OFFBOARDING":
            self._require_offboarding_state(profile, action_type, parameters)
            return
        if (
            profile.employee.active
            or profile.employee.employment_status != "PENDING_ONBOARDING"
        ):
            raise EmployeeOnboardingExecutionError(
                "employee is no longer pending onboarding"
            )
        if action_type == "create_disabled_corporate_account":
            if profile.account is not None:
                raise EmployeeOnboardingExecutionError("account already exists")
        elif action_type == "assign_baseline_access_package":
            package = self._client.query_access_package(
                str(parameters["package_code"])
            )
            if (
                package is None
                or not package.active
                or package.version != int(parameters["package_version"])
                or list(package.role_bindings) != list(parameters["role_bindings"])
            ):
                raise EmployeeOnboardingExecutionError(
                    "baseline access package changed after approval"
                )
        elif action_type == "create_asset_assignment_task":
            if any(
                task.task_type == "PROVISION" and task.status != "CANCELLED"
                for task in profile.asset_tasks
            ):
                raise EmployeeOnboardingExecutionError(
                    "asset provision task already exists"
                )
        elif action_type == "activate_employee_and_account":
            self._require_activation_prerequisites(profile, parameters)

    def _require_transfer_state(
        self,
        profile: EnterpriseOpsEmployeeLifecycleProfile,
        action_type: str,
        parameters: dict[str, JsonValue],
    ) -> None:
        if not profile.employee.active or profile.employee.employment_status != "ACTIVE":
            raise EmployeeOnboardingExecutionError("transfer employee is not active")
        if action_type == "update_employee_assignment":
            department = self._client.query_organization_unit(
                str(parameters["department_code"])
            )
            job = self._client.query_job_profile(str(parameters["job_code"]))
            location = self._client.query_work_location(
                str(parameters["work_location_code"])
            )
            manager = self._client.query_employee_lifecycle_profile(
                str(parameters["manager_id"])
            )
            if (
                profile.employee.version
                != int(parameters["expected_employee_version"])
                or department is None
                or job is None
                or location is None
                or manager is None
                or department.version
                != int(parameters["expected_department_version"])
                or job.version != int(parameters["expected_job_version"])
                or location.version
                != int(parameters["expected_work_location_version"])
                or not department.active
                or not job.active
                or not location.active
                or job.department_code != department.department_code
                or manager.employee.department_code != department.department_code
            ):
                raise EmployeeOnboardingExecutionError(
                    "transfer master data changed after approval"
                )
        elif action_type == "revoke_obsolete_baseline_access":
            current = {
                (item.application_code, item.role_code)
                for item in profile.active_access
            }
            requested = {
                (str(item["application_code"]), str(item["role_code"]))
                for item in parameters["role_bindings"]
            }
            if not requested.issubset(current):
                raise EmployeeOnboardingExecutionError(
                    "obsolete access changed after approval"
                )
        elif action_type == "grant_target_baseline_access":
            package = self._client.query_access_package(
                str(parameters["package_code"])
            )
            requested = {
                (str(item["application_code"]), str(item["role_code"]))
                for item in parameters["role_bindings"]
            }
            allowed = {
                (str(item["application_code"]), str(item["role_code"]))
                for item in (package.role_bindings if package else ())
            }
            if (
                package is None
                or not package.active
                or package.version != int(parameters["package_version"])
                or not requested.issubset(allowed)
            ):
                raise EmployeeOnboardingExecutionError(
                    "target access changed after approval"
                )
        elif action_type == "create_asset_adjustment_task" and any(
            task.task_type == "ADJUST" and task.status != "CANCELLED"
            for task in profile.asset_tasks
        ):
            raise EmployeeOnboardingExecutionError(
                "asset adjustment task already exists"
            )

    @staticmethod
    def _require_offboarding_state(
        profile: EnterpriseOpsEmployeeLifecycleProfile,
        action_type: str,
        parameters: dict[str, JsonValue],
    ) -> None:
        if action_type == "verify_employee_offboarding_consistency":
            if (
                profile.employee.active
                or profile.employee.employment_status != "INACTIVE"
            ):
                raise EmployeeOnboardingExecutionError(
                    "employee is not inactive for final verification"
                )
            return
        if not profile.employee.active or profile.employee.employment_status != "ACTIVE":
            raise EmployeeOnboardingExecutionError("offboarding employee is not active")
        if action_type == "disable_corporate_account":
            expected = parameters.get("expected_account_version")
            if (expected is None) != (profile.account is None):
                raise EmployeeOnboardingExecutionError(
                    "corporate account changed after approval"
                )
            if (
                expected is not None
                and profile.account is not None
                and profile.account.version != int(expected)
            ):
                raise EmployeeOnboardingExecutionError("account version is stale")
        elif action_type == "revoke_all_employee_access":
            if {item.access_id for item in profile.active_access} != set(
                str(item) for item in parameters["access_ids"]
            ):
                raise EmployeeOnboardingExecutionError(
                    "active access changed after approval"
                )
        elif action_type == "create_asset_return_task":
            if {item.task_id for item in profile.asset_tasks} != set(
                str(item) for item in parameters["existing_asset_task_ids"]
            ) or any(
                task.task_type == "RETURN" and task.status != "CANCELLED"
                for task in profile.asset_tasks
            ):
                raise EmployeeOnboardingExecutionError(
                    "asset tasks changed after approval"
                )
        elif action_type == "mark_employee_inactive":
            if (
                profile.employee.version
                != int(parameters["expected_employee_version"])
                or (
                    profile.account is not None
                    and profile.account.status != "DISABLED"
                )
                or profile.active_access
            ):
                raise EmployeeOnboardingExecutionError(
                    "offboarding security prerequisites are incomplete"
                )

    @staticmethod
    def _require_activation_prerequisites(
        profile: EnterpriseOpsEmployeeLifecycleProfile,
        parameters: dict[str, JsonValue],
    ) -> None:
        if (
            profile.account is None
            or profile.account.status != "DISABLED"
            or profile.employee.version
            != int(parameters["expected_employee_version"])
            or profile.account.version != int(parameters["expected_account_version"])
            or not profile.active_access
            or not any(
                task.task_type == "PROVISION" and task.status == "OPEN"
                for task in profile.asset_tasks
            )
        ):
            raise EmployeeOnboardingExecutionError(
                "onboarding prerequisites are incomplete"
            )


class EmployeeLifecycleWriteTool:
    """Dispatch one approved proposal to exactly one MCP write capability."""

    name = "enterprise_ops.employee_lifecycle_step"

    def __init__(self, client: EmployeeLifecycleWritePort) -> None:
        self._client = client

    def execute(
        self,
        proposal: ActionProposal,
        *,
        idempotency_key: str,
    ) -> ToolExecutionResult:
        parameters = proposal.parameters
        payload = self._tool_payload(proposal.action_type, parameters)
        method = getattr(self._client, proposal.action_type)
        try:
            result = method(
                payload,
                idempotency_key=idempotency_key,
            )
        except EnterpriseOpsRejectedError as exc:
            return ToolExecutionResult(
                status=ToolExecutionStatus.FAILED,
                details={"error_type": type(exc).__name__},
            )
        return ToolExecutionResult(
            status=ToolExecutionStatus.SUCCEEDED,
            external_reference=result.resource_id,
            details={
                "action_type": result.action_type,
                "employee_id": result.employee_id,
                "employee_version": result.employee_version,
                "account_version": result.account_version,
                "enterprise_replayed": result.replayed,
            },
        )

    @staticmethod
    def _tool_payload(
        action_type: str,
        parameters: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        employee_id = parameters["subject_employee_id"]
        common = {"employee_id": employee_id}
        if action_type == "create_pending_employee":
            keys = (
                "display_name",
                "department_code",
                "job_code",
                "manager_id",
                "work_location_code",
                "initiator_id",
                "effective_date",
                "business_reason",
                "expected_department_version",
                "expected_job_version",
                "expected_work_location_version",
            )
        elif action_type == "create_disabled_corporate_account":
            keys = ()
        elif action_type in {
            "assign_baseline_access_package",
            "grant_target_baseline_access",
        }:
            keys = ("package_code", "package_version", "role_bindings")
        elif action_type in {
            "create_asset_assignment_task",
            "create_asset_adjustment_task",
        }:
            keys = ("asset_profile_code", "task_type")
        elif action_type == "activate_employee_and_account":
            keys = ("expected_employee_version", "expected_account_version")
        elif action_type == "update_employee_assignment":
            keys = (
                "department_code",
                "job_code",
                "manager_id",
                "work_location_code",
                "initiator_id",
                "effective_date",
                "business_reason",
                "expected_employee_version",
                "expected_department_version",
                "expected_job_version",
                "expected_work_location_version",
            )
        elif action_type == "revoke_obsolete_baseline_access":
            keys = ("role_bindings",)
        elif action_type == "verify_employee_transfer_consistency":
            keys = (
                "expected_department_code",
                "expected_job_code",
                "expected_manager_id",
                "expected_work_location_code",
            )
        elif action_type == "disable_corporate_account":
            keys = (
                "initiator_id",
                "effective_date",
                "business_reason",
                "expected_account_version",
            )
        elif action_type == "revoke_all_employee_access":
            keys = ("access_ids",)
        elif action_type == "create_asset_return_task":
            keys = (
                "task_type",
                "asset_return_note",
                "existing_asset_task_ids",
            )
        elif action_type == "mark_employee_inactive":
            keys = (
                "target_status",
                "expected_employee_version",
                "offboarding_reason",
            )
        elif action_type == "verify_employee_offboarding_consistency":
            keys = (
                "expected_employee_status",
                "expected_account_status",
                "expected_active_access_count",
            )
        else:
            raise ValueError(f"unsupported employee lifecycle action: {action_type}")
        return {**common, **{key: parameters[key] for key in keys}}


class EmployeeLifecycleVerifier:
    """Verify each write through the independent lifecycle read projection."""

    def __init__(self, client: EmployeeLifecycleWritePort) -> None:
        self._client = client

    def verify(
        self,
        proposal: ActionProposal,
        tool_result: ToolExecutionResult,
    ) -> VerificationResult:
        parameters = proposal.parameters
        employee_id = str(parameters["subject_employee_id"])
        profile = self._client.query_employee_lifecycle_profile(employee_id)
        if profile is None:
            return VerificationResult(
                confirmed=False, details={"reason": "employee_not_found"}
            )
        action_type = proposal.action_type
        if action_type == "create_pending_employee":
            confirmed = (
                not profile.employee.active
                and profile.employee.employment_status == "PENDING_ONBOARDING"
                and profile.employee.department_code == parameters["department_code"]
                and profile.employee.job_code == parameters["job_code"]
                and profile.employee.manager_id == parameters["manager_id"]
                and profile.employee.work_location_code
                == parameters["work_location_code"]
            )
        elif action_type == "create_disabled_corporate_account":
            confirmed = profile.account is not None and profile.account.status == "DISABLED"
        elif action_type == "assign_baseline_access_package":
            expected = {
                (str(item["application_code"]), str(item["role_code"]))
                for item in parameters["role_bindings"]
            }
            actual = {
                (item.application_code, item.role_code)
                for item in profile.active_access
            }
            confirmed = actual == expected
        elif action_type == "create_asset_assignment_task":
            confirmed = any(
                task.task_type == "PROVISION"
                and task.status == "OPEN"
                and task.asset_profile_code == parameters["asset_profile_code"]
                for task in profile.asset_tasks
            )
        elif action_type == "activate_employee_and_account":
            confirmed = (
                profile.employee.active
                and profile.employee.employment_status == "ACTIVE"
                and profile.account is not None
                and profile.account.status == "ACTIVE"
                and not profile.open_lifecycle_requests
            )
        elif action_type == "update_employee_assignment":
            confirmed = (
                profile.employee.active
                and profile.employee.department_code == parameters["department_code"]
                and profile.employee.job_code == parameters["job_code"]
                and profile.employee.manager_id == parameters["manager_id"]
                and profile.employee.work_location_code
                == parameters["work_location_code"]
                and profile.employee.version
                == int(parameters["expected_employee_version"]) + 1
            )
        elif action_type in {
            "revoke_obsolete_baseline_access",
            "grant_target_baseline_access",
            "verify_employee_transfer_consistency",
        }:
            job = self._client.query_job_profile(profile.employee.job_code)
            package = (
                self._client.query_access_package(
                    job.baseline_access_package_code
                )
                if job
                else None
            )
            expected = {
                (str(item["application_code"]), str(item["role_code"]))
                for item in (package.role_bindings if package else ())
            }
            actual = {
                (item.application_code, item.role_code)
                for item in profile.active_access
            }
            if action_type == "revoke_obsolete_baseline_access":
                revoked = {
                    (str(item["application_code"]), str(item["role_code"]))
                    for item in parameters["role_bindings"]
                }
                confirmed = not (actual & revoked)
            elif action_type == "grant_target_baseline_access":
                granted = {
                    (str(item["application_code"]), str(item["role_code"]))
                    for item in parameters["role_bindings"]
                }
                confirmed = granted.issubset(actual)
            else:
                confirmed = (
                    actual == expected
                    and profile.employee.department_code
                    == parameters["expected_department_code"]
                    and profile.employee.job_code == parameters["expected_job_code"]
                    and profile.employee.manager_id
                    == parameters["expected_manager_id"]
                    and profile.employee.work_location_code
                    == parameters["expected_work_location_code"]
                    and not profile.open_lifecycle_requests
                )
        elif action_type == "create_asset_adjustment_task":
            confirmed = any(
                task.task_type == "ADJUST"
                and task.status == "OPEN"
                and task.asset_profile_code == parameters["asset_profile_code"]
                for task in profile.asset_tasks
            )
        elif action_type == "disable_corporate_account":
            confirmed = profile.account is None or profile.account.status == "DISABLED"
        elif action_type == "revoke_all_employee_access":
            confirmed = not profile.active_access
        elif action_type == "create_asset_return_task":
            confirmed = any(
                task.task_type == "RETURN" and task.status == "OPEN"
                for task in profile.asset_tasks
            )
        elif action_type == "mark_employee_inactive":
            confirmed = (
                not profile.employee.active
                and profile.employee.employment_status == "INACTIVE"
                and (profile.account is None or profile.account.status == "DISABLED")
                and not profile.active_access
            )
        else:
            confirmed = (
                not profile.employee.active
                and profile.employee.employment_status
                == parameters["expected_employee_status"]
                and (profile.account is None or profile.account.status
                     == parameters["expected_account_status"])
                and len(profile.active_access)
                == int(parameters["expected_active_access_count"])
                and not profile.open_lifecycle_requests
            )
        return VerificationResult(
            confirmed=confirmed,
            details={
                "action_type": action_type,
                "employee_status": profile.employee.employment_status,
                "employee_version": profile.employee.version,
                "account_status": profile.account.status if profile.account else None,
                "account_version": profile.account.version if profile.account else None,
            },
        )


class EmployeeLifecycleExecutionResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    workflow_run_id: str
    workflow_state: WorkflowState
    workflow_version: int
    completed_steps: int
    human_review_reason: str | None = None


class EmployeeLifecyclePlanExecutionService:
    """Execute and persist one approved lifecycle plan step by step."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        gateway: ActionGatewayPort,
    ) -> None:
        self._session_factory = session_factory
        self._gateway = gateway
        self._activity = ExecutionActivityRegistry()

    def is_active(self, workflow_run_id: str) -> bool:
        return self._activity.is_active(workflow_run_id)

    def execute(
        self,
        *,
        workflow_run_id: str,
        expected_workflow_version: int,
        plan_id: str,
        plan_version: int,
        approval_id: str,
        actor_id: str,
    ) -> EmployeeLifecycleExecutionResult:
        with self._activity.claim(workflow_run_id):
            return self._execute_once(
                workflow_run_id=workflow_run_id,
                expected_workflow_version=expected_workflow_version,
                plan_id=plan_id,
                plan_version=plan_version,
                approval_id=approval_id,
                actor_id=actor_id,
            )

    def _execute_once(
        self,
        *,
        workflow_run_id: str,
        expected_workflow_version: int,
        plan_id: str,
        plan_version: int,
        approval_id: str,
        actor_id: str,
    ) -> EmployeeLifecycleExecutionResult:
        with self._session_factory.begin() as session:
            workflows = WorkflowRepository(session)
            workflow = workflows.get(workflow_run_id)
            if workflow.version != expected_workflow_version or workflow.workflow_state not in {
                WorkflowState.RUNNING,
                WorkflowState.EXECUTING,
            }:
                raise ActionPlanVersionError(
                    "workflow is not at the approved execution checkpoint"
                )
            plans = ActionPlanRepository(session)
            plan = plans.get(plan_id, plan_version)
            record = plans.get_record(plan_id, plan_version)
            if (
                record.workflow_run_id != workflow_run_id
                or plan.plan_type not in _ACTIONS_BY_REQUEST_TYPE
            ):
                raise ActionPlanVersionError("plan does not belong to onboarding flow")
            plans.begin_execution(plan_id, plan_version)
            if workflow.workflow_state is WorkflowState.RUNNING:
                workflow = workflows.transition(
                    workflow_run_id,
                    expected_version=workflow.version,
                    target=WorkflowState.EXECUTING,
                    event_type="EMPLOYEE_LIFECYCLE_PLAN_EXECUTION_STARTED",
                    payload={
                        "action_plan_id": plan_id,
                        "action_plan_version": plan_version,
                    },
                )
            executing_version = workflow.version

        while True:
            interrupted_step_id: str | None = None
            with self._session_factory.begin() as session:
                plans = ActionPlanRepository(session)
                record = plans.get_record(plan_id, plan_version)
                executing = next(
                    (
                        item
                        for item in record.steps
                        if item.status == ActionPlanStepStatus.EXECUTING.value
                    ),
                    None,
                )
                if executing is not None:
                    interrupted_step_id = executing.step_id
                    step_record = None
                else:
                    step_record = next(
                    (
                        item
                        for item in record.steps
                        if item.status == ActionPlanStepStatus.PENDING.value
                    ),
                    None,
                )
                if step_record is None:
                    if interrupted_step_id is None:
                        plans.complete_execution(plan_id, plan_version)
                        workflow = WorkflowRepository(session).transition(
                            workflow_run_id,
                            expected_version=executing_version,
                            target=WorkflowState.COMPLETED,
                            event_type="EMPLOYEE_LIFECYCLE_PLAN_COMPLETED",
                            payload={"action_plan_id": plan_id},
                        )
                        return EmployeeLifecycleExecutionResult(
                            workflow_run_id=workflow.id,
                            workflow_state=workflow.workflow_state,
                            workflow_version=workflow.version,
                            completed_steps=len(record.steps),
                        )
                else:
                    plans.start_step(plan_id, plan_version, step_record.step_id)
                    proposal = plan.steps[step_record.step_order - 1].proposal
                    step_id = step_record.step_id
                    step_order = step_record.step_order

            if interrupted_step_id is not None:
                return self._require_human(
                    workflow_run_id,
                    executing_version,
                    plan_id,
                    plan_version,
                    interrupted_step_id,
                    "INTERRUPTED_STEP_REQUIRES_RECONCILIATION",
                )

            try:
                gateway_result = self._gateway.execute(
                    proposal,
                    actor_id=actor_id,
                    approval_id=approval_id,
                    idempotency_key=(
                        f"lifecycle:{plan_id}:v{plan_version}:step:{step_order}"
                    ),
                )
            except Exception as exc:
                return self._require_human(
                    workflow_run_id,
                    executing_version,
                    plan_id,
                    plan_version,
                    step_id,
                    type(exc).__name__,
                    step_status=ActionPlanStepStatus.FAILED,
                )

            status_by_outcome = {
                ActionGatewayOutcome.SUCCEEDED: ActionPlanStepStatus.SUCCEEDED,
                ActionGatewayOutcome.REPLAYED: ActionPlanStepStatus.REPLAYED,
                ActionGatewayOutcome.TOOL_FAILED: ActionPlanStepStatus.FAILED,
                ActionGatewayOutcome.RESULT_UNKNOWN: ActionPlanStepStatus.RESULT_UNKNOWN,
                ActionGatewayOutcome.VERIFICATION_FAILED: (
                    ActionPlanStepStatus.VERIFICATION_FAILED
                ),
            }
            step_status = status_by_outcome[gateway_result.outcome]
            if step_status not in {
                ActionPlanStepStatus.SUCCEEDED,
                ActionPlanStepStatus.REPLAYED,
            }:
                return self._require_human(
                    workflow_run_id,
                    executing_version,
                    plan_id,
                    plan_version,
                    step_id,
                    gateway_result.outcome.value,
                    step_status=step_status,
                )
            with self._session_factory.begin() as session:
                ActionPlanRepository(session).finish_step(
                    plan_id,
                    plan_version,
                    step_id,
                    status=step_status,
                )

    def _require_human(
        self,
        workflow_run_id: str,
        workflow_version: int,
        plan_id: str,
        plan_version: int,
        step_id: str,
        reason: str,
        *,
        step_status: ActionPlanStepStatus | None = None,
    ) -> EmployeeLifecycleExecutionResult:
        with self._session_factory.begin() as session:
            plans = ActionPlanRepository(session)
            if step_status is not None:
                plans.finish_step(
                    plan_id,
                    plan_version,
                    step_id,
                    status=step_status,
                    error_code=reason,
                )
            plans.require_human(plan_id, plan_version)
            record = plans.get_record(plan_id, plan_version)
            workflow = WorkflowRepository(session).transition(
                workflow_run_id,
                expected_version=workflow_version,
                target=WorkflowState.WAITING_HUMAN,
                event_type="EMPLOYEE_LIFECYCLE_PLAN_REQUIRES_HUMAN",
                payload={
                    "action_plan_id": plan_id,
                    "step_id": step_id,
                    "reason": reason,
                },
            )
            completed = sum(
                item.status
                in {
                    ActionPlanStepStatus.SUCCEEDED.value,
                    ActionPlanStepStatus.REPLAYED.value,
                }
                for item in record.steps
            )
            return EmployeeLifecycleExecutionResult(
                workflow_run_id=workflow.id,
                workflow_state=workflow.workflow_state,
                workflow_version=workflow.version,
                completed_steps=completed,
                human_review_reason=reason,
            )
