"""Deterministic V5 procurement validation, plan and approval routing."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Protocol
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session, sessionmaker

from app.actions.contracts import (
    ActionPlan,
    ActionPlanStep,
    ActionProposal,
    ActionStepReversibility,
)
from app.approval.contracts import ApprovalRoute, ApprovalRouteStage
from app.auth.contracts import RoleName
from app.auth.repository import AuthRepository
from app.integrations.enterprise_ops import EnterpriseOpsEmployeeLifecycleProfile
from app.scenarios.procurement.contracts import (
    ProcurementItemDraft,
    ProcurementRequestContext,
    ProcurementRequestDraft,
)


class ProcurementAmountBand(str, Enum):
    """Stable A/B/C amount bands derived only from structured policy facts."""

    A = "A"
    B = "B"
    C = "C"


class ProcurementPolicyErrorCode(str, Enum):
    """Safe, deterministic reasons why automatic procurement must stop."""

    MISSING_INFORMATION = "MISSING_INFORMATION"
    REQUESTER_INVALID = "REQUESTER_INVALID"
    ITEM_INVALID = "ITEM_INVALID"
    ITEM_CATEGORY_NOT_ALLOWED = "ITEM_CATEGORY_NOT_ALLOWED"
    AMOUNT_INVALID = "AMOUNT_INVALID"
    COST_CENTER_NOT_FOUND = "COST_CENTER_NOT_FOUND"
    COST_CENTER_INACTIVE = "COST_CENTER_INACTIVE"
    COST_CENTER_FORBIDDEN = "COST_CENTER_FORBIDDEN"
    CURRENCY_MISMATCH = "CURRENCY_MISMATCH"
    DESIRED_DATE_INVALID = "DESIRED_DATE_INVALID"
    BUSINESS_REASON_INVALID = "BUSINESS_REASON_INVALID"
    POLICY_NOT_FOUND = "POLICY_NOT_FOUND"
    POLICY_INACTIVE = "POLICY_INACTIVE"
    POLICY_NOT_EFFECTIVE = "POLICY_NOT_EFFECTIVE"
    BUDGET_INSUFFICIENT = "BUDGET_INSUFFICIENT"
    DUPLICATE_RISK = "DUPLICATE_RISK"
    APPROVER_INVALID = "APPROVER_INVALID"
    SEPARATION_OF_DUTIES = "SEPARATION_OF_DUTIES"


class ProcurementPolicyError(ValueError):
    """A safe policy rejection with a stable machine-readable code."""

    def __init__(self, code: ProcurementPolicyErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


class ApprovalEligibilityPort(Protocol):
    """Platform identity check kept separate from enterprise business facts."""

    def is_active_approver(self, employee_id: str) -> bool: ...


class AuthApprovalEligibility:
    """Read the authoritative BizOrch account and approver role."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def is_active_approver(self, employee_id: str) -> bool:
        with self._session_factory() as session:
            user = AuthRepository(session).find_user_by_employee_id(employee_id)
            return bool(
                user
                and user.active
                and any(
                    role.role == RoleName.APPROVER.value for role in user.roles
                )
            )


class ProcurementPolicyDecision(BaseModel):
    """Fully deterministic plan and route ready for authoritative persistence."""

    model_config = ConfigDict(frozen=True)

    amount_band: ProcurementAmountBand
    amount: Decimal
    currency: str
    cost_center_code: str
    cost_center_available_amount: Decimal
    policy_code: str
    policy_version: int
    plan: ActionPlan
    route: ApprovalRoute


