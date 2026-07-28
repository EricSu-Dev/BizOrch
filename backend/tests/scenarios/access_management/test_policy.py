import pytest
from pydantic import ValidationError

from app.policy.contracts import PolicyOutcome, RiskLevel
from app.scenarios.access_management.contracts import (
    AccessRequestContext,
    AccessRequestDraft,
)
from app.scenarios.access_management.policy import AccessPolicyEngine


def complete_draft(**overrides: object) -> AccessRequestDraft:
    values: dict[str, object] = {
        "employee_id": "EMP-1001",
        "application_code": "CRM",
        "role_code": "read_only",
        "duration_days": 30,
        "business_reason": "Participate in the East China customer project",
    }
    values.update(overrides)
    return AccessRequestDraft(**values)


def test_blank_extracted_text_is_reported_as_missing() -> None:
    draft = complete_draft(application_code="   ", business_reason=None)

    assert draft.missing_fields() == ("application_code", "business_reason")


def test_duration_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        complete_draft(duration_days=0)


def test_incomplete_request_needs_user_input() -> None:
    decision = AccessPolicyEngine().evaluate(
        AccessRequestDraft(employee_id="EMP-1001"),
        AccessRequestContext(),
    )

    assert decision.outcome is PolicyOutcome.NEEDS_INPUT
    assert "MISSING_APPLICATION_CODE" in decision.reason_codes
    assert decision.approver_id is None


def test_existing_role_produces_no_action() -> None:
    decision = AccessPolicyEngine().evaluate(
        complete_draft(role_code="READ-ONLY"),
        AccessRequestContext(existing_role_codes=frozenset({"read_only"})),
    )

    assert decision.outcome is PolicyOutcome.NO_ACTION
    assert decision.reason_codes == ("ROLE_ALREADY_GRANTED",)


def test_access_longer_than_temporary_limit_is_denied() -> None:
    decision = AccessPolicyEngine().evaluate(
        complete_draft(duration_days=91),
        AccessRequestContext(manager_id="EMP-MANAGER"),
    )

    assert decision.outcome is PolicyOutcome.DENIED
    assert decision.risk_level is RiskLevel.HIGH


def test_short_read_only_access_routes_to_manager() -> None:
    decision = AccessPolicyEngine().evaluate(
        complete_draft(),
        AccessRequestContext(manager_id="EMP-MANAGER"),
    )

    assert decision.outcome is PolicyOutcome.APPROVAL_REQUIRED
    assert decision.risk_level is RiskLevel.LOW
    assert decision.approval_route == "MANAGER"
    assert decision.approver_id == "EMP-MANAGER"


def test_standard_access_routes_to_application_owner() -> None:
    decision = AccessPolicyEngine().evaluate(
        complete_draft(role_code="editor", duration_days=60),
        AccessRequestContext(application_owner_id="EMP-OWNER"),
    )

    assert decision.outcome is PolicyOutcome.APPROVAL_REQUIRED
    assert decision.risk_level is RiskLevel.MEDIUM
    assert decision.approval_route == "APPLICATION_OWNER"


def test_privileged_access_routes_to_security_officer() -> None:
    decision = AccessPolicyEngine().evaluate(
        complete_draft(role_code="ADMIN"),
        AccessRequestContext(security_officer_id="EMP-SECURITY"),
    )

    assert decision.outcome is PolicyOutcome.APPROVAL_REQUIRED
    assert decision.risk_level is RiskLevel.HIGH
    assert decision.approval_route == "SECURITY_OFFICER"


def test_missing_required_approver_routes_to_human_review() -> None:
    decision = AccessPolicyEngine().evaluate(
        complete_draft(role_code="admin"),
        AccessRequestContext(),
    )

    assert decision.outcome is PolicyOutcome.HUMAN_REVIEW
    assert decision.approver_id is None
    assert "APPROVER_NOT_RESOLVED" in decision.reason_codes

