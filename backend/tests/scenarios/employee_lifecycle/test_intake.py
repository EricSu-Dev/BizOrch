from datetime import UTC, date, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.agents.contracts import AgentIntent, SupervisorPlan
from app.agents.scenarios import ScenarioActor, ScenarioPayloadValidationError
from app.auth.service import InsufficientRoleError
from app.integrations.enterprise_ops import (
    EnterpriseOpsAccessPackage,
    EnterpriseOpsEmployeeLifecycleProfile,
    EnterpriseOpsJobProfile,
    EnterpriseOpsLifecycleEmployee,
    EnterpriseOpsOrganizationUnit,
    EnterpriseOpsWorkLocation,
)
from app.persistence.base import Base
from app.scenarios.employee_lifecycle.agent import (
    EmployeeLifecycleDomainAgent,
    EmployeeLifecycleScenarioHandler,
)
from app.scenarios.employee_lifecycle.commands import (
    EmployeeLifecycleActorMismatchError,
    EmployeeLifecycleIntakeService,
)
from app.scenarios.employee_lifecycle.context import (
    EmployeeLifecycleContextResolver,
)
from app.scenarios.employee_lifecycle.contracts import (
    EmployeeLifecycleRequestDraft,
    EmployeeLifecycleRequestType,
    EmployeeLifecycleContext,
)
from app.tickets.service import TicketProjectionService
from app.workflow.state import WorkflowState


class ReadOnlyLifecycleClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def query_employee_lifecycle_profile(self, employee_id: str):
        self.calls.append(("employee", employee_id))
        if employee_id == "EMP-3001" or employee_id not in {
            "EMP-MANAGER",
            "EMP-2001",
        }:
            return None
        department = (
            "PRODUCTION-MGMT"
            if employee_id == "EMP-MANAGER"
            else "PLANT-WORKSHOP-1"
        )
        return lifecycle_profile(
            employee_id,
            department_code=department,
            manager_id=(
                None if employee_id == "EMP-MANAGER" else "EMP-MANAGER"
            ),
        )

    def query_organization_unit(self, department_code: str):
        self.calls.append(("department", department_code))
        if department_code != "PRODUCTION-MGMT":
            return None
        return EnterpriseOpsOrganizationUnit(
            department_code=department_code,
            display_name="生产管理部",
            manager_id="EMP-MANAGER",
            active=True,
            version=1,
        )

    def query_job_profile(self, job_code: str):
        self.calls.append(("job", job_code))
        if job_code != "PRODUCTION-PLANNER":
            return None
        return EnterpriseOpsJobProfile(
            job_code=job_code,
            display_name="生产计划专员",
            department_code="PRODUCTION-MGMT",
            baseline_access_package_code="PRODUCTION-PLANNER",
            asset_profile_code="OFFICE-LAPTOP",
            active=True,
            version=1,
        )

    def query_access_package(self, package_code: str):
        self.calls.append(("package", package_code))
        if package_code != "PRODUCTION-PLANNER":
            return None
        return EnterpriseOpsAccessPackage(
            package_code=package_code,
            display_name="生产计划岗位标准权限",
            role_bindings=(
                {"application_code": "ERP", "role_code": "standard"},
            ),
            version=1,
            active=True,
        )

    def query_work_location(self, location_code: str):
        self.calls.append(("work_location", location_code))
        if location_code not in {"SHANGHAI-HQ", "PLANT-EAST"}:
            return None
        return EnterpriseOpsWorkLocation(
            location_code=location_code,
            display_name=location_code,
            active=True,
            version=1,
        )

    def query_open_employee_lifecycle_request(self, employee_id: str):
        self.calls.append(("open_requests", employee_id))
        return ()


def lifecycle_profile(
    employee_id: str,
    *,
    department_code: str,
    manager_id: str | None,
) -> EnterpriseOpsEmployeeLifecycleProfile:
    return EnterpriseOpsEmployeeLifecycleProfile(
        employee=EnterpriseOpsLifecycleEmployee(
            employee_id=employee_id,
            display_name=employee_id,
            department_code=department_code,
            manager_id=manager_id,
            active=True,
            employment_status="ACTIVE",
            job_code="PLANT-OPERATOR",
            work_location_code="PLANT-EAST",
            version=1,
            updated_at=datetime(2026, 7, 23, tzinfo=UTC),
        ),
        department=EnterpriseOpsOrganizationUnit(
            department_code=department_code,
            display_name=department_code,
            manager_id=manager_id or employee_id,
            active=True,
            version=1,
        ),
        job=EnterpriseOpsJobProfile(
            job_code="PLANT-OPERATOR",
            display_name="生产操作员",
            department_code=department_code,
            baseline_access_package_code="PLANT-OPERATOR",
            asset_profile_code="SHOP-FLOOR-TERMINAL",
            active=True,
            version=1,
        ),
        account=None,
        active_access=(),
        asset_tasks=(),
        open_lifecycle_requests=(),
    )


