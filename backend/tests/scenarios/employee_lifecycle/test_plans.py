from datetime import UTC, datetime

from app.actions.contracts import ActionStepReversibility
from app.integrations.enterprise_ops import (
    EnterpriseOpsEmployeeOnboardingWriteResult,
    EnterpriseOpsUserAccess,
)
from app.scenarios.employee_lifecycle.execution import EmployeeLifecycleWriteTool
from app.scenarios.employee_lifecycle.plans import EmployeeLifecyclePlanBuilder
from tests.scenarios.employee_lifecycle.test_intake import lifecycle_profile
from tests.scenarios.employee_lifecycle.test_policy import (
    offboarding_draft,
    onboarding_draft,
    transfer_draft,
    valid_target_context,
)


def action_types(plan) -> tuple[str, ...]:
    return tuple(step.proposal.action_type for step in plan.steps)


def assert_sequential(plan) -> None:
    assert tuple(step.step_order for step in plan.steps) == (1, 2, 3, 4, 5)
    assert plan.steps[0].depends_on_step_ids == ()
    for previous, current in zip(plan.steps[:-1], plan.steps[1:], strict=True):
        assert current.depends_on_step_ids == (previous.step_id,)


def test_onboarding_plan_is_fixed_deterministic_and_safe_ordered() -> None:
    builder = EmployeeLifecyclePlanBuilder()
    draft = onboarding_draft()
    context = valid_target_context()

    first = builder.build(
        workflow_run_id="onboarding-run",
        draft=draft,
        context=context,
    )
    replay = builder.build(
        workflow_run_id="onboarding-run",
        draft=draft,
        context=context,
    )

    assert first == replay
    assert first.content_digest == replay.content_digest
    assert action_types(first) == (
        "create_pending_employee",
        "create_disabled_corporate_account",
        "assign_baseline_access_package",
        "create_asset_assignment_task",
        "activate_employee_and_account",
    )
    assert first.steps[-1].proposal.parameters["expected_account_version"] == 1
    assert_sequential(first)


def test_transfer_plan_calculates_permission_difference_deterministically() -> None:
    subject = lifecycle_profile(
        "EMP-2001",
        department_code="PLANT-WORKSHOP-1",
        manager_id="EMP-MANAGER",
    ).model_copy(
        update={
            "active_access": (
                EnterpriseOpsUserAccess(
                    access_id="access-old",
                    employee_id="EMP-2001",
                    application_code="CRM",
                    role_code="read_only",
                    expires_at=datetime(2026, 8, 1, tzinfo=UTC),
                    active=True,
                ),
            )
        }
    )
    plan = EmployeeLifecyclePlanBuilder().build(
        workflow_run_id="transfer-run",
        draft=transfer_draft(),
        context=valid_target_context(subject=subject),
    )

    assert action_types(plan) == (
        "update_employee_assignment",
        "revoke_obsolete_baseline_access",
        "grant_target_baseline_access",
        "create_asset_adjustment_task",
        "verify_employee_transfer_consistency",
    )
    assert plan.steps[1].proposal.parameters["role_bindings"] == [
        {"application_code": "CRM", "role_code": "read_only"}
    ]
    assert plan.steps[2].proposal.parameters["role_bindings"] == [
        {"application_code": "ERP", "role_code": "standard"}
    ]
    assert_sequential(plan)


def test_offboarding_plan_keeps_security_actions_irreversible() -> None:
    subject = lifecycle_profile(
        "EMP-2001",
        department_code="PLANT-WORKSHOP-1",
        manager_id="EMP-MANAGER",
    )
    plan = EmployeeLifecyclePlanBuilder().build(
        workflow_run_id="offboarding-run",
        draft=offboarding_draft(),
        context=valid_target_context(subject=subject),
    )

    assert action_types(plan) == (
        "disable_corporate_account",
        "revoke_all_employee_access",
        "create_asset_return_task",
        "mark_employee_inactive",
        "verify_employee_offboarding_consistency",
    )
    assert plan.steps[0].reversibility is ActionStepReversibility.IRREVERSIBLE
    assert plan.steps[1].reversibility is ActionStepReversibility.IRREVERSIBLE
    assert plan.steps[3].reversibility is ActionStepReversibility.IRREVERSIBLE
    assert_sequential(plan)


def test_plan_digest_changes_when_approved_business_fact_changes() -> None:
    builder = EmployeeLifecyclePlanBuilder()
    context = valid_target_context()
    original = builder.build(
        workflow_run_id="same-run",
        draft=onboarding_draft(display_name="王晨"),
        context=context,
    )
    changed = builder.build(
        workflow_run_id="same-run",
        draft=onboarding_draft(display_name="王晨（更正）"),
        context=context,
    )

    assert original.plan_id == changed.plan_id
    assert original.content_digest != changed.content_digest


def test_every_fixed_plan_step_builds_only_its_own_mcp_payload() -> None:
    class RecordingClient:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, object], str]] = []

        def __getattr__(self, action_type: str):
            def invoke(payload, *, idempotency_key):
                self.calls.append((action_type, payload, idempotency_key))
                return EnterpriseOpsEmployeeOnboardingWriteResult(
                    action_type=action_type,
                    employee_id=str(payload["employee_id"]),
                    resource_id=f"resource-{action_type}",
                )

            return invoke

    subject = lifecycle_profile(
        "EMP-2001",
        department_code="PLANT-WORKSHOP-1",
        manager_id="EMP-MANAGER",
    )
    builder = EmployeeLifecyclePlanBuilder()
    plans = (
        builder.build(
            workflow_run_id="tool-onboarding",
            draft=onboarding_draft(),
            context=valid_target_context(),
        ),
        builder.build(
            workflow_run_id="tool-transfer",
            draft=transfer_draft(),
            context=valid_target_context(subject=subject),
        ),
        builder.build(
            workflow_run_id="tool-offboarding",
            draft=offboarding_draft(),
            context=valid_target_context(subject=subject),
        ),
    )
    client = RecordingClient()
    tool = EmployeeLifecycleWriteTool(client)

    for plan in plans:
        for step in plan.steps:
            result = tool.execute(
                step.proposal,
                idempotency_key=f"tool-step-{step.step_id}",
            )
            assert result.status.value == "SUCCEEDED"

    assert [item[0] for item in client.calls] == [
        step.proposal.action_type for plan in plans for step in plan.steps
    ]
    assert all("request_type" not in payload for _, payload, _ in client.calls)
