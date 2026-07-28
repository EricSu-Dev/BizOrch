"""Fixed, digestible action-plan templates for employee lifecycle."""

from uuid import NAMESPACE_URL, uuid5

from app.actions.contracts import (
    ActionPlan,
    ActionPlanStep,
    ActionProposal,
    ActionStepReversibility,
)
from app.scenarios.employee_lifecycle.contracts import (
    EmployeeLifecycleContext,
    EmployeeLifecycleRequestDraft,
    EmployeeLifecycleRequestType,
)


class EmployeeLifecyclePlanBuilder:
    """Build only the three reviewed plan templates; no model text controls steps."""

    SCENARIO_KEY = "employee_lifecycle"

    def build(
        self,
        *,
        workflow_run_id: str,
        draft: EmployeeLifecycleRequestDraft,
        context: EmployeeLifecycleContext,
    ) -> ActionPlan:
        if draft.missing_fields() or draft.subject_employee_id is None:
            raise ValueError("complete lifecycle facts are required for a plan")
        plan_id = str(
            uuid5(
                NAMESPACE_URL,
                f"bizorch:lifecycle-plan:{workflow_run_id}",
            )
        )
        builders = {
            EmployeeLifecycleRequestType.ONBOARDING: self._onboarding_steps,
            EmployeeLifecycleRequestType.TRANSFER: self._transfer_steps,
            EmployeeLifecycleRequestType.OFFBOARDING: self._offboarding_steps,
        }
        steps = builders[draft.request_type](
            plan_id=plan_id,
            draft=draft,
            context=context,
        )
        return ActionPlan(
            plan_id=plan_id,
            scenario_key=self.SCENARIO_KEY,
            plan_type=draft.request_type.value,
            subject_reference=draft.subject_employee_id,
            version=1,
            content_summary=self._plan_summary(draft),
            steps=steps,
        )

    def _onboarding_steps(
        self,
        *,
        plan_id: str,
        draft: EmployeeLifecycleRequestDraft,
        context: EmployeeLifecycleContext,
    ) -> tuple[ActionPlanStep, ...]:
        if (
            context.target_department is None
            or context.target_job is None
            or context.baseline_access_package is None
            or context.work_location is None
        ):
            raise ValueError("validated onboarding target facts are required")
        common = self._common_parameters(draft)
        definitions = (
            (
                "create_pending_employee",
                f"employees/{draft.subject_employee_id}",
                {
                    **common,
                    "display_name": draft.display_name,
                    "department_code": draft.target_department_code,
                    "job_code": draft.target_job_code,
                    "manager_id": draft.target_manager_id,
                    "work_location_code": draft.work_location_code,
                    "expected_department_version": context.target_department.version,
                    "expected_job_version": context.target_job.version,
                    "expected_work_location_version": context.work_location.version,
                },
                ActionStepReversibility.MANUAL_ONLY,
                None,
            ),
            (
                "create_disabled_corporate_account",
                f"employees/{draft.subject_employee_id}/corporate-account",
                {**common, "initial_status": "DISABLED"},
                ActionStepReversibility.REVERSIBLE,
                "remove_disabled_corporate_account",
            ),
            (
                "assign_baseline_access_package",
                f"employees/{draft.subject_employee_id}/baseline-access",
                {
                    **common,
                    "package_code": context.baseline_access_package.package_code,
                    "package_version": context.baseline_access_package.version,
                    "role_bindings": list(
                        context.baseline_access_package.role_bindings
                    ),
                },
                ActionStepReversibility.REVERSIBLE,
                "revoke_onboarding_baseline_access",
            ),
            (
                "create_asset_assignment_task",
                f"employees/{draft.subject_employee_id}/asset-tasks",
                {
                    **common,
                    "asset_profile_code": context.target_job.asset_profile_code,
                    "task_type": "PROVISION",
                },
                ActionStepReversibility.REVERSIBLE,
                "cancel_asset_assignment_task",
            ),
            (
                "activate_employee_and_account",
                f"employees/{draft.subject_employee_id}/activation",
                {
                    **common,
                    "expected_employee_version": 1,
                    "expected_account_version": 1,
                },
                ActionStepReversibility.MANUAL_ONLY,
                None,
            ),
        )
        return self._sequential_steps(plan_id, definitions)

    def _transfer_steps(
        self,
        *,
        plan_id: str,
        draft: EmployeeLifecycleRequestDraft,
        context: EmployeeLifecycleContext,
    ) -> tuple[ActionPlanStep, ...]:
        if (
            context.subject_profile is None
            or context.target_job is None
            or context.baseline_access_package is None
            or context.work_location is None
        ):
            raise ValueError("validated transfer facts are required")
        employee = context.subject_profile.employee
        current_bindings = {
            (item.application_code, item.role_code)
            for item in context.subject_profile.active_access
        }
        target_bindings = {
            (item["application_code"], item["role_code"])
            for item in context.baseline_access_package.role_bindings
        }
        common = self._common_parameters(draft)
        target_location = draft.work_location_code or employee.work_location_code
        definitions = (
            (
                "update_employee_assignment",
                f"employees/{draft.subject_employee_id}/assignment",
                {
                    **common,
                    "department_code": draft.target_department_code,
                    "job_code": draft.target_job_code,
                    "manager_id": draft.target_manager_id,
                    "work_location_code": target_location,
                    "expected_employee_version": employee.version,
                    "expected_department_version": context.target_department.version,
                    "expected_job_version": context.target_job.version,
                    "expected_work_location_version": context.work_location.version,
                },
                ActionStepReversibility.MANUAL_ONLY,
                None,
            ),
            (
                "revoke_obsolete_baseline_access",
                f"employees/{draft.subject_employee_id}/baseline-access/revocations",
                {
                    **common,
                    "role_bindings": self._bindings(current_bindings - target_bindings),
                },
                ActionStepReversibility.IRREVERSIBLE,
                None,
            ),
            (
                "grant_target_baseline_access",
                f"employees/{draft.subject_employee_id}/baseline-access/grants",
                {
                    **common,
                    "package_code": context.baseline_access_package.package_code,
                    "package_version": context.baseline_access_package.version,
                    "role_bindings": self._bindings(target_bindings - current_bindings),
                },
                ActionStepReversibility.MANUAL_ONLY,
                None,
            ),
            (
                "create_asset_adjustment_task",
                f"employees/{draft.subject_employee_id}/asset-tasks",
                {
                    **common,
                    "asset_profile_code": context.target_job.asset_profile_code,
                    "task_type": "ADJUST",
                },
                ActionStepReversibility.REVERSIBLE,
                "cancel_asset_adjustment_task",
            ),
            (
                "verify_employee_transfer_consistency",
                f"employees/{draft.subject_employee_id}/lifecycle-verification",
                {
                    **common,
                    "expected_department_code": draft.target_department_code,
                    "expected_job_code": draft.target_job_code,
                    "expected_manager_id": draft.target_manager_id,
                    "expected_work_location_code": target_location,
                },
                ActionStepReversibility.MANUAL_ONLY,
                None,
            ),
        )
        return self._sequential_steps(plan_id, definitions)

    def _offboarding_steps(
        self,
        *,
        plan_id: str,
        draft: EmployeeLifecycleRequestDraft,
        context: EmployeeLifecycleContext,
    ) -> tuple[ActionPlanStep, ...]:
        if context.subject_profile is None:
            raise ValueError("validated offboarding subject facts are required")
        profile = context.subject_profile
        common = self._common_parameters(draft)
        definitions = (
            (
                "disable_corporate_account",
                f"employees/{draft.subject_employee_id}/corporate-account/disable",
                {
                    **common,
                    "expected_account_version": (
                        profile.account.version if profile.account else None
                    ),
                },
                ActionStepReversibility.IRREVERSIBLE,
                None,
            ),
            (
                "revoke_all_employee_access",
                f"employees/{draft.subject_employee_id}/access/revocations",
                {
                    **common,
                    "access_ids": sorted(
                        item.access_id for item in profile.active_access
                    ),
                },
                ActionStepReversibility.IRREVERSIBLE,
                None,
            ),
            (
                "create_asset_return_task",
                f"employees/{draft.subject_employee_id}/asset-tasks",
                {
                    **common,
                    "task_type": "RETURN",
                    "asset_return_note": draft.asset_return_note,
                    "existing_asset_task_ids": sorted(
                        item.task_id for item in profile.asset_tasks
                    ),
                },
                ActionStepReversibility.MANUAL_ONLY,
                None,
            ),
            (
                "mark_employee_inactive",
                f"employees/{draft.subject_employee_id}/employment-status",
                {
                    **common,
                    "target_status": "INACTIVE",
                    "expected_employee_version": profile.employee.version,
                    "offboarding_reason": draft.offboarding_reason,
                },
                ActionStepReversibility.IRREVERSIBLE,
                None,
            ),
            (
                "verify_employee_offboarding_consistency",
                f"employees/{draft.subject_employee_id}/lifecycle-verification",
                {
                    **common,
                    "expected_employee_status": "INACTIVE",
                    "expected_account_status": "DISABLED",
                    "expected_active_access_count": 0,
                },
                ActionStepReversibility.MANUAL_ONLY,
                None,
            ),
        )
        return self._sequential_steps(plan_id, definitions)

    @staticmethod
    def _common_parameters(
        draft: EmployeeLifecycleRequestDraft,
    ) -> dict[str, object]:
        return {
            "initiator_id": draft.initiator_id,
            "subject_employee_id": draft.subject_employee_id,
            "effective_date": draft.effective_date.isoformat(),
            "business_reason": draft.business_reason,
            "request_type": draft.request_type.value,
        }

    @staticmethod
    def _bindings(
        values: set[tuple[str, str]],
    ) -> list[dict[str, str]]:
        return [
            {"application_code": application, "role_code": role}
            for application, role in sorted(values)
        ]

    @staticmethod
    def _plan_summary(draft: EmployeeLifecycleRequestDraft) -> str:
        labels = {
            EmployeeLifecycleRequestType.ONBOARDING: "入职",
            EmployeeLifecycleRequestType.TRANSFER: "调岗",
            EmployeeLifecycleRequestType.OFFBOARDING: "离职",
        }
        return (
            f"为员工{draft.subject_employee_id}办理{labels[draft.request_type]}协同，"
            f"生效日期{draft.effective_date.isoformat()}，共5个固定步骤"
        )

    @staticmethod
    def _sequential_steps(
        plan_id: str,
        definitions: tuple[
            tuple[
                str,
                str,
                dict[str, object],
                ActionStepReversibility,
                str | None,
            ],
            ...,
        ],
    ) -> tuple[ActionPlanStep, ...]:
        steps: list[ActionPlanStep] = []
        for index, (
            action_type,
            target_resource,
            parameters,
            reversibility,
            compensation,
        ) in enumerate(definitions, start=1):
            step_id = str(
                uuid5(NAMESPACE_URL, f"{plan_id}:step:{index}:{action_type}")
            )
            action_id = str(
                uuid5(NAMESPACE_URL, f"{plan_id}:action:{index}:{action_type}")
            )
            steps.append(
                ActionPlanStep(
                    step_id=step_id,
                    step_order=index,
                    depends_on_step_ids=(
                        (steps[-1].step_id,) if steps else ()
                    ),
                    proposal=ActionProposal(
                        action_id=action_id,
                        action_type=action_type,
                        target_resource=target_resource,
                        parameters=parameters,
                        version=1,
                        content_summary=f"步骤{index}：{action_type}",
                    ),
                    reversibility=reversibility,
                    compensation_action_type=compensation,
                )
            )
        return tuple(steps)
