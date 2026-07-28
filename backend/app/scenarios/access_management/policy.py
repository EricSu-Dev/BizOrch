"""Deterministic access policy rules for the first BizOrch scenario."""

from app.policy.contracts import PolicyDecision, PolicyOutcome, RiskLevel
from app.scenarios.access_management.contracts import (
    AccessRequestContext,
    AccessRequestDraft,
)


class AccessPolicyEngine:
    """Classify access risk and select exactly one approval route."""

    MAX_TEMPORARY_ACCESS_DAYS = 90
    READ_ONLY_ROLE_CODES = frozenset({"read", "readonly", "read_only", "viewer"})
    PRIVILEGED_ROLE_CODES = frozenset(
        {"admin", "administrator", "owner", "super_admin", "write_admin"}
    )

    def evaluate(
        self,
        draft: AccessRequestDraft,
        context: AccessRequestContext,
    ) -> PolicyDecision:
        """Evaluate only explicit fields and trusted enterprise facts."""
        missing_fields = draft.missing_fields()
        if missing_fields:
            return PolicyDecision(
                outcome=PolicyOutcome.NEEDS_INPUT,
                risk_level=RiskLevel.LOW,
                reason_codes=tuple(f"MISSING_{name.upper()}" for name in missing_fields),
            )

        role_code = self._normalize_code(draft.role_code)
        existing_roles = {self._normalize_code(code) for code in context.existing_role_codes}
        if role_code in existing_roles:
            return PolicyDecision(
                outcome=PolicyOutcome.NO_ACTION,
                risk_level=RiskLevel.LOW,
                reason_codes=("ROLE_ALREADY_GRANTED",),
            )

        if draft.duration_days > self.MAX_TEMPORARY_ACCESS_DAYS:
            return PolicyDecision(
                outcome=PolicyOutcome.DENIED,
                risk_level=RiskLevel.HIGH,
                reason_codes=("DURATION_EXCEEDS_TEMPORARY_LIMIT",),
            )

        if role_code in self.PRIVILEGED_ROLE_CODES:
            return self._approval_or_human_review(
                risk_level=RiskLevel.HIGH,
                approver_id=context.security_officer_id,
                route="SECURITY_OFFICER",
                reason_code="PRIVILEGED_ROLE",
            )

        if role_code in self.READ_ONLY_ROLE_CODES and draft.duration_days <= 30:
            return self._approval_or_human_review(
                risk_level=RiskLevel.LOW,
                approver_id=context.manager_id,
                route="MANAGER",
                reason_code="SHORT_TERM_READ_ONLY",
            )

        return self._approval_or_human_review(
            risk_level=RiskLevel.MEDIUM,
            approver_id=context.application_owner_id,
            route="APPLICATION_OWNER",
            reason_code="STANDARD_ACCESS",
        )

    @staticmethod
    def _normalize_code(value: str | None) -> str:
        return (value or "").strip().lower().replace("-", "_")

    @staticmethod
    def _approval_or_human_review(
        *,
        risk_level: RiskLevel,
        approver_id: str | None,
        route: str,
        reason_code: str,
    ) -> PolicyDecision:
        if not approver_id:
            return PolicyDecision(
                outcome=PolicyOutcome.HUMAN_REVIEW,
                risk_level=risk_level,
                reason_codes=(reason_code, "APPROVER_NOT_RESOLVED"),
                approval_route=route,
            )
        return PolicyDecision(
            outcome=PolicyOutcome.APPROVAL_REQUIRED,
            risk_level=risk_level,
            reason_codes=(reason_code,),
            approver_id=approver_id,
            approval_route=route,
        )

