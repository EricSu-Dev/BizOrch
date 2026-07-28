from datetime import UTC, date, datetime

from app.integrations.enterprise_ops import (
    EnterpriseOpsAccessPackage,
    EnterpriseOpsEmployeeLifecycleRequest,
    EnterpriseOpsJobProfile,
    EnterpriseOpsOrganizationUnit,
    EnterpriseOpsWorkLocation,
)
from app.policy.contracts import PolicyOutcome
from app.scenarios.employee_lifecycle.contracts import (
    EmployeeLifecycleContext,
    EmployeeLifecycleRequestDraft,
    EmployeeLifecycleRequestType,
)
from app.scenarios.employee_lifecycle.policy import EmployeeLifecyclePolicyEngine
from tests.scenarios.employee_lifecycle.test_intake import lifecycle_profile


TODAY = date(2026, 7, 23)


def onboarding_draft(**overrides: object) -> EmployeeLifecycleRequestDraft:
    values: dict[str, object] = {
        "request_type": EmployeeLifecycleRequestType.ONBOARDING,
        "initiator_id": "EMP-HR-OPERATOR",
        "subject_employee_id": "EMP-3001",
        "display_name": "王晨",
        "target_department_code": "PRODUCTION-MGMT",
        "target_job_code": "PRODUCTION-PLANNER",
        "target_manager_id": "EMP-MANAGER",
        "work_location_code": "SHANGHAI-HQ",
        "effective_date": TODAY,
        "business_reason": "执行已审批招聘计划的入职办理",
    }
    values.update(overrides)
    return EmployeeLifecycleRequestDraft(**values)


def transfer_draft(**overrides: object) -> EmployeeLifecycleRequestDraft:
    values: dict[str, object] = {
        "request_type": EmployeeLifecycleRequestType.TRANSFER,
        "initiator_id": "EMP-HR-OPERATOR",
        "subject_employee_id": "EMP-2001",
        "target_department_code": "PRODUCTION-MGMT",
        "target_job_code": "PRODUCTION-PLANNER",
        "target_manager_id": "EMP-MANAGER",
        "work_location_code": "SHANGHAI-HQ",
        "effective_date": TODAY,
        "business_reason": "执行已审批组织调整方案",
    }
    values.update(overrides)
    return EmployeeLifecycleRequestDraft(**values)


def offboarding_draft(**overrides: object) -> EmployeeLifecycleRequestDraft:
    values: dict[str, object] = {
        "request_type": EmployeeLifecycleRequestType.OFFBOARDING,
        "initiator_id": "EMP-HR-OPERATOR",
        "subject_employee_id": "EMP-2001",
        "effective_date": TODAY,
        "offboarding_reason": "劳动合同到期",
        "business_reason": "执行已确认的员工离职手续",
    }
    values.update(overrides)
    return EmployeeLifecycleRequestDraft(**values)


def valid_target_context(
    *,
    subject=None,
    open_requests=(),
) -> EmployeeLifecycleContext:
    manager = lifecycle_profile(
        "EMP-MANAGER",
        department_code="PRODUCTION-MGMT",
        manager_id=None,
    )
    return EmployeeLifecycleContext(
        subject_profile=subject,
        target_department=EnterpriseOpsOrganizationUnit(
            department_code="PRODUCTION-MGMT",
            display_name="生产管理部",
            manager_id="EMP-MANAGER",
            active=True,
            version=2,
        ),
        target_job=EnterpriseOpsJobProfile(
            job_code="PRODUCTION-PLANNER",
            display_name="生产计划专员",
            department_code="PRODUCTION-MGMT",
            baseline_access_package_code="PRODUCTION-PLANNER",
            asset_profile_code="OFFICE-LAPTOP",
            active=True,
            version=3,
        ),
        work_location=EnterpriseOpsWorkLocation(
            location_code="SHANGHAI-HQ",
            display_name="上海总部",
            active=True,
            version=2,
        ),
        target_manager_profile=manager,
        approval_manager_profile=manager,
        baseline_access_package=EnterpriseOpsAccessPackage(
            package_code="PRODUCTION-PLANNER",
            display_name="生产计划岗位标准权限",
            role_bindings=(
                {"application_code": "ERP", "role_code": "standard"},
            ),
            version=4,
            active=True,
        ),
        open_requests=open_requests,
    )


def test_normal_onboarding_routes_to_authoritative_manager_approval() -> None:
    decision = EmployeeLifecyclePolicyEngine().evaluate(
        onboarding_draft(),
        valid_target_context(),
        today=TODAY,
    )

    assert decision.outcome is PolicyOutcome.APPROVAL_REQUIRED
    assert decision.approver_id == "EMP-MANAGER"
    assert decision.approval_route == "EMPLOYEE_LIFECYCLE_MANAGER"