def build_handler(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'lifecycle.db'}")
    Base.metadata.create_all(engine)
    sessions: sessionmaker[Session] = sessionmaker(
        bind=engine,
        expire_on_commit=False,
    )
    commands = EmployeeLifecycleIntakeService(
        sessions,
        TicketProjectionService(sessions),
        today_provider=lambda: date(2026, 7, 23),
    )
    client = ReadOnlyLifecycleClient()
    handler = EmployeeLifecycleScenarioHandler(
        EmployeeLifecycleDomainAgent(
            EmployeeLifecycleContextResolver(client)
        ),
        commands,
        today_provider=lambda: date(2026, 7, 23),
    )
    return commands, client, handler


def test_required_fields_are_specific_to_each_lifecycle_intent() -> None:
    onboarding = EmployeeLifecycleRequestDraft(
        request_type=EmployeeLifecycleRequestType.ONBOARDING,
        initiator_id="EMP-HR-OPERATOR",
    )
    transfer = EmployeeLifecycleRequestDraft(
        request_type=EmployeeLifecycleRequestType.TRANSFER,
        initiator_id="EMP-HR-OPERATOR",
    )
    offboarding = EmployeeLifecycleRequestDraft(
        request_type=EmployeeLifecycleRequestType.OFFBOARDING,
        initiator_id="EMP-HR-OPERATOR",
    )

    assert "display_name" in onboarding.missing_fields()
    assert "display_name" not in transfer.missing_fields()
    assert "offboarding_reason" in offboarding.missing_fields()
    assert "target_job_code" not in offboarding.missing_fields()


def test_multiturn_supplement_resumes_same_workflow_and_preserves_actor(tmp_path) -> None:
    commands, client, handler = build_handler(tmp_path)
    actor = ScenarioActor(
        actor_id="EMP-HR-OPERATOR",
        roles=frozenset({"hr"}),
    )
    first = handler.handle(
        SupervisorPlan(
            intent=AgentIntent.EMPLOYEE_ONBOARDING,
            scenario_key="employee_lifecycle",
            scenario_payload={
                "subject_employee_id": "EMP-3001",
                "display_name": "王晨",
                "target_department_code": "PRODUCTION-MGMT",
            },
        ),
        actor=actor,
        request_id="lifecycle-run-1",
        workflow_run_id=None,
    )

    assert first.workflow.workflow_state is WorkflowState.WAITING_USER
    assert first.workflow.workflow_run_id == "lifecycle-run-1"
    assert first.workflow.ticket_id is not None
    assert first.scenario_summary["initiator_id"] == "EMP-HR-OPERATOR"
    assert first.scenario_summary["subject_employee_id"] == "EMP-3001"
    assert ("employee", "EMP-3001") in client.calls

    second = handler.handle(
        SupervisorPlan(
            # A vague supplement may be misclassified; the persisted request type wins.
            intent=AgentIntent.UNKNOWN,
            scenario_key="employee_lifecycle",
            scenario_payload={
                "target_job_code": "PRODUCTION-PLANNER",
                "target_manager_id": "EMP-MANAGER",
                "work_location_code": "SHANGHAI-HQ",
                "effective_date": "2026-07-23",
                "business_reason": "完成已审批招聘计划的入职办理",
            },
        ),
        actor=actor,
        request_id="turn-2",
        workflow_run_id=first.workflow.workflow_run_id,
    )

    assert second.resolved_intent is AgentIntent.EMPLOYEE_ONBOARDING
    assert second.workflow.workflow_run_id == first.workflow.workflow_run_id
    assert second.workflow.service_request_id == first.workflow.service_request_id
    assert second.workflow.ticket_id == first.workflow.ticket_id
    assert second.workflow.workflow_state is WorkflowState.WAITING_APPROVAL
    assert second.workflow.missing_fields == ()
    assert second.workflow.action_plan_id is not None
    assert second.workflow.action_plan_version == 1
    assert second.workflow.approval_id is not None
    plan = commands.current_action_plan(
        first.workflow.workflow_run_id,
        actor_id="EMP-HR-OPERATOR",
    )
    assert plan is not None
    assert plan.plan_type == "ONBOARDING"
    assert len(plan.steps) == 5
    persisted = commands.current_draft(
        first.workflow.workflow_run_id,
        actor_id="EMP-HR-OPERATOR",
    )
    assert persisted.initiator_id == "EMP-HR-OPERATOR"
    assert persisted.subject_employee_id == "EMP-3001"
    assert persisted.request_type is EmployeeLifecycleRequestType.ONBOARDING


