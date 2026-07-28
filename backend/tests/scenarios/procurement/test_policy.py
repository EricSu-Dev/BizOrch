from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.auth.repository import AuthRepository
from app.integrations.enterprise_ops import (
    EnterpriseOpsProcurementRequest,
    EnterpriseOpsProcurementRequestItem,
)
from app.scenarios.procurement.agent import ProcurementDomainAgent
from app.scenarios.procurement.context import ProcurementRequestContextResolver
from app.scenarios.procurement.contracts import (
    ProcurementItemDraft,
    ProcurementRequestDraft,
)
from app.scenarios.procurement.policy import (
    AuthApprovalEligibility,
    ProcurementAmountBand,
    ProcurementPolicyEngine,
    ProcurementPolicyError,
    ProcurementPolicyErrorCode,
)
from app.persistence.base import Base
from tests.scenarios.procurement.test_context import FakeProcurementClient


class FakeApprovalEligibility:
    def __init__(self, *ineligible: str) -> None:
        self._ineligible = frozenset(ineligible)

    def is_active_approver(self, employee_id: str) -> bool:
        return employee_id not in self._ineligible


def test_auth_approval_eligibility_requires_active_approver_role() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    sessions: sessionmaker[Session] = sessionmaker(
        bind=engine,
        expire_on_commit=False,
    )
    with sessions.begin() as session:
        repository = AuthRepository(session)
        repository.create_user(
            employee_id="EMP-APPROVER",
            username="approver",
            password_hash="test",
            roles=frozenset({"approver"}),
        )
        repository.create_user(
            employee_id="EMP-EMPLOYEE",
            username="employee-only",
            password_hash="test",
            roles=frozenset({"employee"}),
        )

    eligibility = AuthApprovalEligibility(sessions)
    assert eligibility.is_active_approver("EMP-APPROVER") is True
    assert eligibility.is_active_approver("EMP-EMPLOYEE") is False
    assert eligibility.is_active_approver("EMP-MISSING") is False


def ready_facts(amount: str = "2600.00"):
    analysis = ProcurementDomainAgent(
        ProcurementRequestContextResolver(FakeProcurementClient())
    ).analyze(
        {
            "items": [
                {
                    "item_name": "人体工学办公椅",
                    "item_category": "OFFICE_EQUIPMENT",
                    "quantity": 2,
                    "specification_note": "用于新项目成员工位",
                }
            ],
            "estimated_total_amount": amount,
            "cost_center_code": "CC-SALES-EAST-001",
            "desired_date": "2026-08-05",
            "delivery_location_code": "SHANGHAI-HQ",
            "business_reason": "客户交付项目新增两名成员，需要补充符合办公安全要求的座椅。",
        },
        actor_id="EMP-1001",
    )
    return analysis.draft, analysis.context


def engine(*ineligible: str) -> ProcurementPolicyEngine:
    return ProcurementPolicyEngine(
        FakeApprovalEligibility(*ineligible),
        today=lambda: date(2026, 7, 26),
    )


@pytest.mark.parametrize(
    ("amount", "expected_band", "expected_stages"),
    [
        ("0.01", ProcurementAmountBand.A, ("BUSINESS_CONFIRMATION",)),
        ("5000.00", ProcurementAmountBand.A, ("BUSINESS_CONFIRMATION",)),
        (
            "5000.01",
            ProcurementAmountBand.B,
            ("BUSINESS_CONFIRMATION", "BUDGET_CONFIRMATION"),
        ),
        (
            "50000.00",
            ProcurementAmountBand.B,
            ("BUSINESS_CONFIRMATION", "BUDGET_CONFIRMATION"),
        ),
        (
            "50000.01",
            ProcurementAmountBand.C,
            (
                "BUSINESS_CONFIRMATION",
                "BUDGET_CONFIRMATION",
                "PROCUREMENT_CONFIRMATION",
            ),
        ),
    ],
)
def test_amount_boundaries_create_fixed_serial_routes(
    amount: str,
    expected_band: ProcurementAmountBand,
    expected_stages: tuple[str, ...],
) -> None:
    draft, context = ready_facts(amount)

    decision = engine().evaluate(
        workflow_run_id=f"run-{amount}",
        draft=draft,
        context=context,
    )

    assert decision.amount_band is expected_band
    assert tuple(stage.stage_code for stage in decision.route.stages) == (
        expected_stages
    )
    assert tuple(stage.stage_order for stage in decision.route.stages) == tuple(
        range(1, len(expected_stages) + 1)
    )
    assert decision.route.route_version == context.policy.version