def test_onboarding_existing_employee_and_open_request_do_not_create_plan() -> None:
    existing = lifecycle_profile(
        "EMP-3001",
        department_code="PRODUCTION-MGMT",
        manager_id="EMP-MANAGER",
    )
    engine = EmployeeLifecyclePolicyEngine()

    duplicate_employee = engine.evaluate(
        onboarding_draft(),
        valid_target_context(subject=existing),
        today=TODAY,
    )
    open_request = EnterpriseOpsEmployeeLifecycleRequest(
        request_id="request-1",
        request_type="ONBOARDING",
        subject_employee_id="EMP-3001",
        initiator_id="EMP-HR-OPERATOR",
        status="PENDING_APPROVAL",
        effective_date=TODAY,
        safe_summary="已有流程",
        created_at=datetime(2026, 7, 23, tzinfo=UTC),
        updated_at=datetime(2026, 7, 23, tzinfo=UTC),
    )
    duplicate_request = engine.evaluate(
        onboarding_draft(),
        valid_target_context(open_requests=(open_request,)),
        today=TODAY,
    )

    assert duplicate_employee.outcome is PolicyOutcome.NO_ACTION
    assert duplicate_employee.reason_codes == (
        "ONBOARDING_SUBJECT_ALREADY_EXISTS",
    )
    assert duplicate_request.outcome is PolicyOutcome.NO_ACTION
    assert duplicate_request.reason_codes == ("OPEN_LIFECYCLE_REQUEST_EXISTS",)


def test_invalid_target_facts_and_vague_reason_request_correction() -> None:
    engine = EmployeeLifecyclePolicyEngine()
    invalid_target = engine.evaluate(
        onboarding_draft(),
        EmployeeLifecycleContext(),
        today=TODAY,
    )
    vague = engine.evaluate(
        onboarding_draft(business_reason="工作需要"),
        valid_target_context(),
        today=TODAY,
    )

    assert invalid_target.outcome is PolicyOutcome.NEEDS_INPUT
    assert "TARGET_DEPARTMENT_NOT_RESOLVED" in invalid_target.reason_codes
    assert "TARGET_JOB_NOT_RESOLVED" in invalid_target.reason_codes
    assert vague.outcome is PolicyOutcome.NEEDS_INPUT
    assert vague.reason_codes == ("VAGUE_BUSINESS_REASON",)


def test_future_date_is_explicitly_denied() -> None:
    decision = EmployeeLifecyclePolicyEngine().evaluate(
        onboarding_draft(effective_date=date(2026, 7, 24)),
        valid_target_context(),
        today=TODAY,
    )

    assert decision.outcome is PolicyOutcome.DENIED
    assert decision.reason_codes == ("FUTURE_EFFECTIVE_DATE_NOT_SUPPORTED",)


def test_transfer_requires_active_subject_and_skips_no_effective_change() -> None:
    engine = EmployeeLifecyclePolicyEngine()
    missing_subject = engine.evaluate(
        transfer_draft(),
        valid_target_context(),
        today=TODAY,
    )
    unchanged_subject = lifecycle_profile(
        "EMP-2001",
        department_code="PRODUCTION-MGMT",
        manager_id="EMP-MANAGER",
    )
    unchanged_subject = unchanged_subject.model_copy(
        update={
            "employee": unchanged_subject.employee.model_copy(
                update={
                    "job_code": "PRODUCTION-PLANNER",
                    "work_location_code": "SHANGHAI-HQ",
                }
            )
        }
    )
    unchanged = engine.evaluate(
        transfer_draft(),
        valid_target_context(subject=unchanged_subject),
        today=TODAY,
    )

    assert missing_subject.outcome is PolicyOutcome.NEEDS_INPUT
    assert missing_subject.reason_codes == ("SUBJECT_EMPLOYEE_NOT_RESOLVED",)
    assert unchanged.outcome is PolicyOutcome.NO_ACTION
    assert unchanged.reason_codes == ("TRANSFER_HAS_NO_EFFECTIVE_CHANGE",)


def test_offboarding_uses_current_manager_and_requires_active_subject() -> None:
    subject = lifecycle_profile(
        "EMP-2001",
        department_code="PLANT-WORKSHOP-1",
        manager_id="EMP-MANAGER",
    )
    manager = lifecycle_profile(
        "EMP-MANAGER",
        department_code="PRODUCTION-MGMT",
        manager_id=None,
    )
    decision = EmployeeLifecyclePolicyEngine().evaluate(
        offboarding_draft(),
        EmployeeLifecycleContext(
            subject_profile=subject,
            approval_manager_profile=manager,
        ),
        today=TODAY,
    )

    assert decision.outcome is PolicyOutcome.APPROVAL_REQUIRED
    assert decision.approver_id == "EMP-MANAGER"
    assert decision.risk_level.value == "HIGH"