class ProcurementPolicyEngine:
    """Validate procurement facts without an LLM, knowledge text or browser input."""

    _VAGUE_REASONS = frozenset(
        {
            "工作需要",
            "领导安排",
            "采购需要",
            "办公需要",
            "临时使用",
            "business need",
            "work requirement",
        }
    )
    _DANGEROUS_TEXT = (
        "<script",
        "javascript:",
        "ignore previous",
        "system prompt",
        "忽略之前",
        "系统提示词",
    )

    def __init__(
        self,
        approval_eligibility: ApprovalEligibilityPort,
        *,
        today: Callable[[], date] = date.today,
    ) -> None:
        self._approval_eligibility = approval_eligibility
        self._today = today

    def evaluate(
        self,
        *,
        workflow_run_id: str,
        draft: ProcurementRequestDraft,
        context: ProcurementRequestContext,
    ) -> ProcurementPolicyDecision:
        """Return a fixed single-step plan and a 1-3 stage serial route."""
        if draft.missing_fields():
            self._raise(
                ProcurementPolicyErrorCode.MISSING_INFORMATION,
                "procurement request information is incomplete",
            )
        requester = context.requester_profile
        if not self._active_profile(requester, expected_id=draft.requester_id):
            self._raise(
                ProcurementPolicyErrorCode.REQUESTER_INVALID,
                "requester is not an active enterprise employee",
            )
        assert requester is not None

        policy = context.policy
        if policy is None:
            self._raise(
                ProcurementPolicyErrorCode.POLICY_NOT_FOUND,
                "current procurement policy does not exist",
            )
        assert policy is not None
        if not policy.active:
            self._raise(
                ProcurementPolicyErrorCode.POLICY_INACTIVE,
                "current procurement policy is inactive",
            )
        business_date = self._today()
        if policy.effective_from > business_date:
            self._raise(
                ProcurementPolicyErrorCode.POLICY_NOT_EFFECTIVE,
                "current procurement policy is not effective yet",
            )
        if (
            policy.level_one_limit <= 0
            or policy.level_two_limit <= policy.level_one_limit
        ):
            self._raise(
                ProcurementPolicyErrorCode.POLICY_INACTIVE,
                "procurement policy amount thresholds are invalid",
            )

        items = self._validate_items(draft.items, policy.allowed_item_categories)
        amount = self._validate_amount(draft.estimated_total_amount)
        cost_center = context.cost_center
        if cost_center is None:
            self._raise(
                ProcurementPolicyErrorCode.COST_CENTER_NOT_FOUND,
                "cost center does not exist",
            )
        assert cost_center is not None
        if not cost_center.active:
            self._raise(
                ProcurementPolicyErrorCode.COST_CENTER_INACTIVE,
                "cost center is inactive",
            )
        if cost_center.department_code != requester.employee.department_code:
            self._raise(
                ProcurementPolicyErrorCode.COST_CENTER_FORBIDDEN,
                "requester is not assigned to the cost center department",
            )
        if cost_center.currency != "CNY" or policy.currency != "CNY":
            self._raise(
                ProcurementPolicyErrorCode.CURRENCY_MISMATCH,
                "V5 procurement only supports CNY",
            )
        if cost_center.currency != policy.currency:
            self._raise(
                ProcurementPolicyErrorCode.CURRENCY_MISMATCH,
                "cost center and procurement policy currencies differ",
            )
        if amount > cost_center.available_amount:
            self._raise(
                ProcurementPolicyErrorCode.BUDGET_INSUFFICIENT,
                "cost center available budget is insufficient",
            )

        desired_date = draft.desired_date
        assert desired_date is not None
        if desired_date < business_date:
            self._raise(
                ProcurementPolicyErrorCode.DESIRED_DATE_INVALID,
                "desired date must not be in the past",
            )
        delivery_location = self._safe_text(
            draft.delivery_location_code,
            field="delivery location",
            minimum=2,
            maximum=100,
            error_code=ProcurementPolicyErrorCode.ITEM_INVALID,
        )
        reason = self._validate_reason(draft.business_reason)
        if self._has_similar_open_request(items, context):
            self._raise(
                ProcurementPolicyErrorCode.DUPLICATE_RISK,
                "a similar open procurement request already exists",
            )

        manager_id = requester.employee.manager_id
        if not manager_id:
            self._raise(
                ProcurementPolicyErrorCode.APPROVER_INVALID,
                "requester has no authoritative manager",
            )
        band = self._amount_band(
            amount,
            level_one_limit=policy.level_one_limit,
            level_two_limit=policy.level_two_limit,
        )
        route = self._route(
            band,
            route_version=policy.version,
            requester_id=draft.requester_id,
            manager_id=manager_id,
            budget_owner_id=cost_center.budget_owner_id,
            procurement_approver_id=policy.procurement_approver_id,
            context=context,
        )
        plan = self._plan(
            workflow_run_id=workflow_run_id,
            draft=draft,
            items=items,
            amount=amount,
            currency=policy.currency,
            policy_code=policy.policy_code,
            policy_version=policy.version,
            expected_cost_center_version=cost_center.version,
            expected_reserved_amount=cost_center.reserved_amount,
            expected_business_approver_id=manager_id,
            expected_budget_owner_id=cost_center.budget_owner_id,
            expected_procurement_approver_id=policy.procurement_approver_id,
            delivery_location=delivery_location,
            reason=reason,
        )
        return ProcurementPolicyDecision(
            amount_band=band,
            amount=amount,
            currency=policy.currency,
            cost_center_code=cost_center.cost_center_code,
            cost_center_available_amount=cost_center.available_amount,
            policy_code=policy.policy_code,
            policy_version=policy.version,
            plan=plan,
            route=route,
        )

    def _route(
        self,
        band: ProcurementAmountBand,
        *,
        route_version: int,
        requester_id: str,
        manager_id: str,
        budget_owner_id: str,
        procurement_approver_id: str,
        context: ProcurementRequestContext,
    ) -> ApprovalRoute:
        stage_values = [
            ("BUSINESS_CONFIRMATION", manager_id),
        ]
        if band in {ProcurementAmountBand.B, ProcurementAmountBand.C}:
            stage_values.append(("BUDGET_CONFIRMATION", budget_owner_id))
        if band is ProcurementAmountBand.C:
            stage_values.append(
                ("PROCUREMENT_CONFIRMATION", procurement_approver_id)
            )
        approver_profiles = {
            profile.employee.employee_id: profile
            for profile in context.approver_profiles
        }
        for _, approver_id in stage_values:
            if not self._active_profile(
                approver_profiles.get(approver_id),
                expected_id=approver_id,
            ) or not self._approval_eligibility.is_active_approver(approver_id):
                self._raise(
                    ProcurementPolicyErrorCode.APPROVER_INVALID,
                    f"approval actor is unavailable: {approver_id}",
                )
        try:
            route = ApprovalRoute(
                route_version=route_version,
                stages=tuple(
                    ApprovalRouteStage(
                        stage_order=index,
                        stage_code=stage_code,
                        approver_id=approver_id,
                    )
                    for index, (stage_code, approver_id) in enumerate(
                        stage_values, start=1
                    )
                ),
            )
            route.require_separation_of_duties(requester_id)
            return route
        except ValueError as exc:
            raise ProcurementPolicyError(
                ProcurementPolicyErrorCode.SEPARATION_OF_DUTIES,
                "approval route violates separation of duties",
            ) from exc

    @classmethod
    def _plan(
        cls,
        *,
        workflow_run_id: str,
        draft: ProcurementRequestDraft,
        items: tuple[ProcurementItemDraft, ...],
        amount: Decimal,
        currency: str,
        policy_code: str,
        policy_version: int,
        expected_cost_center_version: int,
        expected_reserved_amount: Decimal,
        expected_business_approver_id: str,
        expected_budget_owner_id: str,
        expected_procurement_approver_id: str,
        delivery_location: str,
        reason: str,
    ) -> ActionPlan:
        assert draft.cost_center_code is not None
        assert draft.desired_date is not None
        plan_id = str(
            uuid5(NAMESPACE_URL, f"bizorch:procurement:plan:{workflow_run_id}:v1")
        )
        action_id = str(
            uuid5(
                NAMESPACE_URL,
                f"bizorch:procurement:action:{workflow_run_id}:v1",
            )
        )
        item_payload = [
            {
                "line_no": line_no,
                "item_name": item.item_name,
                "item_category": item.item_category,
                "quantity": item.quantity,
                "specification_note": item.specification_note,
            }
            for line_no, item in enumerate(items, start=1)
        ]
        proposal = ActionProposal(
            action_id=action_id,
            action_type="CREATE_PROCUREMENT_REQUEST_AND_RESERVE_BUDGET",
            target_resource=f"cost-center/{draft.cost_center_code}",
            parameters={
                "workflow_run_id": workflow_run_id,
                "requester_id": draft.requester_id,
                "items": item_payload,
                "estimated_total_amount": format(amount, ".2f"),
                "currency": currency,
                "cost_center_code": draft.cost_center_code,
                "desired_date": draft.desired_date.isoformat(),
                "delivery_location_code": delivery_location,
                "business_reason_summary": reason,
                "policy_code": policy_code,
                "policy_version": policy_version,
                "expected_cost_center_version": expected_cost_center_version,
                "expected_reserved_amount": format(
                    expected_reserved_amount, ".2f"
                ),
                "expected_business_approver_id": (
                    expected_business_approver_id
                ),
                "expected_budget_owner_id": expected_budget_owner_id,
                "expected_procurement_approver_id": (
                    expected_procurement_approver_id
                ),
            },
            version=1,
            content_summary=(
                f"为{draft.requester_id}创建{format(amount, '.2f')}元办公采购申请"
                f"并从{draft.cost_center_code}原子预占同额预算"
            ),
        )
        return ActionPlan(
            plan_id=plan_id,
            scenario_key="procurement",
            plan_type="OFFICE_PROCUREMENT",
            subject_reference=draft.cost_center_code,
            version=1,
            content_summary=(
                f"办公采购：{len(items)}项，预计{format(amount, '.2f')}元，"
                f"成本中心{draft.cost_center_code}"
            ),
            steps=(
                ActionPlanStep(
                    step_id="create-request-and-reserve-budget",
                    step_order=1,
                    proposal=proposal,
                    reversibility=ActionStepReversibility.MANUAL_ONLY,
                ),
            ),
        )

    @classmethod
    def _validate_items(
        cls,
        items: tuple[ProcurementItemDraft, ...],
        allowed_categories: tuple[str, ...],
    ) -> tuple[ProcurementItemDraft, ...]:
        if not 1 <= len(items) <= 20:
            cls._raise(
                ProcurementPolicyErrorCode.ITEM_INVALID,
                "procurement request must contain 1 to 20 items",
            )
        allowed = frozenset(allowed_categories)
        normalized: list[ProcurementItemDraft] = []
        for item in items:
            item_name = cls._safe_text(
                item.item_name,
                field="item name",
                minimum=2,
                maximum=200,
                error_code=ProcurementPolicyErrorCode.ITEM_INVALID,
            )
            category = cls._safe_text(
                item.item_category,
                field="item category",
                minimum=2,
                maximum=100,
                error_code=ProcurementPolicyErrorCode.ITEM_INVALID,
            )
            if category not in allowed:
                cls._raise(
                    ProcurementPolicyErrorCode.ITEM_CATEGORY_NOT_ALLOWED,
                    f"item category is not allowed: {category}",
                )
            if item.quantity is None or not 1 <= item.quantity <= 999:
                cls._raise(
                    ProcurementPolicyErrorCode.ITEM_INVALID,
                    "item quantity must be between 1 and 999",
                )
            note = (
                cls._safe_text(
                    item.specification_note,
                    field="specification note",
                    minimum=1,
                    maximum=500,
                    error_code=ProcurementPolicyErrorCode.ITEM_INVALID,
                )
                if item.specification_note is not None
                else None
            )
            normalized.append(
                ProcurementItemDraft(
                    item_name=item_name,
                    item_category=category,
                    quantity=item.quantity,
                    specification_note=note,
                )
            )
        return tuple(normalized)

    @classmethod
    def _validate_amount(cls, value: Decimal | None) -> Decimal:
        if value is None or not value.is_finite() or value <= 0:
            cls._raise(
                ProcurementPolicyErrorCode.AMOUNT_INVALID,
                "estimated amount must be a positive decimal",
            )
        assert value is not None
        if value.as_tuple().exponent < -2:
            cls._raise(
                ProcurementPolicyErrorCode.AMOUNT_INVALID,
                "estimated amount supports at most two decimal places",
            )
        return value.quantize(Decimal("0.01"))

    @classmethod
    def _validate_reason(cls, value: str | None) -> str:
        reason = cls._safe_text(
            value,
            field="business reason",
            minimum=8,
            maximum=500,
            error_code=ProcurementPolicyErrorCode.BUSINESS_REASON_INVALID,
        )
        if reason.casefold() in cls._VAGUE_REASONS:
            cls._raise(
                ProcurementPolicyErrorCode.BUSINESS_REASON_INVALID,
                "business reason is too vague",
            )
        return reason

    @classmethod
    def _safe_text(
        cls,
        value: str | None,
        *,
        field: str,
        minimum: int,
        maximum: int,
        error_code: ProcurementPolicyErrorCode,
    ) -> str:
        normalized = " ".join(value.split()) if value is not None else ""
        lowered = normalized.casefold()
        if (
            not minimum <= len(normalized) <= maximum
            or any(
                unicodedata.category(character) == "Cc"
                for character in normalized
            )
            or any(token in lowered for token in cls._DANGEROUS_TEXT)
        ):
            cls._raise(error_code, f"{field} is invalid")
        return normalized

    @staticmethod
    def _amount_band(
        amount: Decimal,
        *,
        level_one_limit: Decimal,
        level_two_limit: Decimal,
    ) -> ProcurementAmountBand:
        if amount <= level_one_limit:
            return ProcurementAmountBand.A
        if amount <= level_two_limit:
            return ProcurementAmountBand.B
        return ProcurementAmountBand.C

    @classmethod
    def _has_similar_open_request(
        cls,
        items: tuple[ProcurementItemDraft, ...],
        context: ProcurementRequestContext,
    ) -> bool:
        requested_names = {
            cls._comparison_key(item.item_name or "") for item in items
        }
        requested_names.discard("")
        for open_request in context.open_requests:
            existing_names = {
                cls._comparison_key(item.item_name)
                for item in open_request.items
            }
            if requested_names & existing_names:
                return True
            if any(
                min(len(left), len(right)) >= 4
                and (left in right or right in left)
                for left in requested_names
                for right in existing_names
            ):
                return True
        return False

    @staticmethod
    def _comparison_key(value: str) -> str:
        return re.sub(r"[\W_]+", "", value, flags=re.UNICODE).casefold()

    @staticmethod
    def _active_profile(
        profile: EnterpriseOpsEmployeeLifecycleProfile | None,
        *,
        expected_id: str,
    ) -> bool:
        if profile is None:
            return False
        # Strong contracts are validated at the integration boundary. Keeping
        # these accesses explicit makes every authority fact reviewable.
        employee = profile.employee
        account = profile.account
        return bool(
            employee.employee_id == expected_id
            and employee.active
            and employee.employment_status == "ACTIVE"
            and profile.department.active
            and profile.job.active
            and account is not None
            and account.status == "ACTIVE"
        )

    @staticmethod
    def _raise(code: ProcurementPolicyErrorCode, message: str) -> None:
        raise ProcurementPolicyError(code, message)