def test_onboarding_normalizes_explicit_display_names_and_today(tmp_path) -> None:
    commands, _, handler = build_handler(tmp_path)
    actor = ScenarioActor(
        actor_id="EMP-HR-OPERATOR",
        roles=frozenset({"hr"}),
    )

    result = handler.handle(
        SupervisorPlan(
            intent=AgentIntent.EMPLOYEE_ONBOARDING,
            scenario_key="employee_lifecycle",
            scenario_payload={
                "subject_employee_id": "EMP-3001",
                "display_name": "王晨",
                "target_department_code": "生产管理部",
                "target_job_code": "生产计划专员",
                "target_manager_id": "EMP-MANAGER",
                "work_location_code": "上海总部",
            },
        ),
        actor=actor,
        request_id="onboarding-display-name-aliases",
        workflow_run_id=None,
        message=(
            "请为新员工 EMP-3001 王晨办理今天入职，部门是生产管理部，"
            "岗位是生产计划专员，直属负责人是 EMP-MANAGER，办公地点为上海总部。"
        ),
    )

    assert result.workflow.workflow_state is WorkflowState.WAITING_USER
    assert result.workflow.missing_fields == ("business_reason",)
    assert result.authoritative_reply is not None
    assert "待新建员工" in result.authoritative_reply
    assert "业务理由" in result.authoritative_reply
    draft = commands.current_draft(
        result.workflow.workflow_run_id,
        actor_id="EMP-HR-OPERATOR",
    )
    assert draft.target_department_code == "PRODUCTION-MGMT"
    assert draft.target_job_code == "PRODUCTION-PLANNER"
    assert draft.work_location_code == "SHANGHAI-HQ"
    assert draft.effective_date == date(2026, 7, 23)


def test_supplement_recovers_persisted_display_name_aliases(tmp_path) -> None:
    commands, client, handler = build_handler(tmp_path)
    actor = ScenarioActor(
        actor_id="EMP-HR-OPERATOR",
        roles=frozenset({"hr"}),
    )
    stale_draft = EmployeeLifecycleRequestDraft(
        request_type=EmployeeLifecycleRequestType.ONBOARDING,
        initiator_id=actor.actor_id,
        subject_employee_id="EMP-3001",
        display_name="王晨",
        target_department_code="生产管理部",
        target_job_code="生产计划专员",
        target_manager_id="EMP-MANAGER",
        work_location_code="上海总部",
        effective_date=date(2026, 7, 23),
    )
    waiting = commands.create(
        stale_draft,
        context=EmployeeLifecycleContextResolver(client).resolve(stale_draft),
        actor_id=actor.actor_id,
        run_id="persisted-display-name-aliases",
    )

    result = handler.handle(
        SupervisorPlan(
            intent=AgentIntent.UNKNOWN,
            scenario_key="employee_lifecycle",
            scenario_payload={
                "business_reason": "完成已审批招聘计划的入职办理",
            },
        ),
        actor=actor,
        request_id="persisted-display-name-aliases-turn-2",
        workflow_run_id=waiting.workflow_run_id,
        message="业务理由：完成已审批招聘计划的入职办理。",
    )

    assert result.workflow.workflow_state is WorkflowState.WAITING_APPROVAL
    recovered = commands.current_draft(
        waiting.workflow_run_id,
        actor_id=actor.actor_id,
    )
    assert recovered.target_department_code == "PRODUCTION-MGMT"
    assert recovered.target_job_code == "PRODUCTION-PLANNER"
    assert recovered.work_location_code == "SHANGHAI-HQ"