def test_policy_generates_one_atomic_write_step_with_approved_budget_snapshot() -> None:
    draft, context = ready_facts("12000.00")

    decision = engine().evaluate(
        workflow_run_id="run-plan-shape",
        draft=draft,
        context=context,
    )

    assert len(decision.plan.steps) == 1
    proposal = decision.plan.steps[0].proposal
    assert proposal.action_type == (
        "CREATE_PROCUREMENT_REQUEST_AND_RESERVE_BUDGET"
    )
    assert proposal.parameters["estimated_total_amount"] == "12000.00"
    assert proposal.parameters["currency"] == "CNY"
    assert proposal.parameters["policy_version"] == 1
    assert proposal.parameters["expected_cost_center_version"] == 1
    assert proposal.parameters["expected_reserved_amount"] == "5000.00"
    assert proposal.parameters["expected_business_approver_id"] == "EMP-MANAGER"
    assert proposal.parameters["expected_budget_owner_id"] == "EMP-BUDGET-OWNER"
    assert (
        proposal.parameters["expected_procurement_approver_id"]
        == "EMP-PROCUREMENT-OWNER"
    )
    assert "budget_version" not in proposal.parameters
    assert "available_amount" not in proposal.parameters
    repeated = engine().evaluate(
        workflow_run_id="run-plan-shape",
        draft=draft,
        context=context,
    )
    assert repeated.plan.plan_id == decision.plan.plan_id
    assert repeated.plan.content_digest == decision.plan.content_digest


@pytest.mark.parametrize(
    ("draft_change", "context_change", "expected_code"),
    [
        (
            {"estimated_total_amount": Decimal("1.001")},
            {},
            ProcurementPolicyErrorCode.AMOUNT_INVALID,
        ),
        (
            {
                "items": (
                    ProcurementItemDraft(
                        item_name="生产原料",
                        item_category="RAW_MATERIAL",
                        quantity=1,
                    ),
                )
            },
            {},
            ProcurementPolicyErrorCode.ITEM_CATEGORY_NOT_ALLOWED,
        ),
        (
            {"desired_date": date(2026, 7, 25)},
            {},
            ProcurementPolicyErrorCode.DESIRED_DATE_INVALID,
        ),
        (
            {"business_reason": "工作需要"},
            {},
            ProcurementPolicyErrorCode.BUSINESS_REASON_INVALID,
        ),
        (
            {"estimated_total_amount": Decimal("70000.01")},
            {},
            ProcurementPolicyErrorCode.BUDGET_INSUFFICIENT,
        ),
        (
            {},
            {"cost_center": None},
            ProcurementPolicyErrorCode.COST_CENTER_NOT_FOUND,
        ),
    ],
)
def test_policy_rejects_invalid_fields_and_authoritative_facts(
    draft_change: dict[str, object],
    context_change: dict[str, object],
    expected_code: ProcurementPolicyErrorCode,
) -> None:
    draft, context = ready_facts()

    with pytest.raises(ProcurementPolicyError) as caught:
        engine().evaluate(
            workflow_run_id="run-invalid",
            draft=draft.model_copy(update=draft_change),
            context=context.model_copy(update=context_change),
        )

    assert caught.value.code is expected_code


def test_similar_open_request_stops_automatic_processing() -> None:
    draft, context = ready_facts()
    existing = EnterpriseOpsProcurementRequest(
        request_id="request-existing",
        external_workflow_run_id="run-existing",
        requester_id="EMP-1001",
        cost_center_code="CC-SALES-EAST-001",
        estimated_total_amount=Decimal("2400.00"),
        currency="CNY",
        desired_date=date(2026, 8, 2),
        delivery_location_code="SHANGHAI-HQ",
        business_reason_summary="已有申请",
        status="PENDING_APPROVAL",
        policy_code="OFFICE-PROCUREMENT-2026",
        policy_version=1,
        items=(
            EnterpriseOpsProcurementRequestItem(
                line_no=1,
                item_name="人体工学办公椅",
                item_category="OFFICE_EQUIPMENT",
                quantity=2,
                specification_note=None,
            ),
        ),
        created_at=datetime(2026, 7, 25, tzinfo=UTC),
        updated_at=datetime(2026, 7, 25, tzinfo=UTC),
    )

    with pytest.raises(ProcurementPolicyError) as caught:
        engine().evaluate(
            workflow_run_id="run-duplicate",
            draft=draft,
            context=context.model_copy(update={"open_requests": (existing,)}),
        )

    assert caught.value.code is ProcurementPolicyErrorCode.DUPLICATE_RISK


def test_separation_of_duties_never_silently_removes_a_stage() -> None:
    draft, context = ready_facts("12000.00")
    requester = context.requester_profile
    assert requester is not None
    self_approving_requester = requester.model_copy(
        update={
            "employee": requester.employee.model_copy(
                update={"manager_id": "EMP-1001"}
            )
        }
    )

    with pytest.raises(ProcurementPolicyError) as caught:
        engine().evaluate(
            workflow_run_id="run-self-approval",
            draft=draft,
            context=context.model_copy(
                update={
                    "requester_profile": self_approving_requester,
                    "approver_profiles": (
                        requester,
                        *context.approver_profiles,
                    ),
                }
            ),
        )

    assert caught.value.code is ProcurementPolicyErrorCode.SEPARATION_OF_DUTIES


def test_inactive_or_unprivileged_approver_blocks_route() -> None:
    draft, context = ready_facts("12000.00")

    with pytest.raises(ProcurementPolicyError) as caught:
        engine("EMP-BUDGET-OWNER").evaluate(
            workflow_run_id="run-invalid-approver",
            draft=draft,
            context=context,
        )

    assert caught.value.code is ProcurementPolicyErrorCode.APPROVER_INVALID
