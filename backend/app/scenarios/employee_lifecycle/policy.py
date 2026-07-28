"""Deterministic employee-lifecycle validation and approval routing."""

from datetime import date

from app.policy.contracts import PolicyDecision, PolicyOutcome, RiskLevel
from app.scenarios.employee_lifecycle.contracts import (
    EmployeeLifecycleContext,
    EmployeeLifecycleRequestDraft,
    EmployeeLifecycleRequestType,
)


class EmployeeLifecyclePolicyEngine:
    """Evaluate authoritative facts without using model-generated decisions."""

    _VAGUE_REASONS = frozenset(
        {
            "工作需要",
            "领导安排",
            "业务需要",
            "公司安排",
            "work required",
            "manager request",
        }
    )

    def evaluate(
        self,
        draft: EmployeeLifecycleRequestDraft,
        context: EmployeeLifecycleContext,
        *,
        today: date,
    ) -> PolicyDecision:
        missing = draft.missing_fields()
        if missing:
            return self._needs_input(
                *(f"MISSING_{field.upper()}" for field in missing)
            )
        if self._is_vague_reason(draft.business_reason):
            return self._needs_input("VAGUE_BUSINESS_REASON")
        if draft.effective_date != today:
            return PolicyDecision(
                outcome=PolicyOutcome.DENIED,
                risk_level=RiskLevel.MEDIUM,
                reason_codes=(
                    "FUTURE_EFFECTIVE_DATE_NOT_SUPPORTED"
                    if draft.effective_date and draft.effective_date > today
                    else "PAST_EFFECTIVE_DATE_NOT_SUPPORTED",
                ),
            )
        if context.open_requests:
            return PolicyDecision(
                outcome=PolicyOutcome.NO_ACTION,
                risk_level=RiskLevel.MEDIUM,
                reason_codes=("OPEN_LIFECYCLE_REQUEST_EXISTS",),
            )

        if draft.request_type is EmployeeLifecycleRequestType.ONBOARDING:
            if context.subject_profile is not None:
                return PolicyDecision(
                    outcome=PolicyOutcome.NO_ACTION,
                    risk_level=RiskLevel.MEDIUM,
                    reason_codes=("ONBOARDING_SUBJECT_ALREADY_EXISTS",),
                )
        else:
            if context.subject_profile is None:
                return self._needs_input("SUBJECT_EMPLOYEE_NOT_RESOLVED")
            if (
                context.subject_profile.employee.employment_status != "ACTIVE"
                or not context.subject_profile.employee.active
            ):
                return PolicyDecision(
                    outcome=PolicyOutcome.NO_ACTION,
                    risk_level=RiskLevel.MEDIUM,
                    reason_codes=("SUBJECT_EMPLOYEE_NOT_ACTIVE",),
                )

        if draft.request_type is not EmployeeLifecycleRequestType.OFFBOARDING:
            invalid_fields = self._invalid_target_fields(draft, context)
            if invalid_fields:
                return self._needs_input(*invalid_fields)
            if context.work_location is None or not context.work_location.active:
                return self._needs_input("WORK_LOCATION_NOT_RESOLVED")
            if (
                draft.request_type is EmployeeLifecycleRequestType.TRANSFER
                and context.subject_profile is not None
            ):
                current = context.subject_profile.employee
                target_location = (
                    draft.work_location_code or current.work_location_code
                )
                if (
                    current.department_code == draft.target_department_code
                    and current.job_code == draft.target_job_code
                    and current.manager_id == draft.target_manager_id
                    and current.work_location_code == target_location
                ):
                    return PolicyDecision(
                        outcome=PolicyOutcome.NO_ACTION,
                        risk_level=RiskLevel.LOW,
                        reason_codes=("TRANSFER_HAS_NO_EFFECTIVE_CHANGE",),
                    )

        approver = context.approval_manager_profile
        if (
            approver is None
            or approver.employee.employment_status != "ACTIVE"
            or not approver.employee.active
        ):
            return PolicyDecision(
                outcome=PolicyOutcome.HUMAN_REVIEW,
                risk_level=RiskLevel.HIGH,
                reason_codes=("AUTHORITATIVE_APPROVER_NOT_RESOLVED",),
                approval_route="EMPLOYEE_LIFECYCLE_MANAGER",
            )
        approver_id = approver.employee.employee_id
        if draft.request_type is not EmployeeLifecycleRequestType.OFFBOARDING:
            if approver.employee.department_code != draft.target_department_code:
                return self._needs_input("TARGET_MANAGER_OUTSIDE_DEPARTMENT")

        return PolicyDecision(
            outcome=PolicyOutcome.APPROVAL_REQUIRED,
            risk_level=(
                RiskLevel.HIGH
                if draft.request_type is EmployeeLifecycleRequestType.OFFBOARDING
                else RiskLevel.MEDIUM
            ),
            reason_codes=(
                f"{draft.request_type.value}_PLAN_REQUIRES_APPROVAL",
            ),
            approver_id=approver_id,
            approval_route="EMPLOYEE_LIFECYCLE_MANAGER",
        )

    @staticmethod
    def _invalid_target_fields(
        draft: EmployeeLifecycleRequestDraft,
        context: EmployeeLifecycleContext,
    ) -> tuple[str, ...]:
        reasons: list[str] = []
        if context.target_department is None or not context.target_department.active:
            reasons.append("TARGET_DEPARTMENT_NOT_RESOLVED")
        if context.target_job is None or not context.target_job.active:
            reasons.append("TARGET_JOB_NOT_RESOLVED")
        elif context.target_job.department_code != draft.target_department_code:
            reasons.append("TARGET_JOB_DEPARTMENT_MISMATCH")
        if (
            context.target_manager_profile is None
            or not context.target_manager_profile.employee.active
            or context.target_manager_profile.employee.employment_status != "ACTIVE"
        ):
            reasons.append("TARGET_MANAGER_NOT_RESOLVED")
        if (
            context.baseline_access_package is None
            or not context.baseline_access_package.active
        ):
            reasons.append("BASELINE_ACCESS_PACKAGE_NOT_RESOLVED")
        return tuple(reasons)

    @classmethod
    def _is_vague_reason(cls, value: str | None) -> bool:
        normalized = " ".join((value or "").strip().lower().split())
        return normalized in cls._VAGUE_REASONS or len(normalized) < 6

    @staticmethod
    def _needs_input(*reason_codes: str) -> PolicyDecision:
        return PolicyDecision(
            outcome=PolicyOutcome.NEEDS_INPUT,
            risk_level=RiskLevel.LOW,
            reason_codes=tuple(reason_codes),
        )