def test_supplement_cannot_overwrite_established_subject(tmp_path) -> None:
    _, _, handler = build_handler(tmp_path)
    actor = ScenarioActor(
        actor_id="EMP-HR-OPERATOR",
        roles=frozenset({"hr"}),
    )
    first = handler.handle(
        SupervisorPlan(
            intent=AgentIntent.EMPLOYEE_OFFBOARDING,
            scenario_key="employee_lifecycle",
            scenario_payload={"subject_employee_id": "EMP-2001"},
        ),
        actor=actor,
        request_id="offboarding-run-1",
        workflow_run_id=None,
    )

    with pytest.raises(ScenarioPayloadValidationError):
        handler.handle(
            SupervisorPlan(
                intent=AgentIntent.EMPLOYEE_OFFBOARDING,
                scenario_key="employee_lifecycle",
                scenario_payload={"subject_employee_id": "EMP-1001"},
            ),
            actor=actor,
            request_id="turn-2",
            workflow_run_id=first.workflow.workflow_run_id,
        )


def test_invalid_enterprise_codes_can_be_corrected_in_same_workflow(tmp_path) -> None:
    _, _, handler = build_handler(tmp_path)
    actor = ScenarioActor(
        actor_id="EMP-HR-OPERATOR",
        roles=frozenset({"hr"}),
    )
    first = handler.handle(
        SupervisorPlan(
            intent=AgentIntent.EMPLOYEE_ONBOARDING,
            scenario_key="employee_lifecycle",
            scenario_payload={
                "subject_employee_id": "EMP-3001",
                "display_name": "王晨",
                "target_department_code": "BAD-DEPARTMENT",
                "target_job_code": "BAD-JOB",
                "target_manager_id": "BAD-MANAGER",
                "work_location_code": "SHANGHAI-HQ",
                "effective_date": "2026-07-23",
                "business_reason": "执行已审批招聘计划的入职办理",
            },
        ),
        actor=actor,
        request_id="correction-run",
        workflow_run_id=None,
    )

    assert first.workflow.workflow_state is WorkflowState.WAITING_USER
    assert set(first.workflow.missing_fields) == {
        "target_department_code",
        "target_job_code",
        "target_manager_id",
    }

    corrected = handler.handle(
        SupervisorPlan(
            intent=AgentIntent.EMPLOYEE_ONBOARDING,
            scenario_key="employee_lifecycle",
            scenario_payload={
                "target_department_code": "PRODUCTION-MGMT",
                "target_job_code": "PRODUCTION-PLANNER",
                "target_manager_id": "EMP-MANAGER",
            },
        ),
        actor=actor,
        request_id="correction-turn-2",
        workflow_run_id=first.workflow.workflow_run_id,
    )

    assert corrected.workflow.workflow_run_id == first.workflow.workflow_run_id
    assert corrected.workflow.workflow_state is WorkflowState.WAITING_APPROVAL
    assert corrected.workflow.action_plan_id is not None


def test_employee_and_operator_roles_cannot_start_lifecycle_workflow(tmp_path) -> None:
    _, _, handler = build_handler(tmp_path)

    for role in ("employee", "operator"):
        with pytest.raises(InsufficientRoleError):
            handler.handle(
                SupervisorPlan(
                    intent=AgentIntent.EMPLOYEE_TRANSFER,
                    scenario_key="employee_lifecycle",
                    scenario_payload={"subject_employee_id": "EMP-2001"},
                ),
                actor=ScenarioActor(
                    actor_id=f"ACTOR-{role}",
                    roles=frozenset({role}),
                ),
                request_id=f"run-{role}",
                workflow_run_id=None,
            )


def test_command_service_rejects_another_hr_actor(tmp_path) -> None:
    commands, _, _ = build_handler(tmp_path)
    snapshot = commands.create(
        EmployeeLifecycleRequestDraft(
            request_type=EmployeeLifecycleRequestType.OFFBOARDING,
            initiator_id="EMP-HR-OPERATOR",
            subject_employee_id="EMP-2001",
            effective_date=date(2026, 7, 23),
            offboarding_reason="劳动合同到期",
            business_reason="执行已确认的离职手续",
        ),
        context=EmployeeLifecycleContext(
            subject_profile=lifecycle_profile(
                "EMP-2001",
                department_code="PLANT-WORKSHOP-1",
                manager_id="EMP-MANAGER",
            ),
            approval_manager_profile=lifecycle_profile(
                "EMP-MANAGER",
                department_code="PRODUCTION-MGMT",
                manager_id=None,
            ),
        ),
        actor_id="EMP-HR-OPERATOR",
        run_id="owned-run",
    )
    assert snapshot.workflow_state is WorkflowState.WAITING_APPROVAL

    with pytest.raises(EmployeeLifecycleActorMismatchError):
        commands.get("owned-run", actor_id="EMP-OTHER-HR")
